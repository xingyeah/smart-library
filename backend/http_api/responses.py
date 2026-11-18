import json
import logging
import os
import shutil
import urllib.parse
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("http_api.responses")

MAX_JSON_BODY_BYTES = 2 * 1024 * 1024
RANGE_CHUNK_BYTES = 1024 * 1024


class PayloadTooLargeError(ValueError):
    """Raised when Content-Length exceeds MAX_JSON_BODY_BYTES."""


class InvalidJsonBodyError(ValueError):
    """Raised when request Content-Length or JSON body is invalid."""


def parse_bytes_range(spec: str, total_len: int) -> Optional[Tuple[int, int]]:
    """Parse a single `bytes=` range; None on malformed or unsatisfiable. Suffix ranges supported."""
    if not spec or not spec.startswith("bytes="):
        return None
    spec = spec[6:].strip()
    if "," in spec or "-" not in spec:
        return None
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":
            n = int(end_s)
            if n <= 0:
                return None
            return (max(0, total_len - n), total_len - 1)
        start = int(start_s)
        end = int(end_s) if end_s != "" else total_len - 1
        if start < 0 or end < start or start >= total_len:
            return None
        return (start, min(end, total_len - 1))
    except ValueError:
        return None


def send_json(handler: Any, data: Any, status: int = 200) -> None:
    """Serialize data to JSON and send response with standard headers."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: Any) -> Dict[str, Any]:
    """Read and parse JSON request payload safely with size and object-type guards."""
    raw_len = handler.headers.get("Content-Length") or "0"
    try:
        length = int(raw_len)
    except ValueError as exc:
        raise InvalidJsonBodyError("Invalid Content-Length header") from exc
    if length < 0:
        raise InvalidJsonBodyError("Negative Content-Length header")
    if length == 0:
        return {}
    if length > MAX_JSON_BODY_BYTES:
        raise PayloadTooLargeError(f"Request body exceeds {MAX_JSON_BODY_BYTES} bytes")
    raw = handler.rfile.read(length).decode("utf-8", errors="ignore")
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        logger.warning("Failed to parse request JSON payload: %s (err: %s)", raw[:100], exc)
        raise InvalidJsonBodyError("Malformed JSON body") from exc
    if not isinstance(parsed, dict):
        raise InvalidJsonBodyError("Request body must be a JSON object")
    return parsed


def _stream_file_range(final_path: str, start: int, length: int, wfile: Any) -> None:
    remaining = length
    with open(final_path, "rb") as fp:
        fp.seek(start)
        while remaining > 0:
            chunk = fp.read(min(RANGE_CHUNK_BYTES, remaining))
            if not chunk:
                break
            wfile.write(chunk)
            remaining -= len(chunk)


def send_range_stream(
    handler: Any,
    final_path: str,
    range_hdr: Optional[str],
    content_type: str,
    as_attachment: bool = False,
) -> None:
    """Stream file with support for HTTP 206 Partial Content / HTTP 416 Range Not Satisfiable."""
    fname = urllib.parse.quote(os.path.basename(final_path))
    disp = f"attachment; filename*=UTF-8''{fname}" if as_attachment else "inline"
    total_len = os.path.getsize(final_path)

    if range_hdr and range_hdr.strip().startswith("bytes="):
        rng = parse_bytes_range(range_hdr, total_len)
        if rng is not None:
            start, end = rng
            length = end - start + 1
            handler.send_response(206)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Content-Length", str(length))
            handler.send_header("Content-Range", f"bytes {start}-{end}/{total_len}")
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Disposition", disp)
            handler.end_headers()
            _stream_file_range(final_path, start, length, handler.wfile)
            return

        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{total_len}")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return

    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(total_len))
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Disposition", disp)
    handler.end_headers()
    with open(final_path, "rb") as fp:
        shutil.copyfileobj(fp, handler.wfile, RANGE_CHUNK_BYTES)
