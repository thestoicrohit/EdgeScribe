"""A bank of real spoken sentences (the machine's offline TTS voices) with known transcripts and
labels. Used to train and score the spiking trigger on actual speech acoustics, and to measure
the speech recogniser's word error rate end-to-end. Still synthetic voices, not human recordings:
labelled 'voice bank' everywhere it appears."""
import hashlib
import json
import logging
import random
import threading

import numpy as np

from . import audioio, config, corpus, sim
from .config import SR

log = logging.getLogger("edgescribe.voicebank")
DIR = config.DATA / "voicebank"
INDEX = DIR / "index.json"
TARGET = 60


_read = audioio.read_wav          # resamples: eSpeak writes 22.05 kHz
_write = audioio.write_wav


def trim(x, pad=0.05):
    """Cut leading/trailing silence (10 ms frames, -40 dB relative to the loudest frame)."""
    n = len(x) // 160
    if n == 0:
        return x
    e = np.sqrt((x[:n * 160].reshape(n, 160) ** 2).mean(1))
    on = np.nonzero(e > e.max() * 0.01)[0]
    if not len(on):
        return x
    a = max(0, on[0] * 160 - int(pad * SR))
    b = min(len(x), (on[-1] + 1) * 160 + int(pad * SR))
    return x[a:b]


class Bank:
    def __init__(self, engine):
        self.engine = engine             # any tts_engines engine, or None
        self.clips = []
        self.lock = threading.Lock()
        self.building = False
        self._cache = {}
        self.load()

    def load(self):
        try:
            self.clips = [c for c in json.loads(INDEX.read_text("utf-8")) if (DIR / c["file"]).exists()]
        except (OSError, ValueError):
            self.clips = []
        return self

    def ready(self, need=24):
        return len(self.clips) >= need

    def status(self):
        return dict(clips=len(self.clips), target=TARGET, building=self.building,
                    voices=sorted({c["voice"] for c in self.clips}))

    def build(self, target=TARGET, progress=None):
        """Synthesize clips until the bank holds `target` (idempotent, resumable)."""
        if self.engine is None:
            return
        with self.lock:
            if self.building:
                return
            self.building = True
        try:
            DIR.mkdir(parents=True, exist_ok=True)
            voices = [v["name"] for v in self.engine.voices()][:4] or [None]
            rng = random.Random(len(self.clips) + 11)
            have = {c["text"] + "|" + c["voice"] for c in self.clips}
            while len(self.clips) < target:
                text, label = corpus.sample(rng, rng.choice(["train", "test"]), p_action=0.4)
                voice, rate = rng.choice(voices), rng.randint(-2, 2)
                if text + "|" + str(voice) in have:
                    continue
                name = hashlib.sha1(f"{text}|{voice}|{rate}".encode()).hexdigest()[:16] + ".wav"
                raw = DIR / ("raw-" + name)
                self.engine.tts_to_file(text, raw, voice, rate)
                x = trim(_read(raw))
                raw.unlink(missing_ok=True)
                _write(DIR / name, x)
                self.clips.append(dict(file=name, text=text, label=label, voice=voice or "default", rate=rate,
                                       dur=round(len(x) / SR, 3)))
                have.add(text + "|" + str(voice))
                INDEX.write_text(json.dumps(self.clips, indent=0), "utf-8")
                if progress:
                    progress(len(self.clips), target)
            log.info("voice bank ready: %d clips", len(self.clips))
        finally:
            self.building = False

    def audio(self, clip):
        x = self._cache.get(clip["file"])
        if x is None:
            x = self._cache[clip["file"]] = _read(DIR / clip["file"])
        return x

    def episode(self, seed, dur=60.0, hard=0.5, distractors=True):
        """Same contract as sim.episode, but utterances are real synthesized speech clips."""
        if not self.clips:
            raise RuntimeError("voice bank is empty")
        rng = np.random.default_rng(seed)
        x, noise, dtimes = sim.background(rng, dur, hard, None, distractors)
        n = len(x)
        utts, cursor = [], float(rng.uniform(1.0, 3.0))
        while True:
            k = int(rng.integers(1, 3))
            picks = [self.clips[int(i)] for i in rng.choice(len(self.clips), size=k, replace=False)]
            parts, sents = [], []
            for c in picks:
                parts += [self.audio(c), np.zeros(int(rng.uniform(0.25, 0.6) * SR), np.float32)]
                sents.append(dict(text=c["text"], label=c["label"], voice=c["voice"]))
            seg = np.concatenate(parts[:-1])
            L = len(seg) / SR
            if cursor + L > dur - 0.5:
                break
            peak = float(np.abs(seg).max()) or 1.0
            amp = rng.uniform(0.12 - 0.09 * hard, 0.3 - 0.15 * hard) * (1 if noise < 0.012 else 1.6)
            i = int(cursor * SR)
            x[i:i + len(seg)] += seg / peak * amp
            utts.append(dict(t0=round(cursor, 2), t1=round(cursor + L, 2),
                             text=" ".join(s["text"] for s in sents), sentences=sents))
            cursor += L + float(rng.uniform(2.5, 9.0))
        return dict(audio=np.clip(x[:n], -1, 1).astype(np.float32), utts=utts, noise=noise,
                    distractor_times=dtimes, seed=int(seed), voice=True)
