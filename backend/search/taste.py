"""
Scores every book by how close it sits to the nearest of a few source vectors: the books a reader marked, or one
phrase of their interest prompt.

Cosines are taken after subtracting the library's mean vector. bge-m3 vectors all lean the same way (two unrelated
books already have a cosine near 0.6), and the mean is that shared lean; without it the differences that carry
taste are a small part of every score. A per-category mean of the liked books, used before, loses them altogether:
on the offline evaluation (backend/eval_recommend.py, copies of liked books set aside) it found 5.7% of hidden
liked books in the top 100 at a median rank of 2912; this finds about 9% at a median rank near 1500.
"""

from typing import NamedTuple, Optional, Sequence, Tuple

import numpy as np

NEAREST_LIKED_BOOKS = 3
# Score = PEAK_WEIGHT x the best cosine + the rest x the mean of the nearest few. The mean alone divides a single
# close match by the neighbour count, so one liked book on a niche topic loses to many loosely similar ones; the
# best alone ignores agreement. On the offline evaluation every weight from 0 to 1 lands within noise of the
# others (recall@100 8.1% to 9.3% on 311 books), so the middle is taken.
PEAK_WEIGHT = 0.5
# A book this close to a liked one is taken for the same work in another edition, script or language (titles do
# not show it: '小岛经济学' and '小島經濟學', '超预测' and 'Superforecasting'). Of the 70 unmarked books at or above
# it on this library about 50 are such copies; the rest are mostly other books by the same author.
# HACK(same work by cosine): a cosine cannot tell a copy from a sibling; drop once copies share one book record
SAME_WORK_COSINE = 0.6
SCORE_CHUNK_ROWS = 8192  # bounds the (rows x sources) block held at once: 11 MB for 338 sources
# A row or source this close to the library mean has no direction left: its centred norm is within the float32
# rounding of the subtraction below, and dividing by it would turn that rounding into a score
MIN_CENTRED_NORM = 1e-3

# (matrix, mean, centred row norms) of the matrix scored last; the engine keeps one matrix until the index reloads
_centering_cache: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]] = None


def _centering(matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """(mean row, norm of every row after subtracting it). Rows with NaN or inf are left out of the mean; they and
    rows within MIN_CENTRED_NORM of the mean get a NaN norm, which scores them -inf. No centred copy of the matrix
    is made: the norm comes from |m|^2 - 2 m.mean + |mean|^2."""
    global _centering_cache
    cached = _centering_cache
    if cached is not None and cached[0] is matrix:
        return cached[1], cached[2]
    squared_norms = np.einsum("ij,ij->i", matrix, matrix)
    is_finite = np.isfinite(squared_norms)
    mean = (matrix if is_finite.all() else matrix[is_finite]).mean(axis=0)
    centred_squared = squared_norms - 2.0 * (matrix @ mean) + float(mean @ mean)
    with np.errstate(invalid="ignore"):
        norms = np.sqrt(centred_squared)  # NaN where rounding took a tiny square below zero
    norms[~(norms >= MIN_CENTRED_NORM)] = np.nan  # written with ~ so that a NaN norm is caught too
    _centering_cache = (matrix, mean, norms)
    return mean, norms


def centred_unit_rows(matrix: np.ndarray, rows: Sequence[int]) -> np.ndarray:
    """The given rows of `matrix` with the library mean subtracted, as unit vectors: the dot product of two is
    the centred cosine of the two books. A row with a broken vector or no direction comes back as zeros, so it is
    like nothing."""
    mean, row_norms = _centering(matrix)
    index = list(rows)
    units = (matrix[index] - mean) / row_norms[index, None]
    return np.nan_to_num(units, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


class SourceScores(NamedTuple):
    """Per row of the matrix: the blended score, the index of the nearest source, and the cosine to it."""

    scores: np.ndarray
    nearest: np.ndarray
    peak: np.ndarray


def nearest_source_scores(matrix: np.ndarray, source_vectors: np.ndarray, neighbours: int) -> SourceScores:
    """For each row of `matrix`: a blend of its best centred cosine to a row of `source_vectors` and the mean over
    its `neighbours` nearest ones (all of them when there are fewer; negative cosines count as zero in the mean),
    the index of the single nearest source, and that best cosine itself.

    A row with a broken vector or no direction of its own scores -inf, as does every row when no source vector is
    usable (none given, or all NaN or on the library mean); scores and peaks are never NaN."""
    scores = np.full(len(matrix), -np.inf, dtype=np.float32)
    peak = scores.copy()
    nearest = np.zeros(len(matrix), dtype=np.int64)
    if len(matrix) == 0 or len(source_vectors) == 0:
        return SourceScores(scores, nearest, peak)
    mean, row_norms = _centering(matrix)
    centred_sources = source_vectors - mean
    source_norms = np.linalg.norm(centred_sources, axis=1)
    usable = np.flatnonzero(np.isfinite(source_norms) & (source_norms >= MIN_CENTRED_NORM))
    if len(usable) == 0:
        return SourceScores(scores, nearest, peak)
    unit_sources = (centred_sources[usable] / source_norms[usable, None]).astype(np.float32)
    mean_dot_sources = mean @ unit_sources.T
    kept = min(neighbours, len(usable))
    for start in range(0, len(matrix), SCORE_CHUNK_ROWS):
        rows = slice(start, start + SCORE_CHUNK_ROWS)
        cosines = matrix[rows] @ unit_sources.T  # the one block per chunk; everything below works inside it
        cosines -= mean_dot_sources
        cosines /= row_norms[rows, None]
        # A broken row is NaN in every column, so its argmax is meaningless but harmless: its score is -inf
        nearest[rows] = usable[cosines.argmax(axis=1)]
        cosines.partition(-kept, axis=1)
        top = cosines[:, -kept:]
        peak[rows] = top.max(axis=1)
        scores[rows] = PEAK_WEIGHT * peak[rows] + (1.0 - PEAK_WEIGHT) * np.maximum(top, 0.0).mean(axis=1)
    is_scored = np.isfinite(scores)
    return SourceScores(np.where(is_scored, scores, -np.inf), nearest, np.where(is_scored, peak, -np.inf))
