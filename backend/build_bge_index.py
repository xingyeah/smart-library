from concurrent.futures import ProcessPoolExecutor, as_completed
import json
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
    _worker_tokenizer.enable_truncation(max_length=144)
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


def build_book_passage(row: Any) -> str:
    title = (row["title"] or "").strip()
    subtitle = (row["subtitle"] or "").strip()
    author = (row["author"] or "").strip()
    category = (row["category"] or "").strip()
    summary = (row["summary"] or "").strip()
    toc_text = (row["toc_text"] or "").strip()

    head = f"《{title}》"
    if subtitle:
        head += f" ({subtitle[:80]})"
    if author and author != "未知作者":
        head += f" 作者：{author[:40]}"
    if category:
        head += f" 领域：{category}"

    body = summary[:240]
    toc_part = f" 章节：{toc_text[:100]}" if toc_text else ""
    return f"{head}。{body}{toc_part}"


_sync_lock = threading.Lock()


def sync_bge_m3_index(force_full: bool = False, batch_size: int = 48, reencode_ids=()) -> Dict[str, Any]:
    """
    Build or incrementally update the 1024-dim BAAI/bge-m3 dense vector index for all books.
    reencode_ids: books whose text changed (metadata edit), encoded again even if cached.
    """
    # An organize run and a metadata save can rebuild at once; both write the same .tmp.npy and meta file
    with _sync_lock:
        return _sync_bge_m3_index(force_full, batch_size, reencode_ids)


def _sync_bge_m3_index(force_full: bool, batch_size: int, reencode_ids) -> Dict[str, Any]:
    t0 = time.time()
    engine = get_bge_engine()
    if not engine.is_embedder_ready():
        return {"ok": False, "error": "bge-m3 ONNX model not found"}

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, subtitle, author, category, summary, toc_text FROM books ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()

    existing_map: Dict[int, np.ndarray] = {}
    if not force_full and BGE_VEC_NPY_PATH.exists() and BGE_VEC_META_PATH.exists():
        try:
            with open(BGE_VEC_META_PATH, "r", encoding="utf-8") as mf:
                meta = json.load(mf)
            old_ids = [int(x) for x in (meta.get("book_ids") or [])]
            old_mat = np.load(str(BGE_VEC_NPY_PATH))
            if old_mat.ndim == 2 and old_mat.shape[0] == len(old_ids) and old_mat.shape[1] == EMBED_DIM:
                for idx, bid in enumerate(old_ids):
                    existing_map[bid] = old_mat[idx]
        except Exception:
            existing_map = {}

    all_ids: List[int] = []
    missing_indices: List[int] = []
    missing_texts: List[str] = []
    final_mat = np.zeros((len(rows), EMBED_DIM), dtype=np.float32)

    for i, r in enumerate(rows):
        bid = int(r["id"])
        all_ids.append(bid)
        if bid in existing_map and bid not in reencode_ids:
            final_mat[i] = existing_map[bid]
        else:
            missing_indices.append(i)
            missing_texts.append(build_book_passage(r))

    total_to_encode = len(missing_texts)
    print(
        f"[bge-m3] Total books: {len(rows)} | Reused cached: {len(rows) - total_to_encode} | To encode: {total_to_encode}",
        flush=True,
    )

    if total_to_encode > 0:
        if total_to_encode <= 64:
            vecs = engine.encode_texts(missing_texts, max_length=144, batch_size=8)
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

    tmp_npy = BGE_VEC_NPY_PATH.with_suffix(".tmp.npy")
    np.save(str(tmp_npy), final_mat)
    tmp_npy.replace(BGE_VEC_NPY_PATH)

    with open(BGE_VEC_META_PATH, "w", encoding="utf-8") as mf:
        json.dump(
            {
                "count": len(all_ids),
                "dim": EMBED_DIM,
                "model": "BAAI/bge-m3",
                "book_ids": all_ids,
                "updated_at": time.time(),
            },
            mf,
        )

    # Reload in-memory index
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
