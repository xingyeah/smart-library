import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from config import GOOGLE_BOOKS_API_KEY
from enrichers.base import BaseBookEnricher, BookQuery, EnrichedBookMeta

logger = logging.getLogger("enrichers.google_books")


class GoogleBooksEnricher(BaseBookEnricher):
    """Channel adapter for Google Books Volumes API."""

    source_name: str = "google_books"

    def _build_query_url(self, isbn: str, title: str, author: str) -> str:
        clean_isbn = "".join(ch for ch in isbn if ch.isdigit() or ch.upper() == "X")
        if len(clean_isbn) in (10, 13):
            q = f"isbn:{clean_isbn}"
        else:
            q_parts = []
            if title:
                q_parts.append(f"intitle:{title.strip()}")
            if author:
                q_parts.append(f"inauthor:{author.strip()}")
            q = "+".join(q_parts) or title.strip()
        encoded = urllib.parse.quote(q)
        api_key = (
            os.environ.get("GOOGLE_BOOKS_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or GOOGLE_BOOKS_API_KEY
            or ""
        )
        key_param = f"&key={api_key}" if api_key else ""
        return f"https://www.googleapis.com/books/v1/volumes?q={encoded}&maxResults=1{key_param}"

    def _parse_volume(self, item: Dict[str, Any]) -> EnrichedBookMeta:
        vid = str(item.get("id") or "")
        info = item.get("volumeInfo") or {}
        raw_rate = float(info.get("averageRating") or 0.0)
        votes = int(info.get("ratingsCount") or 0)
        norm_rate = round(raw_rate * 2.0, 1)  # Scale from 5.0 to 10.0
        desc = str(info.get("description") or "").strip()
        img_links = info.get("imageLinks") or {}
        cover_url = str(img_links.get("thumbnail") or img_links.get("smallThumbnail") or "")
        if cover_url.startswith("http://"):
            cover_url = "https://" + cover_url[7:]

        return EnrichedBookMeta(
            source=self.source_name,
            external_id=vid,
            rating=norm_rate,
            raw_rating=raw_rate,
            votes=votes,
            description=desc,
            reviews=[],
            cover_url=cover_url,
            status="success",
        )

    def fetch(self, query: BookQuery) -> Optional[EnrichedBookMeta]:
        url = self._build_query_url(query.isbn, query.title, query.author)
        headers = {
            "User-Agent": "Mozilla/5.0 (SmartLibrary/1.0)",
            "Accept": "application/json",
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=self.timeout_sec, context=self._get_ssl_context()) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                items = data.get("items") or []
                if not items:
                    return None
                return self._parse_volume(items[0])
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            logger.warning("Google Books HTTP error %d for '%s'", e.code, query.title)
            raise
        except (OSError, TimeoutError) as e:
            logger.warning("Google Books network failure for '%s': %s", query.title, e)
            raise
        except Exception:
            # Malformed JSON or unexpected schema payload
            return None
