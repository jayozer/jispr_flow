"""TranscriptPolisher: rules + LLM polish, and per-call style overrides."""

import pytest

from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.polish.polisher import TranscriptPolisher


class TestStyleOverrideWarning:
    def test_unknown_override_style_yields_a_warning(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        llm = MockChatClient(["ok"])
        polisher = TranscriptPolisher(llm, store, style="default")

        result = polisher.polish("hello world", style="bogus")

        assert any(
            "style 'bogus' not found; using 'default'" in w for w in result.warnings
        )

    def test_known_override_style_yields_no_warning(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        llm = MockChatClient(["ok"])
        polisher = TranscriptPolisher(llm, store, style="default")

        result = polisher.polish("hello world", style="casual")

        assert result.warnings == []

    def test_no_override_uses_constructor_style_without_warning(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        llm = MockChatClient(["ok"])
        polisher = TranscriptPolisher(llm, store, style="default")

        result = polisher.polish("hello world")

        assert result.warnings == []


class TestStyleProperty:
    """`style` is a settable property (the tray app's Style submenu changes
    it live, without rebuilding the pipeline).
    """

    def test_getter_returns_constructor_value(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        polisher = TranscriptPolisher(MockChatClient(["ok"]), store, style="casual")
        assert polisher.style == "casual"

    def test_setting_style_changes_the_next_polish_call(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        llm = MockChatClient(["ok"])
        polisher = TranscriptPolisher(llm, store, style="default")

        polisher.style = "casual"
        assert polisher.style == "casual"

        polisher.polish("hello world")  # no explicit style= override

        casual_rules = store.style_rules("casual")[1]
        system = llm.requests[0][0]["content"]
        assert casual_rules in system

    def test_explicit_polish_override_still_wins_over_the_property(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        llm = MockChatClient(["ok"])
        polisher = TranscriptPolisher(llm, store, style="default")
        polisher.style = "casual"

        polisher.polish("hello world", style="email")

        email_rules = store.style_rules("email")[1]
        system = llm.requests[0][0]["content"]
        assert email_rules in system


class TestUnsafeModelOutputFallback:
    @pytest.mark.parametrize(
        ("rough", "completion"),
        [
            ("send the draft", "<|im_start|>assistant\nSend the draft.<|im_end|>"),
            ("send the draft", "Assistant: Send the draft."),
            ("send the draft", "Sure, I can send the draft."),
            ("Thank you.", "You're welcome."),
            ("Thank you.", "<|channel>thought <channel|>You're welcome."),
        ],
    )
    def test_rejects_non_transcript_output(self, tmp_path, rough, completion):
        store = PersonalizationStore(tmp_path)
        polisher = TranscriptPolisher(MockChatClient([completion]), store)

        result = polisher.polish(rough)

        assert result.polished == result.cleaned
        assert result.used_llm is False
        assert any("LM Studio polish rejected" in warning for warning in result.warnings)

    def test_accepts_youre_welcome_when_it_was_dictated(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        polisher = TranscriptPolisher(MockChatClient(["You're welcome."]), store)

        result = polisher.polish("you're welcome")

        assert result.polished == "You're welcome."
        assert result.used_llm is True
        assert result.warnings == []
