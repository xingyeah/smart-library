"""One score per book from all its external ratings (Douban, Google Books, ...), for the card and the detail view."""

from typing import Any, Dict, Iterable, Optional

RATING_PRIOR = 7.5  # near the library's mean external rating (10-point scale)
RATING_PRIOR_VOTES = 10  # a rating from a handful of readers stays close to RATING_PRIOR


def composite_rating(ratings: Iterable[Dict[str, Any]]) -> Optional[float]:
    """Vote-weighted mean of the ratings above zero, shrunk toward RATING_PRIOR as if RATING_PRIOR_VOTES readers
    had given it (a Bayesian average): one 10/10 vote shows 7.7, not 10. None when no rating is above zero."""
    rated = [(float(r["rating"]), max(1, int(r.get("votes") or 0)))  # an unknown count counts as one reader
             for r in ratings if float(r.get("rating") or 0) > 0]
    if not rated:
        return None
    weighted = sum(rating * votes for rating, votes in rated) + RATING_PRIOR * RATING_PRIOR_VOTES
    return round(weighted / (sum(votes for _, votes in rated) + RATING_PRIOR_VOTES), 1)
