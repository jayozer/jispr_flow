"""Tests for the ``local-flow setup`` onboarding wizard."""

from __future__ import annotations

import tomllib

import pytest

from local_flow.config import Config, load_config
from local_flow.errors import ConfigError
from local_flow.setup_wizard import _hotkey_options, run_wizard


def _scripted_ask(answers):
    """Return an ``ask`` callable that yields ``answers`` in order."""
    queue = list(answers)

    def ask(_prompt: str) -> str:
        return queue.pop(0)

    return ask


def _say_recorder():
    messages: list[str] = []
    return messages.append, messages


def _stub_probes():
    return (lambda _module: True), (lambda: (True, "qwen2.5-7b-instruct"))


class TestHappyPathDefaults:
    def test_defaults_only_writes_expected_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # 4 questions with the multilingual model NOT chosen: hotkey, mode,
        # asr model, style -- all answered with "" (accept default).
        ask = _scripted_ask(["", "", "", ""])

        result = run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        assert result == target
        assert target.exists()
        data = tomllib.loads(target.read_text())
        assert data["hotkey"] == "fn"  # darwin default
        assert data["mode"] == "push-to-talk"
        assert data["asr_model"] == "small.en"
        assert data["style"] == "default"
        assert "asr_language" not in data  # english-only model skips the question

        # The file must also validate on its own via the real load_config.
        # This fixture verifies the file the wizard wrote. Keep a developer's
        # repository-local .env from overriding that isolated config.
        loaded = load_config(config_file=target, env={})
        assert loaded.hotkey == "fn"

    def test_non_macos_hotkey_default_is_f9(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "linux")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        say, messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()
        ask = _scripted_ask(["", "", "", ""])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["hotkey"] == "f9"
        # Linux firmware swallows fn and the hotkey factory rejects space, so
        # neither should ever be offered.
        assert not any("space" in m.lower() for m in messages)
        assert not any(m.strip().startswith("1. fn") for m in messages)


class TestHotkeyOptionsByPlatform:
    """``_hotkey_options()`` must never offer "space" on Linux (the hotkey
    factory rejects it there), while macOS and other non-Linux platforms
    (e.g. Windows) keep offering it."""

    def test_darwin_offers_fn_space_f9(self, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        options, default = _hotkey_options()
        assert options == ["fn", "space", "f9"]
        assert default == "fn"

    def test_linux_offers_only_f9(self, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "linux")
        options, default = _hotkey_options()
        assert options == ["f9"]
        assert default == "f9"

    def test_other_non_macos_still_offers_space(self, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "win32")
        options, default = _hotkey_options()
        assert options == ["space", "f9"]
        assert default == "f9"


class TestAnswerValidation:
    def test_invalid_then_valid_answer_is_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        say, messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # hotkey: default; mode: bogus then valid "hands-free"; asr: default; style: default
        ask = _scripted_ask(["", "bogus", "hands-free", "", ""])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["mode"] == "hands-free"
        assert any("Invalid choice" in m for m in messages)

    def test_three_invalid_answers_falls_back_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        say, messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # hotkey: default; mode: 3 invalid answers; asr: default; style: default
        ask = _scripted_ask(["", "nope", "nah", "still-no", "", ""])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["mode"] == "push-to-talk"  # fell back to the default
        assert any("Too many invalid attempts" in m for m in messages)

    def test_answer_by_number_selects_option(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # hotkey: "2" (space); mode: default; asr: default; style: default
        ask = _scripted_ask(["2", "", "", ""])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["hotkey"] == "space"


class TestMultilingualLanguageQuestion:
    def test_multilingual_model_asks_language(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # hotkey: default; mode: default; asr: "2" -> small (multilingual);
        # language: "auto"; style: default
        ask = _scripted_ask(["", "", "2", "auto", ""])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["asr_model"] == "small"
        assert data["asr_language"] == "auto"

    def test_english_only_model_skips_language_question(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # hotkey: default; mode: default; asr: "1" -> small.en; style: default
        # (only 4 answers -- if a language question were (wrongly) asked, the
        # scripted queue would run out and raise IndexError)
        ask = _scripted_ask(["", "", "1", ""])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["asr_model"] == "small.en"
        assert "asr_language" not in data


class TestOverwriteConfirmation:
    def test_decline_overwrite_leaves_file_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        original = 'hotkey = "f9"\n'
        target.write_text(original)
        say, messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # 4 question answers, then decline the overwrite.
        ask = _scripted_ask(["", "", "", "", "n"])

        result = run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        assert result == target
        assert target.read_text() == original
        assert any("keeping existing config" in m for m in messages)

    def test_accept_overwrite_rewrites_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        target.write_text('hotkey = "f9"\n')
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # hotkey: "space"; mode/asr/style: defaults; then confirm overwrite.
        ask = _scripted_ask(["space", "", "", "", "y"])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["hotkey"] == "space"

    def test_accept_overwrite_preserves_unasked_keys(self, tmp_path, monkeypatch):
        """Review item 12: overwriting must merge with the existing config,
        not replace it -- a customized `data_dir` (or any other key the
        wizard never asked about) survives a wizard re-run, with its TOML
        type intact, while the answered keys are updated.
        """
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        custom_data_dir = str(tmp_path / "custom-data")
        target.write_text(
            'hotkey = "f9"\n'
            f'data_dir = "{custom_data_dir}"\n'
            "vad_silence_ms = 900\n"
            "context_styles = false\n"
            "lmstudio_timeout = 30.5\n",
            encoding="utf-8",
        )
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # hotkey: "space"; mode/asr/style: defaults; then confirm overwrite.
        ask = _scripted_ask(["space", "", "", "", "y"])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["hotkey"] == "space"  # asked key: updated
        assert data["data_dir"] == custom_data_dir  # unasked keys: preserved
        assert data["vad_silence_ms"] == 900
        assert data["context_styles"] is False
        assert data["lmstudio_timeout"] == 30.5

    @pytest.mark.parametrize(
        ("model_answer", "expected_model", "stale_language"),
        [("1", "small.en", "auto"), ("3", "base.en", "fr")],
    )
    def test_english_only_model_drops_stale_multilingual_language(
        self,
        tmp_path,
        monkeypatch,
        model_answer,
        expected_model,
        stale_language,
    ):
        """An overwrite that switches from a multilingual model to an
        English-only model must not merge the old language back in after the
        wizard intentionally skips the language question.
        """
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(
            data_dir=tmp_path / "data",
            asr_model="small",
            asr_language=stale_language,
        )
        target = tmp_path / "config.toml"
        target.write_text(
            'asr_model = "small"\n'
            f'asr_language = "{stale_language}"\n'
            "vad_silence_ms = 900\n",
            encoding="utf-8",
        )
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # hotkey, mode, English-only model, style, confirm overwrite. There
        # is deliberately no language answer because .en skips that prompt.
        ask = _scripted_ask(["", "", model_answer, "", "y"])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["asr_model"] == expected_model
        assert "asr_language" not in data
        assert data["vad_silence_ms"] == 900  # unrelated merge behavior remains

        # Validate the generated file itself, independent of a developer's
        # repository-local .env.
        loaded = load_config(config_file=target, env={})
        assert loaded.asr_model == expected_model
        assert loaded.asr_language == "en"

    def test_overwrite_of_unparseable_config_warns_and_writes_wizard_keys(
        self, tmp_path, monkeypatch
    ):
        """A corrupt existing config can't have its keys preserved; the wizard
        must say so and still write a valid config from its own answers."""
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        target.write_text("not [ valid = toml\n", encoding="utf-8")
        say, messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()
        ask = _scripted_ask(["", "", "", "", "y"])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["hotkey"] == "fn"  # darwin default answered by Enter
        assert any("could not parse" in m for m in messages)


class TestConfigErrorCleansUp:
    def test_config_error_removes_the_written_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")

        def _broken_load_config(*, config_file):
            raise ConfigError("boom", hint="fix it")

        monkeypatch.setattr("local_flow.setup_wizard.load_config", _broken_load_config)

        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()
        ask = _scripted_ask(["", "", "", ""])

        with pytest.raises(ConfigError):
            run_wizard(
                config,
                ask=ask,
                say=say,
                target=target,
                probe_import=probe_import,
                probe_lmstudio=probe_lmstudio,
            )

        assert not target.exists()
        assert list(tmp_path.glob("*.toml")) == []  # no stray temp file left behind

    def test_config_error_after_overwrite_preserves_original(self, tmp_path, monkeypatch):
        """A pre-existing config must survive a validation failure untouched:
        the wizard writes to a temp file and only swaps it in on success."""
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")

        def _broken_load_config(*, config_file):
            raise ConfigError("boom", hint="fix it")

        monkeypatch.setattr("local_flow.setup_wizard.load_config", _broken_load_config)

        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        original = b'hotkey = "f9"\n'
        target.write_bytes(original)
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # 4 question answers (defaults), then accept the overwrite.
        ask = _scripted_ask(["", "", "", "", "y"])

        with pytest.raises(ConfigError):
            run_wizard(
                config,
                ask=ask,
                say=say,
                target=target,
                probe_import=probe_import,
                probe_lmstudio=probe_lmstudio,
            )

        assert target.read_bytes() == original
        assert list(tmp_path.glob("*.toml")) == [target]


class TestProbeReporting:
    def test_probes_reported_via_say(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        say, messages = _say_recorder()

        calls: list[str] = []

        def probe_import(module: str) -> bool:
            calls.append(module)
            return module != "pystray"  # pretend everything but pystray is installed

        probe_lmstudio = lambda: (False, "connection refused")  # noqa: E731

        ask = _scripted_ask(["", "", "", ""])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        assert calls == [
            "faster_whisper",
            "mlx_whisper",
            "sounddevice",
            "pynput",
            "pyperclip",
            "pystray",
        ]
        assert any("pystray" in m and "missing" in m and "tray" in m for m in messages)
        assert any("faster_whisper" in m and "installed" in m for m in messages)
        assert any("unreachable" in m and "connection refused" in m for m in messages)

    def test_next_steps_printed_on_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(data_dir=tmp_path / "data")
        target = tmp_path / "config.toml"
        say, messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()
        ask = _scripted_ask(["", "", "", ""])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        joined = "\n".join(messages)
        assert "local-flow check" in joined
        assert "local-flow run" in joined
        assert "Accessibility" in joined  # darwin-specific reminder


class TestStyleChoices:
    def test_custom_styles_are_offered(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        data_dir = tmp_path / "data"
        from local_flow.personalization.store import PersonalizationStore

        store = PersonalizationStore(data_dir)
        # Confirm "default" plus the built-ins are already available, and
        # picking a non-default one by name works.
        assert "professional" in store.styles()

        config = Config(data_dir=data_dir)
        target = tmp_path / "config.toml"
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        ask = _scripted_ask(["", "", "", "professional"])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["style"] == "professional"


class TestDefaultsSeededFromLiveConfig:
    """Question defaults come from the passed-in ``Config`` when its value is
    among the offered options, else the hardcoded factory fallback."""

    def test_round_trips_config_values_on_enter(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        config = Config(
            data_dir=tmp_path / "data",
            hotkey="space",
            mode="hands-free",
            asr_model="small",
            asr_language="auto",
        )
        target = tmp_path / "config.toml"
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()

        # hotkey, mode, asr model, asr language ("small" triggers the
        # language question), style -- all "" (accept the seeded defaults).
        ask = _scripted_ask(["", "", "", "", ""])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["hotkey"] == "space"
        assert data["mode"] == "hands-free"
        assert data["asr_model"] == "small"
        assert data["asr_language"] == "auto"

    def test_un_offered_model_falls_back_to_hardcoded_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        # "medium" isn't one of the ASR models the wizard offers.
        config = Config(data_dir=tmp_path / "data", asr_model="medium")
        target = tmp_path / "config.toml"
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()
        ask = _scripted_ask(["", "", "", ""])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["asr_model"] == "small.en"
        assert "asr_language" not in data

    def test_style_default_is_active_style_from_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "darwin")
        data_dir = tmp_path / "data"
        from local_flow.personalization.store import PersonalizationStore

        store = PersonalizationStore(data_dir)
        store.set_active_style("professional")

        config = Config(data_dir=data_dir)
        target = tmp_path / "config.toml"
        say, _messages = _say_recorder()
        probe_import, probe_lmstudio = _stub_probes()
        ask = _scripted_ask(["", "", "", ""])

        run_wizard(
            config,
            ask=ask,
            say=say,
            target=target,
            probe_import=probe_import,
            probe_lmstudio=probe_lmstudio,
        )

        data = tomllib.loads(target.read_text())
        assert data["style"] == "professional"
