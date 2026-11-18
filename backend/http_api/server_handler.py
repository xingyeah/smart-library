import logging
import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict

from http_api.gate import check_lan_gate, verify_json_content_type
from http_api.responses import read_json_body, send_json
from http_api.routes import build_default_router

logger = logging.getLogger("http_api.handler")


class LibraryRequestHandler(BaseHTTPRequestHandler):
    """HTTP request dispatcher matching paths via declarative routing table."""

    router = build_default_router()

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr access logging."""
        pass

    def _send_json(self, data: Any, status: int = 200) -> None:
        """Backward compatible helper forwarding to http_api.responses."""
        send_json(self, data, status=status)

    def _read_json_body(self) -> Dict[str, Any]:
        """Backward compatible helper forwarding to http_api.responses."""
        return read_json_body(self)

    def _lan_gate(self) -> bool:
        """Backward compatible helper forwarding to http_api.gate."""
        return check_lan_gate(self)

    def _guarded(self, handler_fn) -> None:
        """Central exception barrier converting unhandled faults to HTTP 500."""
        try:
            if not self._lan_gate():
                return
            handler_fn()
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected prematurely; silent drop is standard
            pass
        except Exception:
            logger.exception("Internal error during request processing: %s", self.path)
            try:
                self._send_json({"error": "internal server error"}, 500)
            except Exception:
                # Connection dropped after partial write, ignore
                pass

    def do_GET(self) -> None:
        self._guarded(self._handle_get)

    def do_POST(self) -> None:
        self._guarded(self._handle_post)

    def _handle_get(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        qs = urllib.parse.parse_qs(parsed_url.query)
        matched = self.router.dispatch(self, "GET", path, qs=qs, body={})
        if not matched:
            self.send_error(404, "Not Found")

    def _handle_post(self) -> None:
        if not verify_json_content_type(self):
            return
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        qs = urllib.parse.parse_qs(parsed_url.query)
        body = self._read_json_body()
        matched = self.router.dispatch(self, "POST", path, qs=qs, body=body)
        if not matched:
            self._send_json({"error": "Unknown endpoint"}, 404)
