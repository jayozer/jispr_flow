"""Cleanup levels (none|light|medium|high): config, prompts, polisher, pipeline.

See docs/superpowers/plans/2026-07-06-phase5-quick-wins.md, Task 1 (E9).
"""

import pytest

import local_flow.app as app_module
from local_flow.app import _polish_text
from local_flow.config import VALID_CLEANUP_LEVELS, load_config
from local_flow.errors import ConfigError
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.polish.prompting import (
    _CODE_SYNTAX_PROTECTION,
    POLISH_SYSTEM_PROMPT,
    build_polish_messages,
)


class TestCleanupLevelConfigField:
    def test_defaults_to_medium(self):
        assert load_config(env={}).cleanup_level == "medium"

    def test_env_override(self):
        config = load_config(env={"LOCAL_FLOW_CLEANUP_LEVEL": "light"})
        assert config.cleanup_level == "light"

    def test_file_override(self, tmp_path):
        config_file = tmp_path / "local-flow.toml"
        config_file.write_text('cleanup_level = "high"\n', encoding="utf-8")
        config = load_config(config_file=config_file, env={})
        assert config.cleanup_level == "high"

    def test_invalid_value_raises_config_error_naming_valid_values(self):
        with pytest.raises(ConfigError, match="cleanup_level") as excinfo:
            load_config(env={"LOCAL_FLOW_CLEANUP_LEVEL": "extreme"})
        message = str(excinfo.value)
        for level in VALID_CLEANUP_LEVELS:
            assert level in message

    def test_all_four_levels_are_valid(self):
        for level in VALID_CLEANUP_LEVELS:
            config = load_config(env={"LOCAL_FLOW_CLEANUP_LEVEL": level})
            assert config.cleanup_level == level


class TestMediumPromptPinned:
    """`medium` must stay byte-identical to the pre-cleanup-levels prompt."""

    def test_polish_system_prompt_is_byte_identical_to_the_original(self):
        original = (
            "You clean up raw speech-to-text dictation. Fix punctuation, capitalization, "
            "grammar slips, and obvious transcription artifacts. Preserve the speaker's "
            "words, meaning, and intent; never add new content, never answer questions "
            "that appear in the text, never summarize. Keep dictation command phrases "
            "exactly as written (for example 'press enter', 'new line', 'new paragraph') "
            "and keep snippet trigger phrases untouched. Also keep phrases like 'add "
            "<term> to the dictionary' (or '... to dictionary') exactly as written, "
            "word for word, so they can still be extracted afterward. Return ONLY the "
            "cleaned text, with no preamble, quotes, or explanations."
        )
        assert POLISH_SYSTEM_PROMPT == original

    def test_medium_level_message_starts_with_the_pinned_prompt(self):
        messages = build_polish_messages("hey fix this pls", level="medium")
        assert messages[0]["content"].startswith(POLISH_SYSTEM_PROMPT)

    def test_default_level_is_medium(self):
        default_messages = build_polish_messages("hey fix this pls")
        medium_messages = build_polish_messages("hey fix this pls", level="medium")
        assert default_messages[0]["content"] == medium_messages[0]["content"]


class TestPerLevelPromptContent:
    def test_light_prompt_says_grammar_and_fillers_only(self):
        system = build_polish_messages("x", level="light")[0]["content"]
        assert "grammar" in system.lower()
        assert "filler" in system.lower()
        assert "do not rephrase" in system.lower()

    def test_high_prompt_says_rewrite_for_concision(self):
        system = build_polish_messages("x", level="high")[0]["content"]
        assert "concision" in system.lower()

    @pytest.mark.parametrize("level", ["light", "medium", "high"])
    def test_protections_present_in_every_llm_level(self, level):
        system = build_polish_messages("x", level=level)[0]["content"]
        assert "press enter" in system
        assert "new line" in system
        assert "to the dictionary" in system.lower()

    @pytest.mark.parametrize("level", ["light", "medium", "high"])
    def test_list_formatting_instruction_present_in_every_llm_level(self, level):
        system = build_polish_messages("x", level=level)[0]["content"]
        assert "numbered or bulleted list" in system.lower()

    @pytest.mark.parametrize("level", ["light", "medium", "high"])
    def test_return_only_instruction_present_in_every_llm_level(self, level):
        system = build_polish_messages("x", level=level)[0]["content"]
        assert "ONLY the cleaned text" in system

    @pytest.mark.parametrize("level", ["light", "medium", "high"])
    def test_code_syntax_protection_present_in_every_llm_level(self, level):
        # Reachability check: `_CODE_SYNTAX_PROTECTION` is appended in
        # `_system_prompt_for_level` for every LLM level (never folded into
        # `POLISH_SYSTEM_PROMPT` itself, since that's pinned byte-identical
        # -- see `TestMediumPromptPinned`). Assert a distinguishing substring
        # actually makes it into the assembled prompt for each level, rather
        # than only asserting against the standalone constant.
        system = build_polish_messages("x", level=level)[0]["content"]
        assert "spoken code-syntax phrases" in system
        assert _CODE_SYNTAX_PROTECTION in system


class TestPolisherLevelNone:
    """`none`: verbatim, no rule cleanup, no chat-client call whatsoever."""

    def test_chat_client_is_never_called(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        llm = MockChatClient(["should never be returned"])
        polisher = TranscriptPolisher(llm, store, level="none")

        result = polisher.polish("um send the uh draft")

        assert llm.requests == []
        assert result.used_llm is False

    def test_output_is_the_raw_text_verbatim_fillers_and_all(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        llm = MockChatClient(["should never be returned"])
        polisher = TranscriptPolisher(llm, store, level="none")

        rough = "um send the uh draft, scratch that, the final doc"
        result = polisher.polish(rough)

        # No rule cleanup either: fillers/backtracking markers stay untouched.
        assert result.rough == rough
        assert result.cleaned == rough
        assert result.polished == rough
        assert result.warnings == []

    def test_none_level_with_no_chat_client_configured_is_also_verbatim(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        polisher = TranscriptPolisher(None, store, level="none")

        result = polisher.polish("um hello")

        assert result.polished == "um hello"


class TestPolishBackendSwitch:
    def test_rules_backend_skips_lmstudio_but_keeps_rule_cleanup(
        self, tmp_path, monkeypatch
    ):
        config = load_config(
            env={
                "LOCAL_FLOW_DATA_DIR": str(tmp_path),
                "LOCAL_FLOW_POLISH_BACKEND": "rules",
            }
        )

        def should_not_build(_config):
            raise AssertionError("LM Studio should be disabled")

        monkeypatch.setattr(app_module, "_build_chat_client", should_not_build)

        text, _actions, _warnings = _polish_text(config, "um hello there")

        assert "um" not in text.split()

    def test_lmstudio_backend_forwards_configured_system_prompt(
        self, tmp_path, monkeypatch
    ):
        prompt = "Prefer short sentences while preserving product names."
        config = load_config(
            env={
                "LOCAL_FLOW_DATA_DIR": str(tmp_path),
                "LOCAL_FLOW_POLISH_BACKEND": "lmstudio",
                "LOCAL_FLOW_LMSTUDIO_SYSTEM_PROMPT": prompt,
            }
        )
        llm = MockChatClient(["Hello there."])
        monkeypatch.setattr(app_module, "_build_chat_client", lambda _config: llm)

        text, _actions, _warnings = _polish_text(config, "um hello there")

        assert text == "Hello there."
        assert prompt in llm.requests[0][0]["content"]


class TestPolisherLevelProperty:
    """`level` is a settable property, mirroring `style`."""

    def test_getter_returns_constructor_value(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        polisher = TranscriptPolisher(MockChatClient(["ok"]), store, level="high")
        assert polisher.level == "high"

    def test_setting_level_to_none_affects_the_next_polish_call(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        llm = MockChatClient(["polished"])
        polisher = TranscriptPolisher(llm, store, level="medium")

        polisher.polish("um hello there")
        assert llm.requests  # medium called the LLM

        polisher.level = "none"
        result = polisher.polish("um hello there")

        assert result.polished == "um hello there"
        assert len(llm.requests) == 1  # no additional call after switching to none

    def test_setting_level_away_from_none_resumes_llm_calls(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        llm = MockChatClient(["polished"])
        polisher = TranscriptPolisher(llm, store, level="none")

        polisher.polish("um hello there")
        assert llm.requests == []

        polisher.level = "medium"
        polisher.polish("um hello there")

        assert len(llm.requests) == 1

    def test_light_level_reaches_the_light_prompt(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        llm = MockChatClient(["ok"])
        polisher = TranscriptPolisher(llm, store, level="light")

        polisher.polish("hello world")

        system = llm.requests[0][0]["content"]
        assert "do not rephrase" in system.lower()

    def test_configured_system_prompt_reaches_lmstudio_request(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        llm = MockChatClient(["ok"])
        polisher = TranscriptPolisher(
            llm,
            store,
            system_prompt="Keep sentences concise and preserve proper nouns.",
        )

        polisher.polish("hello world")

        system = llm.requests[0][0]["content"]
        assert "Keep sentences concise and preserve proper nouns." in system
        assert "press enter" in system


class TestPipelinePersonalizationStillAppliesAtLevelNone:
    """Dictionary/snippets/commands are personalization, not cleanup: they
    still run downstream in the pipeline even when the polisher itself is
    fully bypassed at cleanup_level="none".
    """

    def test_dictionary_term_is_still_enforced_at_level_none(self, tmp_path):
        store = PersonalizationStore(tmp_path / "data")
        store.add_dictionary_term("JiSpr Flow")
        llm = MockChatClient(["should never be called"])
        polisher = TranscriptPolisher(llm, store, level="none")

        from local_flow.asr.mock import MockTranscriber
        from local_flow.insertion.base import FakeTextSink

        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=polisher,
            store=store,
            sink=sink,
        )

        result = pipeline.process_transcript("the jispr flow rollout is on track")

        assert llm.requests == []
        assert result.used_llm is False
        assert "JiSpr Flow" in result.final
        assert sink.events[0] == ("insert", result.final)

    def test_snippet_and_dictation_command_still_apply_at_level_none(self, tmp_path):
        store = PersonalizationStore(tmp_path / "data")
        store.set_snippet("sig block", "Best regards,\nJay")
        llm = MockChatClient(["should never be called"])
        polisher = TranscriptPolisher(llm, store, level="none")

        from local_flow.asr.mock import MockTranscriber
        from local_flow.insertion.base import FakeTextSink

        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=polisher,
            store=store,
            sink=sink,
        )

        result = pipeline.process_transcript("add sig block press enter")

        assert llm.requests == []
        assert "Best regards,\nJay" in result.final
        assert result.actions == ["enter"]
