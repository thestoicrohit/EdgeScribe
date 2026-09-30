"""Flashcards generated from notes and clean transcripts, scheduled with SM-2 spaced repetition.

Two card kinds:
  definition  "Bayes theorem is a rule for updating beliefs."  ->  Q: What is Bayes theorem?
  cloze       a sentence with one of the item's key phrases blanked out.
Transcript sentences only become cards when the recognizer was confident (garbled speech makes
bad cards). Grades: 1 again, 3 hard, 4 good, 5 easy."""
import re
import time

from . import db
from .tasks import norm

DAY = 86400.0
AGAIN_DELAY = 10 * 60                  # a failed card comes back in 10 minutes
NEW_PER_DAY = 20
_BAD_TERM = re.compile(r"^(it|this|that|these|those|there|here|which|what|who|we|you|i|he|she|they|today|"
                       r"everyone|someone|one|so|and|but|now|the deadline|the answer|the point|the idea)\b", re.I)
RX_DEF = re.compile(r"^(?:(?:so|and|now|remember|recall|note)(?: that)?,?\s+)?(?P<term>[A-Za-z][\w'\- ]{2,60}?)\s+"
                    r"(?P<verb>is defined as|is called|refers to|means|is|are)\s+(?P<def>[^.?!]{8,200})[.!]?$", re.I)


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if len(s.split()) >= 5]


def definition_card(sentence):
    m = RX_DEF.match(sentence.strip())
    if not m:
        return None
    term, verb, body = m.group("term").strip(), m.group("verb").lower(), m.group("def").strip()
    if _BAD_TERM.match(term) or len(term.split()) > 6 or len(body.split()) < 3:
        return None
    q = f"What {'are' if verb == 'are' else 'is'} {term[0].lower() + term[1:] if not term[:2].isupper() else term}?"
    return dict(front=q, back=body[0].upper() + body[1:], kind="definition")


def cloze_card(sentence, phrases):
    n = len(sentence.split())
    if not 6 <= n <= 30:
        return None
    for p in phrases:
        m = re.search(r"\b" + re.escape(p) + r"\b", sentence, re.I)
        if m and len(p) >= 4:
            front = sentence[:m.start()] + "_____" + sentence[m.end():]
            return dict(front=front, back=m.group(0), kind="cloze")
    return None


def sm2(card, grade, now=None):
    """-> dict(ease, interval (days), reps, lapses, due). Classic SM-2 with a 10-minute relearn step."""
    now = time.time() if now is None else now
    ease, interval, reps, lapses = card["ease"], card["interval"], card["reps"], card["lapses"]
    if grade < 3:
        reps, lapses, interval = 0, lapses + 1, 0.0
        due = now + AGAIN_DELAY
    else:
        prev = interval
        interval = 1.0 if reps == 0 else 6.0 if reps == 1 else round(interval * ease, 2)
        if grade == 3 and reps >= 2:
            interval = round(max(prev * 1.2, prev + 1), 2)       # hard: grow slowly, not by the full ease
        if grade == 5:
            interval = 4.0 if reps == 0 else round(interval * 1.3, 2)   # easy: a new card skips ahead
        reps += 1
        due = now + interval * DAY
    ease = max(1.3, round(ease + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02), 3))
    return dict(ease=ease, interval=interval, reps=reps, lapses=lapses, due=due)


def preview(card, now=None):
    """Next interval for each button, for the UI ('10m', '1d', '6d', ...)."""
    now = time.time() if now is None else now
    out = {}
    for g in (1, 3, 4, 5):
        secs = sm2(card, g, now)["due"] - now
        out[g] = f"{round(secs / 60)}m" if secs < 3600 else f"{round(secs / 3600)}h" if secs < DAY else f"{round(secs / DAY)}d"
    return out


class Study:
    def add(self, front, back, kind, source_type, source_id):
        n = norm(front + " | " + back)
        if db.one("SELECT id FROM cards WHERE norm=?", (n,)):
            return None
        return db.execute("INSERT INTO cards(front,back,kind,norm,source_type,source_id,created,due) VALUES(?,?,?,?,?,?,?,?)",
                          (front, back, kind, n, source_type, source_id, time.time(), None))

    def generate(self, text, source_type, source_id, phrases=(), limit=6):
        made = 0
        for s in sentences(text):
            card = definition_card(s) or cloze_card(s, phrases)
            if card and self.add(card["front"], card["back"], card["kind"], source_type, source_id):
                made += 1
                if made >= limit:
                    break
        return made

    def next(self, now=None):
        """A due card first, else a new card (up to NEW_PER_DAY a day). None when done for now."""
        now = time.time() if now is None else now
        c = db.one("SELECT * FROM cards WHERE suspended=0 AND due IS NOT NULL AND due<=? ORDER BY due LIMIT 1", (now,))
        if not c and self.new_today(now) < NEW_PER_DAY:
            c = db.one("SELECT * FROM cards WHERE suspended=0 AND due IS NULL ORDER BY id LIMIT 1")
        if c:
            c["preview"] = preview(c, now)
        return c

    def new_today(self, now=None):
        start = (time.time() if now is None else now) - DAY
        return db.one("SELECT COUNT(DISTINCT card_id) n FROM reviews r WHERE ts>=? AND interval_before=0 AND "
                      "NOT EXISTS (SELECT 1 FROM reviews p WHERE p.card_id=r.card_id AND p.ts<r.ts)", (start,))["n"]

    def review(self, card_id, grade, now=None):
        if grade not in (1, 3, 4, 5):
            raise ValueError("grade must be 1 (again), 3 (hard), 4 (good) or 5 (easy)")
        now = time.time() if now is None else now
        c = db.one("SELECT * FROM cards WHERE id=?", (int(card_id),))
        if not c:
            raise KeyError("unknown card")
        s = sm2(c, grade, now)
        db.execute("UPDATE cards SET ease=?, interval=?, reps=?, lapses=?, due=?, last=? WHERE id=?",
                   (s["ease"], s["interval"], s["reps"], s["lapses"], s["due"], now, c["id"]))
        db.execute("INSERT INTO reviews(card_id,ts,grade,interval_before,interval_after) VALUES(?,?,?,?,?)",
                   (c["id"], now, grade, c["interval"], s["interval"]))
        return dict(card_id=c["id"], **s)

    def suspend(self, card_id):
        db.execute("UPDATE cards SET suspended=1 WHERE id=?", (int(card_id),))

    def stats(self, now=None):
        now = time.time() if now is None else now
        r = db.one("SELECT COUNT(*) n, SUM(suspended=0 AND due IS NULL) new, SUM(suspended=0 AND due<=?) due, "
                   "SUM(interval>=21) mature, SUM(suspended=1) suspended FROM cards", (now,))
        rv = db.query("SELECT grade, ts FROM reviews WHERE ts>=? ORDER BY ts", (now - 30 * DAY,))
        recalled = [g >= 3 for g in (x["grade"] for x in rv)]
        per_day = {}
        for x in rv:
            d = time.strftime("%m-%d", time.localtime(x["ts"]))
            per_day.setdefault(d, [0, 0])
            per_day[d][0] += 1
            per_day[d][1] += x["grade"] >= 3
        return dict(total=r["n"] or 0, new=r["new"] or 0, due=r["due"] or 0, mature=r["mature"] or 0,
                    suspended=r["suspended"] or 0, reviews_30d=len(rv),
                    retention=round(sum(recalled) / len(recalled), 3) if recalled else None,
                    per_day=[dict(day=d, reviews=v[0], recalled=v[1]) for d, v in sorted(per_day.items())])
