import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from native_bridge import FallbackNativeBridge, create_native_bridge


class NativeBridgeFallbackTest(unittest.TestCase):
    def test_missing_dylib_returns_fallback_bridge(self):
        bridge = create_native_bridge(Path("/nonexistent/libmacbookai.dylib"))
        self.assertIsInstance(bridge, FallbackNativeBridge)
        self.assertFalse(bridge.is_native)
        self.assertFalse(bridge.can_render_pdf_pages)
        self.assertEqual(bridge.to_pinyin("测试"), "")
        self.assertEqual(bridge.expand_query("test"), {"tokens": [], "neighbors": {}})
        self.assertEqual(bridge.parse_pdf("/tmp/missing.pdf"), {"ok": False})
        self.assertEqual(bridge.get_pdf_outline("/tmp/missing.pdf"), {"ok": False})
        self.assertEqual(bridge.pdf_page_sizes("/tmp/missing.pdf"), [])
        self.assertFalse(bridge.render_pdf_page("/tmp/a.pdf", 0, 100, 0.8, "/tmp/a.jpg"))
        self.assertEqual(bridge.pdf_page_text("/tmp/missing.pdf", 0), "")
        self.assertEqual(bridge.pdf_page_lines("/tmp/missing.pdf", 0), {"ok": False})

    def test_fallback_batch_cosine_scores_uses_numpy(self):
        bridge = FallbackNativeBridge()
        mat_bytes = struct.pack("4f", 1.0, 0.0, 0.5, 0.5)
        scores = bridge.batch_cosine_scores([1.0, 0.0], mat_bytes, 2)
        self.assertEqual(len(scores), 2)
        self.assertAlmostEqual(scores[0], 1.0, places=5)
        self.assertAlmostEqual(scores[1], 0.5, places=5)


if __name__ == "__main__":
    unittest.main()
