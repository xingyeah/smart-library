"""
Behaviour-diff checks (see tests/behaviour_diff.py).

The comparison against another git ref loads the search models twice and takes minutes, so it runs on request:
  SNAPSHOT_BASE_REF=HEAD~1 .venv/bin/python -m unittest tests.test_snapshots -v
The diff logic itself is always tested.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import behaviour_diff  # noqa: E402

BASE_REF = os.environ.get("SNAPSHOT_BASE_REF", "")


class DiffDumpsTest(unittest.TestCase):
    def test_reports_every_kind_of_change(self):
        base = {
            "shelf | a": [[1, 90.0], [2, 80.0]],
            "shelf | b": [[1, 90.0], [2, 80.0]],
            "shelf | same": [[5, 70.0]],
            "reader | epub book 1 chapter 0": [12, "aaa"],
            "describe | gone": [[9, 50.0]],
        }
        head = {
            "shelf | a": [[2, 80.0], [1, 90.0]],            # order changed
            "shelf | b": [[1, 90.0001], [2, 80.0]],         # only a score changed
            "shelf | same": [[5, 70.0]],
            "reader | epub book 1 chapter 0": [12, "bbb"],  # sanitized HTML changed
            "describe | new": [[9, 50.0]],
        }
        lines = behaviour_diff.diff_dumps(base, head)
        self.assertEqual(len(lines), 5)
        text = "\n".join(lines)
        self.assertIn("shelf | a: ranking differs from position 1", text)
        self.assertIn("shelf | b: same books and order, scores changed", text)
        self.assertIn("reader | epub book 1 chapter 0: output changed", text)
        self.assertIn("describe | gone: only in base", text)
        self.assertIn("describe | new: only in head", text)
        self.assertEqual(behaviour_diff.diff_dumps(base, dict(base)), [])

    def test_eval_queries_are_loaded_once_each(self):
        queries = behaviour_diff.load_eval_queries()
        self.assertGreater(len(queries), 100)
        self.assertEqual(len(queries), len(set(queries)))


@unittest.skipUnless(BASE_REF, "set SNAPSHOT_BASE_REF=<git ref> to compare this checkout's behaviour with that ref")
class BehaviourMatchesBaseRefTest(unittest.TestCase):
    def test_search_and_reader_output_identical(self):
        differences = behaviour_diff.compare_with_ref(BASE_REF)
        self.assertEqual(differences, [], f"{len(differences)} outputs differ from {BASE_REF}:\n" + "\n".join(differences[:40]))


if __name__ == "__main__":
    unittest.main()
