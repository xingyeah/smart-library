import unittest
from unittest import mock


class TestEnricherAdapters(unittest.TestCase):
    def test_enriched_book_meta_data_structure(self):
        from enrichers.base import EnrichedBookMeta

        meta = EnrichedBookMeta(
            source="test_source",
            external_id="12345",
            rating=8.5,
            raw_rating=4.25,
            votes=100,
            description="A great test book",
            reviews=[{"author": "Tom", "rating": 5, "content": "Inspiring!"}],
            status="success",
        )
        self.assertEqual(meta.source, "test_source")
        self.assertEqual(meta.rating, 8.5)
        self.assertEqual(len(meta.reviews), 1)
        d = meta.to_dict()
        self.assertEqual(d["external_id"], "12345")
        self.assertEqual(d["votes"], 100)

    @mock.patch("urllib.request.urlopen")
    def test_google_books_enricher_by_isbn(self, mock_urlopen):
        from enrichers.google_books import GoogleBooksEnricher

        mock_resp_data = {
            "totalItems": 1,
            "items": [
                {
                    "id": "gb_vol_123",
                    "volumeInfo": {
                        "title": "Rewire",
                        "authors": ["Nicole Vignola"],
                        "averageRating": 4.5,
                        "ratingsCount": 210,
                        "description": "Break the cycle and alter your thoughts through neuroplasticity.",
                        "categories": ["Psychology"],
                    },
                }
            ],
        }
        import json

        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
        mock_urlopen.return_value = cm

        enricher = GoogleBooksEnricher(timeout_sec=5.0)
        from enrichers.base import BookQuery
        res = enricher.fetch(BookQuery(isbn="9780593712856", title="Rewire", author="Nicole Vignola", lang="en"))
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "google_books")
        self.assertEqual(res.external_id, "gb_vol_123")
        self.assertEqual(res.raw_rating, 4.5)
        self.assertEqual(res.rating, 9.0)  # 5分制归一化为10分制
        self.assertEqual(res.votes, 210)
        self.assertTrue(res.description.startswith("Break the cycle"))
        self.assertEqual(res.status, "success")

    @mock.patch("urllib.request.urlopen")
    def test_douban_enricher_fetch_with_reviews(self, mock_urlopen):
        from enrichers.douban import DoubanEnricher
        import json

        # Mock suggest response
        suggest_json = json.dumps([{"id": "36890011", "title": "重塑回路"}]).encode("utf-8")
        # Mock subject HTML with rating, votes and short reviews
        subject_html = """
        <html>
            <span property="v:itemreviewed">重塑回路</span>
            <strong class="ll rating_num" property="v:average"> 8.6 </strong>
            <span property="v:votes">1200</span>
            <div id="link-report">
                <span class="all hidden">
                    <div class="intro">
                        <p>探讨神经可塑性的经典之作，引导读者建立积极认知。</p>
                    </div>
                </span>
            </div>
            <div id="comments-section">
                <div class="comment-item">
                    <span class="comment-info">
                        <a href="#">认知派读者</a>
                        <span class="allstar50 rating" title="力荐"></span>
                    </span>
                    <span class="short">非常受用，练习很有针对性。</span>
                </div>
            </div>
        </html>
        """.encode("utf-8")

        cm1 = mock.MagicMock()
        cm1.__enter__.return_value.read.return_value = suggest_json

        cm2 = mock.MagicMock()
        cm2.__enter__.return_value.read.return_value = subject_html

        mock_urlopen.side_effect = [cm1, cm2]

        enricher = DoubanEnricher(timeout_sec=5.0)
        from enrichers.base import BookQuery
        res = enricher.fetch(BookQuery(isbn="", title="重塑回路", author="妮可·维尼奥拉", lang="zh-CN"))
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "douban")
        self.assertEqual(res.external_id, "36890011")
        self.assertEqual(res.rating, 8.6)
        self.assertEqual(res.votes, 1200)
        self.assertIn("经典之作", res.description)
        self.assertEqual(len(res.reviews), 1)
        self.assertEqual(res.reviews[0]["author"], "认知派读者")
        self.assertEqual(res.reviews[0]["content"], "非常受用，练习很有针对性。")

    @staticmethod
    def _page(body: bytes) -> mock.MagicMock:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = body
        return response

    @mock.patch("urllib.request.urlopen")
    def test_douban_block_pages_are_errors_not_misses(self, mock_urlopen):
        from enrichers.base import BookQuery
        from enrichers.douban import DoubanBlockedError, DoubanEnricher

        query = BookQuery(isbn="", title="重塑回路", author="", lang="zh-CN")
        enricher = DoubanEnricher(timeout_sec=5.0)
        mock_urlopen.side_effect = [self._page("<html>检测到有异常请求</html>".encode("utf-8"))]
        with self.assertRaises(DoubanBlockedError):  # suggest answered with HTML
            enricher.fetch(query)
        mock_urlopen.side_effect = [
            self._page(b'[{"id": "36890011", "title": "\xe9\x87\x8d\xe5\xa1\x91\xe5\x9b\x9e\xe8\xb7\xaf"}]'),
            self._page("<html>验证码</html>".encode("utf-8")),
        ]
        with self.assertRaises(DoubanBlockedError):  # subject page without a title
            enricher.fetch(query)

    @mock.patch("urllib.request.urlopen")
    def test_open_library_enricher_by_isbn(self, mock_urlopen):
        from enrichers.open_library import OpenLibraryEnricher
        import json

        ol_resp = {
            "ISBN:9780140328721": {
                "bib_key": "ISBN:9780140328721",
                "info_url": "https://openlibrary.org/books/OL123M",
                "details": {
                    "title": "Fantastic Mr Fox",
                    "description": "Roald Dahl's classic tale.",
                },
            }
        }
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(ol_resp).encode("utf-8")
        mock_urlopen.return_value = cm

        enricher = OpenLibraryEnricher(timeout_sec=5.0)
        from enrichers.base import BookQuery
        res = enricher.fetch(BookQuery(isbn="9780140328721", title="Fantastic Mr Fox", author="Roald Dahl"))
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "open_library")
        self.assertIn("classic tale", res.description)


if __name__ == "__main__":
    unittest.main()
