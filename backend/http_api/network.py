"""Addresses this machine can be reached at: the LAN IP and, when Tailscale is up, its tailnet IP."""

import shutil
import socket
import subprocess
from typing import Optional

TAILSCALE_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
COMMAND_TIMEOUT_SECONDS = 2


def get_local_lan_ip() -> str:
    """Detect LAN IPv4 address on macOS / Linux."""
    for iface in ("en0", "en1"):
        try:
            ip = subprocess.run(
                ["ipconfig", "getifaddr", iface],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            ).stdout.strip()
            if ip:
                return ip
        except (OSError, subprocess.SubprocessError):
            break
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _tailscale_cli() -> Optional[str]:
    return shutil.which("tailscale") or (TAILSCALE_APP_CLI if shutil.which(TAILSCALE_APP_CLI) else None)


def _run_tailscale(*args: str) -> Optional[subprocess.CompletedProcess]:
    cli = _tailscale_cli()
    if not cli:
        return None
    try:
        return subprocess.run([cli, *args], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None


def is_tailscale_running() -> bool:
    """True when Tailscale is installed and connected; `tailscale status` exits non-zero when it is stopped."""
    result = _run_tailscale("status")
    return result is not None and result.returncode == 0


def get_tailscale_ip() -> Optional[str]:
    """This machine's tailnet IPv4, or None when Tailscale is missing or stopped.

    `tailscale ip -4` still prints the address while Tailscale is stopped, so the status is checked first.
    """
    if not is_tailscale_running():
        return None
    result = _run_tailscale("ip", "-4")
    ip = result.stdout.strip().splitlines()[0] if result and result.returncode == 0 and result.stdout.strip() else ""
    return ip or None
