"""Tasks: action items with parsed due dates, linked to the session/sentence they came from.

parse_due() understands the ways deadlines are said out loud:
  today, tonight, tomorrow, (by|on|this|next) <weekday>, next week, this week, end of the week,
  end of the month, in <n> days/weeks, <Month> <day>[st|nd|rd|th], <day>[th] [of] <Month>,
  plus an optional time: at 4pm, at 4:30 pm, at 16:00, at noon.
"next Friday" means the Friday of next week; a bare "Friday" means the coming Friday (never today)."""
import calendar
import datetime as dt
import re
import time

from . import db

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "eight": 8, "nine": 9, "ten": 10, "couple of": 2, "few": 3}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_WD = "|".join(WEEKDAYS)
_NUM = r"\d+|" + "|".join(sorted(NUMBERS, key=len, reverse=True))

RX_DATE_MD = re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
RX_DATE_DM = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MON})\b", re.I)
RX_WEEKDAY = re.compile(rf"\b(next|this|coming)?\s*({_WD})\b", re.I)
RX_IN = re.compile(rf"\bin\s+({_NUM})\s+(day|days|week|weeks)\b", re.I)
RX_TIME = re.compile(r"\bat\s+(noon|midnight|(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?)", re.I)
RELATIVE = [
    (re.compile(r"\btonight\b", re.I), "tonight"),
    (re.compile(r"\btoday\b|\bend of (?:the )?day\b", re.I), "today"),
    (re.compile(r"\btomorrow\b", re.I), "tomorrow"),
    (re.compile(r"\bnext week\b", re.I), "next week"),
    (re.compile(r"\b(?:this week|end of (?:the )?week)\b", re.I), "this week"),
    (re.compile(r"\bend of (?:the )?month\b", re.I), "end of month"),
]


def _eod(d):
    return dt.datetime(d.year, d.month, d.day, 23, 59)


def _apply_time(when, text):
    m = RX_TIME.search(text)
    if not m or when is None:
        return when, None
    word = m.group(1).lower()
    if word == "noon":
        h, mi = 12, 0
    elif word == "midnight":
        h, mi = 23, 59
    else:
        h, mi = int(m.group(2)), int(m.group(3) or 0)
        ap = (m.group(4) or "").replace(".", "").lower()
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if not ap and 1 <= h <= 7:            # "at 4" in a lecture means the afternoon
            h += 12
        if h > 23 or mi > 59:
            return when, None
    return when.replace(hour=h, minute=mi), m.group(0)


def parse_due(text, now=None):
    """-> (datetime or None, matched phrase or None)."""
    now = now or dt.datetime.now()
    today = now.date()
    t = text or ""
    # 1. explicit calendar dates
    for rx, order in ((RX_DATE_MD, "md"), (RX_DATE_DM, "dm")):
        m = rx.search(t)
        if m:
            mon, day = (m.group(1), m.group(2)) if order == "md" else (m.group(2), m.group(1))
            month, day = MONTHS[mon.lower().rstrip(".")], int(day)
            for year in (today.year, today.year + 1):
                try:
                    d = dt.date(year, month, day)
                except ValueError:
                    return None, None
                if d >= today - dt.timedelta(days=1):
                    when, tp = _apply_time(_eod(d), t)
                    return when, m.group(0) + (f" {tp}" if tp else "")
            return None, None
    # 2. relative words
    for rx, kind in RELATIVE:
        m = rx.search(t)
        if not m:
            continue
        if kind == "tonight":
            when = dt.datetime(today.year, today.month, today.day, 21, 0)
        elif kind == "today":
            when = _eod(today)
        elif kind == "tomorrow":
            when = _eod(today + dt.timedelta(days=1))
        elif kind == "next week":
            when = dt.datetime.combine(today + dt.timedelta(days=7 - today.weekday()), dt.time(9, 0))
        elif kind == "this week":
            fri = today + dt.timedelta(days=(4 - today.weekday()) % 7)
            when = _eod(fri if today.weekday() <= 4 else today + dt.timedelta(days=6 - today.weekday()))
        else:
            when = _eod(dt.date(today.year, today.month, calendar.monthrange(today.year, today.month)[1]))
        when, tp = _apply_time(when, t) if kind != "tonight" else (when, None)
        return when, m.group(0) + (f" {tp}" if tp else "")
    # 3. weekdays
    m = RX_WEEKDAY.search(t)
    if m:
        wd = WEEKDAYS.index(m.group(2).lower())
        ahead = (wd - today.weekday()) % 7 or 7          # a bare weekday is never today
        d = today + dt.timedelta(days=ahead)
        if (m.group(1) or "").lower() == "next" and d.isocalendar()[1] == today.isocalendar()[1]:
            d += dt.timedelta(days=7)
        when, tp = _apply_time(_eod(d), t)
        return when, m.group(0).strip() + (f" {tp}" if tp else "")
    # 4. "in N days/weeks"
    m = RX_IN.search(t)
    if m:
        n = m.group(1).lower()
        n = int(n) if n.isdigit() else NUMBERS[n]
        days = n * (7 if m.group(2).lower().startswith("week") else 1)
        when, tp = _apply_time(_eod(today + dt.timedelta(days=days)), t)
        return when, m.group(0) + (f" {tp}" if tp else "")
    return None, None


URGENT = re.compile(r"\b(must|deadline|due|exam|midterm|final|asap|urgent|required)\b", re.I)


def priority(text, due_ts, now_ts=None):
    """2 = high (due within 48 h, or urgent wording with a date), 1 = normal, 0 = low (no date, no urgency)."""
    now_ts = time.time() if now_ts is None else now_ts
    if due_ts is not None and due_ts - now_ts < 48 * 3600:
        return 2
    if URGENT.search(text or ""):
        return 2 if due_ts is not None else 1
    return 1 if due_ts is not None else 0


def norm(text):
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


def clean(text):
    t = " ".join(str(text).split()).strip()
    return (t[0].upper() + t[1:]) if t else t


class TaskStore:
    STATUSES = ("open", "done", "dismissed")

    def create(self, text, origin="auto", session_id=None, sentence_id=None, now=None):
        """Create a task (or return the existing one with the same wording). -> (task, created?)"""
        text = clean(text)[:300]
        if len(norm(text)) < 3:
            raise ValueError("task text is too short")
        n = norm(text)
        existing = db.one("SELECT * FROM tasks WHERE norm=?", (n,))
        if existing:
            return existing, False
        now_dt = now or dt.datetime.now()
        due, phrase = parse_due(text, now_dt)
        due_ts = due.timestamp() if due else None
        ts = time.time()
        tid = db.execute(
            "INSERT INTO tasks(text,norm,due,due_text,status,priority,created,updated,session_id,sentence_id,origin) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (text, n, due_ts, phrase, "open", priority(text, due_ts, now_dt.timestamp()), ts, ts,
             session_id, sentence_id, origin))
        return self.get(tid), True

    def get(self, tid):
        t = db.one("SELECT * FROM tasks WHERE id=?", (int(tid),))
        if not t:
            raise KeyError("unknown task")
        return t

    def find_by_sentence(self, sentence_id=None, text=None):
        if sentence_id is not None:
            t = db.one("SELECT * FROM tasks WHERE sentence_id=?", (sentence_id,))
            if t:
                return t
        return db.one("SELECT * FROM tasks WHERE norm=?", (norm(text),)) if text else None

    def update(self, tid, status=None, text=None):
        t = self.get(tid)
        if status is not None and status not in self.STATUSES:
            raise ValueError(f"status must be one of {', '.join(self.STATUSES)}")
        new_text = clean(text)[:300] if text is not None else t["text"]
        due, phrase = parse_due(new_text, dt.datetime.fromtimestamp(t["created"])) if text is not None else (None, None)
        due_ts = (due.timestamp() if due else None) if text is not None else t["due"]
        db.execute("UPDATE tasks SET status=?, text=?, norm=?, due=?, due_text=?, priority=?, updated=? WHERE id=?",
                   (status or t["status"], new_text, norm(new_text), due_ts,
                    phrase if text is not None else t["due_text"], priority(new_text, due_ts), time.time(), t["id"]))
        return t, self.get(tid)

    def list(self, status="open", limit=200):
        if status == "all":
            return db.query("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        return db.query("SELECT * FROM tasks WHERE status=? ORDER BY due IS NULL, due, priority DESC, id DESC LIMIT ?",
                        (status, limit))

    def buckets(self, now=None):
        now_dt = now or dt.datetime.now()
        eod = _eod(now_dt.date()).timestamp()
        week = eod + 7 * 86400
        out = dict(overdue=[], today=[], week=[], later=[], undated=[])
        for t in self.list("open", 500):
            d = t["due"]
            key = ("undated" if d is None else "overdue" if d < now_dt.timestamp() else "today" if d <= eod
                   else "week" if d <= week else "later")
            out[key].append(t)
        return out

    def counts(self):
        r = db.one("SELECT SUM(status='open') o, SUM(status='done') d, SUM(status='dismissed') x, COUNT(*) n FROM tasks")
        return dict(open=r["o"] or 0, done=r["d"] or 0, dismissed=r["x"] or 0, total=r["n"] or 0)
