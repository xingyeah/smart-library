"""Per-file embedded title/author/ISBN: stored on import and backfilled for older rows. Temp DB only."""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import archiver  # noqa: E402
import backfill_file_metadata as backfill  # noqa: E402
import database  # noqa: E402

CONTAINER_XML = (
    '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
)
OPF_XML = (
    '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Inside Palantir</dc:title>'
    '<dc:creator>J. Hayden Elsen</dc:creator><dc:identifier>9781966302186</dc:identifier></metadata>'
    '<manifest/><spine/></package>'
)


def write_tiny_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("content.opf", OPF_XML)


class EmbeddedMetadataTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db_path = self.root / "t.db"
        database.init_db(self.db_path)
        self.conn = database.get_conn(self.db_path)
        self.addCleanup(self.conn.close)
        self.book_id = self.conn.execute(
            "INSERT INTO books (dedup_key, title, norm_title, primary_format) VALUES ('k', 't', 't', 'epub')"
        ).lastrowid
        self.conn.commit()

    def test_import_stores_embedded_fields(self):
        item = {"file_path": "/x/a.epub", "format": "EPUB", "file_size": 1, "file_hash": "h", "source_name": "s",
                "embedded_title": "Inside Palantir", "embedded_author": "Elsen", "isbn": "9781966302186"}
        archiver._record_book_file(self.conn, self.book_id, item, "", 1)
        row = self.conn.execute("SELECT embedded_title, embedded_author, isbn FROM book_files").fetchone()
        self.assertEqual(tuple(row), ("Inside Palantir", "Elsen", "9781966302186"))

    def test_backfill_reads_the_library_link_when_the_source_is_gone(self):
        epub = self.root / "link.epub"
        write_tiny_epub(epub)
        for i, archived in enumerate((str(epub), "")):
            self.conn.execute(
                "INSERT INTO book_files (book_id, original_path, archived_path, format, is_primary) VALUES (?, ?, ?, 'epub', ?)",
                (self.book_id, f"/gone/{i}.epub", archived, int(i == 0)))
        self.conn.commit()
        with mock.patch.object(backfill, "get_conn", lambda: database.get_conn(self.db_path)), \
                mock.patch.object(backfill, "init_db", lambda: None):
            backfill.run(0)
        rows = self.conn.execute("SELECT embedded_title, embedded_author, isbn FROM book_files ORDER BY id").fetchall()
        self.assertEqual(rows[0]["embedded_title"], "Inside Palantir")
        self.assertEqual(rows[0]["embedded_author"], "J. Hayden Elsen")
        self.assertEqual(rows[1]["embedded_title"], backfill.NOTHING_FOUND)  # missing file: done, not retried


if __name__ == "__main__":
    unittest.main()
