"""A processing session: audio in -> SNN wake events -> speech segments -> ASR -> sentences ->
action-item scores -> summary. Emits live events on the hub as it goes."""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import db, gamify, snn as snnmod, textmetrics
from .config import HOP, N_BANDS, SR

PAD_BEFORE, HANG = 0.5, 1.2     # seconds of audio kept before a wake / after the last one
BIN = 5                          # frames per raster column sent to the UI (50 ms)


def split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if len(s.split()) >= 2]


class Session:
    def __init__(self, core, source, label="", asr=None, truth_utts=None, mode="live", audio_kind="microphone"):
        self.core, self.source, self.mode = core, source, mode
        self.asr = asr or core.asr
        self.truth_utts = truth_utts             # [{t0, t1, text}] when the script is known
        self.truth = [(u["t0"], u["t1"]) for u in truth_utts] if truth_utts is not None else None
        self.last_end = 0.0                      # end of the previous segment (for the noise profile)
        self.net = snnmod.SNN(core.snn_params)   # learned params, fresh state
        self.lock = threading.RLock()
        self.audio = bytearray()
        self.seg = None
        self.wakes = []                          # (event_id, t)
        self.segments, self.sentences = [], []
        self.n_frames = self.n_spikes = 0
        self.awake = 0.0
        self.asr_ms = self.snn_ms = 0.0
        self.band_spk = np.zeros(N_BANDS)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.closing = False      # finish() has begun
        self.done = False         # finish() completed and session_end was published
        self.t_start = time.time()
        self.id = db.execute(
            "INSERT INTO sessions(started,source,mode,label,asr_backend) VALUES(?,?,?,?,?)",
            (self.t_start, source, mode, label, self.asr.name))
        core.hub.publish("session_start", id=self.id, source=source, mode=mode, asr=self.asr.name,
                         simulated=bool(getattr(self.asr, "simulated", False)), audio=audio_kind,
                         params=self.net.params())

    # ---- streaming input ---------------------------------------------------------------
    def feed(self, x):
        with self.lock:
            if self.closing:
                return
            t = time.perf_counter()
            r = self.net.feed(x)
            self.snn_ms += (time.perf_counter() - t) * 1000
            self.audio.extend((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
            T = len(r["spikes"])
            self.n_frames += T
            self.n_spikes += int(r["spikes"].sum())
            self.band_spk += r["spikes"].sum(0)
            if T:
                self._emit_frame(r)
            for e in r["events"]:
                eid = db.execute("INSERT INTO events(session_id,t,feat) VALUES(?,?,?)",
                                 (self.id, e["t"], json.dumps(e["feat"])))
                self.wakes.append((eid, e["t"]))
                self.core.hub.publish("wake", id=eid, t=round(e["t"], 2), session=self.id)
                end = e["t"] + HANG
                self.seg = [max(e["t"] - PAD_BEFORE, 0.0), end] if self.seg is None else [self.seg[0], max(self.seg[1], end)]
            now = self.net.frame * HOP / SR
            if self.seg and now >= self.seg[1]:
                self._close_segment(self.seg[0], min(self.seg[1], now))
                self.seg = None

    def _emit_frame(self, r):
        T = len(r["spikes"])
        nb = T // BIN
        if not nb:
            return
        sp = r["spikes"][:nb * BIN].reshape(nb, BIN, N_BANDS).max(1)
        vm = r["vmem"][:nb * BIN].reshape(nb, BIN).max(1)
        rm = r["rms"][:nb * BIN].reshape(nb, BIN).max(1)
        self.core.hub.publish(
            "frame", t=round(r["first_frame"] * HOP / SR, 2), dt=BIN * HOP / SR,
            spikes=sp.astype(int).tolist(), vmem=[round(float(v), 2) for v in vm],
            rms=[round(float(v), 4) for v in rm], thr=round(self.net.lif_thr, 2),
            awake=bool(self.seg), session=self.id)

    # ---- segments ----------------------------------------------------------------------
    def _close_segment(self, t0, t1):
        a, b = int(t0 * SR), int(t1 * SR)
        audio = np.frombuffer(bytes(self.audio[a * 2:b * 2]), dtype=np.int16).astype(np.float32) / 32768
        n0 = max(self.last_end, t0 - 1.5)            # quiet audio just before the wake = noise profile
        noise = None
        if t0 - n0 >= 0.3:
            noise = np.frombuffer(bytes(self.audio[int(n0 * SR) * 2:a * 2]), dtype=np.int16).astype(np.float32) / 32768
        self.last_end = t1
        self.awake += t1 - t0
        self.core.hub.publish("segment_open", t0=round(t0, 2), t1=round(t1, 2), session=self.id)
        self.pool.submit(self._transcribe, audio, t0, t1, noise)

    def _ref_for(self, t0, t1):
        """Script text of utterances mostly inside [t0, t1] (for per-segment WER)."""
        return " ".join(u["text"] for u in self.truth_utts
                        if min(u["t1"], t1) - max(u["t0"], t0) > 0.5 * (u["t1"] - u["t0"]))

    def _transcribe(self, audio, t0, t1, noise=None):
        t = time.perf_counter()
        try:
            text, conf = self.asr.transcribe(audio, t0, t1, noise_ref=noise)
        except Exception as e:                      # never let one bad segment kill the session
            text, conf = "", 0.0
            self.core.hub.publish("warn", msg=f"ASR failed: {e}")
        ms = (time.perf_counter() - t) * 1000
        self.asr_ms += ms
        ref = err = n = None
        if self.truth_utts is not None and not getattr(self.asr, "simulated", False):
            ref = self._ref_for(t0, t1)
            err, n = textmetrics.wer(ref, text) if ref else (None, None)
        sid = db.execute("INSERT INTO segments(session_id,t0,t1,text,asr_ms,ref,wer_err,wer_n,asr_conf) "
                         "VALUES(?,?,?,?,?,?,?,?,?)", (self.id, t0, t1, text, ms, ref, err, n, conf))
        sents = []
        for s in split_sentences(text):
            p = self.core.clf.predict(s)
            rid = db.execute("INSERT INTO sentences(session_id,segment_id,text,score,source) VALUES(?,?,?,?,?)",
                             (self.id, sid, s, p, "asr"))
            sents.append(dict(id=rid, text=s, score=round(p, 3)))
        self.segments.append(text)
        self.sentences += sents
        self.core.hub.publish("segment", id=sid, t0=round(t0, 2), t1=round(t1, 2), text=text,
                              sentences=sents, asr_ms=round(ms), session=self.id, conf=round(conf, 3),
                              asr_available=self.asr.available, ref=ref,
                              wer=round(err / n, 3) if n else None)

    # ---- end ---------------------------------------------------------------------------
    def finish(self):
        with self.lock:
            if self.closing:
                return {}
            self.closing = True
            now = self.net.frame * HOP / SR
            if self.seg:
                self._close_segment(self.seg[0], min(self.seg[1], now))
                self.seg = None
        self.pool.shutdown(wait=True)
        audio_sec = self.net.frame * HOP / SR
        text = " ".join(self.segments)
        summary, backend = self.core.summ.summarize(text)
        actions = [s["text"] for s in self.sentences if s["score"] > 0.5]
        prec = rec = f1 = wer = None
        if self.truth_utts and not getattr(self.asr, "simulated", False) and self.asr.available:
            e, n = textmetrics.wer(" ".join(u["text"] for u in self.truth_utts), text)
            wer = e / n if n else None                   # end-to-end: includes utterances never woken for
        if self.truth is not None:
            times = [t for _, t in self.wakes]
            verd, missed, prec, rec, f1 = snnmod.score_events(times, self.truth)
            for (eid, _), ok in zip(self.wakes, verd):
                db.execute("UPDATE events SET verdict=?, auto=1 WHERE id=?", (int(ok), eid))
        db.execute("UPDATE sessions SET ended=?,audio_sec=?,awake_sec=?,n_spikes=?,n_frames=?,n_events=?,"
                   "asr_ms=?,snn_ms=?,summary=?,actions=?,precision=?,recall=?,f1=?,wer=? WHERE id=?",
                   (time.time(), audio_sec, min(self.awake, audio_sec), self.n_spikes, self.n_frames,
                    len(self.wakes), self.asr_ms, self.snn_ms, summary, "\n".join(actions),
                    prec, rec, f1, wer, self.id))
        bt = np.array(db.kv_get("band_totals", [0] * N_BANDS), float) + self.band_spk
        db.kv_set("band_totals", [int(v) for v in bt])
        lv = gamify.add_xp(10 + audio_sec / 10)
        out = dict(id=self.id, audio_sec=round(audio_sec, 1), awake_sec=round(min(self.awake, audio_sec), 1),
                   duty=round(min(self.awake, audio_sec) / audio_sec, 3) if audio_sec else 0,
                   events=len(self.wakes), spikes=self.n_spikes, summary=summary, summary_backend=backend,
                   actions=actions, precision=prec, recall=rec, f1=f1, wer=wer, level=lv,
                   snn_rtf=round(audio_sec / (self.snn_ms / 1000), 1) if self.snn_ms else None)
        self.done = True
        self.core.hub.publish("session_end", **out)
        self.core.bus.emit("session.finished", session_id=self.id)
        return out
