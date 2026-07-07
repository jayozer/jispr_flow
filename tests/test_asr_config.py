"""ASR language config: builder-level validation for language/model combos."""

import pytest

from local_flow.app import _build_transcriber
from local_flow.asr.mock import MockTranscriber
from local_flow.config import load_config
from local_flow.errors import ConfigError


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


class TestMockTranscriberLanguageKwarg:
    def test_accepts_and_ignores_language_kwarg(self):
        transcriber = MockTranscriber(["hello"], language="auto")
        assert transcriber.transcribe(b"\x00\x00", 16000) == "hello"
