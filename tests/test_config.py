"""Config defaults, file loading, and env precedence."""

from pathlib import Path

import pytest

from local_flow.config import Config, _read_dotenv, load_config
from local_flow.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent


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


class TestMicPriorityField:
    """`mic_priority`: see local_flow/app.py `parse_mic_priority`/`_build_run_dependencies`."""

    def test_default_is_empty(self):
        assert load_config(env={}).mic_priority == ""

    def test_env_override(self):
        config = load_config(env={"LOCAL_FLOW_MIC_PRIORITY": "AirPods, USB"})
        assert config.mic_priority == "AirPods, USB"

    def test_file_override(self, tmp_path):
        config_file = tmp_path / "local-flow.toml"
        config_file.write_text('mic_priority = "AirPods"\n', encoding="utf-8")
        config = load_config(config_file=config_file, env={})
        assert config.mic_priority == "AirPods"


class TestVadPresetField:
    """`vad_preset`: see local_flow/app.py `_build_vad`."""

    def test_default_is_normal(self):
        assert load_config(env={}).vad_preset == "normal"

    def test_env_override_to_whisper(self):
        config = load_config(env={"LOCAL_FLOW_VAD_PRESET": "whisper"})
        assert config.vad_preset == "whisper"

    def test_invalid_value_raises_config_error_naming_valid_values(self):
        with pytest.raises(ConfigError, match="vad_preset") as excinfo:
            load_config(env={"LOCAL_FLOW_VAD_PRESET": "loud"})
        message = str(excinfo.value)
        assert "normal" in message
        assert "whisper" in message


class TestModeAndBackendValidation:
    """Review item 13: `mode`/`vad_backend`/`asr_backend`/`asr_language` were
    unvalidated, so a typo (`mode=handsfree`) silently ran push-to-talk and
    nothing recorded, or fell back to a different VAD. Each must fail at load
    with a ConfigError naming the allowed values.
    """

    def test_invalid_mode_raises_naming_valid_values(self):
        with pytest.raises(ConfigError, match="mode") as excinfo:
            load_config(env={"LOCAL_FLOW_MODE": "handsfree"})
        message = str(excinfo.value)
        assert "handsfree" in message
        assert "push-to-talk" in message
        assert "hands-free" in message

    def test_invalid_vad_backend_raises_naming_valid_values(self):
        with pytest.raises(ConfigError, match="vad_backend") as excinfo:
            load_config(env={"LOCAL_FLOW_VAD_BACKEND": "silero"})
        message = str(excinfo.value)
        assert "silero" in message
        assert "energy" in message
        assert "webrtc" in message
        assert "mock" in message

    def test_invalid_asr_backend_raises_naming_valid_values(self):
        with pytest.raises(ConfigError, match="asr_backend") as excinfo:
            load_config(env={"LOCAL_FLOW_ASR_BACKEND": "whisper.cpp"})
        message = str(excinfo.value)
        assert "whisper.cpp" in message
        assert "faster-whisper" in message
        assert "mock" in message

    def test_invalid_asr_language_raises_naming_valid_values(self):
        with pytest.raises(ConfigError, match="asr_language") as excinfo:
            load_config(env={"LOCAL_FLOW_ASR_LANGUAGE": "english"})
        message = str(excinfo.value)
        assert "english" in message
        assert "auto" in message
        assert "ISO 639" in message

    def test_uppercase_asr_language_is_rejected(self):
        # Whisper language codes are lowercase; "EN" would be passed through
        # to faster-whisper and fail at transcription time otherwise.
        with pytest.raises(ConfigError, match="asr_language"):
            load_config(env={"LOCAL_FLOW_ASR_LANGUAGE": "EN"})

    def test_valid_values_still_load(self):
        config = load_config(
            env={
                "LOCAL_FLOW_MODE": "hands-free",
                "LOCAL_FLOW_VAD_BACKEND": "webrtc",
                "LOCAL_FLOW_ASR_BACKEND": "mock",
                "LOCAL_FLOW_ASR_LANGUAGE": "auto",
            }
        )
        assert config.mode == "hands-free"
        assert config.vad_backend == "webrtc"
        assert config.asr_backend == "mock"
        assert config.asr_language == "auto"

    def test_two_and_three_letter_language_codes_are_accepted(self):
        # 2-letter ISO 639-1 ("fr") plus the 3-letter codes Whisper knows
        # (e.g. "yue" Cantonese, "haw" Hawaiian).
        assert load_config(env={"LOCAL_FLOW_ASR_LANGUAGE": "fr"}).asr_language == "fr"
        assert load_config(env={"LOCAL_FLOW_ASR_LANGUAGE": "yue"}).asr_language == "yue"


class TestTransformAndCommandHotkeyFields:
    """`transform_hotkey`/`transform_default`/`command_hotkey`/`auto_transform`
    (Phase 6 E8): see `local_flow.app._run_loop`/`_build_pipeline`.
    """

    def test_defaults_are_all_disabled(self):
        config = load_config(env={})
        assert config.transform_hotkey == ""
        assert config.transform_default == "Polish"
        assert config.command_hotkey == ""
        assert config.auto_transform == ""

    def test_env_overrides(self):
        config = load_config(
            env={
                "LOCAL_FLOW_TRANSFORM_HOTKEY": "f6",
                "LOCAL_FLOW_TRANSFORM_DEFAULT": "Prompt Engineer",
                "LOCAL_FLOW_COMMAND_HOTKEY": "f7",
                "LOCAL_FLOW_AUTO_TRANSFORM": "Polish",
            }
        )
        assert config.transform_hotkey == "f6"
        assert config.transform_default == "Prompt Engineer"
        assert config.command_hotkey == "f7"
        assert config.auto_transform == "Polish"

    def test_transform_hotkey_same_as_main_hotkey_rejected(self):
        with pytest.raises(ConfigError, match="transform_hotkey") as excinfo:
            load_config(
                env={"LOCAL_FLOW_HOTKEY": "f9", "LOCAL_FLOW_TRANSFORM_HOTKEY": "f9"}
            )
        assert "distinct" in excinfo.value.hint

    def test_command_hotkey_same_as_main_hotkey_rejected(self):
        with pytest.raises(ConfigError, match="command_hotkey"):
            load_config(
                env={"LOCAL_FLOW_HOTKEY": "f9", "LOCAL_FLOW_COMMAND_HOTKEY": "f9"}
            )

    def test_transform_hotkey_same_as_command_hotkey_rejected(self):
        with pytest.raises(ConfigError, match="command_hotkey"):
            load_config(
                env={
                    "LOCAL_FLOW_HOTKEY": "fn",
                    "LOCAL_FLOW_TRANSFORM_HOTKEY": "f6",
                    "LOCAL_FLOW_COMMAND_HOTKEY": "f6",
                }
            )

    def test_collision_check_is_case_insensitive(self):
        with pytest.raises(ConfigError, match="transform_hotkey"):
            load_config(
                env={"LOCAL_FLOW_HOTKEY": "F9", "LOCAL_FLOW_TRANSFORM_HOTKEY": "f9"}
            )

    def test_all_empty_by_default_never_collides(self):
        # transform_hotkey/command_hotkey both default to "": must not be
        # treated as colliding with each other or with the main hotkey.
        config = load_config(env={})
        assert config.transform_hotkey == config.command_hotkey == ""

    def test_distinct_hotkeys_are_accepted(self):
        config = load_config(
            env={
                "LOCAL_FLOW_HOTKEY": "fn",
                "LOCAL_FLOW_TRANSFORM_HOTKEY": "f6",
                "LOCAL_FLOW_COMMAND_HOTKEY": "f7",
            }
        )
        assert config.transform_hotkey == "f6"
        assert config.command_hotkey == "f7"


class TestScratchpadHotkeyField:
    """`scratchpad_hotkey` (Phase 7 E13): see `local_flow.app._run_loop`'s
    scratchpad-hotkey block and `local_flow.scratchpad`.
    """

    def test_default_is_disabled(self):
        config = load_config(env={})
        assert config.scratchpad_hotkey == ""

    def test_env_override(self):
        config = load_config(env={"LOCAL_FLOW_SCRATCHPAD_HOTKEY": "f8"})
        assert config.scratchpad_hotkey == "f8"

    def test_same_as_main_hotkey_rejected(self):
        with pytest.raises(ConfigError, match="scratchpad_hotkey") as excinfo:
            load_config(
                env={"LOCAL_FLOW_HOTKEY": "f9", "LOCAL_FLOW_SCRATCHPAD_HOTKEY": "f9"}
            )
        assert "distinct" in excinfo.value.hint

    def test_same_as_transform_hotkey_rejected(self):
        with pytest.raises(ConfigError, match="scratchpad_hotkey"):
            load_config(
                env={
                    "LOCAL_FLOW_HOTKEY": "fn",
                    "LOCAL_FLOW_TRANSFORM_HOTKEY": "f6",
                    "LOCAL_FLOW_SCRATCHPAD_HOTKEY": "f6",
                }
            )

    def test_same_as_command_hotkey_rejected(self):
        with pytest.raises(ConfigError, match="scratchpad_hotkey"):
            load_config(
                env={
                    "LOCAL_FLOW_HOTKEY": "fn",
                    "LOCAL_FLOW_COMMAND_HOTKEY": "f7",
                    "LOCAL_FLOW_SCRATCHPAD_HOTKEY": "f7",
                }
            )

    def test_collision_check_is_case_insensitive(self):
        with pytest.raises(ConfigError, match="scratchpad_hotkey"):
            load_config(
                env={"LOCAL_FLOW_HOTKEY": "F9", "LOCAL_FLOW_SCRATCHPAD_HOTKEY": "f9"}
            )

    def test_empty_never_collides_with_the_other_empty_hotkeys(self):
        config = load_config(env={})
        assert config.transform_hotkey == config.command_hotkey == config.scratchpad_hotkey == ""

    def test_all_four_distinct_hotkeys_are_accepted(self):
        config = load_config(
            env={
                "LOCAL_FLOW_HOTKEY": "fn",
                "LOCAL_FLOW_TRANSFORM_HOTKEY": "f6",
                "LOCAL_FLOW_COMMAND_HOTKEY": "f7",
                "LOCAL_FLOW_SCRATCHPAD_HOTKEY": "f8",
            }
        )
        assert config.scratchpad_hotkey == "f8"


class TestMaxUtteranceMinField:
    """`max_utterance_min`: see local_flow/app.py `_handle_utterance`."""

    def test_default_is_twenty(self):
        assert load_config(env={}).max_utterance_min == 20

    def test_env_override(self):
        config = load_config(env={"LOCAL_FLOW_MAX_UTTERANCE_MIN": "5"})
        assert config.max_utterance_min == 5

    def test_bad_numeric_value_raises_config_error(self):
        with pytest.raises(ConfigError, match="max_utterance_min"):
            load_config(env={"LOCAL_FLOW_MAX_UTTERANCE_MIN": "soon"})


class TestCancelHotkeyCollision:
    """`cancel_hotkey` joins the hotkey-distinctness check (review finding):
    it previously covered only hotkey/transform_hotkey/command_hotkey/
    scratchpad_hotkey pairwise, so setting `cancel_hotkey` to the same key as
    any of those silently created an undefined-winner race between two
    listeners on the same physical keypress.
    """

    def test_same_as_main_hotkey_rejected(self):
        with pytest.raises(ConfigError, match="cancel_hotkey") as excinfo:
            load_config(
                env={"LOCAL_FLOW_HOTKEY": "f9", "LOCAL_FLOW_CANCEL_HOTKEY": "f9"}
            )
        assert "distinct" in excinfo.value.hint

    def test_same_as_transform_hotkey_rejected(self):
        with pytest.raises(ConfigError, match="cancel_hotkey"):
            load_config(
                env={
                    "LOCAL_FLOW_HOTKEY": "fn",
                    "LOCAL_FLOW_TRANSFORM_HOTKEY": "f6",
                    "LOCAL_FLOW_CANCEL_HOTKEY": "f6",
                }
            )

    def test_default_esc_does_not_collide_with_default_hotkey(self):
        # cancel_hotkey defaults to "esc", the main hotkey to "fn" (macOS) or
        # "f9" elsewhere -- neither must ever be treated as a collision out
        # of the box.
        config = load_config(env={})
        assert config.cancel_hotkey == "esc"
        assert config.hotkey != config.cancel_hotkey

    def test_all_defaults_together_are_accepted(self):
        # Every hotkey field at its default (cancel_hotkey="esc", the others
        # empty/platform-default) must load without raising.
        config = load_config(env={})
        assert config.cancel_hotkey == "esc"
        assert config.transform_hotkey == config.command_hotkey == config.scratchpad_hotkey == ""


class TestReadDotenvInlineComments:
    """`_read_dotenv` (review finding): a copy-pasted `.env.example` line like
    ``LOCAL_FLOW_MOUSE_MODE=hold             # hold (press-and-hold) | ...``
    used to leave the trailing comment IN the parsed value (`"hold ... #
    ... toggle (click on/off)"`), which then failed `mouse_mode`'s enum
    check -- bricking onboarding for anyone who followed the README's "copy
    .env.example to .env" instruction. `_read_dotenv` now strips a trailing
    `` #``-prefixed (space-then-hash) inline comment from the value.
    """

    def test_inline_comment_is_stripped_to_a_clean_value(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "LOCAL_FLOW_MOUSE_MODE=hold             "
            "# hold (press-and-hold) | toggle (click on/off)\n",
            encoding="utf-8",
        )
        values = _read_dotenv(env_file)
        assert values["LOCAL_FLOW_MOUSE_MODE"] == "hold"

    def test_value_with_no_inline_comment_is_unaffected(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("LOCAL_FLOW_HOTKEY=f9\n", encoding="utf-8")
        assert _read_dotenv(env_file)["LOCAL_FLOW_HOTKEY"] == "f9"

    def test_quoted_value_with_inline_comment_still_strips_quotes(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            'LOCAL_FLOW_STYLE="default"   # a style name from styles.json\n',
            encoding="utf-8",
        )
        assert _read_dotenv(env_file)["LOCAL_FLOW_STYLE"] == "default"

    def test_hash_inside_double_quotes_is_preserved(self, tmp_path):
        # Review item 29: a ` #` inside a quoted value (e.g. a dir named
        # "my #notes") is part of the value, not an inline comment.
        env_file = tmp_path / ".env"
        env_file.write_text(
            'LOCAL_FLOW_DATA_DIR="/Users/me/my #notes"\n', encoding="utf-8"
        )
        assert _read_dotenv(env_file)["LOCAL_FLOW_DATA_DIR"] == "/Users/me/my #notes"

    def test_hash_inside_single_quotes_is_preserved(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "LOCAL_FLOW_DATA_DIR='/Users/me/my #notes'\n", encoding="utf-8"
        )
        assert _read_dotenv(env_file)["LOCAL_FLOW_DATA_DIR"] == "/Users/me/my #notes"

    def test_quoted_hash_value_with_trailing_comment_keeps_the_hash(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            'LOCAL_FLOW_DATA_DIR="/tmp/my #notes"   # where history lives\n',
            encoding="utf-8",
        )
        assert _read_dotenv(env_file)["LOCAL_FLOW_DATA_DIR"] == "/tmp/my #notes"

    def test_unterminated_quote_still_gets_comment_stripped(self, tmp_path):
        # No closing quote: fall back to the unquoted-value behavior
        # (comment stripped, stray quote removed), same as before.
        env_file = tmp_path / ".env"
        env_file.write_text('LOCAL_FLOW_HOTKEY="f9   # comment\n', encoding="utf-8")
        assert _read_dotenv(env_file)["LOCAL_FLOW_HOTKEY"] == "f9"


class TestEnvExampleFileIsValid:
    """Regression pin (review finding): `.env.example` copied verbatim to
    `.env`, exactly as the README instructs, must always produce a loadable
    config. Pins the whole file, not just the one line that broke it, so any
    future uncommented inline comment (or other stray value) fails this test
    instead of silently bricking onboarding again.
    """

    def test_env_example_loads_without_error(self, tmp_path):
        env_example = REPO_ROOT / ".env.example"
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")

        values = _read_dotenv(dotenv_path)
        config = load_config(env=values)

        assert isinstance(config, Config)
