"""In-process event bus: how every feature is connected to every other.

Emitters never wait for reactors: events are queued and handled in order on one worker thread, so a
slow or failing reactor can't stall a session or an HTTP request. Reactors may emit further events
(chains). Each reactor's calls, errors and time are counted and shown in Insights -> System.

Topics used by the app:
  session.finished(session_id)                      -> search index, tasks, topics, flashcards, awards
  doc.added(doc_id)                                 -> topics, flashcards
  sentence.labeled(sentence_id, text, label, via)   -> create / dismiss a task
  task.changed(task_id, old, new, origin, via)      -> teach the classifier, XP
  card.reviewed(card_id, grade)                     -> XP, streak, awards
  training.finished(kind)                           -> awards, KPIs
"""
import logging
import queue
import threading
import time

log = logging.getLogger("edgescribe.events")


class EventBus:
    def __init__(self, sync=False):
        self.sync = sync                        # tests: run reactors inline
        self.handlers = {}                      # topic -> [(name, fn)]
        self.stats = {}                         # name -> {calls, errors, ms, last_error}
        self.emitted = {}                       # topic -> count
        self.q = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        self._pending = 0
        self._lock = threading.Lock()
        if not sync:
            threading.Thread(target=self._worker, daemon=True, name="event-bus").start()

    def on(self, topic, fn, name=None):
        name = name or f"{topic}:{getattr(fn, '__name__', 'handler')}"
        self.handlers.setdefault(topic, []).append((name, fn))
        self.stats.setdefault(name, dict(topic=topic, calls=0, errors=0, ms=0.0, last_error=None))
        return fn

    def emit(self, topic, **payload):
        with self._lock:
            self.emitted[topic] = self.emitted.get(topic, 0) + 1
            if not self.handlers.get(topic):
                return
            self._pending += 1
            self._idle.clear()
        if self.sync:
            self._dispatch(topic, payload)
        else:
            self.q.put((topic, payload))

    def _worker(self):
        while True:
            topic, payload = self.q.get()
            self._dispatch(topic, payload)

    def _dispatch(self, topic, payload):
        try:
            for name, fn in list(self.handlers.get(topic, [])):
                st = self.stats[name]
                t = time.perf_counter()
                try:
                    fn(**payload)
                except Exception as e:           # a broken reactor must never take down the others
                    st["errors"] += 1
                    st["last_error"] = f"{type(e).__name__}: {e}"[:200]
                    log.exception("event reactor %s failed on %s", name, topic)
                finally:
                    st["calls"] += 1
                    st["ms"] += (time.perf_counter() - t) * 1000
        finally:
            with self._lock:
                self._pending -= 1
                if self._pending == 0:
                    self._idle.set()

    def drain(self, timeout=30):
        """Block until every queued event (including chained ones) has been handled."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._idle.wait(0.05) and self._pending == 0:
                return True
        return False

    def snapshot(self):
        return dict(emitted=dict(self.emitted), queued=self._pending,
                    reactors=[dict(name=n, **{k: (round(v, 1) if k == "ms" else v) for k, v in s.items()})
                              for n, s in sorted(self.stats.items())])
