"""Portability: engines for macOS / Linux / Vosk are exercised with fakes, so they're tested on any OS."""
import json
import pathlib
import sys
import tempfile
import types
import unittest

import numpy as np

from edgescribe import audioio, backends, config, tts_engines, voicebank

SAY_OUT = """Alex                en_US    # Most people recognize me by my voice.
Amélie              fr_CA    # Bonjour, je m'appelle Amélie.
Bad News            en_US    # The light you see at the end of the tunnel is the headlamp of a fast approaching train.
Samantha            en_US    # Hello! My name is Samantha.
Daniel              en_GB    # Hello, my name is Daniel.
"""
ESPEAK_OUT = """Pty Language       Age/Gender VoiceName          File                 Other Languages
 2  en-029          --/M      English_(Caribbean) gmw/en-029
 2  en-gb           --/M      English_(Great_Britain) gmw/en
 5  en-us           --/F      English_(America)  gmw/en-US
"""


class ParseTests(unittest.TestCase):
    def test_say_voice_list(self):
        v = tts_engines.parse_say_voices(SAY_OUT)
        self.assertEqual([x["name"] for x in v], ["Alex", "Bad News", "Samantha", "Daniel"])   # English only
        self.assertEqual(v[3]["culture"], "en-GB")

    def test_espeak_voice_list(self):
        v = tts_engines.parse_espeak_voices(ESPEAK_OUT)
        self.assertEqual([x["name"] for x in v], ["en-029", "en-gb", "en-us"])
        self.assertEqual(v[2]["gender"], "Female")

    def test_rate_mapping(self):
        self.assertEqual(tts_engines.wpm(0), 175)
        self.assertLess(tts_engines.wpm(-5), 175)
        self.assertGreater(tts_engines.wpm(5), 175)
        self.assertEqual(tts_engines.wpm(99), tts_engines.wpm(5))       # clamped


FAKE_TTS = r'''
import sys, wave, math, struct
args = sys.argv[1:]
out = args[args.index("-w") + 1]
text = open(args[args.index("-f") + 1], encoding="utf-8").read()
if "FAIL" in text:
    sys.stderr.write("synthesis failed"); sys.exit(3)
sr = 22050                                   # like eSpeak: not 16 kHz
n = int(sr * 0.05 * max(1, len(text.split())))
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / sr))) for i in range(n)))
'''


class FakeCmdTTS(tts_engines._CmdEngine):
    """Same _run path as the macOS/Linux engines, but the 'binary' is a Python script."""
    name = "fake"

    def __init__(self, script):
        self.exe = sys.executable
        self.script = script

    def voices(self):
        return [dict(name="fake-a", culture="en-US", gender=""), dict(name="fake-b", culture="en-GB", gender="")]

    def tts_to_file(self, text, path, voice=None, rate=0):
        self._run([self.script, "-w", str(path)], text)


class CommandEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = pathlib.Path(tempfile.mkdtemp())
        (cls.dir / "fake_tts.py").write_text(FAKE_TTS, "utf-8")
        cls.eng = FakeCmdTTS(str(cls.dir / "fake_tts.py"))

    def test_text_goes_through_a_file_even_if_it_looks_like_an_option(self):
        wav = self.eng.tts("-v evil --help remember the lab report")
        self.assertEqual(wav[:4], b"RIFF")
        self.assertGreater(len(audioio.decode_wav(wav)), 1000)

    def test_engine_errors_surface(self):
        with self.assertRaises(RuntimeError):
            self.eng.tts("please FAIL now")

    def test_voice_bank_builds_from_any_engine_and_resamples(self):
        old_dir, old_index = voicebank.DIR, voicebank.INDEX
        voicebank.DIR = self.dir / "vb"
        voicebank.INDEX = voicebank.DIR / "index.json"
        try:
            b = voicebank.Bank(self.eng)
            b.build(target=6)
            self.assertEqual(len(b.clips), 6)
            x = b.audio(b.clips[0])
            words = len(b.clips[0]["text"].split())
            self.assertAlmostEqual(len(x) / config.SR, 0.05 * words, delta=0.15)   # 22.05 kHz -> 16 kHz
            ep = b.episode(1, dur=30, hard=0.2)
            self.assertTrue(ep["utts"])
        finally:
            voicebank.DIR, voicebank.INDEX = old_dir, old_index


class FakeRecognizer:
    def __init__(self, model, sr):
        self.chunks = 0

    def SetWords(self, on):
        pass

    def AcceptWaveform(self, data):
        self.chunks += 1
        return self.chunks == 2

    def Result(self):
        return json.dumps({"text": "remember to submit the lab report", "result": [{"conf": 0.9}, {"conf": 0.7}]})

    def FinalResult(self):
        return json.dumps({"text": "by friday", "result": [{"conf": 0.8}]})


class VoskTests(unittest.TestCase):
    def test_vosk_backend_turns_results_into_sentences(self):
        fake = types.SimpleNamespace(SetLogLevel=lambda n: None, Model=lambda p: object(), KaldiRecognizer=FakeRecognizer)
        sys.modules["vosk"] = fake
        try:
            asr = backends.VoskASR("/models/vosk-model-small-en-us-0.15")
            text, conf = asr.transcribe(np.zeros(16000, np.float32))
        finally:
            del sys.modules["vosk"]
        self.assertEqual(text, "Remember to submit the lab report. By friday.")
        self.assertAlmostEqual(conf, 0.8, places=5)
        self.assertIn("vosk-model-small", asr.name)

    def test_model_discovery_never_invents_a_path(self):
        import os
        old = os.environ.pop("EDGESCRIBE_VOSK_MODEL", None)
        os.environ["EDGESCRIBE_VOSK_MODEL"] = str(pathlib.Path(tempfile.mkdtemp()) / "missing")
        try:
            self.assertIsNone(backends.vosk_model_path())
        finally:
            os.environ.pop("EDGESCRIBE_VOSK_MODEL")
            if old is not None:
                os.environ["EDGESCRIBE_VOSK_MODEL"] = old


class AudioIOTests(unittest.TestCase):
    def test_roundtrip_and_stereo_resample(self):
        x = (0.5 * np.sin(2 * np.pi * 440 * np.arange(16000) / 16000)).astype(np.float32)
        y = audioio.decode_wav(audioio.encode_wav(x))
        self.assertLess(np.abs(x - y).max(), 1e-3)
        import io, wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
            w.writeframes(np.zeros(48000 * 2, np.int16).tobytes())
        self.assertEqual(len(audioio.decode_wav(buf.getvalue())), 16000)

    def test_detect_returns_an_engine_or_none(self):
        eng = tts_engines.detect(None)
        self.assertTrue(eng is None or hasattr(eng, "tts_to_file"))


class DoctorTests(unittest.TestCase):
    def test_doctor_reports_core_and_recognition(self):
        from edgescribe import doctor
        out = doctor.run()
        self.assertIn("numpy", out)
        self.assertIn("speech recognition", out)
        self.assertIn("python run.py", out)


if __name__ == "__main__":
    unittest.main()
