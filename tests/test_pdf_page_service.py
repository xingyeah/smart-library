"""Server-rendered PDF pages for the phone reader: paragraphs, rendering through PDFKit, cache refresh and eviction."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import database  # noqa: E402
import pdf_page_service as pps  # noqa: E402
from native_bridge import get_native_bridge  # noqa: E402

PAGE_TEXTS = ["Hello first page. Second sentence here.", "Another page with words."]


def write_text_pdf(path: Path, texts) -> None:
    """A minimal valid PDF with one line of Helvetica text per page."""
    count = len(texts)
    objects = ["<< /Type /Catalog /Pages 2 0 R >>",
               f"<< /Type /Pages /Kids [{' '.join(f'{3 + 2 * i} 0 R' for i in range(count))}] /Count {count} >>"]
    font_id = 3 + 2 * count
    for i, text in enumerate(texts):
        stream = f"BT /F1 12 Tf 40 700 Td ({text}) Tj ET"
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 800] /Contents {4 + 2 * i} 0 R "
                       f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = "%PDF-1.4\n", []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(out.encode("latin-1"))


@unittest.skipUnless(get_native_bridge().can_render_pdf_pages, "native library predates PDF page rendering")
class RenderedPagesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.pdf = root / "book.pdf"
        write_text_pdf(self.pdf, PAGE_TEXTS)
        db = root / "t.db"
        database.init_db(db)
        self.cache = root / "pages"
        for patch in (mock.patch.object(pps, "get_conn", lambda: database.get_conn(db)),
                      mock.patch.object(pps, "PAGE_CACHE_DIR", self.cache),
                      mock.patch.object(pps, "PREFETCH_PAGES", 0)):
            patch.start()
            self.addCleanup(patch.stop)
        conn = database.get_conn(db)
        conn.execute("INSERT INTO books (id, title, norm_title, primary_path, archived_path, primary_format, dedup_key) "
                     "VALUES (1, 'Book', 'book', ?, ?, 'pdf', 'k1')", (str(self.pdf), str(self.pdf)))
        conn.commit()
        conn.close()

    def test_layout_image_and_text(self):
        layout = pps.page_layout(1)
        self.assertEqual(layout["page_count"], 2)
        self.assertEqual(layout["sizes"][0], [400, 800])
        image = pps.page_image(1, 2)
        self.assertEqual(image.read_bytes()[:2], b"\xff\xd8")  # JPEG
        self.assertEqual([b["text"] for b in pps.page_blocks(1, 1)], ["Hello first page. Second sentence here."])
        with self.assertRaises(ValueError):
            pps.page_image(1, 3)

    def test_changed_file_drops_cached_pages(self):
        old_image = pps.page_image(1, 1)
        time.sleep(0.01)
        write_text_pdf(self.pdf, PAGE_TEXTS + ["Third page."])
        self.assertEqual(pps.page_layout(1)["page_count"], 3)
        self.assertFalse(old_image.exists())

    def test_truncated_layout_is_rebuilt(self):
        layout_path = self.cache / "1" / pps.LAYOUT_FILE
        pps.page_layout(1)
        layout_path.write_text('{"source": {"pa', encoding="utf-8")  # a crash mid-write
        self.assertEqual(pps.page_layout(1)["page_count"], 2)
        self.assertEqual(pps._read_layout(layout_path)["page_count"], 2)

    def test_eviction_drops_least_recently_read_book_first(self):
        for book_id, age in (("7", 300), ("8", 200), ("9", 0)):
            book_dir = self.cache / book_id
            book_dir.mkdir(parents=True)
            (book_dir / "1.jpg").write_bytes(b"x" * 100)
            os.utime(book_dir, (time.time() - age, time.time() - age))
        with mock.patch.object(pps, "CACHE_LIMIT_BYTES", 250):
            pps._evict_over_limit(keep_book=9)
        self.assertEqual(sorted(p.name for p in self.cache.iterdir()), ["8", "9"])


if __name__ == "__main__":
    unittest.main()
