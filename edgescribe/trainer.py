"""Self-training loops. The SNN learns from ground-truth episodes via reward-modulated updates;
the classifier learns from its curriculum. Both log every step so progress is visible.

Episode sources: "synth" (harmonic speech-like signal) and "voice" (real synthesized speech from
the voice bank; registered by the app core once the bank is built)."""
import functools
import time

import numpy as np

from . import db, sim, snn as snnmod

EVAL_SEEDS = list(range(9000, 9006))


SOURCES = {"synth": sim.episode}


def register_source(name, fn):
    SOURCES[name] = fn
    _episode.cache_clear()


def best_source():
    return "voice" if "voice" in SOURCES else "synth"


@functools.lru_cache(maxsize=32)
def _episode(seed, hard, source):
    return SOURCES[source](seed, hard=hard)


def snn_run(params, seed, hard, source="synth"):
    ep = _episode(seed, hard, source)
    ev, spikes, _ = snnmod.run_offline(ep["audio"], params)
    spans = [(u["t0"], u["t1"]) for u in ep["utts"]]
    verd, missed, p, r, f = snnmod.score_events([e["t"] for e in ev], spans)
    return ev, verd, missed, spans, spikes, p, r, f


def snn_eval(params, hard=0.7, source="synth"):
    """Mean precision/recall/F1 on fixed held-out episodes (no learning happens here)."""
    res = np.array([snn_run(params, s, hard, source)[5:] for s in EVAL_SEEDS])
    return dict(prec=float(res[:, 0].mean()), rec=float(res[:, 1].mean()), f1=float(res[:, 2].mean()))


def snn_train_episode(net, seed, hard=0.7, eta=0.25, k=0.08, source="synth"):
    """One training episode (updates `net` in place), then a held-out evaluation.
    Contrastive three-factor rule: weights move along (mean spike pattern of true wakes) minus
    (mean pattern of false alarms), so bands that separate speech from noise gain weight and
    bands that mostly fire on transients turn inhibitory. Missed speech adds its own pattern.
    The thresholds move by the false-alarm fraction minus the miss fraction."""
    ev, verd, missed, spans, spikes, p, r, f = snn_run(net.params(), seed, hard, source)
    good = [e["feat"] for e, ok in zip(ev, verd) if ok]
    bad = [e["feat"] for e, ok in zip(ev, verd) if not ok]
    if good and bad:
        net.reward(np.mean(good, 0) - np.mean(bad, 0), +1.0, eta)
    elif bad:
        net.reward(np.mean(bad, 0), -1.0, eta)
    for i in missed:
        a, b = int(spans[i][0] * 100), int(spans[i][1] * 100)
        if b > a:
            net.reward(spikes[a:b].mean(0), +1.0, eta)
    fp_frac = len(bad) / max(len(ev), 1)
    miss_frac = len(missed) / max(len(spans), 1)
    net.nudge_thresh(k * (fp_frac - miss_frac))
    net.nudge_lif(0.5 * (fp_frac - miss_frac))
    net.nudge_sustain(3.0 * (fp_frac - miss_frac))
    return snn_eval(net.params(), hard, source)


def log_snn(step, m, note="sim-reward"):
    db.execute("INSERT INTO learn_log(ts,learner,step,acc,loss,f1,prec,rec,note) VALUES(?,?,?,?,?,?,?,?,?)",
               (time.time(), "snn", step, None, None, m["f1"], m["prec"], m["rec"], note))
