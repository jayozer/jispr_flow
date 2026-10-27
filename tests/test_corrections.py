"""Explicit correction learning, safe persistence, exact matching, and undo."""

import json
from dataclasses import asdict

import pytest

from local_flow.errors import ConfigError
from local_flow.personalization.corrections import (
    MAX_REVIEW_CHARS,
    MAX_REVIEW_WORDS,
    CorrectionStore,
    CorrectionSuggestion,
    suggest_corrections,
)


def test_missing_store_is_empty_without_creating_user_data(tmp_path):
    directory = tmp_path / "not-created"
    store = CorrectionStore(directory)

    assert store.path == directory / "corrections.json"
    assert store.list_rules() == []
    assert store.apply("hello") == ("hello", 0)
    assert not directory.exists()


def test_suggestions_require_explicit_learning_to_persist(tmp_path):
    store = CorrectionStore(tmp_path)
    suggestions = suggest_corrections("Meet Gisper tomorrow", "Meet JiSpr tomorrow")

    assert suggestions == [CorrectionSuggestion("Gisper", "JiSpr")]
    assert not store.path.exists()

    change = store.learn(suggestions[0].source, suggestions[0].replacement)

    assert change.before is None
    assert change.after.source == "Gisper"
    assert CorrectionStore(tmp_path).list_rules() == [change.after]
    assert json.loads(store.path.read_text()) == {"version": 1, "rules": [asdict(change.after)]}


def test_global_rule_and_exact_app_override_share_a_source(tmp_path):
    store = CorrectionStore(tmp_path)
    store.learn("sequel", "SQL")
    store.learn("SEQUEL", "Sequel Pro", app_id="com.example.Editor")

    assert store.apply("sequel", app_id="com.example.Editor") == ("Sequel Pro", 1)
    assert store.apply("sequel", app_id="com.example.editor") == ("SQL", 1)
    assert store.apply("sequel") == ("SQL", 1)


def test_app_scoped_rule_does_not_leak_into_other_apps(tmp_path):
    store = CorrectionStore(tmp_path)
    store.learn("Sara", "Sarah", app_id="com.example.Mail")

    assert store.apply("Sara", app_id="com.example.Mail") == ("Sarah", 1)
    assert store.apply("Sara", app_id="com.example.Chat") == ("Sara", 0)
    assert store.apply("Sara") == ("Sara", 0)


def test_longest_phrase_wins_and_replacements_do_not_cascade(tmp_path):
    store = CorrectionStore(tmp_path)
    store.learn("jisper", "JiSpr")
    store.learn("jisper flow", "JiSpr Flow")
    store.learn("JiSpr", "Other")

    assert store.apply("jisper flow and jisper") == ("JiSpr Flow and JiSpr", 2)


def test_replacements_are_literal_and_respect_unicode_word_boundaries(tmp_path):
    store = CorrectionStore(tmp_path)
    store.learn("winpath", r"C:\Users\Jay")
    store.learn("cat", "Cat")

    assert store.apply("winpath: cat scat cat_2 caté cat\u0301") == (
        "C:\\Users\\Jay: Cat scat cat_2 caté cat\u0301", 2
    )


def test_casefold_supports_non_ascii_and_expanding_characters(tmp_path):
    store = CorrectionStore(tmp_path)
    store.learn("straße", "Strasse")
    store.learn("café", "Café")
    store.learn("猫", "Cat")

    assert store.apply("STRASSE CAFÉ 猫 猫咪") == ("Strasse Café Cat 猫咪", 3)


def test_casefold_expansion_cannot_match_half_a_character(tmp_path):
    store = CorrectionStore(tmp_path)
    store.learn("s", "ess")

    assert store.apply("ß s") == ("ß ess", 1)


def test_bare_words_do_not_rewrite_email_path_or_code_identifier_parts(tmp_path):
    store = CorrectionStore(tmp_path)
    store.learn("foo", "bar")
    store.learn("C", "See")

    assert store.apply("foo foo@example.com /tmp/foo C C++") == (
        "bar foo@example.com /tmp/foo See C++", 2
    )


def test_complete_email_path_and_code_identifiers_can_be_corrected(tmp_path):
    store = CorrectionStore(tmp_path)
    store.learn("foo", "bar")
    store.learn("foo@example.com", "bar@example.com")
    store.learn("/tmp/foo", "/tmp/bar")
    store.learn("C++", "Rust")

    assert store.apply("foo@example.com /tmp/foo C++") == (
        "bar@example.com /tmp/bar Rust", 3
    )


def test_bare_word_corrections_preserve_possessives_until_explicitly_taught(tmp_path):
    store = CorrectionStore(tmp_path)
    store.learn("foo", "bar")

    assert store.apply("foo's foo") == ("foo's bar", 1)

    store.learn("foo's", "bar's")
    assert store.apply("foo's foo") == ("bar's bar", 2)


def test_word_corrections_still_work_beside_ordinary_sentence_punctuation(tmp_path):
    store = CorrectionStore(tmp_path)
    store.learn("foo", "bar")

    assert store.apply("(foo), 'foo' foo:") == ("(bar), 'bar' bar:", 3)


def test_case_only_normalization_is_allowed_and_counts_only_changes(tmp_path):
    store = CorrectionStore(tmp_path)
    store.learn("jispr", "JiSpr")

    assert store.apply("jispr JiSpr JISPR") == ("JiSpr JiSpr JiSpr", 2)


def test_identical_and_duplicate_learning_are_noops(tmp_path):
    store = CorrectionStore(tmp_path)
    assert store.learn("same", "same") is None
    assert not store.path.exists()
    original = store.learn("jisper", "JiSpr")
    saved = store.path.read_bytes()

    assert store.learn("JISPER", "JiSpr") is None
    assert store.path.read_bytes() == saved
    assert store.list_rules() == [original.after]


def test_learning_updated_replacement_keeps_identity_and_records_before(tmp_path):
    store = CorrectionStore(tmp_path)
    first = store.learn("Sara", "Sarah")
    update = store.learn("sara", "Sára")

    assert update.before == first.after
    assert update.after.id == first.after.id
    assert update.after.created_at != first.after.created_at
    assert store.list_rules() == [update.after]
    assert store.apply("SARA") == ("Sára", 1)


def test_fresh_store_can_undo_a_new_rule_once(tmp_path):
    change = CorrectionStore(tmp_path).learn("jisper", "JiSpr")
    store = CorrectionStore(tmp_path)

    assert store.undo(change)
    assert store.list_rules() == []
    assert not store.undo(change)


def test_undo_restores_previous_rule_on_update(tmp_path):
    store = CorrectionStore(tmp_path)
    first = store.learn("Sara", "Sarah")
    update = store.learn("Sara", "Sára")

    assert store.undo(update)
    assert store.list_rules() == [first.after]


def test_forget_and_undo_restore_the_exact_rule(tmp_path):
    store = CorrectionStore(tmp_path)
    learned = store.learn("jisper", "JiSpr", app_id="com.example.Editor")
    forgotten = store.forget(learned.after.id)

    assert forgotten.before == learned.after
    assert forgotten.after is None
    assert store.list_rules() == []
    assert store.undo(forgotten)
    assert store.list_rules() == [learned.after]
    assert not store.undo(forgotten)
    assert store.forget("missing") is None


def test_undo_does_not_overwrite_a_newer_edit(tmp_path):
    store = CorrectionStore(tmp_path)
    first = store.learn("Sara", "Sarah")
    newest = CorrectionStore(tmp_path).learn("Sara", "Sára")
    original = store.path.read_bytes()

    assert not store.undo(first)
    assert store.path.read_bytes() == original
    assert store.list_rules() == [newest.after]


def test_undo_detects_newer_edits_even_when_value_was_changed_back(tmp_path):
    store = CorrectionStore(tmp_path)
    first = store.learn("Sara", "Sarah")
    store.learn("Sara", "Sára")
    store.learn("Sara", "Sarah")

    assert not store.undo(first)


def test_undo_forget_cannot_create_duplicate_after_a_new_learn(tmp_path):
    store = CorrectionStore(tmp_path)
    first = store.learn("Sara", "Sarah")
    forgotten = store.forget(first.after.id)
    newest = store.learn("SARA", "Sára")

    assert not store.undo(forgotten)
    assert store.list_rules() == [newest.after]


def test_atomic_failure_preserves_previous_rules_and_cleans_temporary_file(tmp_path, monkeypatch):
    store = CorrectionStore(tmp_path)
    first = store.learn("jisper", "JiSpr")
    original = store.path.read_bytes()

    def fail_replace(*_args):
        raise OSError("simulated interrupted write")

    monkeypatch.setattr("local_flow.atomicio.os.replace", fail_replace)
    with pytest.raises(OSError, match="interrupted write"):
        store.learn("Sara", "Sarah")

    assert store.path.read_bytes() == original
    assert store.list_rules() == [first.after]
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "bad_source,bad_replacement",
    [
        ("", "JiSpr"),
        ("jisper", "   "),
        ("jisper\nflow", "JiSpr"),
        ("jisper", "JiSpr\rFlow"),
        ("jisper\tflow", "JiSpr"),
        ("jisper", "JiSpr\x00"),
        ("jisper", "JiSpr\u2028Flow"),
        ("x" * 121, "JiSpr"),
        ("jisper", "x" * 121),
        ("one two three four five six seven eight nine", "JiSpr"),
        ("jisper", "one two three four five six seven eight nine"),
        ("123", "456"),
        ("price", "$123"),
        ("!!!", "JiSpr"),
        ("First sentence. Second sentence.", "JiSpr"),
    ],
)
def test_invalid_and_overbroad_rules_are_rejected_without_writes(
    tmp_path, bad_source, bad_replacement
):
    store = CorrectionStore(tmp_path)

    with pytest.raises(ConfigError):
        store.learn(bad_source, bad_replacement)

    assert not store.path.exists()


def test_invalid_app_id_is_rejected_without_writes(tmp_path):
    store = CorrectionStore(tmp_path)

    with pytest.raises(ConfigError, match="app_id"):
        store.learn("jisper", "JiSpr", app_id="app\nother")

    assert not store.path.exists()


@pytest.mark.parametrize(
    "content",
    [
        "{not json}",
        "[]",
        '{"rules": []}',
        '{"version": 2, "rules": []}',
        '{"version": true, "rules": []}',
        '{"version": 1, "rules": null}',
        '{"version": 1, "rules": [{}]}',
        '{"version": 1, "version": 1, "rules": []}',
        '{"version": 1, "rules": [], "future_field": true}',
    ],
)
def test_corrupt_or_unknown_formats_surface_errors_without_overwriting(tmp_path, content):
    store = CorrectionStore(tmp_path)
    store.path.write_text(content)

    with pytest.raises(ConfigError) as error:
        store.learn("jisper", "JiSpr")

    assert "corrections.json" in str(error.value)
    assert "backup" in error.value.hint
    assert store.path.read_text() == content


def test_corrupt_rule_fields_do_not_hide_behind_valid_schema(tmp_path):
    store = CorrectionStore(tmp_path)
    change = store.learn("jisper", "JiSpr")
    invalid_rule = {**asdict(change.after), "created_at": "yesterday"}
    store.path.write_text(json.dumps({"version": 1, "rules": [invalid_rule]}))
    original = store.path.read_bytes()

    with pytest.raises(ConfigError, match="Invalid"):
        store.apply("jisper")

    assert store.path.read_bytes() == original


def test_duplicate_ids_and_scoped_sources_are_corruption(tmp_path):
    store = CorrectionStore(tmp_path)
    learned = store.learn("jisper", "JiSpr")
    rule = asdict(learned.after)
    store.path.write_text(json.dumps({"version": 1, "rules": [rule, rule]}))
    with pytest.raises(ConfigError, match="unique"):
        store.list_rules()

    other_id_same_source = {**rule, "id": "other", "source": "JISPER"}
    store.path.write_text(json.dumps({"version": 1, "rules": [rule, other_id_same_source]}))
    with pytest.raises(ConfigError, match="unique"):
        store.list_rules()


def test_rule_limit_allows_updates_but_rejects_new_entries(tmp_path):
    store = CorrectionStore(tmp_path)
    rules = [
        {"id": str(index), "source": f"wrong{index}", "replacement": f"Right{index}",
         "app_id": "", "created_at": "2026-10-27T00:00:00+00:00"}
        for index in range(500)
    ]
    store.path.write_text(json.dumps({"version": 1, "rules": rules}))

    assert store.learn("wrong0", "Updated") is not None
    original = store.path.read_bytes()
    with pytest.raises(ConfigError, match="500"):
        store.learn("another", "Another")
    assert store.path.read_bytes() == original


def test_suggestion_diff_excludes_insertions_deletions_numbers_and_punctuation():
    assert suggest_corrections("hello world", "hello brave world") == []
    assert suggest_corrections("hello brave world", "hello world") == []
    assert suggest_corrections("hello, world", "hello world!") == []
    assert suggest_corrections("Buy 12 eggs", "Buy 24 eggs") == []


def test_suggestion_diff_supports_phrase_merges_and_case_changes():
    assert suggest_corrections("Use duck dee bee today", "Use DuckDB today") == [
        CorrectionSuggestion("duck dee bee", "DuckDB")
    ]
    assert suggest_corrections("try jispr now", "try JiSpr now") == [
        CorrectionSuggestion("jispr", "JiSpr")
    ]


def test_suggestion_diff_preserves_unicode_and_literal_path_punctuation():
    assert suggest_corrections("Use AC/DC and cafe", "Use AC\\DC and café") == [
        CorrectionSuggestion("AC/DC", "AC\\DC"), CorrectionSuggestion("cafe", "café")
    ]


@pytest.mark.parametrize(
    "original,corrected,expected_source",
    [
        ("Use C++ here", "Use Rust here", "C++"),
        ("Use F# here", "Use FSharp here", "F#"),
        ("Send foo@example.com now", "Send bar@example.com now", "foo@example.com"),
    ],
)
def test_single_identifier_edit_learns_complete_identifier_and_round_trips(
    tmp_path, original, corrected, expected_source
):
    suggestions = suggest_corrections(original, corrected)
    assert len(suggestions) == 1
    assert suggestions[0].source == expected_source
    store = CorrectionStore(tmp_path)
    for suggestion in suggestions:
        store.learn(suggestion.source, suggestion.replacement)
    assert store.apply(original)[0] == corrected


def test_single_edit_cannot_leave_behind_changed_punctuation():
    assert suggest_corrections("Use Gisper?", "Use JiSpr!") == []


def test_single_edit_cannot_learn_a_global_rule_that_changes_unchanged_occurrences():
    assert suggest_corrections("john met john", "Jon met john") == []


@pytest.mark.parametrize("oversized_side", ["original", "corrected"])
def test_repeated_word_limit_is_checked_before_constructing_sequence_matcher(
    monkeypatch, oversized_side
):
    # The reviewed 31,600-character alternating-token input fits the host's
    # character cap but makes unbounded autojunk=False matching quadratic.
    long_text = "a b " * 7_900
    assert len(long_text) < MAX_REVIEW_CHARS
    assert len(long_text.split()) > MAX_REVIEW_WORDS

    def forbidden_matcher(*_args, **_kwargs):
        pytest.fail("SequenceMatcher must not run for an oversized review")

    monkeypatch.setattr("local_flow.personalization.corrections.SequenceMatcher", forbidden_matcher)
    values = {"original": "a", "corrected": "b", oversized_side: long_text}
    with pytest.raises(ConfigError, match="too long.*shorter passage"):
        suggest_corrections(**values)


def test_review_character_limit_is_checked_before_constructing_sequence_matcher(monkeypatch):
    def forbidden_matcher(*_args, **_kwargs):
        pytest.fail("SequenceMatcher must not run for an oversized review")

    monkeypatch.setattr("local_flow.personalization.corrections.SequenceMatcher", forbidden_matcher)
    with pytest.raises(ConfigError, match="too long"):
        suggest_corrections("a" * (MAX_REVIEW_CHARS + 1), "b")


def test_suggestion_diff_deduplicates_repeated_corrections():
    assert suggest_corrections("jisper is jisper", "JiSpr is JiSpr") == [
        CorrectionSuggestion("jisper", "JiSpr")
    ]


def test_suggestion_diff_skips_a_source_with_conflicting_replacements():
    assert suggest_corrections("john met john", "Jon met Jean") == []


def test_suggestion_limit_is_eight_and_long_phrases_are_skipped():
    original = " ".join(f"bad{index} anchor{index}" for index in range(12))
    corrected = " ".join(f"good{index} anchor{index}" for index in range(12))

    assert len(suggest_corrections(original, corrected)) == 8
    assert suggest_corrections(
        "one two three four five six seven eight nine",
        "different replacement sentence with completely new vocabulary to learn",
    ) == []
