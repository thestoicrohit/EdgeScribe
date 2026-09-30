import unittest

import numpy as np

from edgescribe import sim, snn
from edgescribe.config import SR


class SNNTests(unittest.TestCase):
    def test_silence_does_not_wake(self):
        x = (0.005 * np.random.default_rng(0).standard_normal(SR * 15)).astype(np.float32)
        ev, _, _ = snn.run_offline(x)
        self.assertEqual(ev, [])

    def test_speech_wakes_inside_span_only(self):
        ep = sim.episode(3, hard=0.0, distractors=False)
        ev, _, _ = snn.run_offline(ep["audio"], dict(sustain=8))
        spans = [(u["t0"], u["t1"]) for u in ep["utts"]]
        verdicts, missed, prec, rec, _ = snn.score_events([e["t"] for e in ev], spans)
        self.assertGreater(len(ev), 0)
        self.assertEqual(missed, [])
        self.assertGreaterEqual(prec, 0.9)

    def test_streaming_equals_offline(self):
        ep = sim.episode(4, dur=30, hard=0.3)
        x = ep["audio"]
        whole = snn.SNN().feed(x)["events"]
        net = snn.SNN()
        chunked = [e for i in range(0, len(x), 3777) for e in net.feed(x[i:i + 3777])["events"]]
        self.assertEqual([e["frame"] for e in whole], [e["frame"] for e in chunked])

    def test_feedback_moves_parameters_the_right_way(self):
        net = snn.SNN()
        w0, thr0 = net.w.copy(), net.thresh
        net.reward([1.0] + [0.0] * 15, -1.0)
        self.assertLess(net.w[0], w0[0])
        net.nudge_thresh(0.1)
        self.assertGreater(net.thresh, thr0)
        net.w[:] = 100
        net.reward([1.0] * 16, +1.0)
        self.assertTrue((net.w <= 3.0).all())        # bounded

    def test_scoring(self):
        v, missed, p, r, f = snn.score_events([1.0, 9.0], [(0.5, 2.0), (5.0, 6.0)])
        self.assertEqual(v, [True, False])
        self.assertEqual(missed, [1])
        self.assertAlmostEqual(p, 0.5)
        self.assertAlmostEqual(r, 0.5)


if __name__ == "__main__":
    unittest.main()
