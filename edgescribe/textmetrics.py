"""Word error rate and friends."""
import re

_TOK = re.compile(r"[a-z0-9']+")


def words(text):
    return _TOK.findall((text or "").lower().replace("e-mail", "email"))


def edit_distance(ref, hyp):
    d = list(range(len(hyp) + 1))
    for i in range(1, len(ref) + 1):
        prev, d[0] = d[:], i
        for j in range(1, len(hyp) + 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (ref[i - 1] != hyp[j - 1]))
    return d[len(hyp)]


def wer(ref, hyp):
    """(errors, reference word count). WER = errors / count; can exceed 1 with insertions."""
    r, h = words(ref), words(hyp)
    return edit_distance(r, h), len(r)
