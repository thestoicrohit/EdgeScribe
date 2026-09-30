import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from edgescribe import core as core_mod, db, server


def wait_idle(core, timeout=90):
    t = time.time()
    time.sleep(0.3)
    while core._busy() and time.time() - t < timeout:
        time.sleep(0.2)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init(":memory:")
        cls.core = core_mod.Core(load_asr=False, speech=False)

    def setUp(self):
        self.core.reset()

    def test_sim_session_end_to_end(self):
        q = self.core.hub.subscribe()
        self.core.run_sim(seed=5, hard=0.3, speed=0, source="synth")
        wait_idle(self.core)
        msgs = []
        while not q.empty():
            msgs.append(q.get())
        types = [m["type"] for m in msgs]
        self.assertIn("session_start", types)
        self.assertIn("segment", types)
        end = next(m for m in msgs if m["type"] == "session_end")
        self.assertAlmostEqual(end["audio_sec"], 60.0, delta=0.1)
        self.assertLess(end["awake_sec"], end["audio_sec"])
        self.assertIsNotNone(end["f1"])
        row = db.one("SELECT * FROM sessions WHERE id=?", (end["id"],))
        self.assertIsNotNone(row["ended"])
        self.assertGreater(db.one("SELECT COUNT(*) n FROM sentences")["n"], 0)

    def test_feedback_updates_models_and_gamification(self):
        r = self.core.feedback_sentence("Remember to submit the lab report by Friday.", 1)
        self.assertGreater(r["after"], r["before"])
        self.assertGreater(db.kv_get("xp"), 0)

    def test_training_logs_baseline_then_improves(self):
        self.core.train("clf", 6)
        t = time.time()
        while self.core.training and time.time() - t < 60:
            time.sleep(0.2)
        rows = db.query("SELECT f1,note FROM learn_log WHERE learner='action' ORDER BY id")
        self.assertIn("baseline", rows[0]["note"])
        self.assertGreater(rows[-1]["f1"], 0.8)

    def test_wav_validation(self):
        with self.assertRaises(ValueError):
            core_mod.read_wav(b"not a wav")


class ServerTests(unittest.TestCase):
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

    def req(self, path, body=None, headers=None, method=None):
        h = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_state_and_static(self):
        code, body = self.req("/api/state")
        self.assertEqual(code, 200)
        self.assertIn("kpis", json.loads(body))
        self.assertEqual(self.req("/")[0], 200)
        self.assertEqual(self.req("/app.js")[0], 200)

    def test_post_requires_custom_header(self):
        self.assertEqual(self.req("/api/reset", {})[0], 403)
        self.assertEqual(self.req("/api/reset", {}, {"X-EdgeScribe": "1"})[0], 200)

    def test_foreign_host_and_origin_rejected(self):
        self.assertEqual(self.req("/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("/api/state", headers={"Origin": "https://evil.example"})[0], 403)

    def test_no_path_traversal(self):
        self.assertEqual(self.req("/..%2fedgescribe/config.py")[0], 404)
        self.assertEqual(self.req("/%2e%2e/edgescribe/config.py")[0], 404)

    def test_unknown_route_and_bad_input(self):
        self.assertEqual(self.req("/api/nope", {}, {"X-EdgeScribe": "1"})[0], 404)
        self.assertEqual(self.req("/api/feedback/sentence", {}, {"X-EdgeScribe": "1"})[0], 400)


if __name__ == "__main__":
    unittest.main()
