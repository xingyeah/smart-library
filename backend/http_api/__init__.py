import logging
import sys
import threading
from http.server import ThreadingHTTPServer

from auto_summary_refresher import start_background_auto_summary_refresher
from enrichers.scheduler import start_background_slow_enricher
from database import init_db
from http_api import background, gate, responses, state
from http_api.network import get_local_lan_ip, get_tailscale_ip
from http_api.server_handler import LibraryRequestHandler
from search_engine import warmup_search_engine

logger = logging.getLogger("http_api")


def run_server(host: str = "0.0.0.0", port: int = 8765) -> None:
    """Initialize database and background threads, then serve HTTP on specified port."""
    init_db()
    state.LAN_TOKEN = state.load_or_create_token()

    threading.Thread(target=background.backup_daemon, daemon=True).start()
    threading.Thread(target=warmup_search_engine, daemon=True).start()
    threading.Thread(target=background.scheduler_daemon, daemon=True).start()
    start_background_slow_enricher()
    start_background_auto_summary_refresher()

    httpd = ThreadingHTTPServer((host, port), LibraryRequestHandler)
    lan_ip = get_local_lan_ip()

    print("=" * 60)
    print("📚 我的智能图书馆 (Smart Library) 服务已启动")
    print(f"   ▶ 本机访问:  http://127.0.0.1:{port}")
    if lan_ip and lan_ip != "127.0.0.1":
        if sys.stdout.isatty():
            lan_url = f"http://{lan_ip}:{port}/?token={state.LAN_TOKEN}"
        else:
            lan_url = f"http://{lan_ip}:{port}/?token=<口令见 data/.lan_token>"
        print(f"   ▶ 局域网访问: {lan_url} (手机/平板首次打开此地址，之后免口令)")
    tailscale_ip = get_tailscale_ip()
    if tailscale_ip:
        print(f"   ▶ Tailscale:  http://{tailscale_ip}:{port} (App 连上后自动在两个地址间切换)")
    else:
        print("   ⚠️ Tailscale 未运行，外网无法访问")
    print("=" * 60)
    httpd.serve_forever()


__all__ = [
    "LibraryRequestHandler",
    "run_server",
    "get_local_lan_ip",
    "state",
    "gate",
    "responses",
    "background",
]
