"""The connected features: tasks, due dates, topics, flashcards, the event bus, jobs, and the
end-to-end flows that tie them together through a real Core and a real HTTP server."""
import datetime as dt
import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from edgescribe import core as core_mod, db, server, study, tasks, topics
from edgescribe.events import EventBus
from edgescribe.jobs import JobManager

NOW = dt.datetime(2026, 9, 29, 14, 30)          # a Tuesday afternoon


class DueDateTests(unittest.TestCase):
    CASES = [
        ("Submit the lab report by Friday.", "2026-10-02 23:59", "Friday"),
        ("Email the professor tomorrow.", "2026-09-30 23:59", "tomorrow"),
        ("Read chapter 3 tonight.", "2026-09-29 21:00", "tonight"),
        ("Finish the problem set by next Monday.", "2026-10-05 23:59", "next Monday"),
        ("Assignment 3 is due December 12th.", "2026-12-12 23:59", "December 12th"),
        ("Bring your laptop on the 4th of October.", "2026-10-04 23:59", "4th of October"),
        ("Revise your proposal in two weeks.", "2026-10-13 23:59", "in two weeks"),
        ("Complete the quiz by Thursday at 4pm.", "2026-10-01 16:00", "Thursday at 4pm"),
        ("Upload the dataset today at noon.", "2026-09-29 12:00", "today at noon"),
        ("Make sure you finish before Tuesday.", "2026-10-06 23:59", "Tuesday"),     # never 'today'
        ("Book the lab room this week.", "2026-10-02 23:59", "this week"),
        ("The report is due at the end of the month.", "2026-09-30 23:59", "end of the month"),
        ("Due Sept 3rd.", "2027-09-03 23:59", "Sept 3rd"),                           # past date -> next year
    ]

    def test_cases(self):
        for text, want, phrase in self.CASES:
            d, p = tasks.parse_due(text, NOW)
            self.assertEqual(d.strftime("%Y-%m-%d %H:%M"), want, text)
            self.assertEqual(p, phrase, text)

    def test_no_date_and_recurring(self):
        for text in ("We need to form a team.", "Office hours are Tuesdays at 4pm.", "February 30th is not real."):
            self.assertEqual(tasks.parse_due(text, NOW), (None, None), text)

    def test_priority(self):
        self.assertEqual(tasks.priority("x", NOW.timestamp() + 3600, NOW.timestamp()), 2)
        self.assertEqual(tasks.priority("You must submit it", None, NOW.timestamp()), 1)
        self.assertEqual(tasks.priority("Bring snacks", None, NOW.timestamp()), 0)


class StudyTests(unittest.TestCase):
    def test_sm2_schedule(self):
        c = dict(ease=2.5, interval=0, reps=0, lapses=0)
        out = []
        for g in (4, 4, 4):
            c.update(study.sm2(c, g, 0))
            out.append(c["interval"])
        self.assertEqual(out, [1.0, 6.0, 15.0])
        self.assertEqual(study.sm2(c, 3, 0)["interval"], 18.0)          # hard grows 15 -> 18, good -> 37.5
        self.assertEqual(study.sm2(dict(ease=2.5, interval=0, reps=0, lapses=0), 5, 0)["interval"], 4.0)
        prev = study.preview(dict(ease=2.5, interval=0, reps=0, lapses=0), 0)
        self.assertEqual(prev, {1: "10m", 3: "1d", 4: "1d", 5: "4d"})
        c.update(study.sm2(c, 1, 0))
        self.assertEqual((c["reps"], c["lapses"], c["interval"]), (0, 1, 0.0))
        self.assertEqual(c["due"], study.AGAIN_DELAY)
        self.assertGreaterEqual(c["ease"], 1.3)

    def test_definition_cards(self):
        c = study.definition_card("Hash tables are structures that map keys to buckets.")
        self.assertEqual(c["front"], "What are hash tables?")
        self.assertEqual(c["back"], "Structures that map keys to buckets")
        for bad in ("It is a table that maps keys to buckets.", "Today we will talk about recursion.",
                    "This works because recursion is a function that calls itself."):
            self.assertIsNone(study.definition_card(bad), bad)

    def test_cloze(self):
        c = study.cloze_card("The TCP handshake is a three step connection setup used on every connection.",
                             ["TCP handshake"])
        self.assertIn("_____", c["front"])
        self.assertEqual(c["back"], "TCP handshake")


class TopicTests(unittest.TestCase):
    def setUp(self):
        db.init(":memory:")

    def test_extract_and_relate(self):
        T = topics.Topics()
        T.extract("session", 1, "Today we will talk about the TCP handshake. The TCP handshake is a three step connection setup.")
        T.extract("session", 2, "Remember that the TCP handshake opens every connection. Hash tables map keys to buckets.")
        T.extract("note", 3, "Photosynthesis is the way plants make energy from light.")
        self.assertIn("tcp handshake", [t["phrase"] for t in T.of("session", 1)])
        rel = T.related("session", 1)
        self.assertEqual((rel[0]["item_type"], rel[0]["item_id"]), ("session", 2))
        self.assertNotIn(("note", 3), [(r["item_type"], r["item_id"]) for r in rel])
        self.assertEqual(T.top(1)[0]["items"], 2)

    def test_misheard_speech_needs_corroboration(self):
        T = topics.Topics()
        T.extract("note", 1, "Photosynthesis is the way plants make energy from light.")
        got = [p.lower() for p, _ in T.extract("session", 9, "Thom Maurice chevalier laughed off. Here are two views of "
                                                             "photosynthesis.", corroborate=True)]
        self.assertIn("photosynthesis", got)                       # backed by the note
        self.assertFalse([p for p in got if "maurice" in p])       # a one-off mishearing is not a topic
        got = [p.lower() for p, _ in T.extract("session", 10, "The Krebs cycle makes ATP. The Krebs cycle runs in "
                                                              "mitochondria.", corroborate=True)]
        self.assertIn("krebs cycle", got)                          # said twice: kept

    def test_filler_and_verbs_are_not_topics(self):
        cand = topics.candidates("So today we will basically talk about it. Please bring your laptop tonight.")
        self.assertFalse([s for s, _, _ in cand.values() if s.lower() in ("today", "bring", "tonight", "basically")])


class BusAndJobTests(unittest.TestCase):
    def test_order_isolation_and_chains(self):
        bus, seen = EventBus(), []
        bus.on("a", lambda x: seen.append(("first", x)))
        bus.on("a", lambda x: 1 / 0, "broken")
        bus.on("a", lambda x: (seen.append(("third", x)), bus.emit("b", y=x + 1)))
        bus.on("b", lambda y: seen.append(("chained", y)))
        bus.emit("a", x=1)
        self.assertTrue(bus.drain(5))
        self.assertEqual(seen, [("first", 1), ("third", 1), ("chained", 2)])
        snap = {r["name"]: r for r in bus.snapshot()["reactors"]}
        self.assertEqual(snap["broken"]["errors"], 1)
        self.assertIn("ZeroDivisionError", snap["broken"]["last_error"])

    def test_jobs_progress_cancel_exclusive(self):
        jm = JobManager()

        def slow(job):
            for i in range(200):
                job.check()
                job.progress(i + 1, 200)
                time.sleep(0.01)
        j = jm.submit("train", slow, total=200)
        with self.assertRaises(RuntimeError):
            jm.submit("train", slow)                      # exclusive per kind
        time.sleep(0.1)
        jm.cancel(j.id)
        self.assertEqual(jm.wait(j.id).state, "cancelled")
        self.assertLess(j.done, 200)
        f = jm.submit("x", lambda job: 1 / 0)
        self.assertEqual(jm.wait(f.id).state, "failed")
        self.assertIn("ZeroDivisionError", f.error)


class ConnectedFlowTests(unittest.TestCase):
    """A real Core: a finished session must flow into search, topics, tasks and flashcards."""

    @classmethod
    def setUpClass(cls):
        db.init(":memory:")
        cls.core = core_mod.Core(load_asr=False, speech=False, backfill=False)
        for e in range(12):                              # a trained classifier, so action items score > 0.6
            cls.core.clf.train_curriculum(32, seed=e)

    def run_session(self, seed):
        c = self.core
        r = c.run_sim(seed=seed, hard=0.2, speed=0, source="synth")
        t = time.time()
        while c._busy() and time.time() - t < 60:
            time.sleep(0.1)
        self.assertTrue(c.bus.drain(30))
        return r["id"]

    def test_session_flows_everywhere(self):
        sid = self.run_session(5)
        c = self.core
        hits = c.index.ask(db.one("SELECT text FROM segments WHERE session_id=? AND text!='' LIMIT 1", (sid,))["text"],
                           kind="session")
        self.assertTrue(hits and hits[0]["ref_id"] == sid, "finished session should be searchable")
        self.assertTrue(c.topics.of("session", sid), "topics should be extracted")
        auto = db.query("SELECT * FROM tasks WHERE session_id=? AND origin='auto'", (sid,))
        self.assertTrue(auto, "action items should become tasks")
        self.assertTrue(db.query("SELECT 1 FROM cards WHERE source_type='session' AND source_id=?", (sid,)),
                        "clean transcripts should produce flashcards")
        links = c.related("session", sid)
        self.assertEqual({t["id"] for t in links["tasks"]}, {t["id"] for t in auto})
        md = c.export_session_md(sid)
        self.assertIn("## Transcript", md)
        self.assertIn("## Tasks", md)

    def test_labels_tasks_and_classifier_feed_each_other(self):
        c = self.core
        sid = self.run_session(7)
        s = db.one("SELECT id, text FROM sentences WHERE session_id=? ORDER BY score LIMIT 1", (sid,))
        c.feedback_sentence(s["text"], 1, s["id"], "session")        # you say "this is an action item"
        c.bus.drain(10)
        t = c.tasks.find_by_sentence(s["id"])
        self.assertEqual((t["status"], t["origin"]), ("open", "label"))
        c.feedback_sentence(s["text"], 0, s["id"], "session")        # ...then change your mind
        c.bus.drain(10)
        self.assertEqual(c.tasks.get(t["id"])["status"], "dismissed")

        auto = db.one("SELECT * FROM tasks WHERE origin='auto' AND status='open' LIMIT 1")
        before = c.clf.predict(auto["text"])
        c.update_task(auto["id"], status="dismissed")                # "not a task" teaches the classifier
        c.bus.drain(10)
        self.assertLess(c.clf.predict(auto["text"]), before)
        self.assertEqual(db.one("SELECT label FROM sentences WHERE id=?", (auto["sentence_id"],))["label"], 0)

        phrase = "Water the lab plants before Thursday."
        p0 = c.clf.predict(phrase)
        r = c.create_task(phrase)                                    # typing a task in teaches it too
        c.bus.drain(10)
        self.assertTrue(r["created"])
        self.assertIsNotNone(r["task"]["due"])
        self.assertGreater(c.clf.predict(phrase), p0)

    def test_notes_become_topics_and_cards_and_study_gives_xp(self):
        c = self.core
        r = c.add_doc("Entropy is a measure of disorder. Bayes theorem is a rule for updating beliefs with evidence. "
                      "Virtual memory refers to a trick that lets programs use more memory than exists.", "Revision notes")
        c.bus.drain(10)
        self.assertTrue(c.topics.of("note", r["doc_id"]))
        fronts = [x["front"] for x in db.query("SELECT front FROM cards WHERE source_type='note' AND source_id=?", (r["doc_id"],))]
        self.assertIn("What is entropy?", fronts)
        xp0 = db.kv_get("xp", 0)
        nxt = c.study_next()
        self.assertIsNotNone(nxt["card"])
        res = c.study_review(nxt["card"]["id"], 4)
        c.bus.drain(10)
        self.assertEqual(res["interval"], 1.0)
        self.assertGreater(db.kv_get("xp", 0), xp0)
        with self.assertRaises(ValueError):
            c.study_review(nxt["card"]["id"], 2)

    def test_today_backup_and_race(self):
        c = self.core
        c.create_task("Pay the lab fee today")
        c.bus.drain(10)
        d = c.today()
        self.assertTrue(d["buckets"]["today"] or d["buckets"]["overdue"])
        self.assertIn("Due today", d["spoken"])
        raw = c.backup_bytes()
        p = tempfile.mktemp(suffix=".db")
        open(p, "wb").write(raw)
        con = sqlite3.connect(p)
        self.assertGreaterEqual(con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        con.close()
        results = []

        def start():
            try:
                results.append(c.run_sim(seed=1, hard=0.2, speed=4, source="synth")["id"])
            except RuntimeError:
                results.append("busy")
        ts = [threading.Thread(target=start) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sum(1 for r in results if r != "busy"), 1, results)   # exactly one session started
        while c._busy():
            time.sleep(0.1)
        c.bus.drain(20)

    def test_training_job_can_be_cancelled(self):
        c = self.core
        j = c.train("clf", 100)
        time.sleep(0.3)
        c.jobs.cancel(j["id"])
        self.assertEqual(c.jobs.wait(j["id"], 30).state, "cancelled")
        self.assertIsNone(c.training)


class RouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init(":memory:")
        server.core = core_mod.Core(load_asr=False, speech=False, backfill=False)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_keep_alive_survives_routes_that_ignore_their_body(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        H = {"X-EdgeScribe": "1", "Content-Type": "application/json"}
        conn.request("POST", "/api/feedback/missed", body=json.dumps({"padding": "x" * 500}), headers=H)
        r = conn.getresponse(); r.read()
        self.assertEqual(r.status, 200)
        conn.request("GET", "/api/state")                    # same connection: must parse cleanly
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        self.assertIn("kpis", json.loads(r.read()))
        conn.close()

    def test_status_codes(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        H = {"X-EdgeScribe": "1", "Content-Type": "application/json"}
        for method, path, body, want in [
            ("GET", "/api/sessions/abc", None, 404), ("GET", "/api/sessions/999", None, 404),
            ("GET", "/api/tasks/1", None, 405), ("POST", "/api/tasks", "{}", 400),
            ("POST", "/api/tasks", json.dumps({"text": "Email the TA by Friday"}), 200),
            ("POST", "/api/tasks/1", json.dumps({"status": "nope"}), 400),
            ("POST", "/api/tasks/424242", json.dumps({"status": "done"}), 404),
            ("POST", "/api/study/review", json.dumps({"id": 1, "grade": 9}), 400),
            ("POST", "/api/jobs/77/cancel", "{}", 404), ("GET", "/api/today", None, 200),
            ("GET", "/api/topics", None, 200), ("GET", "/api/related/banana/1", None, 404),
        ]:
            conn.request(method, path, body=body, headers=H if method == "POST" else {})
            r = conn.getresponse(); r.read()
            self.assertEqual(r.status, want, f"{method} {path}")
        conn.request("GET", "/api/metrics")
        m = json.loads(conn.getresponse().read())
        self.assertGreater(m["http"]["requests"], 5)
        self.assertTrue(any(r["route"].startswith("POST /api/tasks") for r in m["http"]["routes"]))
        self.assertIn("reactors", m["events"])
        conn.close()

    def test_backup_download(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/backup")
        r = conn.getresponse()
        data = r.read()
        self.assertEqual(r.status, 200)
        self.assertIn("attachment", r.getheader("Content-Disposition"))
        self.assertTrue(data.startswith(b"SQLite format 3"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
