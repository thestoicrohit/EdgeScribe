"""Application core: shared models, live event hub, jobs (simulate / upload / train), feedback,
settings, text-to-speech, health checks and the statistics aggregator behind the dashboard."""
import collections
import json
import logging
import queue
import random
import threading
import time

import numpy as np

from . import audioio, backends, config, corpus, db, gamify, sim, trainer, tts_engines, voicebank, winspeech
from .config import N_BANDS, SR
from .features import ConnectedFeatures
from .jobs import Cancelled
from .learner import ActionClassifier
from .pipeline import Session
from .rag import LocalIndex
from .snn import SNN


log = logging.getLogger("edgescribe.core")

DEFAULT_SETTINGS = dict(tts_mode="offline", tts_voice="", tts_rate=0, auto_read=False, denoise=True,
                        sim_source="voice")
_VALIDATORS = dict(
    tts_mode=lambda v: v if v in ("offline", "online") else None,
    tts_voice=lambda v: v[:120] if isinstance(v, str) else None,
    tts_rate=lambda v: int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and -5 <= v <= 5 else None,
    auto_read=lambda v: v if isinstance(v, bool) else None,
    denoise=lambda v: v if isinstance(v, bool) else None,
    sim_source=lambda v: v if v in ("voice", "synth") else None,
)


class Hub:
    """Fan-out of live events to every connected dashboard (Server-Sent Events)."""

    def __init__(self):
        self.clients, self.lock = [], threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=400)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def publish(self, type_, **data):
        msg = dict(type=type_, ts=time.time(), **data)
        with self.lock:
            for q in self.clients:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass                # a slow client just drops frames


read_wav = audioio.decode_wav


class Core(ConnectedFeatures):
    MAX_KEPT_SESSIONS = 8

    def __init__(self, load_asr=True, speech=True, backfill=True):
        db.init()
        self.hub = Hub()
        self._init_features()
        self.clf = ActionClassifier().load()
        self.snn_params = db.kv_get("snn_params") or SNN().params()
        self.index = LocalIndex().load()
        self.speech = winspeech.WinSpeech() if (speech and winspeech.supported()) else None
        self.tts_engine = tts_engines.detect(self.speech) if speech else None
        self.bank = voicebank.Bank(self.tts_engine)
        if self.bank.ready():
            trainer.register_source("voice", self.bank.episode)
        self.asr = backends.load_asr(self.speech, self.settings) if load_asr else backends.NullASR()
        self.summ = backends.Summarizer()
        self.hw = backends.hardware()
        self.bench = backends.benchmark_snn(10)
        self.sessions = {}
        self.lock = threading.Lock()
        self._tts_cache = collections.OrderedDict()
        if self.tts_engine is not None and len(self.bank.clips) < voicebank.TARGET:
            self.jobs.submit("voicebank", self._build_bank, "Build the voice bank", total=voicebank.TARGET)
        if backfill:
            self.backfill()
        log.info("core ready: asr=%s npu=%s voicebank=%d", self.asr.name, self.hw["npu"], len(self.bank.clips))

    def _build_bank(self, job):
        def progress(i, n):
            job.check()
            job.progress(i, n)
            self.hub.publish("bank", clips=i, target=n)
        try:
            self.bank.build(progress=progress)
        finally:
            if self.bank.ready():
                trainer.register_source("voice", self.bank.episode)
            self.hub.publish("bank", clips=len(self.bank.clips), target=voicebank.TARGET, done=True)

    # ---- settings / speech -------------------------------------------------------------
    def settings(self):
        return {**DEFAULT_SETTINGS, **db.kv_get("settings", {})}

    def update_settings(self, patch):
        if not isinstance(patch, dict):
            raise ValueError("settings must be an object")
        cur = self.settings()
        for k, v in patch.items():
            if k not in _VALIDATORS:
                raise ValueError(f"unknown setting: {k}")
            ok = _VALIDATORS[k](v)
            if ok is None:
                raise ValueError(f"invalid value for {k}")
            cur[k] = ok
        db.kv_set("settings", cur)
        self.hub.publish("settings", **cur)
        return cur

    def voices(self):
        server = []
        if self.tts_engine is not None:
            try:
                server = self.tts_engine.voices()
            except Exception as e:
                log.warning("listing voices failed: %s", e)
        recognizers = self.speech.voices()["recognizers"] if self.speech is not None else []
        return dict(server=server, recognizers=recognizers, engine=getattr(self.tts_engine, "name", None))

    def tts(self, text, voice=None, rate=None):
        """Offline text-to-speech on this machine -> 16 kHz WAV bytes."""
        if self.tts_engine is None:
            raise RuntimeError("offline text-to-speech is not available on this system")
        text = " ".join(str(text).split())[:3000]
        if not text:
            raise ValueError("nothing to say")
        st = self.settings()
        voice = voice if voice is not None else st["tts_voice"]
        voice = voice[4:] if voice.startswith("srv:") else ("" if voice.startswith("web:") else voice)
        rate = int(rate if rate is not None else st["tts_rate"])
        if not -5 <= rate <= 5:
            raise ValueError("rate must be between -5 and 5")
        key = (text, voice, rate)
        if key in self._tts_cache:
            self._tts_cache.move_to_end(key)
            return self._tts_cache[key]
        wav = self.tts_engine.tts(text, voice or None, rate)
        self._tts_cache[key] = wav
        while len(self._tts_cache) > 24:
            self._tts_cache.popitem(last=False)
        return wav

    def health(self):
        st = self.settings()
        size = config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0
        llm = self.summ.llm_up()
        c = [
            dict(name="Spiking listener", status="ok", detail=f"{self.bench['rtf']}x realtime on CPU"),
            dict(name="Speech recognition", status="ok" if self.asr.available else "off",
                 detail=f"{self.asr.name} - {self.asr.device}" if self.asr.available else "no ASR backend found"),
            dict(name="Noise suppression", status="ok" if st["denoise"] else "off", detail="spectral gating before ASR"),
            dict(name="Offline voices", status="ok" if self.tts_engine else "off",
                 detail=f"{self.tts_engine.name}, on this device" if self.tts_engine
                 else "none found - browser voices still work; see python run.py --doctor"),
            dict(name="Online voices", status="ok" if st["tts_mode"] == "online" else "off",
                 detail="browser cloud voices - text leaves the device" if st["tts_mode"] == "online" else "disabled (private)"),
            dict(name="Summaries", status="ok" if llm else "warn",
                 detail=f"Ollama {self.summ.model}" if llm else "extractive (Ollama not running)"),
            dict(name="NPU", status="ok" if self.hw["npu"] else "warn",
                 detail="QNN execution provider" if self.hw["npu"] else self.hw["npu_note"]),
            dict(name="Voice bank", status="ok" if self.bank.ready() else ("warn" if self.bank.building else "off"),
                 detail=f"{len(self.bank.clips)}/{voicebank.TARGET} spoken clips"),
            dict(name="Database", status="ok", detail=f"schema v{db.version()} - {size / 1024:.0f} KB - local"),
        ]
        return dict(components=c, network="localhost only" if st["tts_mode"] == "offline"
                    else "localhost + browser cloud voices")

    # ---- sessions ----------------------------------------------------------------------
    def _busy(self):
        return any(not s.done for s in self.sessions.values())

    def _claim(self, make):
        """Create a session only if none is running - atomically, so two clicks can't start two."""
        with self.session_lock:
            if self._busy():
                raise RuntimeError("a session is already running")
            s = make()
            self.sessions[s.id] = s
            for old in sorted(k for k, v in self.sessions.items() if v.done)[:-self.MAX_KEPT_SESSIONS]:
                del self.sessions[old]
            return s

    def run_sim(self, seed=None, hard=0.7, speed=8.0, dur=60.0, source=None):
        seed = int(seed if seed is not None else random.randint(1, 10**6))
        source = source or self.settings()["sim_source"]
        if source == "voice" and self.bank.ready():
            ep = self.bank.episode(seed, dur=dur, hard=hard)
            asr = self.asr if self.asr.available else backends.SimASR(ep["utts"])
            kind, mode = "voice bank (synthesized speech)", "voice"
        else:
            ep = sim.episode(seed, dur=dur, hard=hard)
            asr, kind, mode = backends.SimASR(ep["utts"]), "synthetic signal", "sim"
        s = self._claim(lambda: Session(self, "simulation", label=f"{mode} seed {seed} hard {hard}", asr=asr,
                                        truth_utts=ep["utts"], mode=mode, audio_kind=kind))

        def run():
            try:
                step = SR // 2
                for i in range(0, len(ep["audio"]), step):
                    s.feed(ep["audio"][i:i + step])
                    if speed:
                        time.sleep(0.5 / speed)
            finally:
                s.finish()
        threading.Thread(target=run, daemon=True).start()
        return dict(id=s.id, seed=seed, utterances=len(ep["utts"]), audio=kind)

    def run_wav(self, data: bytes, name="upload"):
        x = read_wav(data)
        if len(x) < 1600:
            raise ValueError("the WAV file is shorter than 0.1 s")
        s = self._claim(lambda: Session(self, "upload", label=name, mode="file", audio_kind="your file"))

        def run():
            try:
                for i in range(0, len(x), SR // 2):
                    s.feed(x[i:i + SR // 2])
            finally:
                s.finish()
        threading.Thread(target=run, daemon=True).start()
        return dict(id=s.id, seconds=round(len(x) / SR, 1))

    def live_start(self):
        s = self._claim(lambda: Session(self, "microphone", label="live mic", mode="live", audio_kind="microphone"))
        return dict(id=s.id)

    def live_chunk(self, sid, pcm: bytes):
        s = self.sessions.get(sid)
        if s and not s.closing and len(pcm) >= 2:
            s.feed(np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768)

    def live_stop(self, sid):
        s = self.sessions.get(sid)
        return s.finish() if s else {}

    # ---- training ----------------------------------------------------------------------
    def train(self, kind="both", episodes=10):
        if kind not in ("both", "clf", "snn"):
            raise ValueError("kind must be both, clf or snn")
        label = {"both": "Train classifier + listener", "clf": "Train classifier", "snn": "Train listener"}[kind]
        return self.jobs.submit("train", lambda job: self._train(job, kind, episodes), label, total=episodes).as_dict()

    def _train(self, job, kind, episodes):
        try:
            net = SNN(self.snn_params)
            src = trainer.best_source()
            if kind in ("clf", "both") and not db.one("SELECT 1 x FROM learn_log WHERE learner='action'"):
                self.clf.log(self.clf.evaluate(), note="baseline (before training)")
            if kind in ("snn", "both") and not db.one(
                    "SELECT 1 x FROM learn_log WHERE learner='snn' AND note LIKE ?", (src + "%",)):
                trainer.log_snn(0, trainer.snn_eval(net.params(), source=src), note=f"{src} baseline")
            for k in range(episodes):
                job.check()
                net = SNN(self.snn_params)      # pick up any trigger feedback given meanwhile
                if kind in ("clf", "both"):
                    m = self.clf.train_curriculum(32, seed=random.randint(1, 10**6))
                    self.hub.publish("learn", learner="action", step=self.clf.t, acc=m["acc"], loss=m["loss"], f1=m["f1"])
                if kind in ("snn", "both"):
                    n = db.kv_get("snn_eps", 0) + 1
                    m = trainer.snn_train_episode(net, seed=100 + n, source=src)
                    trainer.log_snn(n, m, note=src)
                    db.kv_set("snn_eps", n)
                    self.snn_params = net.params()
                    db.kv_set("snn_params", self.snn_params)
                    self.hub.publish("learn", learner="snn", step=n, f1=m["f1"], prec=m["prec"], rec=m["rec"],
                                     params=self.snn_params, source=src)
                db.kv_set("train_eps", db.kv_get("train_eps", 0) + 1)
                gamify.add_xp(3)
                job.progress(k + 1, episodes)
                self.hub.publish("train_progress", done=k + 1, total=episodes, kind=kind)
        except Cancelled:
            self.hub.publish("train_progress", done=episodes, total=episodes, kind=kind, cancelled=True)
            raise
        except Exception as e:
            self.hub.publish("warn", msg=f"training failed: {e}")
            raise
        finally:
            self.bus.emit("training.finished", kind=kind)

    # ---- feedback (this is how it self-teaches from you) -------------------------------
    def teach_next(self):
        r = db.query("SELECT id,text,score FROM sentences WHERE label IS NULL ORDER BY ABS(score-0.5) LIMIT 1")
        if r:
            return dict(id=r[0]["id"], text=r[0]["text"], score=round(r[0]["score"], 3), origin="session")
        text, y = corpus.sample(random.Random(), "test")
        return dict(id=None, text=text, score=round(self.clf.predict(text), 3), origin="practice", oracle=y)

    def learn_from_user(self, text, label, sentence_id=None, note="feedback"):
        """The one path every user label takes (Teach cards, transcript flags, task decisions):
        score the model's guess first (running accuracy), update the personal layer, persist, log."""
        before = self.clf.predict(text)
        self.clf.user_update(text, label)
        after = self.clf.predict(text)
        if sentence_id:
            db.execute("UPDATE sentences SET label=? WHERE id=?", (label, sentence_id))
        with self.lock:
            pq = db.kv_get("preq", dict(n=0, ok=0, series=[]))
            pq["n"] += 1
            pq["ok"] += int((before >= 0.5) == bool(label))
            pq["series"].append([pq["n"], round(pq["ok"] / pq["n"], 4)])
            pq["series"] = pq["series"][-500:]
            db.kv_set("preq", pq)
        m = self.clf.evaluate()
        self.clf.log(m, note=note)
        self.clf.save()
        self.hub.publish("learn", learner="action", step=self.clf.t, acc=m["acc"], loss=m["loss"], f1=m["f1"], note=note)
        return before, after, m, pq["ok"] / pq["n"]

    def feedback_sentence(self, text, label, sid=None, origin="session", oracle=None):
        label = int(label)
        if label not in (0, 1):
            raise ValueError("label must be 0 or 1")
        used = int(oracle) if (origin == "practice" and oracle is not None) else label
        before, after, m, preq = self.learn_from_user(text, used, sid)
        if sid:
            row = db.one("SELECT session_id FROM sentences WHERE id=?", (sid,))
            self.bus.emit("sentence.labeled", sentence_id=sid, text=text, label=used,
                          session_id=row and row["session_id"], via="user")
        lv = gamify.add_xp(5 + (3 if (oracle is not None and label == int(oracle)) else 0))
        news = gamify.check(self.achievement_stats())
        for a in news:
            self.hub.publish("achievement", **a)
        return dict(before=round(before, 3), after=round(after, 3), used_label=used, model_was_right=(before >= 0.5) == bool(used),
                    heldout=dict(acc=m["acc"], f1=m["f1"]), level=lv, achievements=news, preq=preq)

    def feedback_event(self, eid, verdict):
        r = db.one("SELECT feat FROM events WHERE id=?", (int(eid),))
        if not r:
            raise KeyError("unknown event")
        feat = json.loads(r["feat"])
        net = SNN(self.snn_params)
        if int(verdict) == 0:                             # false alarm
            net.reward(feat, -1.0, eta=0.1)
            net.nudge_thresh(0.02)
            net.nudge_lif(0.05)
            net.nudge_sustain(0.5)
        else:
            net.reward(feat, +1.0, eta=0.03)
        self._save_snn(net)
        db.execute("UPDATE events SET verdict=?, auto=0 WHERE id=?", (int(verdict), int(eid)))
        return self._teach_reply("snn-feedback")

    def feedback_missed(self):
        net = SNN(self.snn_params)
        net.nudge_thresh(-0.05)
        net.nudge_lif(-0.1)
        net.nudge_sustain(-1.0)
        self._save_snn(net)
        return self._teach_reply("snn-missed")

    def _save_snn(self, net):
        self.snn_params = net.params()
        db.kv_set("snn_params", self.snn_params)
        db.kv_set("snn_fb", db.kv_get("snn_fb", 0) + 1)
        self.hub.publish("learn", learner="snn-params", params=self.snn_params)

    def _teach_reply(self, note):
        lv = gamify.add_xp(4)
        news = gamify.check(self.achievement_stats())
        for a in news:
            self.hub.publish("achievement", **a)
        return dict(params=self.snn_params, level=lv, achievements=news)

    # ---- statistics --------------------------------------------------------------------
    def achievement_stats(self):
        k = self.kpis()
        clf_last = db.one("SELECT f1 FROM learn_log WHERE learner='action' ORDER BY id DESC LIMIT 1")
        snn_last = db.one("SELECT f1 FROM learn_log WHERE learner='snn' ORDER BY id DESC LIMIT 1")
        shared = db.one("SELECT MAX(n) m FROM (SELECT COUNT(*) n FROM topics GROUP BY key)")
        return dict(sessions=k["sessions"], feedback=k["feedback"], audio_sec=k["audio_sec"], duty=k["duty"],
                    clf_f1=(clf_last or {}).get("f1") or 0, snn_f1=(snn_last or {}).get("f1") or 0,
                    train_eps=db.kv_get("train_eps", 0), reviews=db.one("SELECT COUNT(*) n FROM reviews")["n"],
                    tasks_done=db.one("SELECT COUNT(*) n FROM tasks WHERE status='done'")["n"],
                    topic_links=(shared or {}).get("m") or 0)

    def kpis(self):
        s = db.one("SELECT COUNT(*) n, COALESCE(SUM(audio_sec),0) a, COALESCE(SUM(awake_sec),0) w, "
                   "COALESCE(SUM(n_spikes),0) sp, COALESCE(SUM(n_events),0) ev, COALESCE(SUM(asr_ms),0) am, "
                   "COALESCE(SUM(n_frames),0) fr FROM sessions WHERE ended IS NOT NULL")
        fb = db.one("SELECT COUNT(*) n FROM sentences WHERE label IS NOT NULL")["n"] + \
            db.one("SELECT COUNT(*) n FROM events WHERE verdict IS NOT NULL AND auto=0")["n"]
        pq = db.kv_get("preq", dict(n=0, ok=0))
        w = db.one("SELECT COALESCE(SUM(wer_err),0) e, COALESCE(SUM(wer_n),0) n, AVG(asr_conf) c, COUNT(*) k "
                   "FROM segments WHERE wer_n IS NOT NULL")
        return dict(sessions=s["n"], audio_sec=round(s["a"], 1), awake_sec=round(s["w"], 1),
                    asr_wer=round(w["e"] / w["n"], 3) if w["n"] else None, asr_segments=w["k"],
                    asr_words=w["n"],
                    duty=round(s["w"] / s["a"], 3) if s["a"] else None, spikes=int(s["sp"]),
                    frames=int(s["fr"]), events=int(s["ev"]), asr_ms=round(s["am"]), feedback=fb,
                    preq_acc=round(pq["ok"] / pq["n"], 3) if pq["n"] else None, train_eps=db.kv_get("train_eps", 0))

    def state(self):
        return dict(kpis=self.kpis(), game=gamify.state(), hardware=self.hw, bench=self.bench,
                    asr=dict(name=self.asr.name, available=self.asr.available, device=self.asr.device),
                    settings=self.settings(), bank=self.bank.status(), tts_offline=self.tts_engine is not None,
                    llm=dict(up=self.summ.llm_up(), model=self.summ.model),
                    snn=self.snn_params, training=self.training, jobs=self.jobs.list()[:10],
                    tasks=self.tasks.counts(), cards=self.study.stats(),
                    running=[s.id for s in self.sessions.values() if not s.done])

    def stats(self):
        def series(learner):
            return db.query("SELECT id,step,acc,loss,f1,prec,rec,note,ts FROM learn_log WHERE learner=? ORDER BY id", (learner,))
        sess = db.query("SELECT id,started,source,mode,audio_sec,awake_sec,n_events,n_spikes,precision,recall,f1,wer,"
                        "asr_backend,asr_ms,snn_ms FROM sessions WHERE ended IS NOT NULL ORDER BY id DESC LIMIT 40")[::-1]
        segs = db.query("SELECT id,session_id,t0,t1,asr_ms,wer_err,wer_n,asr_conf FROM segments "
                        "WHERE wer_n IS NOT NULL ORDER BY id DESC LIMIT 200")[::-1]
        conf_bins = []
        for lo in np.arange(0, 1, 0.2):
            b = [r["wer_err"] / r["wer_n"] for r in segs if r["asr_conf"] is not None and r["wer_n"]
                 and lo <= r["asr_conf"] < lo + 0.2 + (1e-9 if lo > 0.7 else 0)]
            conf_bins.append(dict(lo=round(float(lo), 1), n=len(b), wer=float(np.mean(b)) if b else None))
        pos, neg = self.clf.top_features(8)
        cal = []
        rows = db.query("SELECT score,label FROM sentences WHERE label IS NOT NULL")
        for lo in np.arange(0, 1, 0.2):
            b = [r["label"] for r in rows if lo <= r["score"] < lo + 0.2 + (1e-9 if lo > 0.7 else 0)]
            cal.append(dict(lo=round(float(lo), 1), n=len(b), pos=(sum(b) / len(b)) if b else None))
        scores = [r["score"] for r in db.query("SELECT score FROM sentences ORDER BY id DESC LIMIT 300")]
        hist = np.histogram(scores, bins=10, range=(0, 1))[0].tolist() if scores else [0] * 10
        ev = db.query("SELECT COUNT(*) n, SUM(CASE WHEN verdict=1 THEN 1 ELSE 0 END) tp, "
                      "SUM(CASE WHEN verdict=0 THEN 1 ELSE 0 END) fp FROM events")[0]
        conf = self.clf.evaluate()["confusion"]
        return dict(
            kpis=self.kpis(), game=gamify.state(),
            clf=series("action"), snn=series("snn"), preq=db.kv_get("preq", dict(series=[]))["series"],
            sessions=sess, band_w=self.snn_params["w"], band_totals=db.kv_get("band_totals", [0] * N_BANDS),
            snn_params=self.snn_params, features=dict(pos=pos, neg=neg), calibration=cal, score_hist=hist,
            events=ev, confusion=conf, asr_segments=segs, asr_conf_bins=conf_bins,
            asr=dict(name=self.asr.name, device=self.asr.device, available=self.asr.available),
            bank=self.bank.status(),
            model=dict(clf_params=self.clf.n_params(), clf_steps=self.clf.t, clf_feedback=self.clf.fb,
                       personal_feats=self.clf.personal_size()[0], personal_norm=round(self.clf.personal_size()[1], 3),
                       snn_feedback=db.kv_get("snn_fb", 0), snn_episodes=db.kv_get("snn_eps", 0),
                       rag_boosts=len(self.index.boost), rag_chunks=len(self.index.chunks)),
            hardware=self.hw, bench=self.bench)

    def reset(self):
        if self._busy() or self.jobs.running():
            raise RuntimeError("wait for the running session or background job to finish")
        self.bus.drain(30)
        db.wipe()
        self.clf = ActionClassifier()
        self.snn_params = SNN().params()
        self.index = LocalIndex()
        self.sessions = {}
