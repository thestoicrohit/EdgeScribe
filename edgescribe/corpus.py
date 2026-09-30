"""Synthetic lecture-sentence generators with ground-truth 'is this an action item?' labels.

Train and test use DIFFERENT phrasing templates, so held-out accuracy measures generalisation
to unseen wording rather than memorisation. This is a curriculum for the demo; real learning
comes from the user's feedback on real transcripts."""
import random

VP = ["read chapter {n}", "submit the lab report", "email the professor", "review the slides",
      "finish the problem set", "install the toolkit", "prepare the demo", "update the repository",
      "schedule a meeting", "send the draft", "complete the quiz", "book the lab room",
      "revise your proposal", "upload the dataset", "bring your laptop", "form a team"]
DAYS = ["Friday", "Monday", "tomorrow", "next week", "Wednesday", "the end of the month",
        "tonight", "Thursday"]
EVENTS = ["exam", "midterm", "demo", "review session", "deadline", "lab"]
TOPICS = ["gradient descent", "the Fourier transform", "binary search trees", "supply and demand",
          "photosynthesis", "the TCP handshake", "neural networks", "Bayes theorem",
          "the French Revolution", "hash tables", "entropy", "virtual memory", "cell division",
          "recursion", "the Doppler effect"]
DESCS = ["a way to minimize a loss function", "the process of converting signals between domains",
         "a structure that keeps keys ordered", "how prices settle in a market",
         "the way plants make energy from light", "a three step connection setup",
         "models built from layers of simple units", "a rule for updating beliefs with evidence",
         "a period of major political change", "a table that maps keys to buckets",
         "a measure of disorder", "a trick that lets programs use more memory than exists",
         "how a cell copies itself", "a function that calls itself",
         "a shift in frequency caused by motion"]
NOUNS = ["algorithm", "theorem", "protocol", "assignment", "project", "report", "framework"]
PEOPLE = ["Shannon", "Turing", "Newton", "Ada Lovelace", "Fourier", "Bayes"]

ACTION_TRAIN = ["You must {vp} by {day}.", "Please {vp} before the {event}.", "Remember to {vp}.",
                "We need to {vp}.", "Don't forget to {vp} by {day}.", "Assignment {n} is due {day}.",
                "Someone should {vp}.", "Everyone will {vp} before {day}."]
ACTION_TEST = ["Make sure you {vp} before {day}.", "I'd like everyone to {vp} by {day}.",
               "The deadline for the {noun} is {day}.", "It's your job to {vp}.",
               "Can you {vp} this week?", "Your homework is to {vp}."]
FACT_TRAIN = ["{topic} is {desc}.", "Today we will talk about {topic}.",
              "The {noun} is defined as {desc}.", "In {year}, {person} introduced {topic}.",
              "This works because {topic} is {desc}.", "As you can see on the slide, {topic} matters.",
              "{topic} shows up in many real systems."]
FACT_TEST = ["Let me explain how {topic} relates to {topic2}.", "One example of {topic} is {desc}.",
             "Historically, {topic} came from {desc}.", "Notice that {topic} is {desc}.",
             "Think of {topic} as {desc}.", "There are two views of {topic}."]


def _fill(tpl, rng):
    topics = rng.sample(TOPICS, 2)
    s = tpl.format(vp=rng.choice(VP).format(n=rng.randint(1, 9)), day=rng.choice(DAYS),
                   event=rng.choice(EVENTS), topic=topics[0], topic2=topics[1],
                   desc=rng.choice(DESCS), noun=rng.choice(NOUNS), person=rng.choice(PEOPLE),
                   year=rng.randint(1700, 2015), n=rng.randint(1, 9))
    return s[0].upper() + s[1:]


def sample(rng, split="train", p_action=0.5):
    """One (sentence, label) pair. label 1 = action item."""
    act = rng.random() < p_action
    pool = (ACTION_TRAIN if act else FACT_TRAIN) if split == "train" else (
        ACTION_TEST if act else FACT_TEST)
    return _fill(rng.choice(pool), rng), int(act)


def batch(n, split="train", seed=None):
    rng = random.Random(seed)
    return [sample(rng, split) for _ in range(n)]


def lecture(n_sentences, seed=None):
    """A mixed script for simulated lectures (both phrasings, ~35% action items)."""
    rng = random.Random(seed)
    return [sample(rng, rng.choice(["train", "test"]), p_action=0.35) for _ in range(n_sentences)]
