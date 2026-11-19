from concurrent.futures import ProcessPoolExecutor, as_completed
import fcntl
import hashlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from database import get_conn
from semantic_models import (
    BGE_M3_ONNX,
    BGE_M3_TOKENIZER,
    BGE_VEC_META_PATH,
    BGE_VEC_NPY_PATH,
    EMBED_DIM,
    get_bge_engine,
)

logger = logging.getLogger("build_bge_index")

EMBED_MAX_LENGTH = 512
PASSAGE_SCHEMA_VERSION = 2
SUMMARY_MAX_CHARS = 360
CORE_POINTS_MAX_CHARS = 180
TOC_MAX_CHARS = 80
MAX_KEYWORDS = 8
MAX_CORE_POINTS = 4

_worker_session = None
_worker_tokenizer = None
_worker_input_names: List[str] = []


def _init_worker() -> None:
    global _worker_session, _worker_tokenizer, _worker_input_names
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    _worker_session = ort.InferenceSession(
        str(BGE_M3_ONNX),
        sess_options=opts,
        providers=["CPUExecutionProvider"],
    )
    _worker_input_names = [inp.name for inp in _worker_session.get_inputs()]
    _worker_tokenizer = Tokenizer.from_file(str(BGE_M3_TOKENIZER))
    _worker_tokenizer.enable_truncation(max_length=EMBED_MAX_LENGTH)
    _worker_tokenizer.enable_padding(pad_id=1, pad_token="<pad>")


def _encode_worker_chunk(chunk: List[Tuple[int, str]]) -> Tuple[List[int], np.ndarray]:
    global _worker_session, _worker_tokenizer, _worker_input_names
    if _worker_session is None or _worker_tokenizer is None:
        _init_worker()
    # Sort by text length inside chunk to minimize padding waste
    sorted_pairs = sorted(chunk, key=lambda x: len(x[1]))
    indices = [p[0] for p in sorted_pairs]
    texts = [p[1] if p[1].strip() else " " for p in sorted_pairs]

    sub_batch = 8
    vecs_list: List[np.ndarray] = []
    for s in range(0, len(texts), sub_batch):
        sub_t = texts[s : s + sub_batch]
        encoded = _worker_tokenizer.encode_batch(sub_t)
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in _worker_input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)
        outputs = _worker_session.run(None, feeds)
        vec = None
        for out in outputs:
            if isinstance(out, np.ndarray) and out.ndim == 2 and out.shape[1] == EMBED_DIM:
                vec = out.astype(np.float32)
                break
        if vec is None:
            vec = outputs[0].astype(np.float32)[:, 0, :]
        norms = np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-12)
        vecs_list.append(vec / norms)

    return indices, np.vstack(vecs_list)


def _row_str(row: Any, key: str) -> str:
    try:
        val = row[key]
    except (KeyError, IndexError, TypeError):
        return ""
    return str(val).strip() if val is not None else ""


def _parse_json_list(raw: str) -> List[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x).strip() for x in parsed if str(x or "").strip()]


def compute_passage_hash(passage: str) -> str:
    payload = f"v{PASSAGE_SCHEMA_VERSION}:{EMBED_MAX_LENGTH}:{passage}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def build_book_passage(row: Any) -> str:
    title = _row_str(row, "clean_title") or _row_str(row, "title")
    subtitle = _row_str(row, "subtitle")
    author = _row_str(row, "author")
    cat = _row_str(row, "ai_category") or _row_str(row, "category")
    sub = _row_str(row, "subfield")
    summary = _row_str(row, "summary")
    toc_text = _row_str(row, "toc_text")

    head = f"《{title}》"
    if subtitle and subtitle not in title:
        head += f" ({subtitle[:60]})"
    if author and author != "未知作者":
        head += f" 作者：{author[:40]}"
    domain = "/".join(dict.fromkeys(x for x in (cat, sub) if x))
    if domain:
        head += f" 领域：{domain}"
    meta_bits = "/".join(x for x in (_row_str(row, "kind"), _row_str(row, "level")) if x)
    if meta_bits:
        head += f" 类型：{meta_bits}"

    parts = [head + "。"]
    kws = _parse_json_list(_row_str(row, "keywords"))[:MAX_KEYWORDS]
    if kws:
        parts.append("关键词：" + "、".join(kws) + "。")
    if summary:
        parts.append(summary[:SUMMARY_MAX_CHARS])
    if _row_str(row, "summary_source") == "ai_refreshed":
        cps = _parse_json_list(_row_str(row, "core_points"))[:MAX_CORE_POINTS]
        if cps:
            parts.append(" 看点：" + "；".join(cps)[:CORE_POINTS_MAX_CHARS])
    if toc_text:
        parts.append(" 章节：" + toc_text[:TOC_MAX_CHARS])
    return "".join(parts)


_BOOK_PASSAGE_SELECT = """
    SELECT b.id, b.title, b.subtitle, b.author, b.category, b.summary,
           b.summary_source, b.core_points, b.toc_text,
           m.clean_title, m.keywords, m.level, m.kind, m.ai_category, m.subfield
    FROM books b
    LEFT JOIN book_ai_meta m ON m.book_id = b.id
"""


def refresh_book_vector_in_memory(book_id: int, conn_factory: Any = None) -> bool:
    """Re-encode one active book and swap its vector in RAM immediately after AI enrichment."""
    conn = (conn_factory or get_conn)()
    try:
        row = conn.execute(
            f"{_BOOK_PASSAGE_SELECT} WHERE b.id = ? AND b.is_deleted = 0",
            (int(book_id),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return False
    engine = get_bge_engine()
    if not engine.is_embedder_ready() or not engine.load_vector_index():
        return False
    vecs = engine.encode_texts([build_book_passage(row)], max_length=EMBED_MAX_LENGTH, batch_size=1)
    return engine.upsert_book_vector(int(book_id), vecs[0])


_sync_lock = threading.Lock()


def sync_bge_m3_index(force_full: bool = False, batch_size: int = 48, reencode_ids=()) -> Dict[str, Any]:
    """
    Build or incrementally update the 1024-dim BAAI/bge-m3 dense vector index for all books.
    reencode_ids: books whose text changed (metadata edit), encoded again even if cached.
    """
    # An organize run and a metadata save can rebuild at once; both write the same .tmp.npy and meta file
    with _sync_lock:
        return _sync_bge_m3_index(force_full, batch_size, reencode_ids)


def _load_existing_bge_cache(force_full: bool) -> Tuple[Dict[int, np.ndarray], Dict[int, str]]:
    existing_map: Dict[int, np.ndarray] = {}
    existing_hashes: Dict[int, str] = {}
    if force_full or not (BGE_VEC_NPY_PATH.exists() and BGE_VEC_META_PATH.exists()):
        return existing_map, existing_hashes
    try:
        with open(BGE_VEC_META_PATH, "r", encoding="utf-8") as mf:
            meta = json.load(mf)
        old_ids = [int(x) for x in (meta.get("book_ids") or [])]
        old_hashes = meta.get("passage_hashes") or []
        old_mat = np.load(str(BGE_VEC_NPY_PATH))
        if old_mat.ndim == 2 and old_mat.shape[0] == len(old_ids) and old_mat.shape[1] == EMBED_DIM:
            for idx, bid in enumerate(old_ids):
                existing_map[bid] = old_mat[idx]
            if len(old_hashes) == len(old_ids):
                existing_hashes = {bid: str(h) for bid, h in zip(old_ids, old_hashes)}
    except Exception:
        return {}, {}
    return existing_map, existing_hashes


def _write_bge_outputs(final_mat: np.ndarray, all_ids: List[int], all_hashes: List[str]) -> None:
    tmp_npy = BGE_VEC_NPY_PATH.with_name(f".{BGE_VEC_NPY_PATH.name}.{os.getpid()}.tmp.npy")
    tmp_meta = BGE_VEC_META_PATH.with_name(f".{BGE_VEC_META_PATH.name}.{os.getpid()}.tmp.json")
    lock_path = BGE_VEC_META_PATH.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_fp:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
        try:
            np.save(str(tmp_npy), final_mat)
            tmp_npy.replace(BGE_VEC_NPY_PATH)
            with open(tmp_meta, "w", encoding="utf-8") as mf:
                json.dump(
                    {
                        "count": len(all_ids),
                        "dim": EMBED_DIM,
                        "max_length": EMBED_MAX_LENGTH,
                        "passage_version": PASSAGE_SCHEMA_VERSION,
                        "model": "BAAI/bge-m3",
                        "book_ids": all_ids,
                        "passage_hashes": all_hashes,
                        "updated_at": time.time(),
                    },
                    mf,
                )
            tmp_meta.replace(BGE_VEC_META_PATH)
        except BaseException:
            tmp_npy.unlink(missing_ok=True)
            tmp_meta.unlink(missing_ok=True)
            raise
        finally:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)


def _sync_bge_m3_index(force_full: bool, batch_size: int, reencode_ids) -> Dict[str, Any]:
    t0 = time.time()
    engine = get_bge_engine()
    if not engine.is_embedder_ready():
        return {"ok": False, "error": "bge-m3 ONNX model not found"}

    conn = get_conn()
    try:
        rows = conn.execute(
            f"{_BOOK_PASSAGE_SELECT} WHERE b.is_deleted = 0 ORDER BY b.id ASC"
        ).fetchall()
    finally:
        conn.close()

    existing_map, existing_hashes = _load_existing_bge_cache(force_full)
    all_ids: List[int] = []
    all_hashes: List[str] = []
    missing_indices: List[int] = []
    missing_texts: List[str] = []
    final_mat = np.zeros((len(rows), EMBED_DIM), dtype=np.float32)

    for i, r in enumerate(rows):
        bid = int(r["id"])
        passage = build_book_passage(r)
        p_hash = compute_passage_hash(passage)
        all_ids.append(bid)
        all_hashes.append(p_hash)
        cache_valid = bid in existing_map and bid not in reencode_ids and (
            bid not in existing_hashes or existing_hashes[bid] == p_hash
        )
        if cache_valid:
            final_mat[i] = existing_map[bid]
        else:
            missing_indices.append(i)
            missing_texts.append(passage)

    total_to_encode = len(missing_texts)
    print(
        f"[bge-m3] Total books: {len(rows)} | Reused cached: {len(rows) - total_to_encode} | To encode: {total_to_encode}",
        flush=True,
    )

    if total_to_encode > 0:
        if total_to_encode <= 64:
            vecs = engine.encode_texts(missing_texts, max_length=EMBED_MAX_LENGTH, batch_size=8)
            for idx_in_missing, row_i in enumerate(missing_indices):
                final_mat[row_i] = vecs[idx_in_missing]
        else:
            # Sort globally by text length so each worker chunk has near-identical token lengths
            paired_missing = list(zip(missing_indices, missing_texts))
            paired_missing.sort(key=lambda x: len(x[1]))
            chunk_size = 96
            chunks = [
                paired_missing[s : s + chunk_size]
                for s in range(0, total_to_encode, chunk_size)
            ]
            done = 0
            max_workers = min(4, os.cpu_count() or 4)
            with ProcessPoolExecutor(max_workers=max_workers, initializer=_init_worker) as pool:
                futures = [pool.submit(_encode_worker_chunk, c) for c in chunks]
                for fut in as_completed(futures):
                    row_indices, chunk_vecs = fut.result()
                    for r_i, v in zip(row_indices, chunk_vecs):
                        final_mat[r_i] = v
                    done += len(row_indices)
                    elapsed = time.time() - t0
                    rate = done / max(0.1, elapsed)
                    eta = (total_to_encode - done) / max(0.1, rate)
                    print(
                        f"  [bge-m3 多核编码进度] {done}/{total_to_encode} ({round(done*100/total_to_encode, 1)}%) | 速度: {round(rate, 1)} 本/秒 | 预计剩余: {round(eta, 1)}s",
                        flush=True,
                    )

    _write_bge_outputs(final_mat, all_ids, all_hashes)
    engine.load_vector_index()
    elapsed_total = round(time.time() - t0, 2)
    return {
        "ok": True,
        "total_books": len(all_ids),
        "newly_encoded": total_to_encode,
        "elapsed_sec": elapsed_total,
    }


if __name__ == "__main__":
    force = "--force" in sys.argv
    res = sync_bge_m3_index(force_full=force)
    print(json.dumps(res, ensure_ascii=False, indent=2))
