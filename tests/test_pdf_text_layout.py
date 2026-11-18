"""Page blocks for side-by-side translation, on line boxes shaped like the books they were tuned on."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from pdf_text_layout import page_blocks  # noqa: E402


def line(text, x, y, w, h=10.8):
    return {"t": text, "x": x, "y": y, "w": w, "h": h}


class PageBlocksTest(unittest.TestCase):
    def test_numbered_list_items_stay_apart_and_keep_their_markers(self):
        lines = [line("Chapter Contents", 72, 100, 229, 26.8)]
        for n, (y, text) in enumerate([(152, "1.1 Electrical Engineering: An Overview"), (184, "1.2 The International System")], 1):
            lines += [line(f"{n}.", 82.5, y, 11.3, 13.6), line(text, 102, y, 240, 13.6)]
        blocks = page_blocks(lines, 612, 792)
        self.assertEqual([(b["role"], b["text"]) for b in blocks], [
            ("title", "Chapter Contents"),
            ("list", "1. 1.1 Electrical Engineering: An Overview"),
            ("list", "2. 1.2 The International System"),
        ])
        self.assertAlmostEqual(blocks[1]["top"], 152 / 792, places=3)

    def test_two_columns_read_left_then_right_and_footer_dropped(self):
        lines = [
            line("Operational Impacts", 48, 33, 100, 18),
            line("O1. Increase sustainability", 48, 55, 110, 12.2),
            line("We enable increasing sustain-", 48, 69, 168), line("ability when we map it.", 48, 80, 90),
            line("O6. Improve emergency response", 228, 55, 142, 12.2),
            line("We enable improving responses.", 228, 69, 168),
            line("Explore > S02. Design the Research", 258, 621, 123, 8.7), line("79", 387, 621, 9, 8.7),
        ]
        blocks = page_blocks(lines, 432, 648)
        self.assertEqual([b["text"] for b in blocks], [
            "Operational Impacts", "O1. Increase sustainability", "We enable increasing sustainability when we map it.",
            "O6. Improve emergency response", "We enable improving responses.",
        ])
        self.assertEqual([b["role"] for b in blocks], ["title", "heading", "para", "heading", "para"])

    def test_short_closing_line_ends_a_paragraph_and_epigraph_stays_one_block(self):
        lines = [
            line("Meaningful achievement depends on lifting one's sights and", 167, 121, 229, 13.6),
            line("pushing toward the horizon.", 287, 133, 109, 13.6),
            line("We're so glad you picked up this book. You might be here", 48, 187, 346, 13.5),
            line("for a product.", 48, 199, 80, 13.5),
            line("But this isn't a normal book.", 48, 211, 346, 13.5),
        ]
        texts = [b["text"] for b in page_blocks(lines, 432, 648)]
        self.assertEqual(texts[0], "Meaningful achievement depends on lifting one's sights and pushing toward the horizon.")
        self.assertEqual(texts[1:], ["We're so glad you picked up this book. You might be here for a product.",
                                     "But this isn't a normal book."])

    def test_chinese_lines_join_without_spaces(self):
        lines = [line("柏拉图坚持认为只有从理想世界的数学知识来理解现实", 48, 100, 340, 13),
                 line("世界的实在性和可知性。", 48, 114, 150, 13)]
        self.assertEqual(page_blocks(lines, 432, 648)[0]["text"], "柏拉图坚持认为只有从理想世界的数学知识来理解现实世界的实在性和可知性。")


if __name__ == "__main__":
    unittest.main()
