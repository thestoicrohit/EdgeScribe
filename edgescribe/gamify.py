"""XP, levels, streaks and achievements: the 'one more round' loop around teaching the models."""
import datetime
import math
import time

from . import db

LEVELS = ["Dormant Neuron", "Spark", "Synapse", "Axon", "Dendrite", "Cortex", "Neural Net",
          "Deep Net", "Singularity"]

ACHIEVEMENTS = [
    ("first_light", "First Light", "Process your first session", lambda s: s["sessions"] >= 1),
    ("teacher", "Good Teacher", "Give 10 labels", lambda s: s["feedback"] >= 10),
    ("mentor", "Mentor", "Give 50 labels", lambda s: s["feedback"] >= 50),
    ("sharp", "Sharp Mind", "Action-item F1 >= 0.90 on held-out phrasing", lambda s: s["clf_f1"] >= 0.9),
    ("calibrated", "Calibrated", "Spiking trigger F1 >= 0.95 on held-out audio", lambda s: s["snn_f1"] >= 0.95),
    ("sleeper", "Light Sleeper", "Heavy models awake < 35% of a 60 s+ session",
     lambda s: s["audio_sec"] >= 60 and s["duty"] is not None and s["duty"] < 0.35),
    ("gym", "Gym Rat", "Run 25 training episodes", lambda s: s["train_eps"] >= 25),
    ("marathon", "Marathon", "Process 10 minutes of audio", lambda s: s["audio_sec"] >= 600),
    ("streak3", "On a Roll", "3-day streak", lambda s: s["streak"] >= 3),
    ("scholar", "Scholar", "Review 25 flashcards", lambda s: s.get("reviews", 0) >= 25),
    ("organized", "Organized", "Finish 5 tasks", lambda s: s.get("tasks_done", 0) >= 5),
    ("connector", "Connector", "A topic that links 3 sessions or notes", lambda s: s.get("topic_links", 0) >= 3),
]


def level_of(xp):
    lv = int(math.sqrt(xp / 30.0))
    lo, hi = 30 * lv * lv, 30 * (lv + 1) ** 2
    return dict(level=lv + 1, name=LEVELS[min(lv, len(LEVELS) - 1)], xp=xp,
                into=xp - lo, span=hi - lo)


def add_xp(n):
    xp = db.kv_get("xp", 0) + int(n)
    db.kv_set("xp", xp)
    touch_streak()
    return level_of(xp)


def touch_streak():
    today = datetime.date.today().isoformat()
    s = db.kv_get("streak", dict(last=None, count=0))
    if s["last"] == today:
        return s["count"]
    y = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    s = dict(last=today, count=(s["count"] + 1) if s["last"] == y else 1)
    db.kv_set("streak", s)
    return s["count"]


def streak():
    s = db.kv_get("streak", dict(last=None, count=0))
    return s["count"] if s["last"] else 0


def state():
    return dict(**level_of(db.kv_get("xp", 0)), streak=streak(),
                unlocked=db.kv_get("ach", {}),
                catalog=[dict(id=a[0], title=a[1], desc=a[2]) for a in ACHIEVEMENTS])


def check(stats):
    """Unlock any newly earned achievements; returns the new ones."""
    stats = dict(stats, streak=streak())
    have = db.kv_get("ach", {})
    new = []
    for aid, title, desc, fn in ACHIEVEMENTS:
        if aid not in have and fn(stats):
            have[aid] = time.time()
            new.append(dict(id=aid, title=title, desc=desc))
    if new:
        db.kv_set("ach", have)
        add_xp(40 * len(new))
    return new
