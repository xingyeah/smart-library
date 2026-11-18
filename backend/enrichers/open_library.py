import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from enrichers.base import BaseBookEnricher, BookQuery, EnrichedBookMeta

logger = logging.getLogger("enrichers.open_library")


class OpenLibraryEnricher(BaseBookEnricher):
    """Fallback channel adapter for OpenLibrary.org (zero auth required)."""

    source_name: str = "open_library"

    def fetch(self, query: BookQuery) -> Optional[EnrichedBookMeta]:
        clean_isbn = "".join(ch for ch in query.isbn if ch.isdigit() or ch.upper() == "X")
        if not clean_isbn:
            return None

        bib_key = f"ISBN:{clean_isbn}"
        url = f"https://openlibrary.org/api/books?bibkeys={bib_key}&jscmd=details&format=json"
        headers = {"User-Agent": "Mozilla/5.0 (SmartLibrary/1.0)", "Accept": "application/json"}
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=self.timeout_sec, context=self._get_ssl_context()) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                entry = data.get(bib_key)
                if not entry:
                    return None
                details = entry.get("details") or {}
                desc_obj = details.get("description")
                desc = desc_obj.get("value", "") if isinstance(desc_obj, dict) else str(desc_obj or "")
                info_url = str(entry.get("info_url") or "")
                covers = details.get("covers") or []
                cover_url = f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg" if covers else ""

                return EnrichedBookMeta(
                    source=self.source_name,
                    external_id=info_url,
                    rating=0.0,
                    raw_rating=0.0,
                    votes=0,
                    description=desc.strip(),
                    reviews=[],
                    cover_url=cover_url,
                    status="success" if (desc or cover_url) else "not_found",
                )
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            logger.warning("OpenLibrary HTTP error %d for '%s'", e.code, query.isbn)
            raise
        except (OSError, TimeoutError) as e:
            logger.warning("OpenLibrary network error for '%s': %s", query.isbn, e)
            raise
        except Exception:
            # Skip unexpected parse error
            return None
