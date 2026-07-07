"""Unit tests for the history-mining dictionary term suggester."""

from local_flow.history.store import HistoryRecord
from local_flow.personalization.learn import Suggestion, suggest_terms


def _rec(final: str) -> HistoryRecord:
    return HistoryRecord(timestamp="2026-07-06T12:00:00Z", rough=final, final=final)


class TestSuggestionDataclass:
    def test_fields(self):
        s = Suggestion(term="Kubernetes", count=4, sample="deploy on Kubernetes")
        assert s.term == "Kubernetes"
        assert s.count == 4
        assert s.sample == "deploy on Kubernetes"


class TestHeuristics:
    def test_camel_case_candidate(self):
        records = [_rec("we deploy JiSpr on staging") for _ in range(3)]
        suggestions = suggest_terms(records, known=[])
        assert any(s.term == "JiSpr" for s in suggestions)

    def test_all_caps_candidate(self):
        records = [_rec("hit the API today") for _ in range(3)]
        suggestions = suggest_terms(records, known=[])
        assert any(s.term == "API" for s in suggestions)

    def test_single_letter_all_caps_not_suggested(self):
        records = [_rec("I said A is fine") for _ in range(5)]
        suggestions = suggest_terms(records, known=[], min_count=1)
        assert all(s.term not in ("I", "A") for s in suggestions)

    def test_dotted_name_candidate(self):
        records = [_rec("edit the config.py file") for _ in range(3)]
        suggestions = suggest_terms(records, known=[])
        assert any(s.term == "config.py" for s in suggestions)

    def test_capitalized_not_sentence_initial_candidate(self):
        records = [_rec("deploy it on Kubernetes today") for _ in range(3)]
        suggestions = suggest_terms(records, known=[])
        assert any(s.term == "Kubernetes" for s in suggestions)

    def test_capitalized_sentence_initial_excluded(self):
        # "Kubernetes" only ever appears at the start of a sentence, so it
        # never matches the plain-Capitalized heuristic and should not
        # accumulate any count at all.
        records = [_rec("Kubernetes runs the cluster well") for _ in range(5)]
        suggestions = suggest_terms(records, known=[], min_count=1)
        assert all(s.term != "Kubernetes" for s in suggestions)


class TestExclusions:
    def test_stopword_excluded_even_when_not_sentence_initial(self):
        records = [_rec("well Monday is packed with meetings") for _ in range(5)]
        suggestions = suggest_terms(records, known=[], min_count=1)
        assert all(s.term != "Monday" for s in suggestions)

    def test_known_term_excluded(self):
        records = [_rec("deploy it on Kubernetes today") for _ in range(5)]
        suggestions = suggest_terms(records, known=["Kubernetes"], min_count=1)
        assert all(s.term != "Kubernetes" for s in suggestions)

    def test_known_apostrophe_variant_excluded(self):
        records = [_rec("then Iva's laptop broke again") for _ in range(5)]
        suggestions = suggest_terms(records, known=["Iva"], min_count=1)
        assert all(s.term not in ("Iva", "Iva's") for s in suggestions)


class TestCasingAndCounting:
    def test_most_frequent_casing_wins(self):
        records = (
            [_rec("deploy it on kubernetes today")]  # lowercase: no heuristic match
            + [_rec("deploy it on Kubernetes today") for _ in range(4)]
            + [_rec("deploy it on KUBERNETES today")]
        )
        suggestions = suggest_terms(records, known=[], min_count=1)
        match = next(s for s in suggestions if s.term.lower() == "kubernetes")
        assert match.term == "Kubernetes"
        assert match.count == 5  # 4 Capitalized + 1 ALL-CAPS; lowercase excluded


class TestMinCountAndLimit:
    def test_min_count_filters_out_rare_terms(self):
        records = [_rec("deploy it on Kubernetes today")] * 2 + [
            _rec("check the Redis cache")
        ]
        suggestions = suggest_terms(records, known=[], min_count=3)
        assert suggestions == []

    def test_min_count_default_is_three(self):
        records = [_rec("deploy it on Kubernetes today")] * 3
        suggestions = suggest_terms(records, known=[])
        assert any(s.term == "Kubernetes" and s.count == 3 for s in suggestions)

    def test_limit_caps_number_of_suggestions(self):
        records = []
        for word in ["Kubernetes", "Redis", "Docker", "Jenkins"]:
            records += [_rec(f"deploy it on {word} today")] * 3
        suggestions = suggest_terms(records, known=[], min_count=1, limit=2)
        assert len(suggestions) == 2

    def test_sorted_by_count_descending(self):
        records = [_rec("deploy it on Kubernetes today")] * 5 + [
            _rec("deploy it on Redis today")
        ] * 3
        suggestions = suggest_terms(records, known=[], min_count=1)
        counts = [s.count for s in suggestions]
        assert counts == sorted(counts, reverse=True)


class TestSample:
    def test_sample_is_at_most_80_chars(self):
        long_text = (
            "so after the long planning meeting this morning we decided to "
            "deploy it on Kubernetes tomorrow morning right after breakfast"
        )
        assert len(long_text) > 80
        records = [_rec(long_text)] * 3
        suggestions = suggest_terms(records, known=[], min_count=1)
        match = next(s for s in suggestions if s.term == "Kubernetes")
        assert len(match.sample) <= 80
        assert "Kubernetes" in match.sample

    def test_short_text_sample_is_whole_text(self):
        records = [_rec("deploy it on Kubernetes today")] * 3
        suggestions = suggest_terms(records, known=[], min_count=1)
        match = next(s for s in suggestions if s.term == "Kubernetes")
        assert match.sample == "deploy it on Kubernetes today"
