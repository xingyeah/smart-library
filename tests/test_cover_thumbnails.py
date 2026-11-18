"""Cover thumbnails: large covers shrink and are cached, small ones are served as they are."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from http_api.handlers import reading  # noqa: E402

LARGE_COVER = next((p for p in sorted(reading.COVERS_DIR.glob("*.jpg")) if p.stat().st_size > 200 * 1024), None)


@unittest.skipUnless(shutil.which("sips") and LARGE_COVER, "needs macOS sips and a large cover in data/covers")
class CoverThumbnailTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patch = mock.patch.object(reading, "COVER_THUMB_DIR", self.root / "thumbs")
        patch.start()
        self.addCleanup(patch.stop)

    def test_large_cover_becomes_a_cached_thumbnail(self):
        cover = self.root / "big.jpg"
        shutil.copy(LARGE_COVER, cover)
        thumb = reading._cover_thumbnail(cover, 360)
        self.assertEqual(thumb, self.root / "thumbs" / "360" / "big.jpg")
        self.assertLess(thumb.stat().st_size, cover.stat().st_size / 2)
        with mock.patch.object(reading.subprocess, "run") as run:
            self.assertEqual(reading._cover_thumbnail(cover, 360), thumb)
        run.assert_not_called()

    def test_small_cover_is_served_as_is(self):
        cover = self.root / "small.jpg"
        cover.write_bytes(LARGE_COVER.read_bytes()[:1000])
        self.assertEqual(reading._cover_thumbnail(cover, 360), cover)


if __name__ == "__main__":
    unittest.main()
