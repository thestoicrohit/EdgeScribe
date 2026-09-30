"""Online learners that improve from data and feedback (numpy only).

ActionClassifier: hashed bag-of-words logistic regression, two layers of weights:
  * base w    trained by SGD on a synthetic curriculum (starts from zero knowledge);
  * personal u trained only by the user's labels, with L2 shrinkage (lambda per label).
Prediction uses w + u. Keeping the user's labels in their own shrunk layer bounds how far a run of
labels on odd text (garbled transcripts) can drag the model, while still learning new phrasings in
both directions. Measured (6 trials, 25 'not action' labels on garbled ASR text, then 15 novel labels):
  update base directly (+replay)  F1 0.945 -> 0.664 after junk, novel non-actions got MORE action-like
  personal layer (lam .05, lr 1)  F1 0.945 -> 0.907 after junk, novel actions +0.28, non-actions -0.07
"""
import math
import re
import zlib

import numpy as np

from . import corpus, db

D = 4096
MODALS = re.compile(r"\b(must|should|need to|needs to|have to|will|remember|don't forget|make sure|"
                    r"please|due|deadline|homework|by \w+day|tomorrow|tonight)\b", re.I)


def featurize(text):
    """Sparse hashed features -> ({idx: value}, {idx: readable name})."""
    toks = re.findall(r"[a-z0-9']+", text.lower())
    names = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
    if MODALS.search(text):
        names.append("__modal")
    if text.strip().endswith("?"):
        names.append("__question")
    names.append("__len%d" % min(len(toks) // 5, 4))
    f, n = {}, {}
    for name in names:
        i = zlib.crc32(name.encode()) % D
        f[i] = f.get(i, 0.0) + 1.0
        n.setdefault(i, name)
    norm = math.sqrt(sum(v * v for v in f.values())) or 1.0
    return {i: v / norm for i, v in f.items()}, n


def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-max(min(z, 30), -30)))


class ActionClassifier:
    KEY = "action_clf"

    def __init__(self):
        self.w = np.zeros(D)
        self.b = 0.0
        self.t = 0                 # SGD steps taken
        self.names = {}            # idx -> feature name (for interpretability)
        self.fb = 0                # user feedback labels absorbed
        self.u = np.zeros(D)       # personal layer (user labels only)
        self._test = corpus.batch(300, "test", seed=1234)   # fixed held-out set

    # ---- persistence -------------------------------------------------------------------
    def save(self):
        sparse = lambda a: {int(i): round(float(a[i]), 5) for i in np.nonzero(np.abs(a) > 1e-6)[0]}
        db.kv_set(self.KEY, dict(w=sparse(self.w), u=sparse(self.u), b=self.b, t=self.t, fb=self.fb,
                                 names={str(k): v for k, v in self.names.items()}))

    def load(self):
        s = db.kv_get(self.KEY)
        if s:
            self.w, self.u = np.zeros(D), np.zeros(D)
            for i, v in s["w"].items():
                self.w[int(i)] = v
            for i, v in s.get("u", {}).items():
                self.u[int(i)] = v
            self.b, self.t, self.fb = s["b"], s["t"], s.get("fb", 0)
            self.names = {int(k): v for k, v in s.get("names", {}).items()}
        return self

    # ---- inference ---------------------------------------------------------------------
    def predict(self, text):
        f, _ = featurize(text)
        return _sigmoid(self.b + sum((self.w[i] + self.u[i]) * v for i, v in f.items()))

    # ---- learning ----------------------------------------------------------------------
    def update(self, text, label, weight=1.0):
        """One curriculum SGD step on the base layer (independent of the personal layer)."""
        f, n = featurize(text)
        self.names.update({i: nm for i, nm in n.items() if i not in self.names})
        p = _sigmoid(self.b + sum(self.w[i] * v for i, v in f.items()))
        lr = 0.8 * weight / math.sqrt(1 + self.t / 100)
        g = p - label
        for i, v in f.items():
            self.w[i] -= lr * (g * v + 1e-4 * self.w[i])
        self.b -= lr * g
        self.t += 1
        return p

    USER_LAMBDA, USER_LR = 0.05, 1.0

    def user_update(self, text, label):
        """Learn from one user label: shrink the personal layer, then one step on it."""
        f, n = featurize(text)
        self.names.update({i: nm for i, nm in n.items() if i not in self.names})
        g = self.predict(text) - label
        self.u *= 1 - self.USER_LAMBDA
        for i, v in f.items():
            self.u[i] -= self.USER_LR * g * v
        self.fb += 1

    def personal_size(self):
        return int(np.count_nonzero(np.abs(self.u) > 1e-3)), float(np.linalg.norm(self.u))

    def evaluate(self, data=None):
        data = data or self._test
        tp = fp = fn = tn = 0
        loss = 0.0
        for text, y in data:
            p = self.predict(text)
            loss -= math.log(max(p if y else 1 - p, 1e-9))
            pred = p >= 0.5
            tp += pred and y
            fp += pred and not y
            fn += (not pred) and y
            tn += (not pred) and not y
        n = len(data)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        return dict(acc=(tp + tn) / n, loss=loss / n, f1=f1, prec=prec, rec=rec,
                    confusion=dict(tp=tp, fp=fp, fn=fn, tn=tn))

    def train_curriculum(self, n=64, seed=None):
        """One 'episode': n fresh synthetic examples, then a held-out evaluation, logged."""
        for text, y in corpus.batch(n, "train", seed=seed):
            self.update(text, y)
        m = self.evaluate()
        self.log(m, note="curriculum")
        self.save()
        return m

    def log(self, m, note=""):
        db.execute("INSERT INTO learn_log(ts,learner,step,acc,loss,f1,prec,rec,note) VALUES(?,?,?,?,?,?,?,?,?)",
                   (_now(), "action", self.t, m["acc"], m["loss"], m["f1"], m["prec"], m["rec"], note))

    def top_features(self, k=8):
        w = self.w + self.u
        order = np.argsort(w)
        pos = [(self.names.get(int(i), "?"), float(w[i])) for i in order[::-1][:k] if w[i] > 0]
        neg = [(self.names.get(int(i), "?"), float(w[i])) for i in order[:k] if w[i] < 0]
        return pos, neg

    def n_params(self):
        return int(np.count_nonzero(self.w)) + 1


def _now():
    import time
    return time.time()
