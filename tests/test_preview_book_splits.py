"""Split preview: split-off files are grouped into the new books they would form."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from preview_book_splits import preview_book  # noqa: E402
from same_work import DIFFERENT, UNCERTAIN  # noqa: E402
from test_same_work import row  # noqa: E402


class PreviewTest(unittest.TestCase):
    def test_strays_of_the_same_work_become_one_new_book(self):
        own = row("Depression A Very Short Introduction (Jan Scott).epub")
        witt_pdf = row("Wittgenstein A Very Short Introduction (A. C. Grayling).pdf")
        witt_epub = row("Wittgenstein A Very Short Introduction (Grayling, Anthony C).epub")
        atheism = row("Atheism A Very Short Introduction (Julian Baggini) (牛津通识读本：无神论).pdf")
        preview = preview_book([own, witt_pdf, witt_epub, atheism], "Depression A Very Short Introduction", {})
        self.assertEqual(preview["anchor"], own["id"])
        self.assertEqual(preview["new_books"], [[witt_pdf["id"], witt_epub["id"]], [atheism["id"]]])

    def test_user_decisions_beat_the_rules(self):
        own = row("American Political Parties and Elections A Very Short Introduction (L. Sandy Maisel).pdf")
        stray = row("第二次世界大战.epub")
        rules_only = preview_book([own, stray], "American Political Parties and Elections", {})
        self.assertEqual(rules_only[UNCERTAIN], [stray["id"]])
        decided = preview_book([own, stray], "American Political Parties and Elections", {stray["id"]: DIFFERENT})
        self.assertEqual((decided[UNCERTAIN], decided["new_books"]), ([], [[stray["id"]]]))


if __name__ == "__main__":
    unittest.main()
