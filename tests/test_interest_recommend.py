"""Interest recommendations on a small library whose book vectors are built by hand."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import database  # noqa: E402
import semantic_models  # noqa: E402
from search import interest  # noqa: E402

PSYCHOLOGY, BUSINESS, HISTORY, COOKING = "心理学", "商业", "历史", "烹饪"
TOPIC_AXIS = {PSYCHOLOGY: 0, BUSINESS: 0, HISTORY: 1, COOKING: 2}  # business books here are about psychology
FIRST_OWN_AXIS = 10
LIKED_IDS = (1, 2, 3)
DELETED_ID, ANTHOLOGY_ID, COPY_OF_LIKED_ID = 8, 9, 10
OTHER_EDITION_ID, OTHER_EDITION_OF = 21, 1  # the same work as liked book 1 under another title
COOKING_REPRINT_ID, COOKING_REPRINT_OF = 22, 17  # one unmarked classic twice
EDITION_LIKENESS = 0.97
COPY_OF = {OTHER_EDITION_ID: OTHER_EDITION_OF, COOKING_REPRINT_ID: COOKING_REPRINT_OF}
NOWHERE_PROMPT, NOWHERE_AXIS = "量子引力", 900  # a phrase no book in this library is about
CLEARLY_LOWER_MATCH = 10  # points of match score
HISTORY_PROMPT, COOKING_PROMPT, PSYCHOLOGY_PROMPT = "明清史", "家常菜", "认知心理"
SECOND_HISTORY_PROMPT = "宋史"
PROMPT_TOPIC = {HISTORY_PROMPT: "历史", COOKING_PROMPT: "烹饪", PSYCHOLOGY_PROMPT: "心理学"}
# id: (category, share of the vector on its topic axis): a larger share is closer to the other books of the topic
BOOKS = {
    1: (PSYCHOLOGY, 0.6), 2: (PSYCHOLOGY, 0.6), 3: (PSYCHOLOGY, 0.6),
    4: (PSYCHOLOGY, 0.7), 5: (PSYCHOLOGY, 0.6), 6: (PSYCHOLOGY, 0.5), 7: (PSYCHOLOGY, 0.4),
    DELETED_ID: (PSYCHOLOGY, 0.8), ANTHOLOGY_ID: (PSYCHOLOGY, 0.8), COPY_OF_LIKED_ID: (PSYCHOLOGY, 0.8),
    11: (BUSINESS, 0.7),
    12: (HISTORY, 0.5), 13: (HISTORY, 0.45), 14: (HISTORY, 0.4), 15: (HISTORY, 0.35), 16: (HISTORY, 0.3),
    17: (COOKING, 0.4), 18: (COOKING, 0.4), 19: (COOKING, 0.4), 20: (COOKING, 0.4),
    OTHER_EDITION_ID: (PSYCHOLOGY, 0.6),
    COOKING_REPRINT_ID: (COOKING, 0.4),
}
CLASSIC_SCORE = {COOKING: 90.0}  # the exploration picks come from the classics
DEFAULT_CLASSIC_SCORE = 30.0


def book_vector(book_id: int) -> np.ndarray:
    category, topic_share = BOOKS[book_id]
    vector = np.zeros(semantic_models.EMBED_DIM, dtype=np.float32)
    if book_id in COPY_OF:
        vector[FIRST_OWN_AXIS + book_id] = np.sqrt(1 - EDITION_LIKENESS ** 2)
        return vector + EDITION_LIKENESS * book_vector(COPY_OF[book_id])
    vector[TOPIC_AXIS[category]] = np.sqrt(topic_share)
    vector[FIRST_OWN_AXIS + book_id] = np.sqrt(1 - topic_share)
    return vector


def axis_vector(axis: int) -> np.ndarray:
    vector = np.zeros(semantic_models.EMBED_DIM, dtype=np.float32)
    vector[axis] = 1.0
    return vector


class InterestLibraryCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.db_path = root / "library.db"
        database.init_db(self.db_path)
        self.insert_books()
        self.set_config(interest_prompt="")
        np.save(str(root / "vectors.npy"), np.stack([book_vector(book_id) for book_id in BOOKS]))
        (root / "vectors.json").write_text(json.dumps({"book_ids": list(BOOKS)}), encoding="utf-8")
        engine = semantic_models.BgeM3Engine()
        prompt_axis = {phrase: TOPIC_AXIS[topic] for phrase, topic in PROMPT_TOPIC.items()}
        prompt_axis[NOWHERE_PROMPT] = NOWHERE_AXIS
        prompt_axis[SECOND_HISTORY_PROMPT] = TOPIC_AXIS[HISTORY]
        engine.encode_query = lambda text: axis_vector(prompt_axis[text]) if text in prompt_axis else None
        patches = (
            mock.patch.object(semantic_models, "BGE_VEC_NPY_PATH", root / "vectors.npy"),
            mock.patch.object(semantic_models, "BGE_VEC_META_PATH", root / "vectors.json"),
            mock.patch.object(interest, "get_conn", lambda: database.get_conn(self.db_path)),
            mock.patch.object(interest, "get_bge_engine", lambda: engine),
            # The real one reads the corpus statistics of the live library
            mock.patch.object(interest, "analyze_query_intent", lambda text: {
                "inferred_categories": [PROMPT_TOPIC.get(text, HISTORY)], "semantic_query": text}),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def insert_books(self) -> None:
        conn = database.get_conn(self.db_path)
        for book_id, (category, _) in BOOKS.items():
            title = "Book 1" if book_id == COPY_OF_LIKED_ID else f"Book {book_id}"
            conn.execute(
                "INSERT INTO books (id, title, norm_title, author, norm_author, category, primary_path, "
                "primary_format, dedup_key, is_deleted, is_anthology, is_favorite, classic_score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'epub', ?, ?, ?, ?, ?)",
                (book_id, title, title.lower(), f"Author {book_id}", f"author {book_id}", category,
                 f"/books/{book_id}", f"key{book_id}", int(book_id == DELETED_ID), int(book_id == ANTHOLOGY_ID),
                 int(book_id in LIKED_IDS), CLASSIC_SCORE.get(category, DEFAULT_CLASSIC_SCORE)))
        conn.commit()
        conn.close()

    def execute(self, sql: str, params: tuple = ()) -> None:
        conn = database.get_conn(self.db_path)
        conn.execute(sql, params)
        conn.commit()
        conn.close()

    def set_config(self, **columns) -> None:
        for column, value in columns.items():
            stored = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
            self.execute(f"UPDATE user_interest_config SET {column} = ? WHERE id = 1", (stored,))

    def recommended(self, top_k: int = 10) -> list:
        return interest.recommend_by_interests(top_k=top_k)["items"]

    def recommended_ids(self, top_k: int = 10) -> list:
        return [book["id"] for book in self.recommended(top_k)]


class TastePicksTest(InterestLibraryCase):
    def test_picks_start_with_the_books_nearest_the_liked_ones(self):
        self.assertEqual(self.recommended_ids()[:2], [4, 11])

    def test_never_picks_marked_deleted_anthology_or_same_title_books(self):
        never = set(LIKED_IDS) | {DELETED_ID, ANTHOLOGY_ID, COPY_OF_LIKED_ID}
        self.assertFalse(never & set(self.recommended_ids(top_k=len(BOOKS))))

    def test_another_edition_of_a_liked_book_is_not_picked_under_its_other_title(self):
        self.assertNotIn(OTHER_EDITION_ID, self.recommended_ids(top_k=len(BOOKS)))

    def test_reason_names_a_liked_book_the_pick_is_near(self):
        first = self.recommended()[0]
        self.assertEqual(first["recommend_type"], "exploit")
        self.assertRegex(first["recommend_reason"], r"《Book [123]》")

    def test_picks_near_the_liked_books_have_match_scores_that_tell_them_apart(self):
        taste_scores = [book["match_score"] for book in self.recommended() if book["recommend_type"] == "exploit"]
        self.assertGreater(len(set(taste_scores)), 2)

    def test_nearer_pick_shows_a_higher_match_score(self):
        picks = {book["id"]: book["match_score"] for book in self.recommended()}
        self.assertLess(picks[6], picks[5])
        self.assertLess(picks[5], picks[4])

    def test_reason_follows_a_newly_liked_book(self):
        """Book 11 sits nearer to book 4 than to books 1-3: once 4 is liked, 11 is recommended because of it. The
        scores kept from the first request would still name one of books 1-3."""
        self.assertRegex(self.recommended()[1]["recommend_reason"], r"《Book [123]》")
        self.execute("UPDATE books SET is_favorite = 1 WHERE id = 4")
        reasons = {book["id"]: book["recommend_reason"] for book in self.recommended()}
        self.assertIn("《Book 4》", reasons[11])

    def test_second_book_by_an_author_already_picked_is_passed_over(self):
        self.execute("UPDATE books SET norm_author = 'author 4' WHERE id = 5")
        ids = self.recommended_ids()
        self.assertIn(4, ids)
        self.assertNotIn(5, ids)

    def test_liked_categories_take_turns(self):
        """With a history book liked too, the eight places split four and four. Ranked as one pool, psychology,
        with three liked books to history's one, would take more."""
        self.execute("UPDATE books SET is_favorite = 1 WHERE id = 16")
        clusters = [book["interest_cluster"] for book in self.recommended()[:8]]
        self.assertEqual({name: clusters.count(name) for name in set(clusters)},
                         {f"{PSYCHOLOGY}偏好": 4, f"{HISTORY}偏好": 4})

    def test_category_with_the_best_candidate_takes_the_first_turn(self):
        self.execute("UPDATE books SET is_favorite = 1 WHERE id = 16")
        self.assertEqual(self.recommended(top_k=2)[0]["id"], 4)

    def test_one_liked_book_is_enough(self):
        self.execute("UPDATE books SET is_favorite = 0 WHERE id IN (2, 3)")
        self.assertEqual(self.recommended_ids()[0], 4)

    def test_newly_marked_book_is_gone_from_the_next_list(self):
        self.assertIn(4, self.recommended_ids())
        self.execute("UPDATE books SET reading_status = 'want' WHERE id = 4")
        self.assertNotIn(4, self.recommended_ids())

    def test_book_deleted_after_a_request_is_gone_from_the_next_list(self):
        self.assertIn(5, self.recommended_ids())
        self.execute("UPDATE books SET is_deleted = 1 WHERE id = 5")
        self.assertNotIn(5, self.recommended_ids())


class CategorySettingsTest(InterestLibraryCase):
    def test_excluded_category_is_never_picked_not_even_for_exploration(self):
        self.set_config(excluded_categories=[COOKING, BUSINESS])
        categories = {book["category"] for book in self.recommended(top_k=len(BOOKS))}
        self.assertFalse({COOKING, BUSINESS} & categories)

    def test_preferred_category_moves_an_equally_near_book_ahead(self):
        self.set_config(preferred_categories=[])
        self.assertEqual(self.recommended_ids()[:2], [4, 11])
        match_before = {book["id"]: book["match_score"] for book in self.recommended()}
        self.set_config(preferred_categories=[BUSINESS])
        preferred = self.recommended()
        self.assertEqual([book["id"] for book in preferred[:2]], [11, 4])
        self.assertEqual(preferred[0]["match_score"], match_before[11])  # the preference orders, it is not a match


class PromptPicksTest(InterestLibraryCase):
    def test_prompt_and_liked_books_share_the_places_evenly(self):
        """One phrase and one liked category take turns through the eight places exploration leaves."""
        self.set_config(interest_prompt=HISTORY_PROMPT)
        picks = self.recommended()[:8]
        prompt_picks = [book for book in picks if book["cluster_source"] == "兴趣罗盘定义"]
        self.assertEqual({book["id"] for book in prompt_picks}, {12, 13, 14, 15})
        self.assertIn(HISTORY_PROMPT, prompt_picks[0]["recommend_reason"])
        self.assertEqual(sum(book["cluster_source"] == "阅读足迹" for book in picks), 4)

    def test_single_place_goes_to_the_liked_books(self):
        self.set_config(interest_prompt=HISTORY_PROMPT)
        only = self.recommended(top_k=1)
        self.assertEqual([(book["cluster_source"], book["id"]) for book in only], [("阅读足迹", 4)])

    def test_two_places_go_to_the_liked_books_and_the_first_phrase(self):
        self.set_config(interest_prompt=f"{HISTORY_PROMPT}、{COOKING_PROMPT}", exploration_ratio=0.34)
        picks = self.recommended(top_k=3)[:2]
        self.assertEqual({book["interest_cluster"] for book in picks}, {f"{PSYCHOLOGY}偏好", HISTORY_PROMPT})

    def test_list_runs_from_the_best_match_down_whoever_picked_first(self):
        """The phrase about nothing in the library takes an early turn but shows last among the matched picks."""
        self.set_config(interest_prompt=NOWHERE_PROMPT)
        matched = [book for book in self.recommended() if book["recommend_type"] == "exploit"]
        scores = [book["match_score"] for book in matched]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(matched[0]["cluster_source"], "阅读足迹")

    def test_copy_of_a_prompt_pick_is_not_added_as_an_exploration_pick(self):
        self.set_config(interest_prompt=COOKING_PROMPT)
        ids = self.recommended_ids(top_k=len(BOOKS))
        self.assertEqual(len({COOKING_REPRINT_ID, COOKING_REPRINT_OF} & set(ids)), 1)

    def test_phrase_nothing_in_the_library_answers_shows_a_much_lower_match(self):
        self.set_config(interest_prompt=f"{HISTORY_PROMPT}、{NOWHERE_PROMPT}")
        best_match = {}
        for book in self.recommended():
            best_match.setdefault(book["interest_cluster"], book["match_score"])
        self.assertLess(best_match[NOWHERE_PROMPT], best_match[HISTORY_PROMPT] - CLEARLY_LOWER_MATCH)

    def test_every_phrase_of_the_prompt_gets_a_pick_and_the_liked_books_keep_theirs(self):
        self.set_config(interest_prompt=f"{HISTORY_PROMPT}、{COOKING_PROMPT}、{PSYCHOLOGY_PROMPT}")
        picks = self.recommended()
        phrases_with_a_pick = {book["interest_cluster"] for book in picks if book["cluster_source"] == "兴趣罗盘定义"}
        self.assertEqual(phrases_with_a_pick, set(PROMPT_TOPIC))
        self.assertIn("阅读足迹", {book["cluster_source"] for book in picks})

    def test_with_every_liked_book_deleted_the_prompt_alone_fills_the_list(self):
        self.set_config(interest_prompt=HISTORY_PROMPT)
        self.execute("UPDATE books SET is_deleted = 1 WHERE is_favorite = 1")
        picks = self.recommended()
        self.assertEqual(picks[0]["id"], 12)
        self.assertEqual({book["id"] for book in picks[:5]}, {12, 13, 14, 15, 16})
        self.assertEqual({book["cluster_source"] for book in picks[:5]}, {"兴趣罗盘定义"})


class ExploreOnlyTest(InterestLibraryCase):
    def test_no_marks_and_no_prompt_gives_only_exploration_picks(self):
        self.execute("UPDATE books SET is_favorite = 0")
        picks = self.recommended(top_k=4)
        self.assertEqual({book["recommend_type"] for book in picks}, {"explore"})
        self.assertEqual(picks[0]["category"], COOKING)  # the greatest classics lead

    def test_classic_from_an_untouched_category_comes_before_a_greater_one_from_a_liked_category(self):
        self.execute("UPDATE books SET classic_score = 95 WHERE id = 7")  # psychology, above cooking's 90
        last_of_four = self.recommended(top_k=4)[-1]
        self.assertEqual((last_of_four["recommend_type"], last_of_four["category"]), ("explore", COOKING))

    def test_reprint_of_an_exploration_pick_is_not_picked_beside_it(self):
        self.execute("UPDATE books SET is_favorite = 0")
        ids = self.recommended_ids(top_k=len(BOOKS))
        self.assertEqual(len({COOKING_REPRINT_ID, COOKING_REPRINT_OF} & set(ids)), 1)

    def test_without_a_vector_index_the_classics_still_come_back(self):
        with mock.patch.object(semantic_models, "BGE_VEC_NPY_PATH", Path("/nonexistent/vectors.npy")):
            picks = self.recommended(top_k=3)
        self.assertEqual([book["recommend_type"] for book in picks], ["explore"] * 3)  # one by quota, two to fill
        self.assertFalse(set(LIKED_IDS) & {book["id"] for book in picks})


class ExplorationShareTest(InterestLibraryCase):
    def explored_at(self, ratio: float, top_k: int = 10) -> int:
        """How many exploration picks a list of `top_k` holds once the reader's ratio is set to `ratio`."""
        self.set_config(exploration_ratio=ratio)
        return sum(book["recommend_type"] == "explore" for book in self.recommended(top_k))

    def test_share_of_the_list_follows_the_ratio(self):
        self.assertEqual(self.explored_at(0.4), 4)
        self.assertEqual(self.explored_at(0.2), 2)
        self.assertEqual(self.explored_at(0.5, top_k=4), 2)

    def test_ratio_zero_means_no_exploration_pick_at_all(self):
        self.assertEqual(self.explored_at(0.0), 0)
        self.execute("UPDATE books SET is_favorite = 0")  # nothing to go on: an empty list, not classics after all
        self.assertEqual(self.recommended(), [])

    def test_ratio_above_one_half_counts_as_one_half(self):
        self.assertEqual(self.explored_at(1.0), 5)
        self.assertEqual(self.explored_at(7.0), 5)

    def test_ratio_that_is_not_a_share_falls_back_to_none(self):
        self.assertEqual(self.explored_at(-0.3), 0)
        self.assertEqual(self.explored_at(float("nan")), 0)

    def test_places_for_a_list_of_a_hundred_follow_the_ratio(self):
        self.assertEqual(interest.explore_places(100, 0.3), 30)

    def test_half_a_place_rounds_up_but_exploration_never_takes_more_than_half(self):
        self.assertEqual(interest.explore_places(10, 0.25), 3)
        self.assertEqual([interest.explore_places(top_k, 0.5) for top_k in (2, 3, 5, 7, 11)], [1, 1, 2, 3, 5])

    def test_list_of_one_explores_only_at_the_top_ratio(self):
        self.assertEqual(interest.explore_places(1, 0.5), 1)
        self.assertEqual(interest.explore_places(1, 0.4), 0)

    def test_second_classic_by_an_author_is_not_explored(self):
        self.execute("UPDATE books SET is_favorite = 0")
        self.execute("UPDATE books SET norm_author = 'one cook' WHERE id IN (17, 18)")
        ids = self.recommended_ids(top_k=len(BOOKS))
        self.assertEqual(len({17, 18} & set(ids)), 1)

    def test_two_phrases_on_one_category_are_two_marks(self):
        """Business has no mark; cooking has two liked books and history two phrases. At two marks each, cooking
        with its greater classics comes before history; counted as one mark, history would come first."""
        self.execute("UPDATE books SET is_favorite = 1 WHERE id IN (17, 18)")
        self.set_config(interest_prompt=f"{HISTORY_PROMPT}、{SECOND_HISTORY_PROMPT}", exploration_ratio=0.5)
        explored = [book["category"] for book in self.recommended(top_k=4) if book["recommend_type"] == "explore"]
        self.assertEqual(explored, [BUSINESS, COOKING])

    def test_exploration_picks_are_spread_over_categories(self):
        """Cooking holds every top classic; history is untouched too and gets its turn."""
        self.set_config(exploration_ratio=0.5)
        explored = [book["category"] for book in self.recommended() if book["recommend_type"] == "explore"]
        self.assertEqual(explored, [COOKING, HISTORY, COOKING, HISTORY, COOKING])

    def test_least_marked_category_is_explored_before_a_more_marked_one_with_greater_classics(self):
        self.execute("UPDATE books SET is_favorite = 1 WHERE id IN (16, 17, 18)")  # history 1, cooking 2
        self.set_config(exploration_ratio=0.5)
        explored = [book["category"] for book in self.recommended(top_k=4) if book["recommend_type"] == "explore"]
        self.assertEqual(explored, [BUSINESS, HISTORY])


class InterestCandidatesTest(InterestLibraryCase):
    def test_candidates_for_a_later_choice_hold_no_exploration_picks(self):
        self.set_config(exploration_ratio=0.5)
        candidates = interest.interest_candidates(top_k=10)
        self.assertEqual({book["recommend_type"] for book in candidates["items"]}, {"exploit"})
        self.assertEqual(candidates["exploration_ratio"], 0.5)  # the reader's setting is reported, not changed


class LanguageFilterTest(InterestLibraryCase):
    def test_only_books_in_the_asked_language_are_picked(self):
        self.execute("UPDATE books SET language = 'en' WHERE id IN (5, 6, 18)")
        self.execute("UPDATE books SET language = 'zh-CN' WHERE id NOT IN (5, 6, 18)")
        english = interest.recommend_by_interests(top_k=10, language="en")["items"]
        self.assertEqual(sorted(book["id"] for book in english), [5, 6, 18])


if __name__ == "__main__":
    unittest.main()
