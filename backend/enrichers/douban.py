import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from enrichers.base import BaseBookEnricher, BookQuery, EnrichedBookMeta, authors_match, titles_match

logger = logging.getLogger("enrichers.douban")


SUGGESTIONS_CHECKED = 5
# Every real subject page carries its title under this property; a captcha or "unusual request" page, which Douban
# serves with status 200, does not
SUBJECT_PAGE_MARKER = 'property="v:itemreviewed"'


class DoubanBlockedError(RuntimeError):
    """Douban answered with a block page instead of data: the book is not known to be missing."""


def pick_suggestion(items: List[Dict[str, Any]], query: BookQuery, by_isbn: bool) -> Tuple[Optional[str], Optional[str]]:
    """(subject id, cover url) of the first suggestion that is this book, else (None, None).

    A title search returns whatever Douban ranks first, often a different book with a similar name; it is only
    accepted when title and author agree. An ISBN search is exact.
    """
    for item in items[:SUGGESTIONS_CHECKED]:
        if not item.get("id"):
            continue
        if by_isbn or (titles_match(item.get("title") or "", query.title) and authors_match(item.get("author_name") or "", query.author)):
            return str(item["id"]), str(item.get("pic") or "")
    return None, None


class DoubanEnricher(BaseBookEnricher):
    """Channel adapter for Douban Books (subject_suggest + subject page)."""

    source_name: str = "douban"

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json,*/*",
            "Referer": "https://book.douban.com/",
        }

    def _query_subject_id(self, query: BookQuery) -> Tuple[Optional[str], Optional[str]]:
        clean_isbn = "".join(ch for ch in query.isbn if ch.isdigit() or ch.upper() == "X")
        by_isbn = len(clean_isbn) in (10, 13)
        short_title = re.split(r"[\(（:：—\-]", query.title)[0].strip() or query.title.strip()
        url = f"https://book.douban.com/j/subject_suggest?q={urllib.parse.quote(clean_isbn if by_isbn else short_title)}"

        req = urllib.request.Request(url, headers=self._get_headers())
        with urllib.request.urlopen(req, timeout=self.timeout_sec, context=self._get_ssl_context()) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        try:
            data = json.loads(body)
        except ValueError as error:
            raise DoubanBlockedError(f"suggest answered with a non-JSON page: {body[:80]!r}") from error
        return pick_suggestion(data if isinstance(data, list) else [], query, by_isbn)

    def _parse_reviews(self, html: str) -> List[Dict[str, Any]]:
        reviews = []
        # Match short comments: author and comment text
        pattern = re.compile(
            r'class="comment-info">.*?<a[^>]*>(.*?)</a>.*?'
            r'class="short">\s*(.*?)\s*</span>',
            re.DOTALL,
        )
        for m in pattern.finditer(html):
            author = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if text:
                reviews.append({"author": author, "content": text, "source": "douban"})
            if len(reviews) >= 5:
                break
        return reviews

    def _parse_subject_html(self, subject_id: str, html: str, fallback_pic: str = "") -> EnrichedBookMeta:
        m_rate = re.search(r'property="v:average">\s*([0-9.]+)\s*<', html)
        m_votes = re.search(r'property="v:votes">\s*(\d+)\s*<', html)
        rating = float(m_rate.group(1)) if m_rate else 0.0
        votes = int(m_votes.group(1)) if m_votes else 0

        # Description extractor
        desc = ""
        m_intro = re.search(r'class="intro">\s*(.*?)\s*</div>', html, re.DOTALL)
        if m_intro:
            desc = re.sub(r"<[^>]+>", "", m_intro.group(1)).strip()

        m_pic = re.search(r'class="nbg"[^>]*href="([^"]+)"', html)
        cover_url = m_pic.group(1).strip() if m_pic else fallback_pic
        reviews = self._parse_reviews(html)

        return EnrichedBookMeta(
            source=self.source_name,
            external_id=subject_id,
            rating=rating,
            raw_rating=rating,
            votes=votes,
            description=desc,
            reviews=reviews,
            cover_url=cover_url,
            status="success" if rating > 0 else "not_found",
        )

    def fetch(self, query: BookQuery) -> Optional[EnrichedBookMeta]:
        try:
            subject_id, pic_url = self._query_subject_id(query)
            if not subject_id:
                return None

            subj_url = f"https://book.douban.com/subject/{subject_id}/"
            req = urllib.request.Request(subj_url, headers=self._get_headers())
            with urllib.request.urlopen(req, timeout=self.timeout_sec, context=self._get_ssl_context()) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            if SUBJECT_PAGE_MARKER not in html:
                raise DoubanBlockedError(f"subject {subject_id} answered with a block page")
            return self._parse_subject_html(subject_id, html, fallback_pic=pic_url or "")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            logger.warning("Douban HTTP error %d for '%s'", e.code, query.title)
            raise
        except (OSError, TimeoutError) as e:
            logger.warning("Douban network error for '%s': %s", query.title, e)
            raise
        except DoubanBlockedError as e:
            logger.warning("Douban blocked the request for '%s': %s", query.title, e)
            raise
        except Exception:
            # Skip invalid HTML parse error silently
            return None
