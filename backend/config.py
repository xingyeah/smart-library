import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = Path(os.environ.get("LIBRARY_BASE_DIR") or _REPO_ROOT).expanduser().resolve()
DATA_DIR = Path(os.environ.get("LIBRARY_DATA_DIR") or (BASE_DIR / "data")).expanduser().resolve()
# This machine's own setup (paths, sources) lives in local/, a git repo of its own that is never pushed
_LOCAL_CONFIG_PATH = BASE_DIR / "local" / "config.json"
CONFIG_PATH = Path(
    os.environ.get("LIBRARY_CONFIG_PATH")
    or (_LOCAL_CONFIG_PATH if _LOCAL_CONFIG_PATH.exists() else DATA_DIR / "config.json")
).expanduser().resolve()

_DEFAULT_SOURCE_DIRS: List[Dict[str, Any]] = [
    {"path": str(BASE_DIR / "books" / "inbox"), "name": "待处理电子书", "trust_weight": 1.0, "priority": 1},
]


def load_runtime_config(config_path: Path) -> Dict[str, Any]:
    """Load optional JSON configuration overrides from disk."""
    if not config_path.exists():
        return {}
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to parse runtime config %s: %s", config_path, exc)
        return {}


def _resolve_dir(raw_value: Any, fallback: Path) -> Path:
    """Resolve configured path string relative to BASE_DIR when non-empty."""
    if not raw_value:
        return fallback
    candidate = Path(str(raw_value)).expanduser()
    return candidate if candidate.is_absolute() else (BASE_DIR / candidate).resolve()


def is_path_within(candidate: Any, base_dir: Path) -> bool:
    """Return True only when resolved candidate sits strictly inside resolved base_dir."""
    if not candidate:
        return False
    try:
        resolved = Path(candidate).resolve()
        base = base_dir.resolve()
        return resolved.is_relative_to(base) and resolved != base
    except OSError:
        return False


_RUNTIME_CFG = load_runtime_config(CONFIG_PATH)

LIBRARY_DIR = _resolve_dir(_RUNTIME_CFG.get("library_dir"), BASE_DIR / "Library")
DUPLICATES_DIR = _resolve_dir(_RUNTIME_CFG.get("duplicates_dir"), BASE_DIR / ".duplicates")
COVERS_DIR = _resolve_dir(_RUNTIME_CFG.get("covers_dir"), DATA_DIR / "covers")
CALIBRE_CFG_DIR = _resolve_dir(_RUNTIME_CFG.get("calibre_cfg_dir"), DATA_DIR / "calibre_cfg")
REMOVED_BOOKS_DIR = _resolve_dir(_RUNTIME_CFG.get("removed_books_dir"), DATA_DIR / "removed_books")
DB_PATH = _resolve_dir(_RUNTIME_CFG.get("db_path"), DATA_DIR / "library.db")
NATIVE_DYLIB_PATH = BASE_DIR / "backend" / "native" / "libmacbookai.dylib"

_cfg_sources = _RUNTIME_CFG.get("source_dirs")
SOURCE_DIRS: List[Dict[str, Any]] = (
    _cfg_sources if isinstance(_cfg_sources, list) else list(_DEFAULT_SOURCE_DIRS)
)
# Curated catalog CSVs read before the one in the repo root
INDEX_CSV_PATHS: List[Path] = [_resolve_dir(p, BASE_DIR) for p in (_RUNTIME_CFG.get("index_csv_paths") or [])]
GOOGLE_BOOKS_API_KEY: str = (
    os.environ.get("GOOGLE_BOOKS_API_KEY")
    or os.environ.get("GOOGLE_API_KEY")
    or str(_RUNTIME_CFG.get("google_books_api_key") or "").strip()
)

# An external rating from fewer votes is noise (Google Books gives 10/10 from one vote): no classic-score bonus
MIN_EXTERNAL_VOTES = 10

# Off in data/config.json while the Anthropic account has no credit; translation then uses the other engines
CLAUDE_TRANSLATE_ENABLED: bool = _RUNTIME_CFG.get("claude_translate", True) is not False

SUPPORTED_EXTENSIONS = {
    ".epub": 100,
    ".azw3": 85,
    ".pdf": 75,
    ".mobi": 65,
    ".azw": 60,
    ".djvu": 45,
    ".txt": 30,
}

for d in [LIBRARY_DIR, DUPLICATES_DIR, DATA_DIR, COVERS_DIR, CALIBRE_CFG_DIR, REMOVED_BOOKS_DIR]:
    d.mkdir(parents=True, exist_ok=True)
