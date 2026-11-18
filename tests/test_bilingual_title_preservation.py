import unittest
from book_enrichment_service import _preserve_bilingual_title, _sanitize_llm_response


class TestBilingualTitlePreservation(unittest.TestCase):
    def test_preserve_bilingual_title_when_llm_strips_english(self):
        # LLM returns pure Chinese title, stripping '(Organizational Change)'
        res = _preserve_bilingual_title("组织变革", "组织变革 (Organizational Change)")
        self.assertEqual(res, "组织变革 (Organizational Change)")

    def test_preserve_bilingual_title_when_llm_already_preserves_english(self):
        # LLM correctly preserves '(Organizational Change)'
        res = _preserve_bilingual_title(
            "组织变革 (Organizational Change)", "组织变革 (Organizational Change)"
        )
        self.assertEqual(res, "组织变革 (Organizational Change)")

    def test_preserve_bilingual_title_when_pure_chinese_book(self):
        # Pure Chinese book without English title in parentheses
        res = _preserve_bilingual_title("经济学原理", "经济学原理")
        self.assertEqual(res, "经济学原理")

    def test_preserve_bilingual_title_when_pure_english_book(self):
        # Pure English book
        res = _preserve_bilingual_title("Organizational Change", "Organizational Change")
        self.assertEqual(res, "Organizational Change")

    def test_sanitize_llm_response_restores_bilingual(self):
        fallback = {"title": "组织变革 (Organizational Change)", "category": "商业管理与创投", "summary": "简介"}
        data = {"clean_title": "组织变革", "category": "商业管理与创投", "summary": "新简介"}
        res = _sanitize_llm_response(data, fallback)
        self.assertEqual(res["clean_title"], "组织变革 (Organizational Change)")


if __name__ == "__main__":
    unittest.main()
