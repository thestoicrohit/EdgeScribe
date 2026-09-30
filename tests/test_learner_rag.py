import unittest

from edgescribe import corpus, db, learner, rag


class LearnerTests(unittest.TestCase):
    def setUp(self):
        db.init(":memory:")

    def test_starts_ignorant_then_learns_unseen_phrasing(self):
        c = learner.ActionClassifier()
        self.assertAlmostEqual(c.predict("Please submit the report by Friday."), 0.5)
        base = c.evaluate()["acc"]
        for e in range(8):
            c.train_curriculum(32, seed=e)
        self.assertGreater(c.evaluate()["acc"], max(0.85, base + 0.3))

    def test_train_and_test_templates_are_disjoint(self):
        self.assertFalse(set(corpus.ACTION_TRAIN) & set(corpus.ACTION_TEST))
        self.assertFalse(set(corpus.FACT_TRAIN) & set(corpus.FACT_TEST))

    def test_user_label_moves_prediction(self):
        c = learner.ActionClassifier()
        s = "Bring snacks on Tuesday."
        p0 = c.predict(s)
        for _ in range(5):
            c.user_update(s, 1)
        self.assertGreater(c.predict(s), p0)

    def test_labels_on_garbled_text_do_not_wreck_the_model(self):
        """Regression: a run of user labels on garbled transcripts used to drop F1 ~0.95 -> ~0.7."""
        import random
        c = learner.ActionClassifier()
        for e in range(20):
            c.train_curriculum(32, seed=e)
        before = c.evaluate()["f1"]
        rng = random.Random(0)
        junk = ["Caldwell said Leslie.", "What started out virtual memory of.", "Work has.",
                "The underside for French revolutionize.", "Brian Tennyson I'll still camera has written a lot."]
        for _ in range(25):                         # exactly what core.feedback_sentence does
            c.user_update(rng.choice(junk), 0)
        self.assertGreater(c.evaluate()["f1"], before - 0.08)
        # ...and it still learns a phrasing the curriculum never used, in both directions
        pos, neg = "Bring snacks for the study group on Tuesday.", "Our mascot is a very old tortoise."
        p_pos, p_neg = c.predict(pos), c.predict(neg)
        for _ in range(3):
            c.user_update(pos, 1)
            c.user_update(neg, 0)
        self.assertGreater(c.predict(pos), p_pos + 0.1)
        self.assertLess(c.predict(neg), p_neg)

    def test_persistence_roundtrip(self):
        c = learner.ActionClassifier()
        c.train_curriculum(64, seed=1)
        before = c.predict("We need to email the professor.")
        c2 = learner.ActionClassifier().load()
        self.assertAlmostEqual(c2.predict("We need to email the professor."), before, places=4)


class RagTests(unittest.TestCase):
    def setUp(self):
        db.init(":memory:")
        self.ix = rag.LocalIndex()
        self.ix.add("Gradient descent minimizes a loss function. " * 5 + "Photosynthesis makes energy from light.", "bio-cs")
        self.ix.add("The final exam is on December twelfth and covers chapters one to seven.", "syllabus")

    def test_finds_relevant_chunk(self):
        top = self.ix.ask("when is the final exam")[0]
        self.assertEqual(top["source"], "syllabus")

    def test_feedback_reorders_and_persists(self):
        hits = self.ix.ask("energy light exam")
        self.assertGreaterEqual(len(hits), 2)
        last = hits[-1]
        for _ in range(6):
            self.ix.feedback(last["key"], +1)
        self.assertEqual(self.ix.ask("energy light exam")[0]["key"], last["key"])
        self.assertEqual(rag.LocalIndex().load().boost, self.ix.boost)

    def test_no_match_returns_empty(self):
        self.assertEqual(self.ix.ask("zzzzqqq"), [])


if __name__ == "__main__":
    unittest.main()
