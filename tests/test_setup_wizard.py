"""Tests for the ``local-flow setup`` onboarding wizard."""

from __future__ import annotations

import tomllib

import pytest

from local_flow.config import Config, load_config
from local_flow.errors import ConfigError
from local_flow.setup_wizard import run_wizard


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
        loaded = load_config(config_file=target)
        assert loaded.hotkey == "fn"

    def test_non_macos_hotkey_default_is_f9(self, tmp_path, monkeypatch):
        monkeypatch.setattr("local_flow.setup_wizard.sys.platform", "linux")
        config = Config(data_dir=tmp_path / "data")
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
        assert data["hotkey"] == "f9"


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

        assert calls == ["faster_whisper", "sounddevice", "pynput", "pyperclip", "pystray"]
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
