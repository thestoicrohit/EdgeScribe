import json
import pathlib
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np

from edgescribe import core as core_mod, db, denoise, server, textmetrics, winspeech


class MetricTests(unittest.TestCase):
    def test_wer(self):
        self.assertEqual(textmetrics.wer("submit the lab report", "submit the lab report"), (0, 4))
        self.assertEqual(textmetrics.wer("submit the lab report", "submit lab report now"), (2, 4))
        self.assertEqual(textmetrics.wer("please e-mail me", "please email me"), (0, 3))
        self.assertEqual(textmetrics.wer("", "noise"), (1, 0))


class DenoiseTests(unittest.TestCase):
    def test_stft_roundtrip_is_exact(self):
        x = np.random.default_rng(0).standard_normal(16000).astype(np.float32) * 0.1
        self.assertLess(np.abs(denoise.istft(denoise.stft(x), len(x)) - x).max(), 1e-4)

    def test_snr_estimate_orders_noise_levels(self):
        t = np.arange(32000) / 16000
        speech = (0.25 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 2 * t) > 0)).astype(np.float32)
        rng = np.random.default_rng(1)
        quiet = denoise.snr_db(speech + 0.001 * rng.standard_normal(len(t)))
        loud = denoise.snr_db(speech + 0.02 * rng.standard_normal(len(t)))
        self.assertGreater(quiet, denoise.ADAPTIVE_SNR_DB)
        self.assertLess(loud, quiet)

    def test_suppression_improves_snr(self):
        rng = np.random.default_rng(2)
        t = np.arange(48000) / 16000
        on = (t % 1.0) < 0.4                                   # 0.4 s tone bursts, 0.6 s gaps
        clean = 0.2 * np.sin(2 * np.pi * 440 * t) * on
        x = (clean + 0.02 * rng.standard_normal(len(t))).astype(np.float32)
        y = denoise.suppress(x)
        self.assertEqual(len(y), len(x))
        gap = ~on & (np.abs(t % 1.0 - 0.7) < 0.2)             # well inside the gaps
        burst = on & (np.abs(t % 1.0 - 0.2) < 0.1)
        snr = lambda s: 10 * np.log10((s[burst] ** 2).mean() / (s[gap] ** 2).mean())
        self.assertGreater(snr(y), snr(x) + 6)                # at least 6 dB better


class MigrationTests(unittest.TestCase):
    def test_upgrade_from_v1_keeps_rows(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "old.db"
        c = sqlite3.connect(p)
        c.executescript(db.MIGRATIONS[0])
        c.execute("INSERT INTO sessions(source) VALUES('old')")
        c.commit()
        c.close()
        db.init(p)
        self.assertEqual(db.version(), len(db.MIGRATIONS))
        self.assertEqual(db.one("SELECT source FROM sessions")["source"], "old")
        self.assertIn("wer_err", [r["name"] for r in db.query("PRAGMA table_info(segments)")])
        db.init(":memory:")


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init(":memory:")
        cls.core = core_mod.Core(load_asr=False, speech=False)

    def test_defaults_and_update(self):
        self.assertEqual(self.core.settings()["tts_mode"], "offline")
        self.assertEqual(self.core.update_settings({"tts_rate": 2})["tts_rate"], 2)

    def test_rejects_bad_values(self):
        for bad in ({"tts_rate": 9}, {"tts_rate": True}, {"nope": 1}, {"tts_mode": "cloud"}, {"auto_read": "yes"}):
            with self.assertRaises(ValueError):
                self.core.update_settings(bad)

    def test_settings_survive_reset(self):
        self.core.update_settings({"tts_mode": "online"})
        self.core.reset()
        self.assertEqual(self.core.settings()["tts_mode"], "online")
        self.core.update_settings({"tts_mode": "offline"})

    def test_tts_without_engine_is_a_clear_error(self):
        with self.assertRaises(RuntimeError):
            self.core.tts("hello")


@unittest.skipUnless(winspeech.supported(), "Windows speech only")
class WindowsSpeechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = winspeech.WinSpeech()

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()

    def test_tts_then_asr_roundtrip(self):
        wav = self.ws.tts("Remember to submit the lab report by Friday.", None, 0)
        self.assertEqual(wav[:4], b"RIFF")
        p = winspeech.TMP / "test-roundtrip.wav"
        p.write_bytes(wav)
        try:
            text, conf = self.ws.asr_file(p, 4)
        finally:
            p.unlink(missing_ok=True)       # also proves the worker released the file
        err, n = textmetrics.wer("Remember to submit the lab report by Friday.", text)
        self.assertLessEqual(err / n, 0.5)

    def test_bad_voice_is_an_error_and_worker_survives(self):
        with self.assertRaises(winspeech.OpError):
            self.ws.tts("x", "No Such Voice", 0)
        self.assertEqual(self.ws.tts("still alive", None, 0)[:4], b"RIFF")


class SpeechRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init(":memory:")
        server.core = core_mod.Core(load_asr=False, speech=False)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, path, body=None, raw=None):
        h = {"X-EdgeScribe": "1", "Content-Type": "application/json"}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_health_settings_voices(self):
        code, body = self.req("/api/health")
        self.assertEqual(code, 200)
        names = [c["name"] for c in json.loads(body)["components"]]
        self.assertIn("Speech recognition", names)
        self.assertEqual(self.req("/api/settings")[0], 200)
        self.assertEqual(self.req("/api/voices")[0], 200)

    def test_settings_validation_is_400(self):
        self.assertEqual(self.req("/api/settings", {"tts_rate": 50})[0], 400)
        self.assertEqual(self.req("/api/settings", raw=b"[1,2]")[0], 400)
        self.assertEqual(self.req("/api/settings", raw=b"{nope")[0], 400)

    def test_tts_unavailable_is_reported(self):
        code, body = self.req("/api/tts", {"text": "hi"})
        self.assertEqual(code, 409)
        self.assertIn("not available", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
