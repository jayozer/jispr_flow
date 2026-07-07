"""TranscriptPolisher: rules + LLM polish, and per-call style overrides."""

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
