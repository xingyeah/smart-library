import hashlib
import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import deepseek_client
from config import CLAUDE_TRANSLATE_ENABLED
from database import get_conn

logger = logging.getLogger("translator")

CLAUDE_MODEL = "claude-haiku-5-5"
APPLE_TRANSLATE_BIN = Path(__file__).resolve().parent / "native" / "apple_translate"
_TARGET_NAMES = {"zh-CN": "简体中文", "zh-TW": "繁體中文", "en": "English", "ja": "日本語"}
_APPLE_LANG = {"zh-CN": "zh-Hans", "zh-TW": "zh-Hant"}
_LANG_CODE_RE = re.compile(r"^(?:auto|[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,4})?)$")


def is_valid_lang_code(code: str) -> bool:
    """Validate BCP-47/auto language code before URL or argv interpolation."""
    return bool(_LANG_CODE_RE.fullmatch(code or ""))
_CLAUDE_SYSTEM = (
    "你是资深的图书译者，负责把读者正在阅读的书籍段落译成{target}。\n"
    "要求：\n"
    "- 忠实原意，不增删内容，不解释、不加注释或译者按语。\n"
    "- 行文自然流畅，符合{target}出版物的表达习惯，避免逐词直译的翻译腔；长句可按中文习惯拆分。\n"
    "- 人名、地名、书名、术语在同一本书内译法保持一致；常见专有名词用通行译名，生僻专有名词首次出现时在译名后括注原文。\n"
    "- 保留原文的语气和文体（叙事、论说、对话）。\n"
    "- 输入中的段落用 <p id=\"N\">…</p> 标记；按相同 id 逐段输出 <p id=\"N\">译文</p>，不输出任何其他内容。"
)
_claude_client = None
_claude_paused_until = 0.0  # after an API error (no credit, bad key, outage) skip Claude for a while
_deepseek_paused_until = 0.0


def _calc_hash(text: str, target_lang: str = "zh-CN") -> str:
    key = f"{target_lang.lower()}_{text.strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def get_cached_translation(text: str, source_lang: str = "auto", target_lang: str = "zh-CN") -> Optional[Dict[str, Any]]:
    thash = _calc_hash(text, target_lang)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM translation_cache WHERE text_hash = ?", (thash,)
        ).fetchone()
        if row:
            dict_entries = []
            if row["dict_json"]:
                try:
                    dict_entries = json.loads(row["dict_json"])
                except Exception:
                    # Corrupted cached translation dictionary json falls back to empty dict
                    logging.getLogger(__name__).warning("translation cache entry is corrupt; ignoring it", exc_info=True)
            return {
                "ok": True,
                "original": row["original_text"],
                "translation": row["translated_text"],
                "phonetic": row["phonetic"] or "",
                "dict": dict_entries,
                "source_lang": row["source_lang"] or source_lang,
                "target_lang": row["target_lang"] or target_lang,
                "cached": True,
                "engine": row["engine"] or "cache",
            }
    except Exception as e:
        logger.warning(f"Cache lookup failed: {e}")
    finally:
        conn.close()
    return None


def save_cached_translation(
    text: str,
    translation: str,
    source_lang: str = "auto",
    target_lang: str = "zh-CN",
    phonetic: str = "",
    dict_entries: Optional[List[Dict[str, Any]]] = None,
    engine: str = "google",
) -> None:
    thash = _calc_hash(text, target_lang)
    conn = get_conn()
    try:
        dict_json = json.dumps(dict_entries or [], ensure_ascii=False) if dict_entries else ""
        conn.execute(
            """
            INSERT OR REPLACE INTO translation_cache 
            (text_hash, source_lang, target_lang, original_text, translated_text, phonetic, dict_json, engine)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thash,
                source_lang,
                target_lang,
                text.strip(),
                translation.strip(),
                phonetic.strip(),
                dict_json,
                engine,
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to save translation cache: {e}")
    finally:
        conn.close()


def _translate_via_google(text: str, source_lang: str = "auto", target_lang: str = "zh-CN") -> Optional[Dict[str, Any]]:
    sl_q = urllib.parse.quote(source_lang, safe="")
    tl_q = urllib.parse.quote(target_lang, safe="")
    quoted = urllib.parse.quote(text)
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl_q}&tl={tl_q}&dt=t&dt=bd&dt=rm&q={quoted}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            trans_parts = []
            phonetic = ""
            if data and len(data) > 0 and data[0]:
                for item in data[0]:
                    if item and len(item) > 0 and item[0]:
                        trans_parts.append(item[0])
                    if item and len(item) > 3 and item[3]:
                        phonetic = item[3]

            dict_entries = []
            if len(data) > 1 and data[1]:
                for pos_group in data[1]:
                    pos = pos_group[0]
                    meanings = pos_group[1] if len(pos_group) > 1 else []
                    dict_entries.append({"pos": pos, "meanings": meanings[:8]})

            detected = data[2] if len(data) > 2 and isinstance(data[2], str) else "en"
            trans_result = "".join(trans_parts).strip()
            if trans_result:
                return {
                    "ok": True,
                    "original": text,
                    "translation": trans_result,
                    "phonetic": phonetic,
                    "dict": dict_entries,
                    "source_lang": detected,
                    "target_lang": target_lang,
                    "cached": False,
                    "engine": "google",
                }
    except Exception as e:
        logger.debug(f"Google translate request failed: {e}")
    return None


def _claude():
    """Lazily build the Anthropic client; None when no credentials are configured."""
    global _claude_client
    if not CLAUDE_TRANSLATE_ENABLED or time.time() < _claude_paused_until:
        return None
    if _claude_client is None and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        try:
            import anthropic

            _claude_client = anthropic.Anthropic(timeout=60.0, max_retries=1)
        except Exception as e:
            logger.warning(f"Claude client unavailable: {e}")
    return _claude_client


def _translate_batch_via_claude(
    texts: List[str], target_lang: str = "zh-CN", context: Optional[Dict[str, str]] = None
) -> Dict[int, str]:
    """Translate paragraphs in one call so terms stay consistent; returns {index: translation} for parsed ones."""
    client = _claude()
    if client is None or not texts or deepseek_client.today_cost() >= deepseek_client.DAILY_CAP_CNY:
        return {}
    import anthropic

    user = _llm_user_prompt(texts, context)
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=min(16000, 1000 + sum(len(t) for t in texts) * 2),
            thinking={"type": "disabled"},  # translation needs no reasoning pass; cuts latency
            system=_CLAUDE_SYSTEM.format(target=_TARGET_NAMES.get(target_lang, target_lang)),
            messages=[{"role": "user", "content": user}],
        )
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        global _claude_paused_until
        _claude_paused_until = time.time() + 300
        logger.warning(f"Claude translate failed, pausing Claude for 5 min: {e}")
        return {}
    deepseek_client.record_usage(
        "translate", CLAUDE_MODEL, {"prompt_tokens": resp.usage.input_tokens, "completion_tokens": resp.usage.output_tokens}
    )
    if resp.stop_reason == "refusal":
        return {}
    return _parse_tagged("".join(b.text for b in resp.content if b.type == "text"), len(texts))


def _llm_user_prompt(texts: List[str], context: Optional[Dict[str, str]]) -> str:
    """Book context + paragraphs tagged <p id="N"> so the reply maps back by id (shared by Claude and DeepSeek)."""
    ctx_lines = []
    if context and context.get("title"):
        ctx_lines.append(f"书名：{context['title']}")
    if context and context.get("author"):
        ctx_lines.append(f"作者：{context['author']}")
    body = "\n".join(f'<p id="{i}">{t}</p>' for i, t in enumerate(texts))
    return ("\n".join(ctx_lines) + "\n\n" if ctx_lines else "") + body


def _parse_tagged(out_text: str, n: int) -> Dict[int, str]:
    found = {}
    for m in re.finditer(r'<p id="(\d+)">(.*?)</p>', out_text, re.S):
        i = int(m.group(1))
        if 0 <= i < n and m.group(2).strip():
            found[i] = m.group(2).strip()
    return found


def _deepseek_key() -> Optional[str]:
    """The key deepseek_client resolves (env var, then the saved setting), or None while paused after an API error."""
    if time.time() < _deepseek_paused_until:
        return None
    return deepseek_client.api_key() or None


def _translate_batch_via_deepseek(
    texts: List[str], target_lang: str = "zh-CN", context: Optional[Dict[str, str]] = None
) -> Dict[int, str]:
    """DeepSeek chat completions (OpenAI-compatible REST), same prompt and <p id> format as Claude."""
    key = _deepseek_key()
    if key is None or not texts:
        return {}
    messages = [
        {"role": "system", "content": _CLAUDE_SYSTEM.format(target=_TARGET_NAMES.get(target_lang, target_lang))},
        {"role": "user", "content": _llm_user_prompt(texts, context)},
    ]
    try:
        content, _ = deepseek_client.chat(
            messages,
            max_tokens=min(8000, 1000 + sum(len(t) for t in texts) * 2),
            temperature=1.3,  # DeepSeek's recommended setting for translation
            feature="translate",
        )
    except Exception as e:  # HTTPError (402 no balance, 401 bad key, 429), URLError, timeout
        global _deepseek_paused_until
        _deepseek_paused_until = time.time() + 300
        logger.warning(f"DeepSeek translate failed, pausing DeepSeek for 5 min: {e}")
        return {}
    return _parse_tagged(content, len(texts))


def _translate_batch_via_apple(texts: List[str], source_lang: str = "auto", target_lang: str = "zh-CN") -> List[str]:
    """On-device Apple Translation (macOS 26). Empty list when the binary or language pack is missing."""
    if not texts or not APPLE_TRANSLATE_BIN.exists():
        return []
    try:
        proc = subprocess.run(
            [str(APPLE_TRANSLATE_BIN), source_lang, _APPLE_LANG.get(target_lang, target_lang)],
            input=json.dumps(texts, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
    except Exception as e:
        logger.debug(f"Apple translate failed: {e}")
        return []
    if proc.returncode != 0:
        logger.debug(f"Apple translate unavailable: {proc.stderr.decode('utf-8', 'ignore').strip()}")
        return []
    out = json.loads(proc.stdout.decode("utf-8"))
    return out if len(out) == len(texts) and all(out) else []


def _is_lookup(text: str) -> bool:
    """Word / short phrase lookups go to the dictionary engine (phonetic + senses), not the LLM."""
    return len(text.split()) <= 3 and len(text) <= 40 and not re.search(r"[.!?。！？]", text)


ENGINES = ("claude", "deepseek", "apple", "google", "mymemory")
LLM_ENGINES = ("claude", "deepseek")


def _llm_batch(engine: str, texts: List[str], target_lang: str, context) -> Dict[int, str]:
    fn = _translate_batch_via_claude if engine == "claude" else _translate_batch_via_deepseek
    return fn(texts, target_lang, context)


def _llm_available(engine: str) -> bool:
    return (_claude() if engine == "claude" else _deepseek_key()) is not None


def _engine_order(engine: str, lookup: bool) -> List[str]:
    """Chosen engine first, then the auto chain as fallback (the result reports which engine answered)."""
    auto = ["google", "mymemory"] if lookup else list(ENGINES)
    return [engine] + [e for e in auto if e != engine] if engine in ENGINES else auto


def _usable_cache(text: str, source_lang: str, target_lang: str, engine: str = "auto") -> Optional[Dict[str, Any]]:
    """Cached result from the chosen engine; in auto mode, passages cached by a non-LLM engine are redone once an LLM works."""
    cached = get_cached_translation(text, source_lang, target_lang)
    if not cached:
        return None
    if engine in ENGINES:
        return cached if cached.get("engine") == engine else None
    if not _is_lookup(text) and cached.get("engine") not in LLM_ENGINES and any(_llm_available(e) for e in LLM_ENGINES):
        return None
    return cached


def _translate_single(engine: str, text: str, source_lang: str, target_lang: str, context) -> Optional[Dict[str, Any]]:
    if engine == "google":
        return _translate_via_google(text, source_lang, target_lang)
    if engine == "mymemory":
        return _translate_via_mymemory(text, source_lang, target_lang)
    if engine in LLM_ENGINES:
        out = _llm_batch(engine, [text], target_lang, context).get(0)
    else:
        out = (_translate_batch_via_apple([text], source_lang, target_lang) or [None])[0]
    if not out:
        return None
    return {
        "ok": True,
        "original": text,
        "translation": out,
        "phonetic": "",
        "dict": [],
        "source_lang": source_lang,
        "target_lang": target_lang,
        "cached": False,
        "engine": engine,
    }


def _translate_via_mymemory(text: str, source_lang: str = "en", target_lang: str = "zh-CN") -> Optional[Dict[str, Any]]:
    sl = "en" if source_lang == "auto" else source_lang
    tl = target_lang
    pair = urllib.parse.quote(f"{sl}|{tl}", safe="|")
    quoted = urllib.parse.quote(text[:500])  # MyMemory free tier recommends <= 500 chars
    url = f"https://api.mymemory.translated.net/get?q={quoted}&langpair={pair}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            mm_trans = data.get("responseData", {}).get("translatedText") or ""
            if mm_trans and not mm_trans.startswith("MYMEMORY WARNING"):
                return {
                    "ok": True,
                    "original": text,
                    "translation": mm_trans.strip(),
                    "phonetic": "",
                    "dict": [],
                    "source_lang": sl,
                    "target_lang": tl,
                    "cached": False,
                    "engine": "mymemory",
                }
    except Exception as e:
        logger.debug(f"MyMemory translate request failed: {e}")
    return None


def translate_text(
    text: str,
    source_lang: str = "auto",
    target_lang: str = "zh-CN",
    context: Optional[Dict[str, str]] = None,
    engine: str = "auto",
) -> Dict[str, Any]:
    clean = text.strip()
    if not clean:
        return {"ok": False, "error": "待翻译文本为空"}

    # 1. Check local cache
    cached = _usable_cache(clean, source_lang, target_lang, engine)
    if cached:
        return cached

    # 2. Engines in order. Auto: passages go Claude -> DeepSeek -> Apple -> Google -> MyMemory; word lookups use
    #    Google first for its phonetic + dictionary senses. A chosen engine goes first, the rest are fallbacks.
    res = None
    for eng in _engine_order(engine, _is_lookup(clean)):
        res = _translate_single(eng, clean, source_lang, target_lang, context)
        if res:
            break

    if res and res.get("ok"):
        # Persist to local cache
        save_cached_translation(
            clean,
            res["translation"],
            source_lang=res.get("source_lang", source_lang),
            target_lang=target_lang,
            phonetic=res.get("phonetic", ""),
            dict_entries=res.get("dict"),
            engine=res.get("engine", "remote"),
        )
        return res

    return {"ok": False, "error": "网络连接超时或翻译服务暂不可用，请稍候重试"}


def book_context(book_id: Any) -> Optional[Dict[str, str]]:
    if not book_id:
        return None
    conn = get_conn()
    try:
        row = conn.execute("SELECT title, author FROM books WHERE id = ?", (int(book_id),)).fetchone()
        return {"title": row["title"] or "", "author": row["author"] or ""} if row else None
    except Exception:
        return None
    finally:
        conn.close()


def batch_translate_paragraphs(
    paragraphs: List[str],
    source_lang: str = "auto",
    target_lang: str = "zh-CN",
    context: Optional[Dict[str, str]] = None,
    engine: str = "auto",
) -> List[Dict[str, Any]]:
    """Translate a page of paragraphs: cache, then engines in order (Claude one call per chunk, Apple one call, web per paragraph)."""
    results: List[Dict[str, Any]] = [{"original": p, "translation": "", "ok": True, "engine": ""} for p in paragraphs]
    pending: List[int] = []
    for i, p in enumerate(paragraphs):
        p_clean = (p or "").strip()
        if not p_clean:
            continue
        cached = _usable_cache(p_clean, source_lang, target_lang, engine)
        if cached:
            results[i].update(translation=cached["translation"], engine=cached.get("engine", "cache"))
        else:
            pending.append(i)

    def fill(idx_list: List[int], translations: Dict[int, str], engine: str) -> List[int]:
        left = []
        for k, i in enumerate(idx_list):
            t = translations.get(k)
            if t:
                results[i].update(translation=t, engine=engine)
                save_cached_translation(paragraphs[i].strip(), t, source_lang, target_lang, engine=engine)
            else:
                left.append(i)
        return left

    for eng in _engine_order(engine, lookup=False):
        if not pending:
            break
        if eng in LLM_ENGINES and _llm_available(eng):
            # Chunks of ~12 paragraphs / 6k chars keep latency per call reasonable
            left: List[int] = []
            chunk: List[int] = []
            for i in pending + [-1]:
                if i == -1 or len(chunk) >= 12 or sum(len(paragraphs[j]) for j in chunk) + len(paragraphs[i]) > 6000:
                    if chunk:
                        texts = [paragraphs[j].strip() for j in chunk]
                        left += fill(chunk, _llm_batch(eng, texts, target_lang, context), eng)
                    chunk = []
                if i != -1:
                    chunk.append(i)
            pending = left
        elif eng == "apple":
            apple = _translate_batch_via_apple([paragraphs[i].strip() for i in pending], source_lang, target_lang)
            pending = fill(pending, dict(enumerate(apple)), "apple")
        elif eng in ("google", "mymemory"):
            web = {}
            for k, i in enumerate(pending):
                res = _translate_single(eng, paragraphs[i].strip(), source_lang, target_lang, context)
                if not res:
                    break  # a dead endpoint would cost its timeout on every paragraph; the next engine takes the rest
                web[k] = res["translation"]
            pending = fill(pending, web, eng)

    for i in pending:
        results[i]["ok"] = False
    return results
