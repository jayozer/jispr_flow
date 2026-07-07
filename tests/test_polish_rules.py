"""Unit tests for the deterministic transcript cleanup rules."""

from local_flow.polish.rules import (
    apply_backtracking,
    apply_dictation_commands,
    clean_transcript,
    enforce_dictionary,
    enforce_dictionary_detailed,
    expand_snippets,
    extract_dictionary_additions,
    remove_fillers,
)


class TestFillerRemoval:
    def test_removes_common_fillers(self):
        assert remove_fillers("um so uh this is erm a test") == "so this is a test"

    def test_removes_filler_with_trailing_comma(self):
        assert remove_fillers("Um, hello there") == "hello there"

    def test_keeps_words_containing_filler_substrings(self):
        assert remove_fillers("the drummer sang aha") == "the drummer sang aha"

    def test_keeps_hyphenated_words(self):
        assert remove_fillers("she said uh-huh loudly") == "she said uh-huh loudly"

    def test_case_insensitive(self):
        assert remove_fillers("UM this UH works") == "this works"

    def test_custom_filler_set(self):
        assert remove_fillers("well this works", fillers={"well"}) == "this works"


class TestBacktracking:
    def test_scratch_that_replaces_previous_segment(self):
        assert (
            apply_backtracking("email John, scratch that, email Sarah")
            == "email Sarah"
        )

    def test_no_wait_with_inline_correction(self):
        assert (
            apply_backtracking("send it Monday, no wait send it Friday.")
            == "send it Friday."
        )

    def test_multiple_corrections(self):
        text = "call Bob, strike that, call Ann, scratch that, call Joe"
        assert apply_backtracking(text) == "call Joe"

    def test_text_without_markers_is_unchanged(self):
        text = "nothing to fix here, just words."
        assert apply_backtracking(text) == text

    def test_earlier_sentences_are_preserved(self):
        text = "First point stands. second draft, scratch that, final draft"
        assert apply_backtracking(text) == "First point stands. final draft"


class TestCleanTranscript:
    def test_combines_backtracking_and_fillers(self):
        rough = "um send the uh report to John, scratch that, send it to Sarah"
        assert clean_transcript(rough) == "send it to Sarah"

    def test_empty_input(self):
        assert clean_transcript("") == ""


class TestDictationCommands:
    def test_trailing_press_enter_becomes_key_action(self):
        text, actions = apply_dictation_commands("ship the release press enter")
        assert text == "ship the release"
        assert actions == ["enter"]

    def test_trailing_press_enter_with_punctuation(self):
        text, actions = apply_dictation_commands("done. Press enter.")
        assert text == "done."
        assert actions == ["enter"]

    def test_mid_text_press_enter_becomes_newline(self):
        text, actions = apply_dictation_commands("first item press enter second item")
        assert text == "first item\nsecond item"
        assert actions == []

    def test_new_line_and_paragraph(self):
        text, actions = apply_dictation_commands("alpha new line beta new paragraph gamma")
        assert text == "alpha\nbeta\n\ngamma"
        assert actions == []

    def test_only_press_enter(self):
        text, actions = apply_dictation_commands("press enter")
        assert text == ""
        assert actions == ["enter"]

    def test_hit_enter_variant(self):
        text, actions = apply_dictation_commands("save the file, hit enter")
        assert text == "save the file"
        assert actions == ["enter"]

    def test_plain_text_untouched(self):
        text, actions = apply_dictation_commands("enter the building through gate two")
        assert text == "enter the building through gate two"
        assert actions == []


class TestDictionaryReplacementCount:
    def test_zero_when_nothing_replaced(self):
        text, count = enforce_dictionary("nothing to see here", ["PostgreSQL"])
        assert text == "nothing to see here"
        assert count == 0

    def test_one_for_a_single_match(self):
        text, count = enforce_dictionary("we use postgresql daily", ["PostgreSQL"])
        assert text == "we use PostgreSQL daily"
        assert count == 1

    def test_counts_same_term_appearing_twice(self):
        text, count = enforce_dictionary(
            "postgresql and postgresql again", ["PostgreSQL"]
        )
        assert text == "PostgreSQL and PostgreSQL again"
        assert count == 2

    def test_counts_across_multiple_terms(self):
        text, count = enforce_dictionary(
            "postgresql and jispr flow", ["PostgreSQL", "JiSpr Flow"]
        )
        assert text == "PostgreSQL and JiSpr Flow"
        assert count == 2


class TestDictionaryDetailedCounts:
    def test_per_term_counts(self):
        text, counts = enforce_dictionary_detailed(
            "postgresql and postgresql again, plus jispr flow",
            ["PostgreSQL", "JiSpr Flow"],
        )
        assert text == "PostgreSQL and PostgreSQL again, plus JiSpr Flow"
        assert counts == {"PostgreSQL": 2, "JiSpr Flow": 1}

    def test_terms_with_zero_matches_are_omitted(self):
        _text, counts = enforce_dictionary_detailed("nothing here", ["PostgreSQL"])
        assert counts == {}

    def test_wrapper_sum_matches_detailed_total(self):
        sample = "postgresql and jispr flow and postgresql"
        terms = ["PostgreSQL", "JiSpr Flow"]
        text_detailed, counts = enforce_dictionary_detailed(sample, terms)
        text_wrapper, total = enforce_dictionary(sample, terms)
        assert text_detailed == text_wrapper
        assert total == sum(counts.values())


class TestExtractDictionaryAdditions:
    def test_single_word_term(self):
        text, terms = extract_dictionary_additions("add JiSpr to the dictionary")
        assert terms == ["JiSpr"]
        assert text == ""

    def test_without_the(self):
        text, terms = extract_dictionary_additions("add JiSpr to dictionary")
        assert terms == ["JiSpr"]
        assert text == ""

    def test_multi_word_term(self):
        text, terms = extract_dictionary_additions("add Kubernetes cluster to the dictionary")
        assert terms == ["Kubernetes cluster"]
        assert text == ""

    def test_multiple_phrases_all_extracted(self):
        text, terms = extract_dictionary_additions(
            "add JiSpr to the dictionary and add PostgreSQL to dictionary"
        )
        assert terms == ["JiSpr", "PostgreSQL"]
        assert text == "and"

    def test_no_match_passthrough(self):
        text, terms = extract_dictionary_additions("ship the release today")
        assert terms == []
        assert text == "ship the release today"

    def test_mid_sentence_whitespace_repair(self):
        text, terms = extract_dictionary_additions("please add JiSpr to the dictionary now")
        assert terms == ["JiSpr"]
        assert text == "please now"

    def test_case_insensitive_match(self):
        text, terms = extract_dictionary_additions("ADD Redis TO THE DICTIONARY")
        assert terms == ["Redis"]
        assert text == ""


class TestSnippetReplacementCount:
    def test_zero_when_nothing_replaced(self):
        text, count = expand_snippets("nothing to expand here", {"sig": "SIG"})
        assert text == "nothing to expand here"
        assert count == 0

    def test_one_for_a_single_trigger(self):
        text, count = expand_snippets("add sig here", {"sig": "Best regards, Jay"})
        assert text == "add Best regards, Jay here"
        assert count == 1

    def test_counts_same_trigger_appearing_twice(self):
        text, count = expand_snippets(
            "sig now and sig again", {"sig": "SIG"}
        )
        assert text == "SIG now and SIG again"
        assert count == 2
