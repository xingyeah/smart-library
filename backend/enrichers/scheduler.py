import logging
import math
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import database
from config import COVERS_DIR, DB_PATH, MIN_EXTERNAL_VOTES
from enrichers.base import BaseBookEnricher, BookQuery, EnrichedBookMeta, search_title
from enrichers.douban import DoubanEnricher
from enrichers.google_books import GoogleBooksEnricher
from enrichers.open_library import OpenLibraryEnricher

logger = logging.getLogger("enrichers.scheduler")


class CircuitBreakerManager:
    """Manages channel-level circuit breaker cooldowns to prevent cascading stalls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cooldowns: Dict[str, float] = {}

    def is_available(self, channel: str) -> bool:
        with self._lock:
            deadline = self._cooldowns.get(channel, 0.0)
            return time.time() >= deadline

    def trigger_cooldown(self, channel: str, duration_sec: float = 300.0) -> None:
        with self._lock:
            self._cooldowns[channel] = time.time() + duration_sec
            logger.warning("Circuit breaker tripped for '%s': cooldown %ds", channel, duration_sec)


def compute_external_bonus(rating: float, votes: int) -> float:
    """Calculate normalized bonus for classic_score based on 10-point scale and vote volume."""
    if rating <= 0.0 or votes < MIN_EXTERNAL_VOTES:
        return 0.0
    vote_weight = math.log10(max(10, min(100000, votes)))
    score_delta = max(0.0, rating - 6.5) * 2.5 + vote_weight * 1.2
    return round(min(25.0, score_delta), 2)


COVER_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}


def _download_channel_cover(source: str, external_id: str, cover_url: str) -> Optional[str]:
    """Download cover from external channel and return web path."""
    if not (external_id and cover_url):
        return None
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", external_id)[:40]
    fname = f"ext_{source}_{safe_id}.jpg"
    dest = COVERS_DIR / fname
    if dest.exists() and dest.stat().st_size > 0:
        return f"/covers/{fname}"
    headers = dict(COVER_DOWNLOAD_HEADERS)
    if "douban" in source or "douban" in cover_url:
        headers["Referer"] = "https://book.douban.com/"
    try:
        req = urllib.request.Request(cover_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            content = resp.read()
            if content.startswith((b"\xff\xd8\xff", b"\x89PNG", b"RIFF")):
                dest.write_bytes(content)
                return f"/covers/{fname}"
    except Exception:
        # Download failure is non-fatal; leave cover missing for text fallback
        logging.getLogger(__name__).debug("cover download failed; card falls back to the text placeholder", exc_info=True)
    return None


class MultiSourceSlowEnricher:
    """Adaptive multi-channel slow background daemon enriching ratings, descriptions and reviews."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.cb = CircuitBreakerManager()
        self.douban = DoubanEnricher()
        self.google_books = GoogleBooksEnricher()
        self.open_library = OpenLibraryEnricher()
        self.stop_event = threading.Event()

    def _select_channel_order(self, lang: str) -> List[BaseBookEnricher]:
        """Route priority based on language."""
        if lang.startswith("zh"):
            return [self.douban, self.google_books, self.open_library]
        return [self.google_books, self.open_library, self.douban]

    def _attempt_channel_fetch(
        self, enricher: BaseBookEnricher, row: Any, lang: str
    ) -> Optional[EnrichedBookMeta]:
        ch = enricher.source_name
        if not self.cb.is_available(ch):
            return None
        try:
            query = BookQuery(
                isbn=str(row["isbn"] or ""),
                title=search_title(str(row["title"] or "")),
                author=str(row["author"] or ""),
                lang=lang,
            )
            return enricher.fetch(query)
        except Exception as e:
            logger.warning("Channel '%s' failed for book #%s: %s", ch, row["id"], e)
            self.cb.trigger_cooldown(ch, duration_sec=300.0)
            return None

    def _update_book_records(self, bid: int, res: EnrichedBookMeta, current_score: float) -> None:
        """Persist metadata to book_external_meta and update books classic score, summary & cover."""
        database.upsert_external_meta(
            self.db_path,
            book_id=bid,
            source=res.source,
            external_id=res.external_id,
            rating=res.rating,
            raw_rating=res.raw_rating,
            votes=res.votes,
            description=res.description,
            reviews=res.reviews,
            status=res.status,
        )

        conn = database.get_conn(self.db_path)
        try:
            bonus = compute_external_bonus(res.rating, res.votes)
            new_score = round(min(99.5, max(10.0, current_score + bonus)), 2)
            book_row = conn.execute("SELECT summary, cover_path FROM books WHERE id = ?", (bid,)).fetchone()
            curr_sum = (book_row["summary"] or "").strip() if book_row else ""
            curr_cover = (book_row["cover_path"] or "").strip() if book_row else ""

            new_cover = curr_cover
            if not curr_cover and res.cover_url:
                dl_cover = _download_channel_cover(res.source, res.external_id, res.cover_url)
                if dl_cover:
                    new_cover = dl_cover

            if len(curr_sum) < 50 and len(res.description) >= 60:
                conn.execute(
                    "UPDATE books SET classic_score = ?, summary = ?, summary_source = ?, cover_path = ? WHERE id = ?",
                    (new_score, res.description, f"ext_{res.source}", new_cover, bid),
                )
            else:
                conn.execute(
                    "UPDATE books SET classic_score = ?, cover_path = ? WHERE id = ?",
                    (new_score, new_cover, bid),
                )
            conn.commit()
        finally:
            conn.close()
        if res.rating > 0:
            from auto_summary_refresher import enqueue_book_summary_refresh

            enqueue_book_summary_refresh(bid)

    def enrich_book_row(self, row: Any) -> bool:
        """Process a candidate book row across all applicable channels and aggregate metadata."""
        bid = int(row["id"])
        lang = (row["language"] or "zh-CN").lower()
        current_score = float(row["classic_score"] or 50.0)
        success_count = 0

        for enricher in self._select_channel_order(lang):
            res = self._attempt_channel_fetch(enricher, row, lang)
            if res and res.status == "success":
                self._update_book_records(bid, res, current_score)
                success_count += 1

        if success_count > 0:
            return True

        database.upsert_external_meta(
            self.db_path,
            book_id=bid,
            source="none",
            status="not_found",
        )
        return False


_slow_enricher_thread: Optional[threading.Thread] = None
_slow_enricher_stop = threading.Event()


def _fetch_next_enrich_candidate(conn) -> Any:
    return conn.execute(
        """
        SELECT b.* FROM books b
        LEFT JOIN book_external_meta em ON b.id = em.book_id
        WHERE em.id IS NULL AND b.is_anthology = 0
        ORDER BY b.classic_score DESC, b.id ASC LIMIT 1
        """
    ).fetchone()


def _enricher_daemon_loop(enricher: MultiSourceSlowEnricher, db_path: Path, interval_sec: float) -> None:
    time.sleep(15.0)
    while not _slow_enricher_stop.is_set():
        conn = database.get_conn(db_path)
        try:
            row = _fetch_next_enrich_candidate(conn)
        finally:
            conn.close()

        if not row:
            _slow_enricher_stop.wait(60.0)
            continue

        try:
            enricher.enrich_book_row(row)
        except Exception as e:
            logger.warning("Error during slow enrichment for book #%s: %s", row["id"], e)

        _slow_enricher_stop.wait(interval_sec)


def start_background_slow_enricher(interval_sec: float = 12.0, db_path: Path = DB_PATH) -> None:
    """Start low-priority background daemon thread for multi-source slow enrichment."""
    global _slow_enricher_thread
    if _slow_enricher_thread and _slow_enricher_thread.is_alive():
        return

    enricher = MultiSourceSlowEnricher(db_path=db_path)
    _slow_enricher_thread = threading.Thread(
        target=_enricher_daemon_loop,
        args=(enricher, db_path, interval_sec),
        daemon=True,
        name="MultiSourceSlowEnricher",
    )
    _slow_enricher_thread.start()
