import logging
import threading
import time
from typing import Any, Dict, List, Optional

from archiver import run_organize_pipeline
from database import backup_database, get_conn
from http_api import state
from jobs import reindex_in_background, spawn_reindex

logger = logging.getLogger("http_api.background")
_bg_stop = threading.Event()


def stop_background_daemons() -> None:
    """Signal scheduler and backup daemons to stop waiting."""
    _bg_stop.set()


def start_background_organize(
    limit_per_dir: Optional[int] = None,
    run_type: str = "manual",
    enable_ocr: bool = False,
) -> Dict[str, Any]:
    """Trigger background library file scan and metadata ingestion."""
    with state._organize_lock:
        if state._organize_state["running"]:
            return {"status": "already_running", "progress": state._organize_state["progress"]}
        state._organize_state["running"] = True
        state._organize_state["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        state._organize_state["progress"] = {"status": "scanning"}

    def _progress_cb(stats: Dict[str, Any]) -> None:
        with state._organize_lock:
            state._organize_state["progress"] = dict(stats)

    def _run() -> None:
        try:
            res = run_organize_pipeline(
                limit_per_dir=limit_per_dir,
                run_type=run_type,
                enable_ocr=enable_ocr,
                max_workers=6,
                progress_callback=_progress_cb,
            )
            with state._organize_lock:
                state._organize_state["last_result"] = res
                state._organize_state["progress"] = res
        except Exception:
            logger.exception("Error in background organize pipeline")
        finally:
            with state._organize_lock:
                state._organize_state["running"] = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"status": "started", "run_type": run_type}


def scheduler_tick(now_ts: float) -> bool:
    """One scheduler check; returns True when it started a scheduled organize run."""
    conn = get_conn()
    try:
        cfg = conn.execute("SELECT * FROM user_interest_config WHERE id = 1").fetchone()
        if not cfg or not cfg["schedule_enabled"]:
            return False
        interval_sec = max(1, int(cfg["schedule_interval_hours"] or 24)) * 3600
        try:
            last_ts = float(cfg["last_scheduled_at"] or "")
        except ValueError:
            last_ts = None
        # Never recorded (or unreadable): start the clock now; the first run comes one interval later
        due = last_ts is not None and now_ts - last_ts >= interval_sec
        if last_ts is None or due:
            conn.execute(
                "UPDATE user_interest_config SET last_scheduled_at = ? WHERE id = 1",
                (str(now_ts),),
            )
            conn.commit()
    finally:
        conn.close()
    if due:
        start_background_organize(limit_per_dir=None, run_type="scheduled", enable_ocr=True)
    return due


def scheduler_daemon() -> None:
    """Periodic background scheduler that checks every 60s whether a scheduled book organization is due."""
    while not _bg_stop.is_set():
        if _bg_stop.wait(60):
            break
        try:
            scheduler_tick(time.time())
        except Exception:
            logger.exception("Scheduler tick failed in background daemon")


def backup_daemon() -> None:
    """Daily consistent DB snapshot into backups/ (first one immediately)."""
    while not _bg_stop.is_set():
        try:
            backup_database()
        except Exception:
            logger.exception("Database backup failed in background daemon")
        if _bg_stop.wait(24 * 3600):
            break
