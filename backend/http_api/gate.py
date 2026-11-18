import hmac
import http.cookies
import urllib.parse
from typing import Any

from http_api import state
from http_api.responses import send_json


# Answers without a token so the iOS app can find which address is this server before sending the token anywhere.
PING_PATH = "/api/ping"


def check_lan_gate(handler: Any) -> bool:
    """
    Validate LAN access permissions and DNS-rebinding protection.
    Returns True if the request is permitted to proceed;
    Otherwise sends 302 redirect (token exchange) or 403 Forbidden and returns False.
    """
    if handler.command == "GET" and urllib.parse.urlparse(handler.path).path == PING_PATH:
        return True
    client_ip = handler.client_address[0]
    # Check loopback whitelist
    loopback_set = getattr(state, "_LOOPBACK", {"127.0.0.1", "::1", "localhost"})
    if client_ip in loopback_set:
        host = (handler.headers.get("Host") or "").lower()
        if host.startswith("["):
            host = host.split("]", 1)[0] + "]"
        else:
            host = host.split(":", 1)[0]
        if host in ("", "localhost", "127.0.0.1", "[::1]"):
            return True

    lan_token = getattr(state, "LAN_TOKEN", "")
    cookie = http.cookies.SimpleCookie(handler.headers.get("Cookie") or "").get("lib_token")
    if lan_token and cookie and hmac.compare_digest(cookie.value, lan_token):
        return True

    parsed = urllib.parse.urlparse(handler.path)
    tok = (urllib.parse.parse_qs(parsed.query).get("token") or [""])[0]
    if lan_token and tok and hmac.compare_digest(tok, lan_token):
        # Swap URL token for cookie so it disappears from the browser address bar
        handler.send_response(302)
        handler.send_header(
            "Set-Cookie",
            f"lib_token={lan_token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000",
        )
        kept = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if k != "token"]
        query = urllib.parse.urlencode(kept)
        safe_path = parsed.path or "/"
        if not safe_path.startswith("/") or safe_path.startswith("//") or "\\" in safe_path:
            safe_path = "/"
        handler.send_header("Location", safe_path + (f"?{query}" if query else ""))
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return False

    send_json(
        handler,
        {"error": "open the LAN address printed in the server terminal (it carries a token)"},
        403,
    )
    return False


def verify_json_content_type(handler: Any) -> bool:
    """
    Enforce application/json for POST requests.
    Rejects simple text/html/form submissions without CORS preflight.
    """
    content_type = (handler.headers.get("Content-Type") or "").lower()
    if not content_type.startswith("application/json"):
        send_json(handler, {"error": "Content-Type must be application/json"}, 415)
        return False
    return True
