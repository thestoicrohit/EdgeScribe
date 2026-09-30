"""SQLite persistence (stdlib) with versioned migrations. Everything learned or measured lands here."""
import json
import logging
import sqlite3
import threading

from . import config

log = logging.getLogger("edgescribe.db")
_lock = threading.RLock()
_conn = None

MIGRATIONS = [
    # v1: original schema
    """
    CREATE TABLE IF NOT EXISTS sessions(
      id INTEGER PRIMARY KEY, started REAL, ended REAL, source TEXT, mode TEXT, label TEXT,
      audio_sec REAL DEFAULT 0, awake_sec REAL DEFAULT 0, n_spikes INTEGER DEFAULT 0,
      n_frames INTEGER DEFAULT 0, n_events INTEGER DEFAULT 0, asr_ms REAL DEFAULT 0,
      snn_ms REAL DEFAULT 0, summary TEXT, actions TEXT, precision REAL, recall REAL, f1 REAL,
      asr_backend TEXT);
    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY, session_id INTEGER, t REAL, feat TEXT, verdict INTEGER, auto INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS segments(
      id INTEGER PRIMARY KEY, session_id INTEGER, t0 REAL, t1 REAL, text TEXT, asr_ms REAL);
    CREATE TABLE IF NOT EXISTS sentences(
      id INTEGER PRIMARY KEY, session_id INTEGER, segment_id INTEGER, text TEXT, score REAL,
      label INTEGER, source TEXT);
    CREATE TABLE IF NOT EXISTS learn_log(
      id INTEGER PRIMARY KEY, ts REAL, learner TEXT, step INTEGER, acc REAL, loss REAL,
      f1 REAL, prec REAL, rec REAL, note TEXT);
    CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, source TEXT, text TEXT, added REAL);
    CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
    """,
    # v2: speech-recognition accuracy + lookup indexes
    """
    ALTER TABLE segments ADD COLUMN ref TEXT;
    ALTER TABLE segments ADD COLUMN wer_err INTEGER;
    ALTER TABLE segments ADD COLUMN wer_n INTEGER;
    ALTER TABLE segments ADD COLUMN asr_conf REAL;
    ALTER TABLE sessions ADD COLUMN wer REAL;
    CREATE INDEX IF NOT EXISTS ix_events_session ON events(session_id);
    CREATE INDEX IF NOT EXISTS ix_segments_session ON segments(session_id);
    CREATE INDEX IF NOT EXISTS ix_sentences_session ON sentences(session_id);
    CREATE INDEX IF NOT EXISTS ix_sentences_label ON sentences(label);
    CREATE INDEX IF NOT EXISTS ix_learn_learner ON learn_log(learner, id);
    """,
    # v3: connected features - tasks, flashcards (+ review log), topics, typed search documents
    """
    ALTER TABLE docs ADD COLUMN kind TEXT DEFAULT 'note';
    ALTER TABLE docs ADD COLUMN ref_id INTEGER;
    CREATE TABLE IF NOT EXISTS tasks(
      id INTEGER PRIMARY KEY, text TEXT NOT NULL, norm TEXT UNIQUE, due REAL, due_text TEXT,
      status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done','dismissed')),
      priority INTEGER DEFAULT 1, created REAL, updated REAL, session_id INTEGER, sentence_id INTEGER, origin TEXT);
    CREATE TABLE IF NOT EXISTS cards(
      id INTEGER PRIMARY KEY, front TEXT NOT NULL, back TEXT NOT NULL, kind TEXT, norm TEXT UNIQUE,
      source_type TEXT, source_id INTEGER, ease REAL DEFAULT 2.5, interval REAL DEFAULT 0, reps INTEGER DEFAULT 0,
      lapses INTEGER DEFAULT 0, due REAL, last REAL, created REAL, suspended INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reviews(
      id INTEGER PRIMARY KEY, card_id INTEGER, ts REAL, grade INTEGER, interval_before REAL, interval_after REAL);
    CREATE TABLE IF NOT EXISTS topics(
      item_type TEXT, item_id INTEGER, key TEXT, phrase TEXT, score REAL, PRIMARY KEY(item_type, item_id, key));
    CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status, due);
    CREATE INDEX IF NOT EXISTS ix_cards_due ON cards(suspended, due);
    CREATE INDEX IF NOT EXISTS ix_reviews_ts ON reviews(ts);
    CREATE INDEX IF NOT EXISTS ix_topics_key ON topics(key);
    CREATE INDEX IF NOT EXISTS ix_docs_kind ON docs(kind, ref_id);
    """,
]
TABLES = ("sessions", "events", "segments", "sentences", "learn_log", "docs", "kv", "tasks", "cards", "reviews",
          "topics")


def init(path=None):
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        p = path or config.DB_PATH
        if str(p) != ":memory:":
            config.DATA.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(p), check_same_thread=False, timeout=10)
        _conn.row_factory = sqlite3.Row
        if str(p) != ":memory:":
            _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        migrate()


def version():
    return _conn.execute("PRAGMA user_version").fetchone()[0]


def migrate():
    with _lock:
        v = version()
        for i, script in enumerate(MIGRATIONS[v:], start=v + 1):
            try:
                _conn.executescript("BEGIN;" + script + f"PRAGMA user_version={i};COMMIT;")
                log.info("database migrated to v%d", i)
            except sqlite3.Error:
                _conn.rollback()
                raise


def query(sql, args=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, args).fetchall()]


def one(sql, args=()):
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql, args=()):
    """Run a write; returns lastrowid."""
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.lastrowid


def kv_get(key, default=None):
    r = one("SELECT v FROM kv WHERE k=?", (key,))
    return json.loads(r["v"]) if r else default


def kv_set(key, value):
    execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, json.dumps(value)))


def wipe(keep=("settings",)):
    with _lock:
        for t in TABLES:
            if t == "kv":
                _conn.execute(f"DELETE FROM kv WHERE k NOT IN ({','.join('?' * len(keep))})", keep)
            else:
                _conn.execute(f"DELETE FROM {t}")
        _conn.commit()
