import os
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from config import DATA_DIR
from jobs import _reindex_lock

# Central storage for all mutable runtime state in http_api.
# Same file as before the refactor: devices that already paired keep working.
LAN_TOKEN_FILE = DATA_DIR / "lan_token"

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# Mutexes
_organize_lock = threading.Lock()

# Organize pipeline state
_organize_state: Dict[str, Any] = {
    "running": False,
    "started_at": None,
    "progress": {},
    "last_result": None,
}


def load_or_create_token(path: Path = LAN_TOKEN_FILE) -> str:
    """Generate or retrieve persistent LAN access token."""
    if not path.exists():
        path.write_text(secrets.token_urlsafe(16))
        os.chmod(path, 0o600)  # the token is a credential: owner-only
    return path.read_text().strip()


# Set by run_server; None denies every non-loopback client. Importing this module must not write files.
LAN_TOKEN: Optional[str] = None
