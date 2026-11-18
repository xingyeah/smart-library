"""
server.py: 兼容门面与 CLI 启动入口。
核心逻辑已解耦迁移至 backend/http_api/ 模块。
"""

import logging
import sys
from typing import Any

try:
    import certifi
    import ssl
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except Exception:
    # certifi injection is optional; fallback silently to system default SSL context
    logging.getLogger(__name__).debug("certifi not available; using the system SSL context", exc_info=True)

from archiver import rebuild_vector_cache
from database import get_conn, init_db
from ebook_parser import normalize_for_dedup
from http_api import (
    background,
    gate,
    get_local_lan_ip,
    responses,
    run_server,
    state,
)
from http_api.handlers import books, organize, reading, recommend, translate
from http_api.server_handler import LibraryRequestHandler

# Locks and the organize-state dict are shared objects, so aliasing them is safe. LAN_TOKEN and _LOOPBACK are
# rebound at runtime: they are read through __getattr__ below so this module never holds a stale copy.
_organize_state = state._organize_state
_state_lock = state._organize_lock
_organize_lock = state._organize_lock
_reindex_lock = state._reindex_lock

# Re-export helper functions
_parse_bytes_range = responses.parse_bytes_range
parse_bytes_range = responses.parse_bytes_range
send_json = responses.send_json
read_json_body = responses.read_json_body
start_background_organize = background.start_background_organize
_scheduler_tick = background.scheduler_tick
_scheduler_daemon = background.scheduler_daemon
_backup_daemon = background.backup_daemon
_reindex_in_background = background.reindex_in_background
_spawn_reindex = background.spawn_reindex
get_similar_books = books.get_similar_books
_format_book_row = books.format_book_row


def __getattr__(name: str) -> Any:
    """Fallback dynamically to http_api.state for mutable tokens and locks."""
    if hasattr(state, name):
        return getattr(state, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "LibraryRequestHandler",
    "run_server",
    "get_local_lan_ip",
    "get_conn",
    "init_db",
    "LAN_TOKEN",
    "_LOOPBACK",
    "_organize_state",
    "_state_lock",
    "_organize_lock",
    "_reindex_lock",
    "_parse_bytes_range",
    "parse_bytes_range",
    "send_json",
    "read_json_body",
    "start_background_organize",
    "_scheduler_tick",
    "_scheduler_daemon",
    "_backup_daemon",
    "_reindex_in_background",
    "_spawn_reindex",
    "get_similar_books",
    "_format_book_row",
]

if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    h = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"
    run_server(host=h, port=p)
