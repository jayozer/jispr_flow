"""ASR config, vocabulary prompting, and builder-level validation."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import local_flow.app as app_module
from local_flow.app import _build_pipeline, _build_transcriber, _is_english_only_model
from local_flow.asr.faster_whisper_asr import (
    FasterWhisperTranscriber,
    resolve_language,
)
from local_flow.asr.mock import MockTranscriber
from local_flow.asr.vocabulary import build_initial_prompt
from local_flow.config import load_config
from local_flow.errors import ConfigError
from local_flow.insertion.base import FakeTextSink


class TestLanguageModelValidation:
    def test_auto_language_with_english_only_model_raises(self):
        config = load_config(
            env={
                "LOCAL_FLOW_ASR_BACKEND": "mock",
                "LOCAL_FLOW_ASR_LANGUAGE": "auto",
                "LOCAL_FLOW_ASR_MODEL": "small.en",
            }
        )
        with pytest.raises(ConfigError, match="small.en") as exc_info:
            _build_transcriber(config)
        assert "small" in exc_info.value.hint

    def test_specific_non_english_language_with_english_only_model_raises(self):
        config = load_config(
            env={
                "LOCAL_FLOW_ASR_BACKEND": "mock",
                "LOCAL_FLOW_ASR_LANGUAGE": "fr",
                "LOCAL_FLOW_ASR_MODEL": "small.en",
            }
        )
        with pytest.raises(ConfigError, match="small.en"):
            _build_transcriber(config)

    def test_auto_language_with_multilingual_model_builds_fine(self):
        config = load_config(
            env={
                "LOCAL_FLOW_ASR_BACKEND": "mock",
                "LOCAL_FLOW_ASR_LANGUAGE": "auto",
                "LOCAL_FLOW_ASR_MODEL": "small",
            }
        )
        transcriber = _build_transcriber(config)
        assert isinstance(transcriber, MockTranscriber)

    def test_default_english_language_with_english_only_model_builds_fine(self):
        config = load_config(
            env={
                "LOCAL_FLOW_ASR_BACKEND": "mock",
                "LOCAL_FLOW_ASR_MODEL": "small.en",
            }
        )
        assert config.asr_language == "en"
        transcriber = _build_transcriber(config)
        assert isinstance(transcriber, MockTranscriber)

    def test_mlx_english_only_repo_is_detected(self):
        assert _is_english_only_model("mlx-community/whisper-small.en-mlx")
        assert not _is_english_only_model("mlx-community/whisper-small-mlx")

    def test_mlx_backend_rejects_non_apple_silicon_before_optional_import(
        self, monkeypatch
    ):
        config = load_config(
            env={
                "LOCAL_FLOW_ASR_BACKEND": "mlx-whisper",
                "LOCAL_FLOW_ASR_MODEL": "mlx-community/whisper-small.en-mlx",
            }
        )
        monkeypatch.setattr(app_module.sys, "platform", "linux")

        with pytest.raises(ConfigError, match="Apple-Silicon"):
            _build_transcriber(config)

    def test_parakeet_backend_rejects_non_apple_silicon_before_optional_import(
        self, monkeypatch
    ):
        config = load_config(
            env={
                "LOCAL_FLOW_ASR_BACKEND": "mlx-parakeet",
                "LOCAL_FLOW_ASR_MODEL": "mlx-community/parakeet-tdt-0.6b-v3",
                "LOCAL_FLOW_ASR_LANGUAGE": "auto",
            }
        )
        monkeypatch.setattr(app_module.sys, "platform", "linux")

        with pytest.raises(ConfigError, match="Apple-Silicon"):
            _build_transcriber(config)


class TestMockTranscriberLanguageKwarg:
    def test_accepts_and_ignores_language_kwarg(self):
        transcriber = MockTranscriber(["hello"], language="auto")
        assert transcriber.transcribe(b"\x00\x00", 16000) == "hello"


class TestResolveLanguage:
    """Pure mapping used by `FasterWhisperTranscriber.transcribe`.

    Tested standalone (rather than via the real class) because constructing
    `FasterWhisperTranscriber` loads an actual Whisper model.
    """

    def test_auto_maps_to_none(self):
        assert resolve_language("auto") is None

    def test_auto_is_case_insensitive(self):
        assert resolve_language("AUTO") is None
        assert resolve_language("Auto") is None

    def test_specific_code_passes_through_unchanged(self):
        assert resolve_language("en") == "en"
        assert resolve_language("fr") == "fr"

    def test_none_passes_through_unchanged(self):
        assert resolve_language(None) is None


class TestMockTranscriberLanguageProperty:
    """Same settable-property shape as `FasterWhisperTranscriber.language`."""

    def test_getter_returns_constructor_value(self):
        transcriber = MockTranscriber(["hi"], language="en")
        assert transcriber.language == "en"

    def test_default_language_is_none(self):
        transcriber = MockTranscriber(["hi"])
        assert transcriber.language is None

    def test_setter_updates_the_stored_value(self):
        transcriber = MockTranscriber(["hi"], language="en")
        transcriber.language = "fr"
        assert transcriber.language == "fr"


class TestInitialPrompt:
    def test_normalizes_deduplicates_and_preserves_priority_order(self):
        prompt = build_initial_prompt(
            ["JiSpr Flow", "  PostgreSQL\nServer ", "jispr flow", ""]
        )

        assert prompt == "Important vocabulary: JiSpr Flow, PostgreSQL Server"

    def test_prompt_is_bounded_without_splitting_terms(self):
        prompt = build_initial_prompt(
            ["first", "term that is much too long", "last"], max_chars=35
        )

        assert prompt == "Important vocabulary: first, last"
        assert len(prompt) <= 35

    def test_too_small_limit_disables_prompt(self):
        assert build_initial_prompt(["JiSpr"], max_chars=10) == ""


class _FakeWhisperModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append((audio, options))
        return [SimpleNamespace(text=" transcript ")], None


def _fake_faster_whisper(provider):
    transcriber = object.__new__(FasterWhisperTranscriber)
    transcriber._model = _FakeWhisperModel()
    transcriber._language = "en"
    transcriber._vocabulary_provider = provider
    return transcriber


class TestDynamicVocabularyBoosting:
    def test_live_and_file_calls_refresh_the_prompt(self):
        terms = ["JiSpr Flow"]
        transcriber = _fake_faster_whisper(lambda: terms)

        assert transcriber.transcribe(b"\x00\x00", 16000) == "transcript"
        terms.append("PostgreSQL")
        assert transcriber.transcribe_path(Path("memo.wav")) == "transcript"

        first_options = transcriber._model.calls[0][1]
        second_options = transcriber._model.calls[1][1]
        assert first_options["initial_prompt"] == "Important vocabulary: JiSpr Flow"
        assert second_options["initial_prompt"] == (
            "Important vocabulary: JiSpr Flow, PostgreSQL"
        )

    def test_empty_dictionary_omits_initial_prompt_for_compatibility(self):
        transcriber = _fake_faster_whisper(lambda: [])

        transcriber.transcribe(b"\x00\x00", 16000)

        assert "initial_prompt" not in transcriber._model.calls[0][1]

    def test_pipeline_attaches_live_store_provider(self, tmp_path):
        class VocabularyCapturingMock(MockTranscriber):
            def set_vocabulary_provider(self, provider):
                self.provider = provider

        transcriber = VocabularyCapturingMock(["hello"])
        config = load_config(
            env={
                "LOCAL_FLOW_ASR_BACKEND": "mock",
                "LOCAL_FLOW_DATA_DIR": str(tmp_path),
                "LOCAL_FLOW_CONTEXT_AWARENESS": "false",
            }
        )

        pipeline = _build_pipeline(
            config, chat_client=None, sink=FakeTextSink(), transcriber=transcriber
        )
        pipeline.store.add_dictionary_term("newly added term")

        assert transcriber.provider() == ["newly added term"]
