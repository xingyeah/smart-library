"""
Search quality evaluation against the labeled query set in tests/search_eval/queries.json.

Metrics per query (top 10 results):
  nDCG@10  graded gain (grade2=3, grade1=1), ideal ranking computed from the whole library
  P@5      share of the top 5 with grade >= 1
  Hit@3    a grade-2 book is in the top 3
  Bad@5    number of known-wrong books in the top 5

Usage:
  .venv/bin/python backend/eval_search.py                      # shelf + describe
  .venv/bin/python backend/eval_search.py --mode shelf --only nl-ai-marketing -v
  .venv/bin/python backend/eval_search.py --save tests/search_eval/baseline.json
  .venv/bin/python backend/eval_search.py --compare tests/search_eval/baseline.json
  .venv/bin/python backend/eval_search.py --holdout             # Phase 0 regression cases, kept out of tuning
"""

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import get_conn  # noqa: E402
import search_engine as se  # noqa: E402

QUERIES_PATH = Path(__file__).resolve().parent.parent / "tests" / "search_eval" / "queries.json"
# DeepSeek-written queries labeled per book id (backend/eval_build.py); only human-verified ones count in the gate
GENERATED_PATH = QUERIES_PATH.parent / "generated_queries.json"
# Hard regression cases (search v2 Phase 0): never in the gate or in weight tuning, only in final checks
HOLDOUT_PATH = QUERIES_PATH.parent / "holdout_queries.json"
MODES = ("shelf", "describe")
K = 10


def load_queries(path: Path = QUERIES_PATH, generated_path: Optional[Path] = GENERATED_PATH) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        queries = json.load(f)["queries"]
    if generated_path and generated_path.exists():
        with open(generated_path, "r", encoding="utf-8") as f:
            queries += [q for q in json.load(f)["queries"] if q.get("judged")]
    for q in queries:
        for key in ("grade2", "grade1", "bad"):
            q["_" + key] = [re.compile(p, re.I) for p in q.get(key, [])]
        # Hand-labeled queries and human-verified generated ones gate quality; DeepSeek-only labels are reference
        q["_gate"] = "judged" not in q or bool(q.get("verified"))
    return queries


def book_text(b: Dict[str, Any]) -> str:
    return f"{b.get('title') or ''} | {b.get('author') or ''} | {b.get('category') or ''}"


def grade(q: Dict[str, Any], text: str, book_id: Any = None) -> int:
    if "judged" in q:  # pooled labels by book id; unjudged books count as irrelevant
        return int(q["judged"].get(str(book_id), 0))
    if any(rx.search(text) for rx in q["_grade2"]):
        return 2
    if any(rx.search(text) for rx in q["_grade1"]):
        return 1
    return 0


def is_bad(q: Dict[str, Any], text: str) -> bool:
    return any(rx.search(text) for rx in q["_bad"])


def dcg(grades: List[int]) -> float:
    gain = {2: 3.0, 1: 1.0, 0: 0.0}
    return sum(gain[g] / math.log2(i + 2) for i, g in enumerate(grades[:K]))


def load_corpus_texts() -> List[str]:
    conn = get_conn()
    try:
        return [book_text(dict(r)) for r in conn.execute("SELECT title, author, category FROM books")]
    finally:
        conn.close()


AI_RERANK = False  # --ai: DeepSeek reranks the top 20 (all judged in the eval pool) before scoring the top 10


def search(mode: str, query: str) -> List[Dict[str, Any]]:
    n = 20 if AI_RERANK else K
    if mode == "shelf":
        items = se.instant_search(q=query, page_size=n)["items"]
    else:
        # Production skips the bge cross-encoder when AI rerank is on
        items = se.recommend_by_description(query, top_k=n, cross_encoder=not AI_RERANK)["items"]
    if AI_RERANK:
        import ai_search

        items, _ = ai_search.rerank(query, items, top_n=20, feature="eval")
    return items[:K]


def evaluate(mode: str, queries: List[Dict[str, Any]], corpus: List[str]) -> Dict[str, Any]:
    per_query = []
    for q in queries:
        t0 = time.perf_counter()
        items = search(mode, q["query"])[:K]
        ms = (time.perf_counter() - t0) * 1000
        texts = [book_text(b) for b in items]
        grades = [grade(q, t, b.get("id")) for t, b in zip(texts, items)]
        if "judged" in q:
            ideal = sorted(q["judged"].values(), reverse=True)[:K]
        else:
            ideal = sorted((grade(q, t) for t in corpus), reverse=True)[:K]
        idcg = dcg(ideal)
        per_query.append(
            {
                "id": q["id"],
                "type": q["type"],
                "gate": q["_gate"],
                "ndcg": round(dcg(grades) / idcg, 4) if idcg else 0.0,
                "p5": round(sum(1 for g in grades[:5] if g >= 1) / 5, 2),
                "hit3": any(g == 2 for g in grades[:3]),
                "bad5": sum(1 for t in texts[:5] if is_bad(q, t)),
                "ms": round(ms),
                "top": [f"{g}|{(b.get('title') or '')[:40]}" for g, b in zip(grades, items)],
            }
        )
    res = summarize(per_query)
    res["gate"] = summarize([r for r in per_query if r["gate"]])
    res["queries"] = per_query
    return res


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = max(1, len(rows))
    return {
        "n": len(rows),
        "ndcg": round(sum(r["ndcg"] for r in rows) / n, 4),
        "p5": round(sum(r["p5"] for r in rows) / n, 4),
        "hit3": round(sum(1 for r in rows if r["hit3"]) / n, 4),
        "bad5": sum(r["bad5"] for r in rows),
        "p50_ms": sorted(r["ms"] for r in rows)[len(rows) // 2] if rows else 0,
    }


def _prepare_queries(queries: List[Dict[str, Any]], only: str, ai: bool) -> List[Dict[str, Any]]:
    global AI_RERANK
    AI_RERANK = ai
    return [q for q in queries if q["id"] == only or only in q["query"]] if only else queries


def run(modes=MODES, only: str = "", ai: bool = False) -> Dict[str, Any]:
    return evaluate_queries(modes, _prepare_queries(load_queries(), only, ai))


def run_holdout(modes=MODES, only: str = "", ai: bool = False) -> Dict[str, Any]:
    """The Phase 0 holdout cases alone; their GATE line is the holdout result."""
    return evaluate_queries(modes, _prepare_queries(load_queries(HOLDOUT_PATH, generated_path=None), only, ai))


def evaluate_queries(modes, queries: List[Dict[str, Any]]) -> Dict[str, Any]:
    se.warmup_search_engine()
    corpus = load_corpus_texts()
    return {m: evaluate(m, queries, corpus) for m in modes}


def print_report(report: Dict[str, Any], baseline: Dict[str, Any], verbose: bool) -> None:
    for mode, res in report.items():
        base = (baseline or {}).get(mode, {})
        base_q = {r["id"]: r for r in base.get("queries", [])}
        print(f"\n=== {mode} ===")
        print(f"{'query':<28}{'nDCG':>7}{'P@5':>6}{'Hit3':>6}{'Bad5':>6}{'ms':>7}  delta")
        for r in res["queries"]:
            b = base_q.get(r["id"])
            delta = f"{r['ndcg'] - b['ndcg']:+.3f}" if b else ""
            print(f"{r['id']:<28}{r['ndcg']:>7.3f}{r['p5']:>6.2f}{'Y' if r['hit3'] else '-':>6}{r['bad5']:>6}{r['ms']:>7}  {delta}")
            if verbose:
                for t in r["top"]:
                    print(f"      {t}")
        g = res["gate"]
        print(f"GATE ({g['n']} hand-labeled + verified) nDCG@10 {g['ndcg']:.4f} | Hit@3 {g['hit3']:.3f} | Bad@5 {g['bad5']}")
        line = f"MEAN ({res['n']} all) nDCG@10 {res['ndcg']:.4f} | P@5 {res['p5']:.3f} | Hit@3 {res['hit3']:.3f} | Bad@5 {res['bad5']} | p50 {res['p50_ms']}ms"
        if base:
            line += f"   (baseline nDCG {base['ndcg']:.4f}, Hit@3 {base['hit3']:.3f}, Bad@5 {base['bad5']})"
        print(line)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("shelf", "describe", "both"), default="both")
    ap.add_argument("--only", default="", help="query id or substring")
    ap.add_argument("--save", help="write report JSON (use as baseline)")
    ap.add_argument("--compare", help="baseline JSON to diff against")
    ap.add_argument("-v", "--verbose", action="store_true", help="print graded top 10 per query")
    ap.add_argument("--ai", action="store_true", help="apply DeepSeek rerank (costs ~CNY 0.005 per query, cached)")
    ap.add_argument("--holdout", action="store_true", help="evaluate only the holdout cases in holdout_queries.json")
    args = ap.parse_args()

    modes = MODES if args.mode == "both" else (args.mode,)
    report = run_holdout(modes, args.only, args.ai) if args.holdout else run(modes, args.only, args.ai)
    baseline = json.loads(Path(args.compare).read_text(encoding="utf-8")) if args.compare else {}
    print_report(report, baseline, args.verbose)
    if args.save:
        # Metrics only: the saved baseline is committed, book titles from the private library are not
        for res in report.values():
            for r in res["queries"]:
                r.pop("top", None)
        Path(args.save).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nsaved {args.save}")


if __name__ == "__main__":
    main()
