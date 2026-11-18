import logging
from typing import Any, Dict, List

from http_api.responses import send_json
from translator import batch_translate_paragraphs, book_context, is_valid_lang_code, translate_text

logger = logging.getLogger("http_api.translate")


def handle_post_translate(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/translate: Translate a sentence or phrase."""
    text = str(body.get("text") or "").strip()
    source_lang = str(body.get("source_lang") or "auto").strip()
    target_lang = str(body.get("target_lang") or "zh-CN").strip()
    if not (is_valid_lang_code(source_lang) and is_valid_lang_code(target_lang)):
        send_json(handler, {"ok": False, "error": "invalid language code"}, 400)
        return
    engine = str(body.get("engine") or "auto").strip()
    res = translate_text(
        text,
        source_lang=source_lang,
        target_lang=target_lang,
        context=book_context(body.get("book_id")),
        engine=engine,
    )
    send_json(handler, res, 200 if res.get("ok") else 400)


def handle_post_translate_batch(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/translate/batch: Batch translate multiple paragraphs."""
    paragraphs = body.get("paragraphs") or []
    source_lang = str(body.get("source_lang") or "auto").strip()
    target_lang = str(body.get("target_lang") or "zh-CN").strip()
    if not (is_valid_lang_code(source_lang) and is_valid_lang_code(target_lang)):
        send_json(handler, {"ok": False, "error": "invalid language code"}, 400)
        return
    res_list = batch_translate_paragraphs(
        paragraphs,
        source_lang=source_lang,
        target_lang=target_lang,
        context=book_context(body.get("book_id")),
        engine=str(body.get("engine") or "auto").strip(),
    )
    send_json(handler, {"ok": True, "results": res_list})
