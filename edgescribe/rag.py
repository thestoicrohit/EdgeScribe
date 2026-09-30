"""Local retrieval over transcripts, summaries and dropped-in notes.
Okapi BM25 with light stemming (numpy; swap in AI Hub embeddings later). Thumbs up/down re-rank
chunks: a small, persistent, learned boost per chunk. Scores are shown normalised to 0..1."""
import re
import threading
import time
import zlib

import numpy as np

from . import db

STOP = set("the a an and or of to in is are was were be it this that for on with as at by we you i "
           "from can will do does did what when where who how which about".split())


def stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[:-len(suf)]
    return w


def tokens(text):
    return [stem(w) for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP and len(w) > 1]


def chunk(text, size=60, overlap=15):
    w = text.split()
    step = size - overlap
    return [" ".join(w[i:i + step + overlap]) for i in range(0, max(len(w) - overlap, 1), step)]


def key(text):
    return "%08x" % zlib.crc32(text.encode())


class LocalIndex:
    """Passages from notes and finished sessions. Each passage remembers where it came from
    (kind = 'note' | 'session', ref_id, doc_id) so results link back to their source."""

    def __init__(self):
        self.chunks, self.sources, self.meta = [], [], []
        self.boost = {}
        self._dirty = True
        self._lock = threading.RLock()

    def load(self):
        with self._lock:
            self.chunks, self.sources, self.meta = [], [], []
            for d in db.query("SELECT id, source, text, kind, ref_id FROM docs ORDER BY id"):
                self._add_chunks(d["text"], d["source"], dict(kind=d["kind"] or "note", ref_id=d["ref_id"], doc_id=d["id"]))
            self.boost = db.kv_get("rag_boost", {})
        return self

    def _add_chunks(self, text, source, meta):
        for c in chunk(text):
            self.chunks.append(c)
            self.sources.append(source)
            self.meta.append(meta)
        self._dirty = True

    def add(self, text, source, kind="note", ref_id=None):
        """Persist a document and index it. Re-adding the same (kind, ref_id) replaces it.
        Returns (doc_id, passages)."""
        if not text.strip():
            return None, 0
        with self._lock:
            if ref_id is not None and db.one("SELECT 1 x FROM docs WHERE kind=? AND ref_id=?", (kind, ref_id)):
                db.execute("DELETE FROM docs WHERE kind=? AND ref_id=?", (kind, ref_id))
                self.load()
            did = db.execute("INSERT INTO docs(source,text,added,kind,ref_id) VALUES(?,?,?,?,?)",
                             (source, text, time.time(), kind, ref_id))
            n0 = len(self.chunks)
            self._add_chunks(text, source, dict(kind=kind, ref_id=ref_id, doc_id=did))
            return did, len(self.chunks) - n0

    def has(self, kind, ref_id):
        return bool(db.one("SELECT 1 x FROM docs WHERE kind=? AND ref_id=?", (kind, ref_id)))

    K1, B = 1.4, 0.75

    def _build(self):
        docs = [tokens(c) for c in self.chunks]
        self.vocab = {w: i for i, w in enumerate(sorted({w for d in docs for w in d}))}
        tf = np.zeros((len(docs), len(self.vocab)), np.float32)
        for r, d in enumerate(docs):
            for w in d:
                tf[r, self.vocab[w]] += 1
        n = len(docs)
        df = (tf > 0).sum(0)
        self.idf = np.log(1 + (n - df + 0.5) / (df + 0.5))
        dl = tf.sum(1, keepdims=True)
        norm = self.K1 * (1 - self.B + self.B * dl / max(float(dl.mean()), 1e-9))
        self.mat = tf * (self.K1 + 1) / (tf + norm)          # BM25 term weights per chunk
        self._dirty = False

    def ask(self, question, k=3, kind=None):
        with self._lock:
            return self._ask(question, k, kind)

    def _ask(self, question, k, kind):
        if not self.chunks:
            return []
        if self._dirty:
            self._build()
        q = [self.vocab[w] for w in set(tokens(question)) if w in self.vocab]
        if not q:
            return []
        raw = self.mat[:, q] @ self.idf[q]
        top = float(raw.max()) or 1.0
        sims = raw / top                                        # 1.0 = best match for this query
        adj = sims + np.array([self.boost.get(key(c), 0.0) for c in self.chunks]) * (sims > 0)
        if kind:
            adj = np.where([m["kind"] == kind for m in self.meta], adj, -1.0)
        best = adj.argsort()[::-1][:k]
        return [dict(source=self.sources[i], text=self.chunks[i], score=float(sims[i]), bm25=float(raw[i]),
                     boost=float(self.boost.get(key(self.chunks[i]), 0.0)), key=key(self.chunks[i]), **self.meta[i])
                for i in best if raw[i] > 0 and adj[i] > -1.0]

    def feedback(self, k, val):
        self.boost[k] = float(np.clip(self.boost.get(k, 0.0) + 0.08 * val, -0.4, 0.4))
        db.kv_set("rag_boost", self.boost)
        return self.boost[k]
