import logging
from collections import OrderedDict
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from config import DATA_DIR

MODELS_DIR = DATA_DIR / "models"
BGE_M3_DIR = MODELS_DIR / "bge-m3"
BGE_M3_ONNX = BGE_M3_DIR / "onnx" / "sentence_transformers_quantized.onnx"
BGE_M3_TOKENIZER = BGE_M3_DIR / "tokenizer.json"

RERANKER_DIR = MODELS_DIR / "bge-reranker-v2-m3"
RERANKER_ONNX = RERANKER_DIR / "onnx" / "model_quantized.onnx"
RERANKER_TOKENIZER = RERANKER_DIR / "tokenizer.json"

BGE_VEC_NPY_PATH = DATA_DIR / "bge_m3_vectors.npy"
BGE_VEC_META_PATH = DATA_DIR / "bge_m3_meta.json"
EMBED_DIM = 1024


MIN_VECTOR_NORM = 1e-12  # a vector shorter than this has no direction to compare


def _id_array(book_ids: Any) -> np.ndarray:
    return np.fromiter(book_ids, dtype=np.int64, count=len(book_ids))


class _LRUCache:
    def __init__(self, capacity: int = 256) -> None:
        self.capacity = capacity
        self.cache: OrderedDict[Any, Any] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: Any) -> Optional[Any]:
        with self.lock:
            if key not in self.cache:
                return None
            self.cache.move_to_end(key)
            return self.cache[key]

    def put(self, key: Any, val: Any) -> None:
        with self.lock:
            self.cache[key] = val
            self.cache.move_to_end(key)
            if len(self.cache) > self.capacity:
                self.cache.popitem(last=False)


class BgeM3Engine:
    """
    Local BAAI/bge-m3 1024-dim Multilingual Dense Embedding + BAAI/bge-reranker-v2-m3 Cross-Encoder
    powered by ONNX Runtime on Apple Silicon.
    """

    def __init__(self) -> None:
        self._embed_session: Optional[ort.InferenceSession] = None
        self._embed_tokenizer: Optional[Tokenizer] = None
        self._embed_input_names: List[str] = []
        self._embed_lock = threading.Lock()

        self._rerank_session: Optional[ort.InferenceSession] = None
        self._rerank_tokenizer: Optional[Tokenizer] = None
        self._rerank_input_names: List[str] = []
        self._rerank_lock = threading.Lock()

        self._query_vec_cache = _LRUCache(capacity=512)
        self._rerank_cache = _LRUCache(capacity=256)

        self._index_lock = threading.Lock()
        self._index_mtime: Tuple[int, int] = (0, 0)
        self._book_ids: List[int] = []
        # (matrix, book ids per row) swapped in one assignment: a search on another thread reads a matching pair
        self._index: Tuple[Optional[np.ndarray], np.ndarray] = (None, np.zeros(0, dtype=np.int64))
        self._index_snap: Tuple[Optional[np.ndarray], np.ndarray, Dict[int, int]] = (
            None, np.zeros(0, dtype=np.int64), {}
        )
        self._id_to_row: Dict[int, int] = {}
        self._matrix: Optional[np.ndarray] = None  # shape (N, 1024) float32 L2-normalized

    def is_embedder_ready(self) -> bool:
        return (
            BGE_M3_ONNX.exists()
            and BGE_M3_ONNX.stat().st_size > 100_000_000
            and BGE_M3_TOKENIZER.exists()
        )

    def is_reranker_ready(self) -> bool:
        return (
            RERANKER_ONNX.exists()
            and RERANKER_ONNX.stat().st_size > 100_000_000
            and RERANKER_TOKENIZER.exists()
        )

    def _ensure_embedder(self) -> bool:
        if self._embed_session is not None and self._embed_tokenizer is not None:
            return True
        with self._embed_lock:
            if self._embed_session is not None and self._embed_tokenizer is not None:
                return True
            if not self.is_embedder_ready():
                return False
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = min(8, os.cpu_count() or 4)  # 8 P-cores on M1 Max: ~30% faster than 4
            opts.inter_op_num_threads = 1
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._embed_session = ort.InferenceSession(
                str(BGE_M3_ONNX),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            self._embed_input_names = [inp.name for inp in self._embed_session.get_inputs()]
            tok = Tokenizer.from_file(str(BGE_M3_TOKENIZER))
            self._embed_tokenizer = tok
            return True

    def _ensure_reranker(self) -> bool:
        if self._rerank_session is not None and self._rerank_tokenizer is not None:
            return True
        with self._rerank_lock:
            if self._rerank_session is not None and self._rerank_tokenizer is not None:
                return True
            if not self.is_reranker_ready():
                return False
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = min(8, os.cpu_count() or 4)  # 8 P-cores on M1 Max: ~30% faster than 4
            opts.inter_op_num_threads = 1
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._rerank_session = ort.InferenceSession(
                str(RERANKER_ONNX),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            self._rerank_input_names = [inp.name for inp in self._rerank_session.get_inputs()]
            tok = Tokenizer.from_file(str(RERANKER_TOKENIZER))
            self._rerank_tokenizer = tok
            return True

    def encode_texts(
        self, texts: List[str], max_length: int = 256, batch_size: int = 32
    ) -> np.ndarray:
        """Encode a list of texts into L2-normalized (N, 1024) float32 embeddings."""
        if not texts:
            return np.zeros((0, EMBED_DIM), dtype=np.float32)
        if not self._ensure_embedder():
            raise RuntimeError("bge-m3 ONNX model is not ready")

        assert self._embed_tokenizer is not None
        assert self._embed_session is not None

        all_vecs: List[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            batch = [t if t.strip() else " " for t in texts[start : start + batch_size]]
            with self._embed_lock:
                self._embed_tokenizer.enable_truncation(max_length=max_length)
                self._embed_tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
                encoded = self._embed_tokenizer.encode_batch(batch)

            input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
            attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
            feeds: Dict[str, np.ndarray] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            }
            if "token_type_ids" in self._embed_input_names:
                feeds["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)

            outputs = self._embed_session.run(None, feeds)
            # sentence_transformers_quantized.onnx outputs (token_embeddings, sentence_embedding)
            # or (last_hidden_state, ...)
            vec: Optional[np.ndarray] = None
            for out in outputs:
                if isinstance(out, np.ndarray) and out.ndim == 2 and out.shape[1] == EMBED_DIM:
                    vec = out.astype(np.float32)
                    break
            if vec is None:
                # CLS pooling fallback on 3D hidden state (B, L, 1024)
                hidden = outputs[0].astype(np.float32)
                vec = hidden[:, 0, :]

            norms = np.linalg.norm(vec, axis=1, keepdims=True)
            norms = np.maximum(norms, 1e-12)
            vec = vec / norms
            all_vecs.append(vec)

        return np.vstack(all_vecs)

    def encode_query(self, query: str) -> Optional[np.ndarray]:
        q = (query or "").strip()
        if not q or not self.is_embedder_ready():
            return None
        cached = self._query_vec_cache.get(q)
        if cached is not None:
            return cached
        vec = self.encode_texts([q], max_length=128, batch_size=1)[0]
        self._query_vec_cache.put(q, vec)
        return vec

    def load_vector_index(self) -> bool:
        """Load contiguous (N, 1024) float32 matrix from disk if updated."""
        if not BGE_VEC_NPY_PATH.exists() or not BGE_VEC_META_PATH.exists():
            return False
        mtime = (BGE_VEC_NPY_PATH.stat().st_mtime_ns, BGE_VEC_META_PATH.stat().st_mtime_ns)
        if self._matrix is not None and mtime == self._index_mtime:
            return True
        with self._index_lock:
            mtime = (BGE_VEC_NPY_PATH.stat().st_mtime_ns, BGE_VEC_META_PATH.stat().st_mtime_ns)
            if self._matrix is not None and mtime == self._index_mtime:
                return True
            try:
                with open(BGE_VEC_META_PATH, "r", encoding="utf-8") as mf:
                    meta = json.load(mf)
                book_ids = [int(x) for x in (meta.get("book_ids") or [])]
                mat = np.load(str(BGE_VEC_NPY_PATH))
                if mat.ndim == 2 and mat.shape[0] == len(book_ids) and mat.shape[1] == EMBED_DIM:
                    norm_mat = np.ascontiguousarray(mat, dtype=np.float32)
                    row_arr = np.array(book_ids, dtype=np.int64)
                    id_to_row = {bid: idx for idx, bid in enumerate(book_ids)}
                    self._matrix = norm_mat
                    self._book_ids = book_ids
                    self._id_to_row = id_to_row
                    self._index = (norm_mat, row_arr)
                    self._index_snap = (norm_mat, row_arr, id_to_row)
                    self._index_mtime = mtime
                    return True
            except Exception:
                # Memory-mapped vector index reloading fails when index file is being rebuilt or corrupted
                logging.getLogger(__name__).warning("vector index could not be loaded; semantic search is off until it reloads", exc_info=True)
            return False

    def dense_search(
        self,
        query: str,
        allowed_ids: Optional[set] = None,
        top_k: int = 150,
    ) -> Tuple[Dict[int, float], Dict[int, int]]:
        """
        Compute exact cosine similarity between query and all books in <1.5ms.
        Returns (scores_by_book_id, ranks_by_book_id).
        """
        if not self.load_vector_index():
            return {}, {}
        matrix, row_book_ids, _ = self._index_snap
        if matrix is None:
            return {}, {}
        q_vec = self.encode_query(query)
        if q_vec is None:
            return {}, {}

        sims = np.dot(matrix, q_vec)  # (N,)
        is_cand = np.isfinite(sims)
        if allowed_ids is not None:
            is_cand &= np.isin(row_book_ids, _id_array(allowed_ids))
        hit_count = min(top_k * 3, int(is_cand.sum()))
        if hit_count <= 0:
            return {}, {}

        sort_keys = np.where(is_cand, -sims, np.inf)
        best = np.argpartition(sort_keys, hit_count - 1)[:hit_count]
        best = best[np.lexsort((best, sort_keys[best]))]
        scores_by_id: Dict[int, float] = {}
        ranks_by_id: Dict[int, int] = {}
        for rank, idx in enumerate(best, start=1):
            bid = int(row_book_ids[idx])
            scores_by_id[bid] = float(sims[idx])
            if rank <= top_k:
                ranks_by_id[bid] = rank
        return scores_by_id, ranks_by_id

    def dense_search_by_vector(
        self,
        q_vec: np.ndarray,
        exclude_ids: Optional[set] = None,
        allowed_ids: Optional[set] = None,
        top_k: int = 100,
    ) -> List[Tuple[int, float]]:
        """The `top_k` books nearest `q_vec` by cosine, best first, as (book id, cosine). Books in `exclude_ids`,
        outside `allowed_ids`, or whose stored vector is broken are skipped, so fewer than `top_k` come back only
        when fewer qualify. Empty when the index is missing or `q_vec` has NaN or no direction."""
        if not self.load_vector_index():
            return []
        matrix, row_book_ids = self._index
        if matrix is None:
            return []
        norm = float(np.linalg.norm(q_vec))
        if not np.isfinite(norm) or norm < MIN_VECTOR_NORM:
            return []
        sims = np.dot(matrix, (q_vec / norm).astype(np.float32))
        is_candidate = np.isfinite(sims)
        if exclude_ids:
            is_candidate &= ~np.isin(row_book_ids, _id_array(exclude_ids))
        if allowed_ids is not None:
            is_candidate &= np.isin(row_book_ids, _id_array(allowed_ids))
        hit_count = min(top_k, int(is_candidate.sum()))
        if hit_count <= 0:
            return []
        sort_keys = np.where(is_candidate, -sims, np.inf)
        # argpartition finds the best `hit_count` in O(N); only those few are then sorted
        best = np.argpartition(sort_keys, hit_count - 1)[:hit_count]
        best = best[np.lexsort((best, sort_keys[best]))]  # equal cosines: the earlier row first, so results repeat
        return [(int(row_book_ids[row]), float(sims[row])) for row in best]

    def get_book_matrix(self) -> Tuple[np.ndarray, np.ndarray]:
        """(book ids, matrix): row i of the (N, EMBED_DIM) unit-vector matrix belongs to book ids[i]. Both empty
        when the index cannot be loaded. Read only: the matrix is the engine's own."""
        matrix, row_book_ids = self._index if self.load_vector_index() else (None, None)
        if matrix is None:
            return np.zeros(0, dtype=np.int64), np.zeros((0, EMBED_DIM), dtype=np.float32)
        return row_book_ids, matrix

    def get_book_vectors(self, book_ids: List[int]) -> Tuple[List[int], np.ndarray]:
        """Retrieve normalized vectors for a list of book IDs."""
        if not self.load_vector_index():
            return [], np.zeros((0, EMBED_DIM), dtype=np.float32)
        matrix, _, id_to_row = self._index_snap
        if matrix is None:
            return [], np.zeros((0, EMBED_DIM), dtype=np.float32)
        valid_ids: List[int] = []
        rows: List[int] = []
        for bid in book_ids:
            r_idx = id_to_row.get(int(bid))
            if r_idx is not None:
                valid_ids.append(int(bid))
                rows.append(r_idx)
        if not rows:
            return [], np.zeros((0, EMBED_DIM), dtype=np.float32)
        return valid_ids, matrix[rows]

    def upsert_book_vector(self, book_id: int, vector: np.ndarray) -> bool:
        """Atomically update or append one book's normalized vector in memory."""
        vec = np.asarray(vector, dtype=np.float32).reshape(-1)
        if vec.shape[0] != EMBED_DIM or not np.all(np.isfinite(vec)):
            return False
        norm = float(np.linalg.norm(vec))
        if norm < MIN_VECTOR_NORM:
            return False
        unit = (vec / norm).astype(np.float32)
        bid = int(book_id)
        with self._index_lock:
            if self._matrix is None:
                return False
            row = self._id_to_row.get(bid)
            if row is not None:
                new_mat = self._matrix.copy()
                new_mat[row] = unit
                new_ids = list(self._book_ids)
                new_row_arr = self._index[1]
                new_id_to_row = dict(self._id_to_row)
            else:
                new_mat = np.ascontiguousarray(np.vstack([self._matrix, unit[None, :]]), dtype=np.float32)
                new_ids = [*self._book_ids, bid]
                new_row_arr = np.array(new_ids, dtype=np.int64)
                new_id_to_row = {**self._id_to_row, bid: len(new_ids) - 1}
            self._matrix = new_mat
            self._book_ids = new_ids
            self._id_to_row = new_id_to_row
            self._index = (new_mat, new_row_arr)
            self._index_snap = (new_mat, new_row_arr, new_id_to_row)
            return True

    def rerank_pairs(
        self, query: str, docs: List[Tuple[int, str]], max_length: int = 128, batch_size: int = 4
    ) -> Dict[int, float]:
        """
        Cross-Encoder reranking via BAAI/bge-reranker-v2-m3.
        docs is a list of (book_id, doc_passage).
        Returns {book_id: probability_in_0_1}.
        """
        if not docs or not self._ensure_reranker():
            return {}
        q = query.strip()
        cache_key = (q, tuple(bid for bid, _ in docs))
        cached = self._rerank_cache.get(cache_key)
        if cached is not None:
            return cached

        assert self._rerank_tokenizer is not None
        assert self._rerank_session is not None

        # Sort docs by length so each mini-batch of 4 has minimal padding overhead
        sorted_docs = sorted(docs, key=lambda x: len(x[1]))

        scores_by_id: Dict[int, float] = {}
        for start in range(0, len(sorted_docs), batch_size):
            sub = sorted_docs[start : start + batch_size]
            pairs = [(q, text if text.strip() else " ") for _, text in sub]
            with self._rerank_lock:
                self._rerank_tokenizer.enable_truncation(max_length=max_length)
                self._rerank_tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
                encoded = self._rerank_tokenizer.encode_batch(pairs)

            input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
            attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
            feeds: Dict[str, np.ndarray] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            }
            if "token_type_ids" in self._rerank_input_names:
                feeds["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)

            logits = self._rerank_session.run(None, feeds)[0]
            logits_flat = np.asarray(logits, dtype=np.float32).reshape(-1)
            probs = 1.0 / (1.0 + np.exp(-logits_flat))
            for (bid, _), prob in zip(sub, probs):
                scores_by_id[int(bid)] = float(prob)

        self._rerank_cache.put(cache_key, scores_by_id)
        return scores_by_id


_engine_instance: Optional[BgeM3Engine] = None
_engine_lock = threading.Lock()


def get_bge_engine() -> BgeM3Engine:
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = BgeM3Engine()
    return _engine_instance
