"""Connected features: tasks, topics, flashcards, the daily digest, jobs, exports, and the reactors
that tie them to sessions, notes and each other. Mixed into Core.

  session finished --> search index --> topics --> tasks --> flashcards --> awards
  notes added      --> topics --> flashcards
  you label a sentence 'action' / 'not'  --> task created / dismissed
  you dismiss an auto task / add one by hand --> the classifier learns from it
  you review a card / finish a task --> XP, streak, awards
"""
import datetime as dt
import logging
import os
import sqlite3
import tempfile
import threading
import time

from . import db, gamify
from .events import EventBus
from .jobs import JobManager
from .study import Study
from .tasks import TaskStore
from .topics import Topics

log = logging.getLogger("edgescribe.features")
AUTO_TASK_SCORE = 0.6            # classifier score needed to turn a sentence into a task by itself
CARD_MIN_CONF = 0.6              # recognizer confidence needed before a transcript line becomes a flashcard
TOPIC_MIN_CONF = 0.3             # ...and before it can shape topics (misheard speech invents 'topics')


class ConnectedFeatures:
    def _init_features(self):
        self.bus = EventBus()
        self.jobs = JobManager(self.hub)
        self.tasks, self.topics, self.study = TaskStore(), Topics(), Study()
        self.session_lock = threading.Lock()
        on = self.bus.on
        on("session.finished", self._r_index_session, "search: index the transcript")
        on("session.finished", self._r_session_topics, "topics: extract from session")
        on("session.finished", self._r_session_tasks, "tasks: action items from session")
        on("session.finished", self._r_session_cards, "study: cards from session")
        on("session.finished", self._r_awards, "awards + KPIs")
        on("doc.added", self._r_doc_topics, "topics: extract from note")
        on("doc.added", self._r_doc_cards, "study: cards from note")
        on("sentence.labeled", self._r_label_to_task, "tasks: follow your labels")
        on("task.changed", self._r_task_to_classifier, "classifier: learn from tasks")
        on("task.changed", self._r_task_xp, "XP: tasks")
        on("card.reviewed", self._r_card_xp, "XP: study")
        on("training.finished", self._r_awards, "awards + KPIs")

    # ------------------------------------------------------------------ helpers
    def _session_row(self, sid):
        return db.one("SELECT id, source, mode, started, summary FROM sessions WHERE id=?", (sid,))

    def _transcript(self, sid, min_conf=None):
        rows = db.query("SELECT text, asr_conf FROM segments WHERE session_id=? ORDER BY t0", (sid,))
        return " ".join(r["text"] for r in rows if r["text"] and (min_conf is None or (r["asr_conf"] or 0) >= min_conf))

    def describe(self, item_type, item_id):
        """A title + snippet for any linkable item."""
        if item_type == "session":
            s = self._session_row(item_id)
            if not s:
                return None
            when = time.strftime("%b %d, %H:%M", time.localtime(s["started"]))
            return dict(item_type="session", item_id=item_id, title=f"Session {item_id} · {s['source']}",
                        subtitle=when, snippet=(self._transcript(item_id) or "")[:160])
        if item_type == "note":
            d = db.one("SELECT source, text FROM docs WHERE id=?", (item_id,))
            return d and dict(item_type="note", item_id=item_id, title=d["source"], subtitle="note",
                              snippet=d["text"][:160])
        return None

    def _publish_counts(self, what, **extra):
        if what == "tasks":
            self.hub.publish("tasks", **self.tasks.counts(), **extra)
        elif what == "cards":
            self.hub.publish("cards", **self.study.stats(), **extra)
        elif what == "topics":
            self.hub.publish("topics", count=len(self.topics.top(200)), **extra)

    # ------------------------------------------------------------------ reactors
    def _r_index_session(self, session_id):
        text = self._transcript(session_id)
        if text.strip():
            self.index.add(text, f"Session {session_id}", "session", session_id)

    def _r_session_topics(self, session_id):
        text = self._transcript(session_id, min_conf=TOPIC_MIN_CONF)
        row = db.one("SELECT asr_backend FROM sessions WHERE id=?", (session_id,)) or {}
        recognized = not (row.get("asr_backend") or "").startswith("simulated")
        if text.strip():
            self.topics.extract("session", session_id, text, corroborate=recognized)
            self._publish_counts("topics")

    def _r_session_tasks(self, session_id):
        made = 0
        for r in db.query("SELECT id, text FROM sentences WHERE session_id=? AND score>? AND (label IS NULL OR label=1)",
                          (session_id, AUTO_TASK_SCORE)):
            try:
                _, created = self.tasks.create(r["text"], "auto", session_id, r["id"])
                made += created
            except ValueError:
                pass
        if made:
            self._publish_counts("tasks", created=made, session_id=session_id)

    def _r_session_cards(self, session_id):
        text = self._transcript(session_id, min_conf=CARD_MIN_CONF)
        phrases = [r["phrase"] for r in self.topics.of("session", session_id)]
        made = self.study.generate(text, "session", session_id, phrases) if text else 0
        if made:
            self._publish_counts("cards", created=made)

    def _r_doc_topics(self, doc_id):
        d = db.one("SELECT text, kind FROM docs WHERE id=?", (doc_id,))
        if d and (d["kind"] or "note") == "note":
            self.topics.extract("note", doc_id, d["text"])
            self._publish_counts("topics")

    def _r_doc_cards(self, doc_id):
        d = db.one("SELECT text, kind FROM docs WHERE id=?", (doc_id,))
        if d and (d["kind"] or "note") == "note":
            phrases = [r["phrase"] for r in self.topics.of("note", doc_id)]
            made = self.study.generate(d["text"], "note", doc_id, phrases, limit=12)
            if made:
                self._publish_counts("cards", created=made)

    def _r_label_to_task(self, sentence_id, text, label, session_id=None, via="user"):
        if via == "task":
            return
        if label == 1:
            t, created = self.tasks.create(text, "label", session_id, sentence_id)
            if not created and t["status"] == "dismissed":
                self.tasks.update(t["id"], status="open")
            self._publish_counts("tasks")
        else:
            t = self.tasks.find_by_sentence(sentence_id, text)
            if t and t["status"] == "open":
                old, new = self.tasks.update(t["id"], status="dismissed")
                self.bus.emit("task.changed", task_id=t["id"], old="open", new="dismissed", origin=t["origin"],
                              text=t["text"], sentence_id=t["sentence_id"], via="label")
                self._publish_counts("tasks")

    def _r_task_to_classifier(self, task_id, old, new, origin, text, sentence_id=None, via="user"):
        if via == "label":
            return                                  # the label already taught the classifier
        label = None
        if new == "dismissed" and old == "open" and origin in ("auto", "label"):
            label = 0                               # "this isn't a task" = "this isn't an action item"
        elif old is None and origin == "manual":
            label = 1                               # typed in by hand = definitely an action item
        elif new == "open" and old == "dismissed":
            label = 1
        if label is None:
            return
        self.learn_from_user(text, label, sentence_id, note="task")

    def _r_task_xp(self, task_id, old, new, **_):
        if new == "done" and old != "done":
            lv = gamify.add_xp(5)
            self.hub.publish("xp", **lv)
            self._r_awards()

    def _r_card_xp(self, card_id, grade):
        lv = gamify.add_xp(2 + (grade >= 4))
        self.hub.publish("xp", **lv)
        self._r_awards()

    def _r_awards(self, **_):
        for a in gamify.check(self.achievement_stats()):
            self.hub.publish("achievement", **a)
        self.hub.publish("kpi", **self.kpis())

    # ------------------------------------------------------------------ tasks API
    def create_task(self, text):
        t, created = self.tasks.create(text, "manual")
        if created:
            self.bus.emit("task.changed", task_id=t["id"], old=None, new="open", origin="manual", text=t["text"],
                          sentence_id=None, via="user")
        self._publish_counts("tasks")
        return dict(task=t, created=created)

    def update_task(self, tid, status=None, text=None):
        old, new = self.tasks.update(tid, status, text)
        if old["status"] != new["status"]:
            self.bus.emit("task.changed", task_id=new["id"], old=old["status"], new=new["status"], origin=new["origin"],
                          text=new["text"], sentence_id=new["sentence_id"], via="user")
        self._publish_counts("tasks")
        return new

    def tasks_view(self, status="open"):
        return dict(buckets=self.tasks.buckets() if status == "open" else None,
                    items=self.tasks.list(status) if status != "open" else None, counts=self.tasks.counts())

    # ------------------------------------------------------------------ topics / links API
    def topics_view(self, limit=30):
        return self.topics.top(limit)

    def topic_detail(self, phrase):
        key = self.topics.key_for(phrase)
        items = [x for x in (self.describe(r["item_type"], r["item_id"]) for r in self.topics.items(key)) if x]
        like = f"%{phrase.lower()}%"
        return dict(phrase=phrase, items=items,
                    tasks=db.query("SELECT id, text, status, due, due_text FROM tasks WHERE lower(text) LIKE ? LIMIT 20", (like,)),
                    cards=db.query("SELECT id, front, back FROM cards WHERE lower(front) LIKE ? OR lower(back) LIKE ? LIMIT 20",
                                   (like, like)))

    def related(self, item_type, item_id):
        out = []
        for r in self.topics.related(item_type, int(item_id)):
            d = self.describe(r["item_type"], r["item_id"])
            if d:
                out.append(dict(d, shared=r["shared"], strength=r["strength"]))
        return dict(topics=self.topics.of(item_type, int(item_id)), related=out,
                    tasks=db.query("SELECT id, text, status, due, due_text FROM tasks WHERE session_id=?", (int(item_id),))
                    if item_type == "session" else [],
                    cards=db.query("SELECT id, front, back FROM cards WHERE source_type=? AND source_id=?",
                                   (item_type, int(item_id))))

    # ------------------------------------------------------------------ study API
    def study_next(self):
        c = self.study.next()
        if c and c["source_type"]:
            c["source"] = self.describe(c["source_type"], c["source_id"])
        return dict(card=c, stats=self.study.stats())

    def study_review(self, card_id, grade):
        r = self.study.review(card_id, int(grade))
        self.bus.emit("card.reviewed", card_id=int(card_id), grade=int(grade))
        return r

    def study_generate_all(self):
        def run(job):
            items = [("session", s["id"]) for s in db.query("SELECT id FROM sessions WHERE ended IS NOT NULL")]
            items += [("note", d["id"]) for d in db.query("SELECT id FROM docs WHERE kind='note' OR kind IS NULL")]
            made = 0
            for i, (t, iid) in enumerate(items):
                job.check()
                phrases = [r["phrase"] for r in self.topics.of(t, iid)]
                text = self._transcript(iid, CARD_MIN_CONF) if t == "session" else \
                    (db.one("SELECT text FROM docs WHERE id=?", (iid,)) or {}).get("text", "")
                made += self.study.generate(text or "", t, iid, phrases, limit=12 if t == "note" else 6)
                job.progress(i + 1, len(items), f"{made} new cards")
            self._publish_counts("cards", created=made)
        return self.jobs.submit("cards", run, "Generate flashcards").as_dict()

    # ------------------------------------------------------------------ today
    def today(self, now=None):
        now_dt = now or dt.datetime.now()
        b, st, g = self.tasks.buckets(now_dt), self.study.stats(), gamify.state()
        h = now_dt.hour
        greeting = "Good morning" if h < 12 else "Good afternoon" if h < 17 else "Good evening"
        say = [f"{greeting}."]
        if b["overdue"]:
            say.append(f"You have {len(b['overdue'])} overdue task{'s' * (len(b['overdue']) > 1)}: " +
                       "; ".join(t["text"].rstrip(".") for t in b["overdue"][:3]) + ".")
        if b["today"]:
            say.append("Due today: " + "; ".join(t["text"].rstrip(".") for t in b["today"][:3]) + ".")
        if b["week"]:
            say.append(f"{len(b['week'])} more due this week.")
        if not (b["overdue"] or b["today"] or b["week"]):
            say.append("Nothing is due this week.")
        if st["due"]:
            say.append(f"{st['due']} flashcard{'s are' if st['due'] > 1 else ' is'} ready for review.")
        if g["streak"] > 1:
            say.append(f"You're on a {g['streak']}-day streak.")
        k = self.kpis()
        if b["overdue"]:
            nxt = dict(action="tasks", label=f"Clear {len(b['overdue'])} overdue task{'s' * (len(b['overdue']) > 1)}")
        elif st["due"]:
            nxt = dict(action="study", label=f"Review {st['due']} card{'s' * (st['due'] > 1)}")
        elif not k["sessions"]:
            nxt = dict(action="live", label="Record or run your first session")
        elif st["new"]:
            nxt = dict(action="study", label=f"Learn {min(st['new'], 20)} new cards")
        else:
            nxt = dict(action="teach", label="Teach the model a few sentences")
        return dict(greeting=greeting, date=now_dt.strftime("%A, %B %d"), buckets=b, cards=st, game=g,
                    spoken=" ".join(say), next=nxt, counts=self.tasks.counts())

    # ------------------------------------------------------------------ jobs / backfill / export
    @property
    def training(self):
        r = self.jobs.running("train")
        return r[0].as_dict() if r else None

    def backfill(self):
        """Connect data that existed before these features (or was missed): runs once at start-up."""
        todo = [s["id"] for s in db.query("SELECT id FROM sessions WHERE ended IS NOT NULL ORDER BY id")
                if not self.index.has("session", s["id"])]
        notes = [d["id"] for d in db.query("SELECT d.id FROM docs d WHERE (kind='note' OR kind IS NULL) AND NOT EXISTS "
                                            "(SELECT 1 FROM topics t WHERE t.item_type='note' AND t.item_id=d.id)")]
        if not todo and not notes:
            return None

        def run(job):
            total = len(todo) + len(notes)
            for i, sid in enumerate(todo):
                job.check()
                self.bus.emit("session.finished", session_id=sid)
                job.progress(i + 1, total, f"session {sid}")
            for j, did in enumerate(notes):
                job.check()
                self.bus.emit("doc.added", doc_id=did)
                job.progress(len(todo) + j + 1, total, f"note {did}")
            self.bus.drain(120)
        return self.jobs.submit("reindex", run, "Connect existing sessions and notes", total=len(todo) + len(notes))

    def add_doc(self, text, name="Note"):
        did, n = self.index.add(text, name, "note")
        if did:
            self.bus.emit("doc.added", doc_id=did)
        self.hub.publish("kpi", **self.kpis())
        return dict(doc_id=did, chunks=n)

    def backup_bytes(self):
        """A consistent snapshot of the database (SQLite online backup), safe while the app runs."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            dst = sqlite3.connect(path)
            with db._lock:
                db._conn.backup(dst)
            dst.close()
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.unlink(path)

    def export_session_md(self, sid):
        s = db.one("SELECT * FROM sessions WHERE id=?", (int(sid),))
        if not s:
            raise KeyError("unknown session")
        segs = db.query("SELECT t0, t1, text, ref FROM segments WHERE session_id=? ORDER BY t0", (s["id"],))
        rel = self.related("session", s["id"])
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["started"]))
        L = [f"# Session {s['id']} - {s['source']}", "", f"*{when} · {s['audio_sec']:.0f} s audio · heavy models awake "
             f"{(s['awake_sec'] / s['audio_sec'] * 100 if s['audio_sec'] else 0):.0f}% · recognizer: {s['asr_backend']}*", ""]
        if s["summary"]:
            L += ["## Summary", "", s["summary"], ""]
        if rel["tasks"]:
            L += ["## Tasks", ""] + [f"- [{'x' if t['status'] == 'done' else ' '}] {t['text']}"
                                     + (f" (due {t['due_text']})" if t["due_text"] else "") for t in rel["tasks"]] + [""]
        if rel["topics"]:
            L += ["## Topics", "", ", ".join(t["phrase"] for t in rel["topics"]), ""]
        L += ["## Transcript", ""]
        for g in segs:
            L.append(f"**{g['t0']:.1f}–{g['t1']:.1f} s** {g['text'] or '*(no transcript)*'}")
            L.append("")
        if rel["cards"]:
            L += ["## Flashcards", ""] + [f"- **Q:** {c['front']}  \n  **A:** {c['back']}" for c in rel["cards"]] + [""]
        return "\n".join(L)
