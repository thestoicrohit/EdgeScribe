"""Background jobs with progress, cancellation and history (training, voice bank, re-indexing)."""
import itertools
import logging
import threading
import time
import traceback

log = logging.getLogger("edgescribe.jobs")


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, jid, kind, label, total, publish):
        self.id, self.kind, self.label, self.total = jid, kind, label, total
        self.done, self.state, self.msg, self.error = 0, "queued", "", None
        self.started = self.ended = None
        self.created = time.time()
        self._cancel = threading.Event()
        self._publish = publish

    @property
    def cancelled(self):
        return self._cancel.is_set()

    def check(self):
        """Call inside loops: raises Cancelled if the user pressed cancel."""
        if self._cancel.is_set():
            raise Cancelled()

    def progress(self, done=None, total=None, msg=None):
        if done is not None:
            self.done = done
        if total is not None:
            self.total = total
        if msg is not None:
            self.msg = msg
        self._publish(self)

    def as_dict(self):
        return dict(id=self.id, kind=self.kind, label=self.label, state=self.state, done=self.done,
                    total=self.total, msg=self.msg, error=self.error, created=self.created,
                    started=self.started, ended=self.ended,
                    seconds=round((self.ended or time.time()) - self.started, 2) if self.started else None)


class JobManager:
    def __init__(self, hub=None, keep=50):
        self.hub, self.keep = hub, keep
        self.jobs = {}
        self.ids = itertools.count(1)
        self.lock = threading.Lock()

    def _publish(self, job):
        if self.hub:
            self.hub.publish("job", **job.as_dict())

    def running(self, kind=None):
        return [j for j in self.jobs.values() if j.state in ("queued", "running") and (kind is None or j.kind == kind)]

    def submit(self, kind, fn, label="", total=0, exclusive=True, on_done=None):
        """Run fn(job) in a thread. exclusive: refuse if a job of this kind is already running."""
        with self.lock:
            if exclusive and self.running(kind):
                raise RuntimeError(f"a {kind} job is already running")
            job = Job(next(self.ids), kind, label or kind, total, self._publish)
            self.jobs[job.id] = job
            for old in sorted(self.jobs.values(), key=lambda j: j.id)[:-self.keep]:
                if old.state not in ("queued", "running"):
                    del self.jobs[old.id]

        def run():
            job.state, job.started = "running", time.time()
            self._publish(job)
            try:
                fn(job)
                job.state = "cancelled" if job.cancelled else "done"
            except Cancelled:
                job.state = "cancelled"
            except Exception as e:
                job.state, job.error = "failed", f"{type(e).__name__}: {e}"
                log.error("job %s (%s) failed\n%s", job.id, kind, traceback.format_exc())
            finally:
                job.ended = time.time()
                self._publish(job)
                if on_done:
                    try:
                        on_done(job)
                    except Exception:
                        log.exception("job on_done failed")
        threading.Thread(target=run, daemon=True, name=f"job-{kind}-{job.id}").start()
        return job

    def cancel(self, jid):
        job = self.jobs.get(int(jid))
        if not job:
            raise KeyError("unknown job")
        if job.state in ("queued", "running"):
            job._cancel.set()
            job.msg = "cancelling..."
            self._publish(job)
        return job.as_dict()

    def list(self):
        return [j.as_dict() for j in sorted(self.jobs.values(), key=lambda j: -j.id)]

    def wait(self, jid, timeout=120):
        t = time.time()
        while time.time() - t < timeout:
            if self.jobs[jid].state not in ("queued", "running"):
                return self.jobs[jid]
            time.sleep(0.05)
        raise TimeoutError(f"job {jid} still running")
