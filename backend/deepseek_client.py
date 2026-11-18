"""Minimal DeepSeek chat-completions client (OpenAI-compatible REST over urllib, no extra dependency)."""

import datetime
import json
import logging
import os
import ssl
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from database import get_conn

logger = logging.getLogger("deepseek")

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
FLASH_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
PRO_MODEL = "deepseek-v4-pro"

# Daily spend cap for interactive features only (search rerank + translation); enrichment / eval are logged, not capped
DAILY_CAP_CNY = float(os.environ.get("AI_DAILY_CAP_CNY", "5"))
AUTO_REFRESH_CAP_CNY = float(os.environ.get("AI_AUTO_REFRESH_CAP_CNY", "2"))
CAPPED_FEATURES = ("search", "translate", "recommend", "refresh")
# CNY per 1M tokens at peak price (input cache-miss, output); off-peak is half, so estimates err high
_PRICE = {PRO_MODEL: (9.0, 27.0), "claude-haiku-5-5": (0.72, 3.6)}  # Haiku: USD 0.10 / 0.50 at ~7.2 CNY/USD
_FLASH_PRICE = (2.0, 8.0)


class DailyCapReached(RuntimeError):
    pass


def _today() -> str:
    return datetime.date.today().isoformat()


def today_cost(features=CAPPED_FEATURES) -> float:
    conn = get_conn()
    try:
        ph = ",".join("?" for _ in features)
        row = conn.execute(f"SELECT SUM(cost) FROM ai_usage WHERE day = ? AND feature IN ({ph})", [_today(), *features]).fetchone()
        return float(row[0] or 0.0)
    finally:
        conn.close()


def record_usage(feature: str, model: str, usage: Dict[str, Any]) -> None:
    tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    pin, pout = _PRICE.get(model, _FLASH_PRICE)
    cost = tin / 1e6 * pin + tout / 1e6 * pout
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO ai_usage (day, feature, calls, tokens_in, tokens_out, cost) VALUES (?, ?, 1, ?, ?, ?) "
            "ON CONFLICT(day, feature) DO UPDATE SET calls = calls + 1, tokens_in = tokens_in + excluded.tokens_in, "
            "tokens_out = tokens_out + excluded.tokens_out, cost = cost + excluded.cost",
            (_today(), feature, tin, tout, cost),
        )
        conn.commit()
    finally:
        conn.close()


def usage_summary() -> Dict[str, Any]:
    conn = get_conn()
    try:
        today = {r["feature"]: round(r["cost"], 4) for r in conn.execute("SELECT feature, cost FROM ai_usage WHERE day = ?", (_today(),))}
        totals = {r["feature"]: round(r["cost"] or 0, 2) for r in conn.execute("SELECT feature, SUM(cost) AS cost FROM ai_usage GROUP BY feature")}
    finally:
        conn.close()
    return {
        "today": today,
        "today_capped": round(sum(v for k, v in today.items() if k in CAPPED_FEATURES), 4),
        "cap": DAILY_CAP_CNY,
        "totals": totals,
        "configured": api_key() is not None,
    }


def _ssl_context() -> Optional[ssl.SSLContext]:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        # Fallback to system default SSL context
        return None


def _send_deepseek_request(req: urllib.request.Request, timeout: float) -> Dict[str, Any]:
    """Execute HTTPS request with verified TLS context."""
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_key() -> Optional[str]:
    k = os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k
    try:
        conn = get_conn()
        try:
            row = conn.execute("SELECT llm_api_key FROM user_interest_config WHERE id = 1").fetchone()
            if row and row["llm_api_key"]:
                return str(row["llm_api_key"]).strip()
        finally:
            conn.close()
    except Exception:
        # DB read failure should not crash key resolution
        logging.getLogger(__name__).debug("could not read the DeepSeek key from the database", exc_info=True)
    return None


def chat(
    messages: List[Dict[str, str]],
    model: str = FLASH_MODEL,
    thinking: bool = False,
    json_mode: bool = False,
    max_tokens: int = 4000,
    timeout: float = 60.0,
    temperature: Optional[float] = None,
    feature: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Returns (content, usage). Raises on HTTP / network errors so callers decide how to fall back.

    Thinking is on by default at DeepSeek; we turn it off unless asked (off is ~3x faster, 4x fewer output tokens).
    JSON mode needs the word "json" and an example in the prompt.
    feature: usage is logged under this name; the interactive ones in CAPPED_FEATURES raise DailyCapReached past the daily cap.
    """
    key = api_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    if feature in CAPPED_FEATURES and today_cost() >= DAILY_CAP_CNY:
        raise DailyCapReached(f"daily AI cap CNY {DAILY_CAP_CNY} reached")
    if feature == "auto_refresh" and today_cost(("auto_refresh",)) >= AUTO_REFRESH_CAP_CNY:
        raise DailyCapReached(f"daily auto_refresh AI cap CNY {AUTO_REFRESH_CAP_CNY} reached")
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if temperature is not None:
        body["temperature"] = temperature
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    data = _send_deepseek_request(req, timeout)
    choices = data.get("choices") or []
    content = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
    usage = data.get("usage") or {}
    if feature:
        try:
            record_usage(feature, model, usage)
        except Exception as e:  # accounting must never break the feature
            logger.warning(f"usage logging failed: {e}")
    return content, usage


def chat_json(messages: List[Dict[str, str]], **kwargs) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """chat() in JSON mode, parsed. Raises ValueError when the reply is not a JSON object."""
    content, usage = chat(messages, json_mode=True, **kwargs)
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("DeepSeek JSON reply is not an object")
    return parsed, usage
