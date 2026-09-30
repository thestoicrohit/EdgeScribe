"""Synthetic lecture audio with known ground truth (speech spans + hard non-speech noises).

Speech-like signal = harmonic source with moving formant envelope and 3-6 Hz syllable
modulation, plus fricative noise. Distractors (mains hum, keyboard clicks, door slams) create
false-wake pressure so the spiking trigger has something real to learn from. This is a
stand-in for real recordings and is labelled SIMULATED everywhere it is shown."""
import numpy as np

from . import corpus
from .config import SR


def _speech(dur, rng):
    t = np.arange(int(dur * SR)) / SR
    f0 = rng.uniform(95, 230) * (1 + 0.08 * np.sin(2 * np.pi * rng.uniform(0.3, 0.9) * t))
    phase = 2 * np.pi * np.cumsum(f0) / SR
    f1 = 500 + 250 * np.sin(2 * np.pi * rng.uniform(0.5, 1.5) * t + rng.uniform(0, 6))
    f2 = 1500 + 500 * np.sin(2 * np.pi * rng.uniform(0.4, 1.2) * t + rng.uniform(0, 6))
    sig = np.zeros_like(t)
    for h in range(1, 28):
        fh = h * f0
        env = (np.exp(-((fh - f1) / 250) ** 2) + 0.7 * np.exp(-((fh - f2) / 400) ** 2)
               + 0.3 * np.exp(-((fh - 2600) / 500) ** 2))
        sig += env * np.sin(h * phase)
    syll = 0.55 + 0.45 * np.sin(2 * np.pi * rng.uniform(3, 6) * t) ** 2
    sig *= syll
    sig += 0.08 * rng.standard_normal(len(t)) * (np.sin(2 * np.pi * 2.2 * t) > 0.6)
    sig /= np.abs(sig).max() + 1e-9
    fade = np.minimum(1, np.minimum(t, dur - t) / 0.05)
    return sig * fade


def background(rng, dur, hard=0.5, noise=None, distractors=True):
    """Room tone + hum + clicks/typing/coughs/beeps. Returns (x float64, noise level, distractor times)."""
    n = int(dur * SR)
    # hard=0 is a quiet room (Windows dictation is usable), hard=0.7 (training/eval) is a noisy one
    noise = float(noise if noise is not None else rng.uniform(0.001 + 0.004 * hard, 0.003 + 0.04 * hard))
    x = noise * rng.standard_normal(n)
    t = np.arange(n) / SR
    if distractors and rng.random() < 0.8:      # mains hum
        x += rng.uniform(0.004, 0.02) * (np.sin(2 * np.pi * 50 * t) + 0.5 * np.sin(2 * np.pi * 150 * t))
    dtimes = []
    if distractors:
        def add(i, sig):
            sig = sig[: max(0, min(len(sig), n - i))]
            x[i:i + len(sig)] += sig
        for _ in range(int(rng.integers(2, 5 + int(6 * hard)))):
            kind = rng.choice(["click", "typing", "cough", "beep"])
            c = float(rng.uniform(1, dur - 3))
            i = int(c * SR)
            if kind == "click":
                L = int(rng.uniform(0.02, 0.09) * SR)
                add(i, rng.standard_normal(L) * np.hanning(L) * rng.uniform(0.15, 0.5))
            elif kind == "typing":
                for k in range(int(rng.integers(4, 12))):
                    L = int(0.015 * SR)
                    add(i + int(k * rng.uniform(0.08, 0.25) * SR), rng.standard_normal(L) * np.hanning(L) * rng.uniform(0.1, 0.3))
            elif kind == "cough":
                L = int(rng.uniform(0.25, 0.5) * SR)
                tt = np.arange(L) / SR
                add(i, rng.standard_normal(L) * np.hanning(L) * rng.uniform(0.1, 0.3) * (1 + np.sin(2 * np.pi * 30 * tt)) / 2)
            else:
                L = int(rng.uniform(0.5, 1.5) * SR)
                tt = np.arange(L) / SR
                add(i, np.sin(2 * np.pi * rng.uniform(800, 2500) * tt) * np.hanning(L) * rng.uniform(0.05, 0.3))
            dtimes.append(round(c, 2))
    return x, noise, dtimes


def episode(seed, dur=60.0, n_utts=None, noise=None, distractors=True, hard=0.5):
    """hard in [0,1]: 0 = clean speech, 1 = quiet speech in noise with many distractors.
    Returns dict(audio float32, utts [{t0,t1,text,sentences}], noise, distractor_times)."""
    rng = np.random.default_rng(seed)
    x, noise, dtimes = background(rng, dur, hard, noise, distractors)
    n_utts = n_utts or int(rng.integers(3, 7))
    script = corpus.lecture(n_utts * 2, seed=int(seed) * 7 + 1)
    utts, cursor = [], rng.uniform(1.0, 3.0)
    for k in range(n_utts):
        sents = script[2 * k:2 * k + 2]
        L = float(np.clip(1.2 + 0.42 * sum(len(s.split()) for s, _ in sents), 2.5, 9.0))
        if cursor + L > dur - 0.5:
            break
        amp = rng.uniform(0.12 - 0.09 * hard, 0.3 - 0.15 * hard) * (1 if noise < 0.012 else 1.6)
        i = int(cursor * SR)
        seg = _speech(L, rng) * amp
        x[i:i + len(seg)] += seg
        utts.append(dict(t0=round(cursor, 2), t1=round(cursor + L, 2),
                         text=" ".join(s for s, _ in sents), sentences=[dict(text=s, label=y) for s, y in sents]))
        cursor += L + rng.uniform(2.5, 9.0)
    return dict(audio=np.clip(x, -1, 1).astype(np.float32), utts=utts, noise=noise,
                distractor_times=dtimes, seed=int(seed))
