import io
import sys
import unittest
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from http_api.handlers.reading import handle_get_asset, handle_get_vendor
from http_api.routes import build_default_router


class _DummyHandler:
    def __init__(self, path: str, headers: Optional[Dict[str, str]] = None) -> None:
        self.path = path
        self.headers = headers or {}
        self.status_code = None
        self.resp_headers = {}
        self.wfile = io.BytesIO()
        self.error_code = None
        self.error_msg = None

    def send_response(self, code: int) -> None:
        self.status_code = code

    def send_header(self, key: str, value: str) -> None:
        self.resp_headers[key] = value

    def end_headers(self) -> None:
        pass

    def send_error(self, code: int, message: Optional[str] = None) -> None:
        self.error_code = code
        self.error_msg = message


class FrontendAssetsRouteTest(unittest.TestCase):
    def test_serves_css_and_js_with_etag_and_no_cache(self):
        h_css = _DummyHandler("/assets/css/main.css")
        handle_get_asset(h_css, {}, {})
        self.assertEqual(h_css.status_code, 200)
        self.assertEqual(h_css.resp_headers.get("Content-Type"), "text/css; charset=utf-8")
        self.assertEqual(h_css.resp_headers.get("Cache-Control"), "no-cache")
        etag = h_css.resp_headers.get("ETag")
        self.assertTrue(etag)
        self.assertGreater(len(h_css.wfile.getvalue()), 100)

        h_js = _DummyHandler("/assets/js/main.js")
        handle_get_asset(h_js, {}, {})
        self.assertEqual(h_js.status_code, 200)
        self.assertEqual(h_js.resp_headers.get("Content-Type"), "application/javascript; charset=utf-8")

    def test_etag_returns_304_when_matched(self):
        h1 = _DummyHandler("/assets/js/state.js")
        handle_get_asset(h1, {}, {})
        etag = h1.resp_headers["ETag"]

        h2 = _DummyHandler("/assets/js/state.js", headers={"If-None-Match": etag})
        handle_get_asset(h2, {}, {})
        self.assertEqual(h2.status_code, 304)
        self.assertEqual(h2.resp_headers.get("ETag"), etag)
        self.assertEqual(h2.wfile.getvalue(), b"")

    def test_blocks_path_traversal_and_missing_files(self):
        for bad_path in ("/assets/../../backend/config.py", "/assets/../index.html", "/assets/missing.js"):
            h = _DummyHandler(bad_path)
            handle_get_asset(h, {}, {})
            self.assertEqual(h.error_code, 404, f"Expected 404 for {bad_path}")

    def test_router_dispatches_assets_route(self):
        router = build_default_router()
        h = _DummyHandler("/assets/css/reader.css")
        matched = router.dispatch(h, "GET", "/assets/css/reader.css", qs={}, body={})
        self.assertTrue(matched)
        self.assertEqual(h.status_code, 200)

    def test_vendor_route_supports_etag_304(self):
        h1 = _DummyHandler("/vendor/pdf.min.js")
        handle_get_vendor(h1, {}, {}, "pdf.min.js")
        self.assertEqual(h1.status_code, 200)
        etag = h1.resp_headers.get("ETag")
        self.assertTrue(etag)
        self.assertEqual(h1.resp_headers.get("Cache-Control"), "public, max-age=86400")

        h2 = _DummyHandler("/vendor/pdf.min.js", headers={"If-None-Match": etag})
        handle_get_vendor(h2, {}, {}, "pdf.min.js")
        self.assertEqual(h2.status_code, 304)
        self.assertEqual(h2.wfile.getvalue(), b"")


if __name__ == "__main__":
    unittest.main()
