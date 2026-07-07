"""Config defaults, file loading, and env precedence."""

from pathlib import Path

import pytest

from local_flow.config import Config, load_config
from local_flow.errors import ConfigError


class TestDefaults:
    def test_lmstudio_defaults_to_localhost(self):
        config = load_config(env={})
        assert config.lmstudio_base_url == "http://localhost:1234/v1"
        assert config.asr_backend == "faster-whisper"
        assert config.vad_backend == "energy"
        assert config.mode == "push-to-talk"


class TestEnvOverrides:
    def test_env_values_are_applied_and_coerced(self):
        config = load_config(
            env={
                "LOCAL_FLOW_LMSTUDIO_BASE_URL": "http://localhost:9999/v1",
                "LOCAL_FLOW_LMSTUDIO_TIMEOUT": "12.5",
                "LOCAL_FLOW_VAD_SILENCE_MS": "800",
                "LOCAL_FLOW_DATA_DIR": "~/somewhere",
            }
        )
        assert config.lmstudio_base_url == "http://localhost:9999/v1"
        assert config.lmstudio_timeout == 12.5
        assert config.vad_silence_ms == 800
        assert config.data_dir == Path("~/somewhere").expanduser()

    def test_bad_numeric_value_raises_config_error(self):
        with pytest.raises(ConfigError, match="lmstudio_timeout"):
            load_config(env={"LOCAL_FLOW_LMSTUDIO_TIMEOUT": "soon"})


class TestConfigFile:
    def test_file_values_apply_but_env_wins(self, tmp_path):
        config_file = tmp_path / "local-flow.toml"
        config_file.write_text(
            'lmstudio_model = "from-file"\nhotkey = "f8"\n', encoding="utf-8"
        )
        config = load_config(
            config_file=config_file,
            env={"LOCAL_FLOW_HOTKEY": "f7"},
        )
        assert config.lmstudio_model == "from-file"  # file applies
        assert config.hotkey == "f7"  # env beats file

    def test_unknown_key_in_file_is_rejected(self, tmp_path):
        config_file = tmp_path / "local-flow.toml"
        config_file.write_text('no_such_key = 1\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="no_such_key"):
            load_config(config_file=config_file, env={})

    def test_invalid_toml_is_rejected(self, tmp_path):
        config_file = tmp_path / "local-flow.toml"
        config_file.write_text("this is not toml ===", encoding="utf-8")
        with pytest.raises(ConfigError, match="TOML"):
            load_config(config_file=config_file, env={})

    def test_missing_explicit_config_errors(self):
        with pytest.raises(ConfigError, match="does not exist"):
            load_config(env={"LOCAL_FLOW_CONFIG": "/nonexistent/path.toml"})


class TestConfigObject:
    def test_frozen(self):
        config = Config()
        with pytest.raises(AttributeError):
            config.hotkey = "f1"  # type: ignore[misc]

    def test_history_enabled_false_from_env(self):
        config = load_config(env={"LOCAL_FLOW_HISTORY_ENABLED": "false"})
        assert config.history_enabled is False

    def test_context_styles_defaults_true(self):
        assert load_config(env={}).context_styles is True

    def test_context_styles_false_from_env(self):
        config = load_config(env={"LOCAL_FLOW_CONTEXT_STYLES": "false"})
        assert config.context_styles is False


class TestHotkeyDefaults:
    def test_hotkey_defaults_to_fn_on_macos(self, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "darwin")
        config = load_config(env={})
        assert config.hotkey == "fn"

    def test_hotkey_defaults_to_f9_elsewhere(self, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "linux")
        config = load_config(env={})
        assert config.hotkey == "f9"

    def test_space_hold_ms_and_cancel_hotkey(self):
        config = load_config(
            env={
                "LOCAL_FLOW_HOTKEY_SPACE_HOLD_MS": "300",
                "LOCAL_FLOW_CANCEL_HOTKEY": "f12",
            }
        )
        assert config.hotkey_space_hold_ms == 300
        assert config.cancel_hotkey == "f12"

    def test_space_hold_ms_defaults(self):
        config = load_config(env={})
        assert config.hotkey_space_hold_ms == 250
        assert config.cancel_hotkey == "esc"


class TestLanguagesField:
    """`languages`: comma-separated codes for the tray's Language quick-switch."""

    def test_defaults_to_empty_string(self):
        assert load_config(env={}).languages == ""

    def test_env_override(self):
        config = load_config(env={"LOCAL_FLOW_LANGUAGES": "en,de,fr"})
        assert config.languages == "en,de,fr"

    def test_file_override(self, tmp_path):
        config_file = tmp_path / "local-flow.toml"
        config_file.write_text('languages = "en, de"\n', encoding="utf-8")
        config = load_config(config_file=config_file, env={})
        assert config.languages == "en, de"


class TestStreamingField:
    """`streaming`/`streaming_pause_ms`: see local_flow/app.py `_run_loop`."""

    def test_defaults(self):
        config = load_config(env={})
        assert config.streaming == "off"
        assert config.streaming_pause_ms == 300

    def test_env_override(self):
        config = load_config(
            env={
                "LOCAL_FLOW_STREAMING": "sentence",
                "LOCAL_FLOW_STREAMING_PAUSE_MS": "150",
            }
        )
        assert config.streaming == "sentence"
        assert config.streaming_pause_ms == 150

    def test_invalid_value_raises_config_error_naming_valid_values(self):
        with pytest.raises(ConfigError, match="streaming") as excinfo:
            load_config(env={"LOCAL_FLOW_STREAMING": "banana"})
        message = str(excinfo.value)
        assert "off" in message
        assert "sentence" in message
        assert "live-preview" in message
