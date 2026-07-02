"""Unit tests for the deterministic transcript cleanup rules."""

from local_flow.polish.rules import (
    apply_backtracking,
    apply_dictation_commands,
    clean_transcript,
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
