from typing import Any, Dict, List

from http_api.network import get_local_lan_ip, get_tailscale_ip
from http_api.responses import send_json


def handle_get_connection(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """GET /api/connection: Base URLs the app may use for this server, LAN first; also its reachability probe."""
    port = handler.server.server_address[1]
    lan_ip = get_local_lan_ip()
    tailscale_ip = get_tailscale_ip()
    addresses = [f"http://{ip}:{port}" for ip in (lan_ip, tailscale_ip) if ip and ip != "127.0.0.1"]
    send_json(handler, {"ok": True, "addresses": addresses})
