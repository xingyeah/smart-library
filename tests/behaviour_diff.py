#!/usr/bin/env python3
"""
Behaviour diff: does this checkout rank and render exactly like another git ref?

Stored baselines went stale within hours (background enrichers keep changing scores, books get imported), so
nothing is stored. Both versions run side by side on the same library at the same moment and their outputs
are compared: search top-20 ids and scores for every eval query in both modes, and the sanitized reader HTML
of sample books in each format. Read-only: no HTTP requests, no writes to the library.

Usage:
  .venv/bin/python tests/behaviour_diff.py compare HEAD~1        # exit 1 and list every difference (~11 min)
  .venv/bin/python tests/behaviour_diff.py compare HEAD~1 --limit 20   # quick check on the first 20 queries
  .venv/bin/python tests/behaviour_diff.py dump out.json         # one version's raw output

Use it before committing a refactor; an intended ranking change will (correctly) show up as differences.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "tests" / "search_eval"
TOP_N = 20
SCORE_DECIMALS = 4
READER_FORMATS = ("epub", "azw3", "mobi", "txt")
BOOKS_PER_FORMAT = 3
CHAPTERS_PER_BOOK = 3
FALLBACK_GUIDE_TITLE = "全书内容与章节导览"  # the reader's page for a file it could not open


def load_eval_queries() -> List[str]:
    """Every distinct query text of the hand-written and generated eval sets."""
    queries: List[str] = []
    for name in ("queries.json", "generated_queries.json"):
        path = EVAL_DIR / name
        if path.exists():
            queries += [q["query"].strip() for q in json.loads(path.read_text(encoding="utf-8"))["queries"] if q.get("query")]
    return list(dict.fromkeys(queries))


def _ranked(items: List[Dict[str, Any]]) -> List[List[Any]]:
    return [[b["id"], round(float(b.get("raw_rank_score") or 0.0), SCORE_DECIMALS)] for b in items[:TOP_N]]


def dump_search(queries: List[str]) -> Dict[str, Any]:
    import search_engine

    search_engine.warmup_search_engine()
    out: Dict[str, Any] = {}
    for q in queries:
        out[f"shelf | {q}"] = _ranked(search_engine.instant_search(q=q, page=1, page_size=TOP_N)["items"])
        out[f"describe | {q}"] = _ranked(search_engine.recommend_by_description(q, top_k=TOP_N)["items"])
    return out


def dump_reader() -> Dict[str, Any]:
    import reader
    from database import get_conn

    out: Dict[str, Any] = {}
    conn = get_conn()
    try:
        for fmt in READER_FORMATS:
            rows = conn.execute("SELECT * FROM books WHERE primary_format = ? ORDER BY id LIMIT 60", (fmt,)).fetchall()
            readable = [r for r in rows if r["primary_path"] and os.path.exists(r["primary_path"])][:BOOKS_PER_FORMAT]
            for row in readable:
                for chapter_idx in range(CHAPTERS_PER_BOOK):
                    payload = reader.extract_book_reader_content(row, chapter_idx=chapter_idx)
                    key = f"reader | {fmt} book {row['id']} chapter {chapter_idx}"
                    if payload.get("chapter_title") == FALLBACK_GUIDE_TITLE:
                        out[key] = "UNREADABLE"  # visible in the dump: this sample proves nothing about the reader
                        break
                    html = payload.get("html") or ""
                    out[key] = [len(payload.get("toc") or []), hashlib.sha256(html.encode("utf-8")).hexdigest()]
    finally:
        conn.close()
    return out


def cmd_dump(out_path: str, backend_dir: str, limit: int = 0) -> None:
    sys.path.insert(0, backend_dir)  # config.BASE_DIR is absolute, so any checkout's code reads the same library
    queries = load_eval_queries()
    result = {**dump_search(queries[:limit] if limit else queries), **dump_reader()}
    Path(out_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def diff_dumps(base: Dict[str, Any], head: Dict[str, Any]) -> List[str]:
    """One line per key whose output differs or exists on one side only."""
    lines: List[str] = []
    for key in sorted(set(base) | set(head)):
        if key not in base or key not in head:
            lines.append(f"{key}: only in {'head' if key in head else 'base'}")
        elif base[key] != head[key]:
            lines.append(f"{key}: {_describe_change(base[key], head[key])}")
    return lines


def _describe_change(base: Any, head: Any) -> str:
    if not (isinstance(base, list) and base and isinstance(base[0], list)):
        return "output changed"
    base_ids, head_ids = [row[0] for row in base], [row[0] for row in head]
    if base_ids == head_ids:
        return "same books and order, scores changed"
    first = next((i for i, pair in enumerate(zip(base_ids, head_ids)) if pair[0] != pair[1]), min(len(base_ids), len(head_ids)))
    return f"ranking differs from position {first + 1} (base {base_ids[first:first + 3]}, head {head_ids[first:first + 3]})"


def _link_untracked_native_builds(worktree: Path) -> None:
    """Compiled helpers (the on-device translator) are not in git; without them the base side would search
    English queries untranslated and every such query would show up as a false difference."""
    native = REPO_ROOT / "backend" / "native"
    for built in native.iterdir():
        target = worktree / "backend" / "native" / built.name
        if not target.exists():
            target.symlink_to(built)


def compare_with_ref(base_ref: str, limit: int = 0) -> List[str]:
    """Run this checkout and `base_ref` at the same time and return their differences.
    limit: only the first N eval queries (a quick check while iterating; 0 means all, about 11 minutes)."""
    python = sys.executable
    script = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "base"
        subprocess.run(["git", "-C", str(REPO_ROOT), "worktree", "add", "--detach", str(worktree), base_ref],
                       check=True, capture_output=True)
        try:
            _link_untracked_native_builds(worktree)
            dumps = {"base": (Path(tmp) / "base.json", worktree / "backend"), "head": (Path(tmp) / "head.json", REPO_ROOT / "backend")}
            # Started together: background jobs keep changing scores, so the two runs must see the same data
            procs = [subprocess.Popen([python, script, "dump", str(out), "--backend", str(backend), "--limit", str(limit)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for out, backend in dumps.values()]
            for proc, name in zip(procs, dumps):
                _, err = proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(f"{name} dump failed: {err.decode('utf-8', 'ignore')[-600:]}")
            base, head = (json.loads(out.read_text(encoding="utf-8")) for out, _ in dumps.values())
        finally:
            subprocess.run(["git", "-C", str(REPO_ROOT), "worktree", "remove", "--force", str(worktree)], capture_output=True)
    unreadable = sum(1 for v in head.values() if v == "UNREADABLE")
    print(f"compared {len(head)} outputs against {base_ref}" + (f" ({unreadable} reader samples unreadable)" if unreadable else ""))
    return diff_dumps(base, head)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    dump = sub.add_parser("dump")
    dump.add_argument("out")
    dump.add_argument("--backend", default=str(REPO_ROOT / "backend"))
    dump.add_argument("--limit", type=int, default=0)
    compare = sub.add_parser("compare")
    compare.add_argument("base_ref")
    compare.add_argument("--limit", type=int, default=0, help="only the first N eval queries")
    args = ap.parse_args()
    if args.cmd == "dump":
        cmd_dump(args.out, args.backend, args.limit)
        return
    differences = compare_with_ref(args.base_ref, args.limit)
    for line in differences:
        print(line)
    print(f"{len(differences)} differences")
    sys.exit(1 if differences else 0)


if __name__ == "__main__":
    main()
