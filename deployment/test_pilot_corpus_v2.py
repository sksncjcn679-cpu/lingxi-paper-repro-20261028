"""Regression checks for the evidence contradiction found during the pilot."""
import copy
import unittest

from pilot_corpus import corpus as corpus_v1
from pilot_corpus_v2 import audit, corpus


class EvidenceConsistencyTests(unittest.TestCase):
    def test_detects_all_four_original_conflicts(self):
        self.assertEqual({p["task_id"] for p in audit(corpus_v1())},
                         {f"pilot-migration-{i:02d}" for i in (2, 3, 4, 6)})

    def test_corrected_corpus_preserves_contract_and_unrelated_observations(self):
        original = corpus_v1()
        saved = copy.deepcopy(original)
        corrected = corpus()
        self.assertEqual(len(corrected), 24)
        self.assertEqual(audit(corrected), [])
        for old, new in zip(original, corrected):
            self.assertEqual(old["expected"], new["expected"])
            self.assertEqual(old["observations"]["symptom"], new["observations"]["symptom"])
            self.assertEqual(set(old["observations"]["checks"]), set(new["observations"]["checks"]))
            if old["domain"] != "migration":
                self.assertEqual(old["observations"], new["observations"])
            else:
                self.assertEqual(new["observations"]["checks"]["keep_binary"], new["expected"]["probe"])
                self.assertEqual(new["observations"]["checks"]["rebuild_binary"], new["expected"]["probe"])
        self.assertEqual(original, saved)
        self.assertEqual(corpus_v1(), saved)


if __name__ == "__main__":
    unittest.main()
