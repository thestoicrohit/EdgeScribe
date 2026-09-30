"""Spiking-network speech trigger: the always-on, event-driven front end.

audio -> 16 band log-energies -> adaptive level-crossing spike encoder (per-band noise floor)
      -> weighted leaky integrate-and-fire (LIF) neuron -> "wake" events.

Learning (three-factor, reward-modulated): each wake event stores the band spike pattern that
caused it. Feedback (true wake = +1, false alarm = -1) nudges the per-band synaptic weights
toward / away from that pattern, and false alarms / misses shift the encoder threshold.
Runs in numpy on the CPU: spiking nets do not map onto the NPU, whose job is the heavy models
this trigger wakes up.
"""
import numpy as np

from .config import FRAME, HOP, N_BANDS, SR

_WIN = np.hanning(FRAME).astype(np.float32)
_EDGES = np.linspace(2, FRAME // 2 + 1, N_BANDS + 1).astype(int)
PATTERN_FRAMES = 30   # spike history (300 ms) attributed to a wake event

SUSTAIN_WIN = 25      # frames (250 ms) over which persistence is judged
DEFAULTS = dict(thresh=0.9, lif_thr=2.5, beta=0.9, refractory=50, sustain=15, w=[1.0] * N_BANDS)


def band_energies(x: np.ndarray) -> np.ndarray:
    """Log10 energy per band for each 25 ms frame. Shape (T, N_BANDS)."""
    n = 1 + (len(x) - FRAME) // HOP if len(x) >= FRAME else 0
    if n <= 0:
        return np.zeros((0, N_BANDS))
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * _WIN, axis=1)) ** 2
    bands = np.stack([spec[:, a:b].sum(1) for a, b in zip(_EDGES[:-1], _EDGES[1:])], 1)
    return np.log10(bands + 1e-10)


class SNN:
    def __init__(self, params=None):
        p = {**DEFAULTS, **(params or {})}
        self.thresh = float(p["thresh"])
        self.lif_thr = float(p["lif_thr"])
        self.beta = float(p["beta"])
        self.refractory = int(p["refractory"])
        self.sustain = float(p["sustain"])   # min. active frames (of SUSTAIN_WIN) to allow a wake
        self.w = np.array(p["w"], dtype=float)
        self.reset_state()

    # ---- state -------------------------------------------------------------------------
    def reset_state(self):
        self.tail = np.zeros(0, np.float32)
        self.base = None
        self.v = 0.0
        self.cool = 0
        self.frame = 0                       # absolute frames processed
        self.hist = np.zeros((PATTERN_FRAMES, N_BANDS), np.float32)
        self.act = np.zeros(SUSTAIN_WIN, np.float32)   # recent "multi-band activity" flags

    def params(self):
        return dict(thresh=self.thresh, lif_thr=self.lif_thr, beta=self.beta,
                    refractory=self.refractory, sustain=self.sustain, w=[round(float(v), 4) for v in self.w])

    # ---- inference ---------------------------------------------------------------------
    def feed(self, x: np.ndarray) -> dict:
        """Consume a chunk of float32 mono 16 kHz audio.
        Returns spikes (T,16), vmem (T,), rms (T,), events [{frame, t, feat}], first_frame."""
        buf = np.concatenate([self.tail, x.astype(np.float32)])
        logE = band_energies(buf)
        T = len(logE)
        used = T * HOP
        self.tail = buf[used:] if T else buf
        first = self.frame
        spikes = np.zeros((T, N_BANDS), np.float32)
        vmem = np.zeros(T, np.float32)
        events = []
        for i in range(T):
            e = logE[i]
            if self.base is None:
                self.base = e.copy()
            s = (e - self.base) > self.thresh
            spikes[i] = s
            warm = 0.05 if (first + i) < 50 else 0.01
            self.base = self.base + np.where(s, 0.0005, warm) * (e - self.base)
            self.v = self.beta * self.v + float(self.w @ s)
            self.hist = np.roll(self.hist, -1, axis=0)
            self.hist[-1] = s
            self.act = np.roll(self.act, -1)
            self.act[-1] = float(s.sum() >= 2)
            fired = False
            if self.cool > 0:
                self.cool -= 1
            elif self.v >= self.lif_thr and self.act.sum() >= self.sustain:
                fired = True
                self.v = 0.0
                self.cool = self.refractory
                events.append(dict(frame=first + i, t=(first + i) * HOP / SR,
                                   feat=[round(float(f), 3) for f in self.hist.mean(0)]))
            vmem[i] = self.lif_thr if fired else self.v
        self.frame += T
        rms = np.sqrt((buf[:used].reshape(-1, HOP) ** 2).mean(1)) if T else np.zeros(0)
        return dict(spikes=spikes, vmem=vmem, rms=rms, events=events, first_frame=first)

    # ---- learning ----------------------------------------------------------------------
    def reward(self, feat, r: float, eta=0.1):
        """Three-factor, error-driven update on the synaptic weights only.
        r=-1: false alarm -> weaken the bands that fired it; r=+1: missed speech -> strengthen
        the bands active during it."""
        f = np.asarray(feat, float)
        self.w = np.clip(self.w + eta * r * f, -1.5, 3.0)

    def nudge_thresh(self, delta):
        """Move the encoder threshold (positive = stricter)."""
        self.thresh = float(np.clip(self.thresh + delta, 0.4, 2.0))

    def nudge_sustain(self, delta):
        """Require more (positive) or less persistent activity before waking."""
        self.sustain = float(np.clip(self.sustain + delta, 2, SUSTAIN_WIN - 2))

    def nudge_lif(self, log_factor):
        """Scale the neuron's firing threshold by exp(log_factor) (positive = stricter)."""
        self.lif_thr = float(np.clip(self.lif_thr * np.exp(log_factor), 1.0, 80.0))


def run_offline(x: np.ndarray, params=None, chunk=SR // 2):
    """Convenience: run a whole signal, return concatenated outputs."""
    snn = SNN(params)
    parts = [snn.feed(x[i:i + chunk]) for i in range(0, len(x), chunk)]
    ev = [e for p in parts for e in p["events"]]
    spikes = np.concatenate([p["spikes"] for p in parts]) if parts else np.zeros((0, N_BANDS))
    return ev, spikes, snn


def score_events(event_times, spans, slack=0.35):
    """Score wake events against ground-truth speech spans [(t0, t1), ...].
    Returns per-event verdicts (True=true wake), missed span indices, precision, recall, f1."""
    verdicts = [any(a - slack <= t <= b + slack for a, b in spans) for t in event_times]
    missed = [i for i, (a, b) in enumerate(spans)
              if not any(a - slack <= t <= b + slack for t in event_times)]
    tp = sum(verdicts)
    fp = len(verdicts) - tp
    hit = len(spans) - len(missed)
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = hit / len(spans) if spans else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return verdicts, missed, prec, rec, f1
