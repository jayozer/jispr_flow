"""Unit tests for the deterministic transcript cleanup rules."""

import pytest

from local_flow.polish.rules import (
    apply_backtracking,
    apply_dictation_commands,
    apply_spoken_code_syntax,
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


class TestBacktrackingAfterFillerRemoval:
    """`clean_transcript` removes fillers before applying backtracking: a
    filler segment between the retracted text and the marker ("email John,
    um, scratch that, ...") would otherwise be popped instead of the text
    being retracted.
    """

    def test_filler_segment_between_text_and_marker(self):
        assert (
            clean_transcript("email John, um, scratch that, email Sarah")
            == "email Sarah"
        )

    def test_multiple_filler_segments_before_marker(self):
        assert (
            clean_transcript("send it Monday, uh, um, no wait send it Friday")
            == "send it Friday"
        )


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


class TestDictionaryBackslashTerms:
    """A term containing a backslash ("AC\\DC", "C:\\Users") must land in the
    output verbatim, never be parsed as a `re.subn` replacement template --
    the template reading raises "bad escape" (killing every dictation that
    runs the rules stage) or injects a control character.
    """

    def test_backslash_term_substitutes_verbatim_without_raising(self):
        text, counts = enforce_dictionary_detailed("we saw ac\\dc live", ["AC\\DC"])
        assert text == "we saw AC\\DC live"
        assert counts == {"AC\\DC": 1}

    def test_windows_path_term_substitutes_verbatim(self):
        text, count = enforce_dictionary("stored under c:\\users today", ["C:\\Users"])
        assert text == "stored under C:\\Users today"
        assert count == 1

    def test_backslash_group_reference_is_not_expanded(self):
        # "\1" in a replacement template would be a group reference; as a
        # dictionary term it must come out as the literal characters.
        text, count = enforce_dictionary("send to team\\1 now", ["Team\\1"])
        assert text == "send to Team\\1 now"
        assert count == 1


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


class TestSpokenCodeSyntax:
    @pytest.mark.parametrize(
        "text, expected, count",
        [
            ("camel case order total", "orderTotal", 1),
            ("snake case user id", "user_id", 1),
            ("all caps api key", "API KEY", 1),
            ("all caps api", "API", 1),
            ("snake case id", "id", 1),
            ("camel case id", "id", 1),
            ("CAMEL CASE order Total", "orderTotal", 1),
            ("snake case Foo Bar BAZ", "foo_bar_baz", 1),
        ],
    )
    def test_table_driven_conversions(self, text, expected, count):
        result, actual_count = apply_spoken_code_syntax(text)
        assert result == expected
        assert actual_count == count

    def test_trigger_absent_leaves_text_unchanged(self):
        text = "just a normal sentence about nothing special"
        result, count = apply_spoken_code_syntax(text)
        assert result == text
        assert count == 0

    def test_trigger_with_no_following_words_is_unchanged(self):
        text = "that formatting is all caps"
        result, count = apply_spoken_code_syntax(text)
        assert result == text
        assert count == 0

    def test_claims_at_most_four_following_words(self):
        text, count = apply_spoken_code_syntax("snake case foo bar baz qux quux")
        assert text == "foo_bar_baz_qux quux"
        assert count == 1

    def test_multiple_occurrences_all_converted(self):
        # Separated by a period (not a word) so each match's greedy word-run
        # stops there instead of swallowing into the next trigger phrase --
        # see `test_greedy_matching_can_swallow_unrelated_words` below for
        # the documented false-positive case when a plain word separates them.
        text, count = apply_spoken_code_syntax(
            "camel case order total. snake case user id"
        )
        assert text == "orderTotal. user_id"
        assert count == 2

    def test_greedy_matching_can_still_merge_across_a_connector(self):
        # Documented residual false-positive risk (see README): the greedy
        # word-run still claims up to four following words, and connector
        # bounding only stops the *conversion* at the first connector -- it
        # does not re-scan the leftover words for a second trigger phrase.
        # So a second trigger phrase folded into the first match's window
        # (here "snake" gets swallowed as the 4th word of "camel case"'s
        # window) is left as literal, unconverted text rather than being
        # converted itself.
        text, count = apply_spoken_code_syntax(
            "camel case order total and snake case user id"
        )
        assert text == "orderTotal and snake case user id"
        assert count == 1

    def test_embedded_in_a_sentence_with_trailing_punctuation(self):
        text, count = apply_spoken_code_syntax("please rename it to camel case order total.")
        assert text == "please rename it to orderTotal."
        assert count == 1


class TestSpokenCodeSyntaxConnectorBounding:
    """The word-run stops at common connector/filler words (see README),
    so continuous speech like "... user id and then send it" doesn't fold
    "and then" into the converted token.
    """

    @pytest.mark.parametrize(
        "text, expected, count",
        [
            (
                "snake case user id and then send it",
                "user_id and then send it",
                1,
            ),
            (
                "camel case order total and more",
                "orderTotal and more",
                1,
            ),
        ],
    )
    def test_connector_bounds_the_conversion_window(self, text, expected, count):
        result, actual_count = apply_spoken_code_syntax(text)
        assert result == expected
        assert actual_count == count

    @pytest.mark.parametrize(
        "text",
        [
            "snake case and",
            "camel case and then",
        ],
    )
    def test_trigger_followed_only_by_connectors_is_left_untouched(self, text):
        # If every word in the (up to four word) window is a connector, there
        # is nothing to convert -- leave the whole phrase exactly as spoken
        # rather than emitting an empty conversion.
        result, count = apply_spoken_code_syntax(text)
        assert result == text
        assert count == 0


class TestSpokenCodeSyntaxNewlineBounding:
    """`apply_spoken_code_syntax` runs after `apply_dictation_commands`, so a
    commanded "new line" may sit inside its match window; the word-run must
    never cross that newline ("... order total new line thanks" would
    otherwise become "orderTotalThanks").
    """

    @pytest.mark.parametrize(
        "text, expected, count",
        [
            ("camel case order total\nthanks", "orderTotal\nthanks", 1),
            ("snake case user id\nsend it", "user_id\nsend it", 1),
            ("all caps api key\ndone", "API KEY\ndone", 1),
            ("camel case\nthanks anyway", "camel case\nthanks anyway", 0),
        ],
    )
    def test_word_run_never_crosses_a_newline(self, text, expected, count):
        result, actual_count = apply_spoken_code_syntax(text)
        assert result == expected
        assert actual_count == count

    def test_commanded_new_line_keeps_next_word_off_the_identifier(self):
        text, actions = apply_dictation_commands("camel case order total new line thanks")
        assert text == "camel case order total\nthanks"
        assert actions == []
        result, count = apply_spoken_code_syntax(text)
        assert result == "orderTotal\nthanks"
        assert count == 1
