"""Shared background job helpers decoupled from the HTTP transport layer."""

import logging
import threading
from typing import Iterable

from archiver import rebuild_vector_cache

logger = logging.getLogger("jobs")

# Serializes .npy/.json vector rebuilds across HTTP handlers and background workers.
_reindex_lock = threading.Lock()


def reindex_in_background(book_ids: Iterable[int]) -> None:
    """Actual reindex work, serialized to avoid racing .npy writes."""
    ids = list(book_ids)
    try:
        with _reindex_lock:
            rebuild_vector_cache(reencode_ids=ids)
    except Exception:
        logger.exception("Background vector reindex failed for books %s", ids)


def spawn_reindex(book_ids: Iterable[int]) -> None:
    """Re-encode changed books' vectors off the calling thread."""
    threading.Thread(target=reindex_in_background, args=(list(book_ids),), daemon=True).start()
