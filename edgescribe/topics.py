"""Key-phrase topics that link sessions, notes, tasks and flashcards to each other.

Candidates are 1-3 word runs of content words inside a sentence (stop words and filler break a
run). Each candidate is scored tf * idf against every other item, so a phrase that shows up in
one lecture but not everywhere ranks highest. Items that share key phrases are 'related'."""
import math
import re

from . import db
from .rag import STOP, stem

FILLER = set("""so now okay ok well just also really very actually basically like let lets let's going gonna want
think see look say said talk talking today tonight yesterday tomorrow morning afternoon evening thing things lot lots way ways something anything
everyone everybody someone somebody one two three four five first second next last new good great sure right
make made get got use used many much more most few some any every each other another same different slide
slides example examples question questions notice view views part parts time times week weeks day days class
please remember need must should will would could can may might don't forget""".split())
# common verbs / function words that break a phrase (no POS tagger: a curated list does most of the work)
VERBS = set("""before after during until since while about into onto over under between through across around
your our their its his her my mine yours it's you're we're they're that's there's here's i'm isn't aren't
show shows showed come came comes keep keeps kept call calls called copy copies map maps mapped let lets
introduce introduced introduces explain explained explains relate relates related work works worked matter
matters mattered define defined defines notice noticed bring brings prepare prepared finish finished submit
email review reviewed read send sent complete completed install installed upload updated update schedule book
revise form grab print meet takes take took give gives gave make makes find found try tried start started end
ended happen happens happened become becomes became mean means meant refer refers known using based because than then though although whether either neither both only even still yet
monday tuesday wednesday thursday friday saturday sunday january february march april june july
august september october november december itself himself herself themselves ourselves yourself
computes compute controls control built build builds runs run makes uses gives sets settle settles
off out down away back ever never always often via per within without upon among against toward towards despite unless whereas""".split())
STOPWORDS = STOP | FILLER | VERBS
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def _key(words):
    return " ".join(stem(w.lower()) for w in words)


def _content(w):
    lw = w.lower()
    return len(lw) > 2 and lw not in STOPWORDS and not lw.endswith("ly") and "'" not in lw


def candidates(text):
    """{key: (surface phrase, count, proper)} for 1-3 word runs of content words.
    proper = a word was capitalised mid-sentence (a named concept: 'Bayes theorem', 'TCP')."""
    out = {}
    for sent in re.split(r"(?<=[.!?])\s+|\n", text or ""):
        run = []
        toks = _WORD.findall(sent)
        for pos, tok in enumerate(toks + [None]):
            if tok and _content(tok):
                run.append((tok, pos > 0 and tok[0].isupper()))
                continue
            for n in (1, 2, 3):
                for i in range(len(run) - n + 1):
                    words = [w for w, _ in run[i:i + n]]
                    if n > 1 and len(words[-1]) > 4 and words[-1].lower().endswith("ed"):
                        continue                  # "keys ordered", "frequency caused": verb tails
                    proper = any(p for _, p in run[i:i + n])
                    k = _key(words)
                    surf, c, pr = out.get(k, (" ".join(words), 0, False))
                    out[k] = (surf, c + 1, pr or proper)
            run = []
    return out


class Topics:
    def _df(self):
        rows = db.query("SELECT key, COUNT(*) n FROM topics GROUP BY key")
        n_items = (db.one("SELECT COUNT(*) n FROM (SELECT DISTINCT item_type, item_id FROM topics)") or {"n": 0})["n"]
        return {r["key"]: r["n"] for r in rows}, n_items

    def extract(self, item_type, item_id, text, k=8, corroborate=False):
        """Score and store the top-k key phrases for one item. Returns [(phrase, score)].
        corroborate=True (for speech-recognizer output): keep only phrases said twice here or also
        found in another item - a misheard name appears once and nowhere else."""
        cand = candidates(text)
        df, n = self._df()
        if corroborate:
            mine = {r["key"] for r in db.query("SELECT key FROM topics WHERE item_type=? AND item_id=?",
                                                (item_type, item_id))}
            cand = {k_: v for k_, v in cand.items() if v[1] >= 2 or df.get(k_, 0) - (k_ in mine) > 0}
        if not cand:
            db.execute("DELETE FROM topics WHERE item_type=? AND item_id=?", (item_type, item_id))
            return []
        scored = []
        for key, (surf, tf, proper) in cand.items():
            words = len(key.split())
            idf = math.log((n + 2) / (df.get(key, 0) + 1)) + 1
            shape = {1: 1.0, 2: 1.45, 3: 1.15}[words]          # two-word concepts are the sweet spot
            scored.append((key, surf, (1 + math.log(tf)) * idf * shape * (1.3 if proper else 1.0)))
        # A longer phrase that always co-occurs here with a sub-phrase that other items also use
        # ('TCP handshake opens' vs 'TCP handshake') gives way to the shared one: that's what links items.
        by_key = {k: [k, s, sc] for k, s, sc in scored}
        for key in sorted(by_key, key=lambda k: -len(k.split())):
            words = key.split()
            if len(words) < 2 or key not in by_key:
                continue
            subs = [" ".join(words[i:i + n]) for n in range(len(words) - 1, 0, -1) for i in range(len(words) - n + 1)]
            for sub in subs:
                if sub in by_key and cand[sub][1] == cand[key][1] and df.get(sub, 0) > df.get(key, 0) \
                        and (len(sub.split()) > 1 or cand[sub][2]):
                    by_key[sub][2] = max(by_key[sub][2], by_key[key][2])
                    del by_key[key]
                    break
        scored = sorted(by_key.values(), key=lambda x: -x[2])
        chosen, used = [], set()
        for key, surf, sc in scored:                     # skip sub-phrases of an already chosen phrase
            if any(key in u or u in key for u in used):
                continue
            chosen.append((key, surf, sc))
            used.add(key)
            if len(chosen) >= k:
                break
        db.execute("DELETE FROM topics WHERE item_type=? AND item_id=?", (item_type, item_id))
        for key, surf, sc in chosen:
            db.execute("INSERT OR REPLACE INTO topics(item_type,item_id,key,phrase,score) VALUES(?,?,?,?,?)",
                       (item_type, item_id, key, surf.lower(), round(sc, 4)))
        return [(s, round(sc, 3)) for _, s, sc in chosen]

    def of(self, item_type, item_id):
        return db.query("SELECT phrase, score FROM topics WHERE item_type=? AND item_id=? ORDER BY score DESC",
                        (item_type, item_id))

    def top(self, limit=30):
        """Phrases across all items; shared phrases (the real 'topics') first."""
        return db.query("SELECT key, MIN(phrase) phrase, COUNT(*) items, ROUND(SUM(score),3) weight FROM topics "
                        "GROUP BY key ORDER BY items DESC, weight DESC LIMIT ?", (limit,))

    def items(self, key):
        return db.query("SELECT item_type, item_id, phrase, score FROM topics WHERE key=? ORDER BY score DESC", (key,))

    def related(self, item_type, item_id, limit=6):
        """Other items whose key phrases overlap this item's (word-level, so 'binary search trees'
        relates to 'search trees'), weighted by both phrases' scores."""
        mine = self.of_keys(item_type, item_id)
        if not mine:
            return []
        rows = db.query("SELECT item_type, item_id, key, phrase, score FROM topics WHERE NOT (item_type=? AND item_id=?)",
                        (item_type, item_id))
        acc = {}
        for r in rows:
            words = set(r["key"].split())
            for key, (phrase, sc) in mine.items():
                overlap = len(words & set(key.split())) / len(words | set(key.split()))
                if overlap >= 0.5:
                    a = acc.setdefault((r["item_type"], r["item_id"]), [0.0, set()])
                    a[0] += overlap * sc * r["score"]
                    a[1].add(phrase if len(phrase) >= len(r["phrase"]) else r["phrase"])   # the fuller concept
        out = [dict(item_type=t, item_id=i, strength=round(s, 3), shared=", ".join(sorted(sh)))
               for (t, i), (s, sh) in acc.items()]
        return sorted(out, key=lambda r: -r["strength"])[:limit]

    def of_keys(self, item_type, item_id):
        return {r["key"]: (r["phrase"], r["score"]) for r in
                db.query("SELECT key, phrase, score FROM topics WHERE item_type=? AND item_id=?", (item_type, item_id))}

    def key_for(self, phrase):
        return _key(_WORD.findall(phrase))
