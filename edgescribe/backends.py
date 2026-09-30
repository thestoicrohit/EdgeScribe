"""Model backends + hardware detection. Everything runs locally unless the user opts into online TTS.

ASR chain (first that works wins):
  1. faster-whisper (CPU int8)      -> on Snapdragon: swap for a Qualcomm AI Hub Whisper on the
                                       QNN execution provider (NPU); see README.
  2. Vosk (any OS, offline)         -> pip install vosk + a ~40 MB model in data/models/.
  3. Windows offline dictation      -> System.Speech, ships with Windows, no download.
  4. none                           -> speech is still detected; no transcript.
All probing of native libraries happens in subprocesses: a broken DLL can't crash the server.
"""
import json
import logging
import os
import platform
import re
import subprocess
import sys
import time
import urllib.request
import uuid

import numpy as np

from . import audioio, config, denoise

log = logging.getLogger("edgescribe.backends")
ALLOW_DOWNLOAD = os.environ.get("EDGESCRIBE_ASR_DOWNLOAD") == "1"


# ---- ASR ---------------------------------------------------------------------------------
class NullASR:
    name, device, available, simulated = "none", "-", False, False

    def transcribe(self, audio, t0=0.0, t1=0.0, noise_ref=None):
        return "", 0.0


class SimASR:
    """Reads the ground-truth script of a simulated episode (clearly labelled SIMULATED)."""
    name, device, available, simulated = "simulated (ground-truth script)", "-", True, True

    def __init__(self, utts):
        self.utts = utts

    def transcribe(self, audio, t0=0.0, t1=0.0, noise_ref=None):
        return " ".join(u["text"] for u in self.utts if u["t1"] > t0 and u["t0"] < t1), 1.0


class WhisperASR:
    name, device, available, simulated = "Whisper tiny.en (faster-whisper)", "CPU int8", True, False

    def __init__(self, size="tiny.en"):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(size, device="cpu", compute_type="int8", local_files_only=not ALLOW_DOWNLOAD)

    def transcribe(self, audio, t0=0.0, t1=0.0, noise_ref=None):
        segs, _ = self.model.transcribe(audio.astype(np.float32), language="en")
        segs = list(segs)
        conf = float(np.mean([np.exp(s.avg_logprob) for s in segs])) if segs else 0.0
        return " ".join(s.text.strip() for s in segs), conf


class WindowsASR:
    name, device, available, simulated = "Windows offline dictation (en-US)", "CPU", True, False

    def __init__(self, speech, settings):
        self.speech, self.settings = speech, settings

    def transcribe(self, audio, t0=0.0, t1=0.0, noise_ref=None):
        if len(audio) < 1600:
            return "", 0.0
        prm = denoise.params_for(denoise.snr_db(audio, noise_ref)) if self.settings().get("denoise", True) else None
        if prm:
            audio = denoise.suppress(audio, noise_ref=noise_ref, strength=prm[0], floor=prm[1])
        path = config.DATA / "tmp" / f"asr-{uuid.uuid4().hex}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            audioio.write_wav(path, audio)
            return self.speech.asr_file(path, len(audio) / config.SR)
        finally:
            path.unlink(missing_ok=True)


def as_sentences(phrases):
    """Recognizers return unpunctuated phrases: make each one a sentence."""
    out = []
    for t in phrases:
        t = " ".join(str(t).split())
        if t:
            t = t[0].upper() + t[1:]
            out.append(t if t[-1] in ".!?" else t + ".")
    return " ".join(out)


def vosk_model_path():
    """EDGESCRIBE_VOSK_MODEL, else the first data/models/vosk-model-* folder. Never downloads."""
    env = os.environ.get("EDGESCRIBE_VOSK_MODEL")
    if env:
        return env if os.path.isdir(env) else None
    models = config.DATA / "models"
    found = sorted(models.glob("vosk-model*")) if models.is_dir() else []
    found = [f for f in found if f.is_dir()]
    return str(found[0]) if found else None


class VoskASR:
    name, device, available, simulated = "Vosk (offline, any OS)", "CPU", True, False

    def __init__(self, model_path):
        import vosk
        vosk.SetLogLevel(-1)
        self.vosk = vosk
        self.model = vosk.Model(model_path)
        self.name = f"Vosk {os.path.basename(model_path)}"

    def transcribe(self, audio, t0=0.0, t1=0.0, noise_ref=None):
        rec = self.vosk.KaldiRecognizer(self.model, config.SR)
        rec.SetWords(True)
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
        results = []
        for i in range(0, len(pcm), 8000):
            if rec.AcceptWaveform(pcm[i:i + 8000]):
                results.append(json.loads(rec.Result()))
        results.append(json.loads(rec.FinalResult()))
        results = [r for r in results if r.get("text")]
        confs = [w.get("conf", 0.0) for r in results for w in r.get("result", [])]
        return as_sentences(r["text"] for r in results), float(np.mean(confs)) if confs else 0.0


_VOSK_PROBE = "\n".join([
    "import sys, vosk",
    "vosk.SetLogLevel(-1)",
    "vosk.KaldiRecognizer(vosk.Model(sys.argv[1]), 16000)",
    "print('PROBE_OK')"])


def vosk_works(model_path):
    try:
        r = subprocess.run([sys.executable, "-c", _VOSK_PROBE, model_path], capture_output=True, timeout=120)
        return b"PROBE_OK" in r.stdout
    except Exception:
        return False


_PROBE = "\n".join([
    "import numpy as np",
    "from faster_whisper import WhisperModel",
    "m = WhisperModel('tiny.en', device='cpu', compute_type='int8', local_files_only=%s)",
    "list(m.transcribe(np.zeros(16000, dtype='float32'))[0])",
    "print('PROBE_OK')"])


def whisper_works():
    try:
        r = subprocess.run([sys.executable, "-c", _PROBE % (not ALLOW_DOWNLOAD)], capture_output=True, timeout=180)
        return b"PROBE_OK" in r.stdout
    except Exception:
        return False


def load_asr(speech=None, settings=lambda: {}):
    if whisper_works():
        try:
            return WhisperASR()
        except Exception as e:
            log.warning("whisper probe passed but load failed: %s", e)
    mp = vosk_model_path()
    if mp and vosk_works(mp):
        try:
            return VoskASR(mp)
        except Exception as e:
            log.warning("vosk probe passed but load failed: %s", e)
    if speech is not None:
        try:
            if speech.voices()["recognizers"]:
                return WindowsASR(speech, settings)
        except Exception as e:
            log.warning("windows speech unavailable: %s", e)
    return NullASR()


# ---- Summarizer --------------------------------------------------------------------------
class Summarizer:
    def __init__(self, model="llama3.2:1b", host="http://127.0.0.1:11434"):
        self.model, self.host = model, host
        self._up, self._checked = False, 0.0

    def llm_up(self):
        if time.time() - self._checked > 15:           # cache: the header polls this
            try:
                urllib.request.urlopen(self.host, timeout=0.4)
                self._up = True
            except Exception:
                self._up = False
            self._checked = time.time()
        return self._up

    def summarize(self, text):
        """Returns (summary, backend_name)."""
        if not text.strip():
            return "", "none"
        if self.llm_up():
            try:
                prompt = "Summarize this lecture transcript in 3 short bullet points:\n\n" + text[:12000]
                req = urllib.request.Request(
                    self.host + "/api/generate",
                    data=json.dumps({"model": self.model, "prompt": prompt, "stream": False}).encode(),
                    headers={"Content-Type": "application/json"})
                out = json.loads(urllib.request.urlopen(req, timeout=120).read())["response"]
                return out.strip(), f"ollama:{self.model}"
            except Exception as e:
                log.warning("ollama summary failed: %s", e)
        return self.extractive(text), "extractive"

    @staticmethod
    def extractive(text, k=3):
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) > 3]
        freq = {}
        for w in re.findall(r"[a-z']+", text.lower()):
            if len(w) > 3:
                freq[w] = freq.get(w, 0) + 1
        score = lambda s: sum(freq.get(w, 0) for w in re.findall(r"[a-z']+", s.lower())) / (len(s.split()) + 1)
        top = sorted(sents, key=score, reverse=True)[:k]
        return "\n".join("- " + s for s in sorted(top, key=sents.index))


# ---- Hardware ----------------------------------------------------------------------------
def hardware():
    info = dict(system=f"{platform.system()} {platform.release()}", machine=platform.machine(),
                cpu=platform.processor() or "unknown", cores=os.cpu_count(),
                providers=[], npu=False, npu_note="")
    try:   # subprocess: a broken onnxruntime build can segfault the interpreter on import
        r = subprocess.run([sys.executable, "-c",
                            "import onnxruntime as o; print('PROV=' + ','.join(o.get_available_providers()))"],
                           capture_output=True, timeout=60, text=True)
        line = next((l for l in r.stdout.splitlines() if l.startswith("PROV=")), None)
        if line:
            info["providers"] = [p for p in line[5:].split(",") if p]
            info["npu"] = "QNNExecutionProvider" in info["providers"]
        else:
            info["npu_note"] = "onnxruntime unavailable"
    except Exception:
        info["npu_note"] = "onnxruntime unavailable"
    if not info["npu"]:
        info["npu_note"] = info["npu_note"] or "no QNN provider: running on CPU"
    info["snapdragon"] = ("qualcomm" in info["cpu"].lower() or "snapdragon" in info["cpu"].lower()
                          or info["machine"].lower() in ("arm64", "aarch64"))
    return info


def benchmark_snn(seconds=20):
    """Real-time factor of the spiking trigger on this machine."""
    from . import snn
    x = (0.01 * np.random.default_rng(0).standard_normal(seconds * 16000)).astype(np.float32)
    net = snn.SNN()
    t = time.perf_counter()
    for i in range(0, len(x), 8000):
        net.feed(x[i:i + 8000])
    dt = time.perf_counter() - t
    return dict(rtf=round(seconds / dt, 1), us_per_frame=round(dt / (seconds * 100) * 1e6, 1))
