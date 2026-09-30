"""Local-only HTTP + SSE server (stdlib). Binds to 127.0.0.1; nothing is exposed to the network.

* Every route is declared once in ROUTES (method, pattern, handler); ids are validated by the pattern.
* POSTs must carry `X-EdgeScribe: 1` (forces a CORS preflight, so other websites can't drive it) and
  the Host/Origin must be local (blocks DNS rebinding).
* Errors map to status codes in one place: ValueError 400, KeyError 404, RuntimeError 409, else 500.
* Every request is timed; /api/metrics reports counts, errors and p50/p95 latency per route."""
import collections
import json
import logging
import logging.handlers
import mimetypes
import queue
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db
from .core import Core

MAX_UPLOAD = 300 * 1024 * 1024
MAX_JSON = 4 * 1024 * 1024
log = logging.getLogger("edgescribe.server")
core: Core = None


def _host_ok(value):
    if not value:
        return True
    host = urllib.parse.urlsplit(value if "//" in value else "//" + value).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.time()
        self.routes = collections.defaultdict(lambda: dict(count=0, errors=0, client_errors=0,
                                                           lat=collections.deque(maxlen=300)))

    def record(self, route, status, ms):
        with self.lock:
            r = self.routes[route]
            r["count"] += 1
            r["errors"] += status >= 500
            r["client_errors"] += 400 <= status < 500
            r["lat"].append(ms)

    def snapshot(self):
        def pct(xs, p):
            xs = sorted(xs)
            return round(xs[min(len(xs) - 1, int(p * len(xs)))], 1) if xs else None
        with self.lock:
            rows = [dict(route=k, count=v["count"], errors=v["errors"], client_errors=v["client_errors"],
                         p50_ms=pct(v["lat"], .5), p95_ms=pct(v["lat"], .95)) for k, v in self.routes.items()]
        return dict(uptime_s=round(time.time() - self.started), requests=sum(r["count"] for r in rows),
                    errors=sum(r["errors"] for r in rows), routes=sorted(rows, key=lambda r: -r["count"]))


METRICS = Metrics()


# ---------------------------------------------------------------------------------- handlers
# Each takes (h, m, q) = (request handler, regex match, query dict) and returns
# a JSON-able object, or (bytes, content_type[, extra_headers]).
def _int(q, k, d):
    try:
        return int(q.get(k, [d])[0])
    except ValueError:
        raise ValueError(f"{k} must be an integer") from None


def session_detail(h, m, q):
    sid = int(m[1])
    s = db.one("SELECT * FROM sessions WHERE id=?", (sid,))
    if not s:
        raise KeyError("unknown session")
    return dict(session=s,
                segments=db.query("SELECT * FROM segments WHERE session_id=? ORDER BY t0", (sid,)),
                sentences=db.query("SELECT id,text,score,label FROM sentences WHERE session_id=? ORDER BY id", (sid,)),
                events=db.query("SELECT id,t,verdict,auto FROM events WHERE session_id=? ORDER BY t", (sid,)),
                links=core.related("session", sid))


def backup(h, m, q):
    name = time.strftime("edgescribe-backup-%Y%m%d-%H%M%S.db")
    return core.backup_bytes(), "application/octet-stream", {"Content-Disposition": f'attachment; filename="{name}"'}


def export_md(h, m, q):
    return (core.export_session_md(int(m[1])).encode("utf-8"), "text/markdown; charset=utf-8",
            {"Content-Disposition": f'attachment; filename="session-{int(m[1])}.md"'})


def metrics(h, m, q):
    return dict(http=METRICS.snapshot(), events=core.bus.snapshot(), jobs=core.jobs.list()[:20])


def sim(h, m, q):
    d = h.json()
    return core.run_sim(d.get("seed"), min(max(float(d.get("hard", 0.7)), 0), 1),
                        min(max(float(d.get("speed", 8)), 0), 50), source=d.get("source"))


def live_chunk(h, m, q):
    core.live_chunk(_int(q, "sid", 0), h.body())
    return dict(ok=True)


def upload(h, m, q):
    return core.run_wav(h.body(), q.get("name", ["upload"])[0][:80])


def need(d, *keys):
    for k in keys:
        if k not in d:
            raise ValueError(f"missing field: {k}")
    return d


ROUTES = [
    # --- state & system
    ("GET", r"/api/state", lambda h, m, q: core.state()),
    ("GET", r"/api/health", lambda h, m, q: core.health()),
    ("GET", r"/api/metrics", metrics),
    ("GET", r"/api/jobs", lambda h, m, q: core.jobs.list()),
    ("POST", r"/api/jobs/(\d+)/cancel", lambda h, m, q: core.jobs.cancel(int(m[1]))),
    ("GET", r"/api/settings", lambda h, m, q: core.settings()),
    ("POST", r"/api/settings", lambda h, m, q: core.update_settings(h.json())),
    ("GET", r"/api/backup", backup),
    ("POST", r"/api/reset", lambda h, m, q: (core.reset(), dict(ok=True))[1]),
    # --- speech
    ("GET", r"/api/voices", lambda h, m, q: core.voices()),
    ("POST", r"/api/tts", lambda h, m, q: (core.tts(h.json().get("text", ""), h.json_cached.get("voice"),
                                                     h.json_cached.get("rate")), "audio/wav")),
    # --- sessions
    ("POST", r"/api/sim", sim),
    ("POST", r"/api/upload", upload),
    ("POST", r"/api/live/start", lambda h, m, q: core.live_start()),
    ("POST", r"/api/live/chunk", live_chunk),
    ("POST", r"/api/live/stop", lambda h, m, q: core.live_stop(int(need(h.json(), "sid")["sid"]))),
    ("GET", r"/api/sessions", lambda h, m, q: db.query(
        "SELECT id,started,source,mode,label,audio_sec,awake_sec,n_events,f1,wer,summary,actions "
        "FROM sessions WHERE ended IS NOT NULL ORDER BY id DESC LIMIT ?", (min(_int(q, "limit", 50), 500),))),
    ("GET", r"/api/sessions/(\d+)", session_detail),
    ("GET", r"/api/sessions/(\d+)/export\.md", export_md),
    # --- learning
    ("GET", r"/api/stats", lambda h, m, q: core.stats()),
    ("POST", r"/api/train", lambda h, m, q: core.train(h.json().get("kind", "both"),
                                                      max(1, min(int(h.json_cached.get("episodes", 10)), 100)))),
    ("GET", r"/api/teach/next", lambda h, m, q: core.teach_next()),
    ("POST", r"/api/feedback/sentence", lambda h, m, q: core.feedback_sentence(
        need(h.json(), "text", "label")["text"], h.json_cached["label"], h.json_cached.get("id"),
        h.json_cached.get("origin", "session"), h.json_cached.get("oracle"))),
    ("POST", r"/api/feedback/event", lambda h, m, q: core.feedback_event(need(h.json(), "id", "verdict")["id"],
                                                                        h.json_cached["verdict"])),
    ("POST", r"/api/feedback/missed", lambda h, m, q: core.feedback_missed()),
    # --- search & notes
    ("POST", r"/api/ask", lambda h, m, q: dict(results=core.index.ask(str(need(h.json(), "q")["q"])[:500], k=5,
                                                                      kind=h.json_cached.get("kind")))),
    ("POST", r"/api/ask/feedback", lambda h, m, q: dict(boost=core.index.feedback(
        need(h.json(), "key", "val")["key"], 1 if int(h.json_cached["val"]) > 0 else -1))),
    ("POST", r"/api/docs", lambda h, m, q: core.add_doc(str(need(h.json(), "text")["text"])[:2_000_000],
                                                        str(h.json_cached.get("name") or "Note")[:80])),
    # --- tasks
    ("GET", r"/api/tasks", lambda h, m, q: core.tasks_view(q.get("status", ["open"])[0])),
    ("POST", r"/api/tasks", lambda h, m, q: core.create_task(str(need(h.json(), "text")["text"]))),
    ("POST", r"/api/tasks/(\d+)", lambda h, m, q: core.update_task(int(m[1]), h.json().get("status"),
                                                                   h.json_cached.get("text"))),
    # --- topics & links
    ("GET", r"/api/topics", lambda h, m, q: core.topics_view(min(_int(q, "limit", 30), 200))),
    ("GET", r"/api/topics/items", lambda h, m, q: core.topic_detail(q.get("phrase", [""])[0][:100])),
    ("GET", r"/api/related/(session|note)/(\d+)", lambda h, m, q: core.related(m[1], int(m[2]))),
    # --- study
    ("GET", r"/api/study/next", lambda h, m, q: core.study_next()),
    ("GET", r"/api/study/stats", lambda h, m, q: core.study.stats()),
    ("POST", r"/api/study/review", lambda h, m, q: core.study_review(need(h.json(), "id", "grade")["id"],
                                                                     h.json_cached["grade"])),
    ("POST", r"/api/study/suspend", lambda h, m, q: (core.study.suspend(need(h.json(), "id")["id"]), dict(ok=True))[1]),
    ("POST", r"/api/study/generate", lambda h, m, q: core.study_generate_all()),
    # --- today
    ("GET", r"/api/today", lambda h, m, q: core.today()),
]
_COMPILED = [(meth, re.compile(pat + r"\Z"), fn, pat) for meth, pat, fn in ROUTES]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    json_cached = {}

    def log_message(self, *a):
        pass

    # ---- plumbing ----------------------------------------------------------------------
    def _send(self, code, body, ctype="application/json", extra=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, default=float).encode()
        self._status = code
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        return self._raw

    def json(self):
        if len(self._raw) > MAX_JSON:
            raise ValueError("request too large")
        raw = self._raw
        try:
            d = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise ValueError("body is not valid JSON") from None
        if not isinstance(d, dict):
            raise ValueError("body must be a JSON object")
        self.json_cached = d
        return d

    def _guard(self, post):
        if not (_host_ok(self.headers.get("Host")) and _host_ok(self.headers.get("Origin"))):
            self._send(403, dict(error="forbidden host"))
            return False
        if post and self.headers.get("X-EdgeScribe") != "1":
            self._send(403, dict(error="missing X-EdgeScribe header"))
            return False
        return True

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        t0, self._status, route = time.perf_counter(), 0, "static"
        self.json_cached, self._raw = {}, b""
        u = urllib.parse.urlsplit(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)
        try:
            # Read the whole body up front, exactly once: a route that ignores its body must not leave
            # bytes in a keep-alive connection (they would be parsed as the next request).
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_UPLOAD:
                self.close_connection = True
                route = "guard"
                return self._send(413 if n > 0 else 400, dict(error="body too large" if n > 0 else "bad Content-Length"))
            self._raw = self.rfile.read(n) if n else b""
            if not self._guard(method == "POST"):
                route = "guard"
                return
            if method == "GET" and path == "/api/stream":
                route = "/api/stream"
                return self._sse()
            for meth, rx, fn, pat in _COMPILED:
                m = rx.match(path)
                if m and meth == method:
                    route = f"{method} {pat}"
                    out = fn(self, m, qs)
                    if isinstance(out, tuple):
                        return self._send(200, out[0], out[1], out[2] if len(out) > 2 else None)
                    return self._send(200, out)
            if path.startswith("/api/"):
                route = "unknown"
                allowed = sorted({meth for meth, rx, *_ in _COMPILED if rx.match(path)})
                if allowed:
                    return self._send(405, dict(error=f"use {' or '.join(allowed)}"), extra={"Allow": ", ".join(allowed)})
                return self._send(404, dict(error="not found"))
            if method != "GET":
                return self._send(405, dict(error="use GET"))
            return self._static(path)
        except ValueError as e:
            self._send(400, dict(error=str(e)))
        except KeyError as e:
            self._send(404, dict(error=str(e).strip("'\"")))
        except RuntimeError as e:
            self._send(409, dict(error=str(e)))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log.exception("%s %s failed", method, path)
            try:
                self._send(500, dict(error=f"{type(e).__name__}: {e}"))
            except OSError:
                pass
        finally:
            if route != "/api/stream":
                METRICS.record(route, self._status, (time.perf_counter() - t0) * 1000)

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (config.WEB / rel).resolve()
        if config.WEB.resolve() not in f.parents and f != config.WEB.resolve() or not f.is_file():
            return self._send(404, dict(error="not found"))
        self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")

    def _sse(self):
        q = core.hub.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(b"data: " + json.dumps(msg, default=float).encode() + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            core.hub.unsubscribe(q)
        self.close_connection = True


def setup_logging():
    config.DATA.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(config.DATA / "edgescribe.log", maxBytes=1_000_000, backupCount=2,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger("edgescribe")
    root.setLevel(logging.INFO)
    root.handlers = [fh, sh]


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python run.py", description="EdgeScribe: private on-device lecture assistant")
    ap.add_argument("--doctor", action="store_true", help="check what works on this machine and exit")
    ap.add_argument("--port", type=int, default=config.PORT, help=f"port on 127.0.0.1 (default {config.PORT})")
    ap.add_argument("--no-speech", action="store_true", help="skip the OS speech engines (voices / recognition)")
    args = ap.parse_args(argv)
    if args.doctor:
        from . import doctor
        print(doctor.run())
        return 0
    global core
    setup_logging()
    core = Core(speech=not args.no_speech)
    try:
        srv = ThreadingHTTPServer((config.HOST, args.port), Handler)
    except OSError as e:
        print(f"Port {args.port} is busy ({e}). Try:  python run.py --port {args.port + 1}")
        return 1
    srv.daemon_threads = True
    print(f"EdgeScribe  http://{config.HOST}:{args.port}   asr={core.asr.name}  "
          f"voices={getattr(core.tts_engine, 'name', 'browser only')}  npu={core.hw['npu']}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
