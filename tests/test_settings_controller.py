"""Headless controller coverage for the native Settings surface."""

from __future__ import annotations

import sys

import local_flow.app as app_module
from local_flow.app import main
from local_flow.settings.controller import ASR_PRESETS, PARAKEET_V3, SettingsController
from local_flow.settings.service import SettingsService


def _controller(tmp_path, *, client_factory=None):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'data_dir = "{tmp_path / "data"}"\n'
        'asr_profile = "custom"\n'
        'asr_backend = "mlx-parakeet"\n'
        f'asr_model = "{PARAKEET_V3}"\n'
        'asr_language = "auto"\n'
    )
    service = SettingsService(env={"LOCAL_FLOW_CONFIG": str(config_path)})
    kwargs = {"service": service}
    if client_factory is not None:
        kwargs["client_factory"] = client_factory
    return SettingsController(**kwargs)


def test_presets_expose_only_parakeet_v3(tmp_path):
    controller = _controller(tmp_path)

    assert controller.preset("Parakeet v3")["asr_model"] == PARAKEET_V3
    assert set(ASR_PRESETS) == {"Whisper Turbo", "Parakeet v3", "Custom"}
    assert all("lmstudio_model" not in values for values in ASR_PRESETS.values())
    assert "v2" not in repr(ASR_PRESETS)


def test_matching_preset_uses_only_asr_fields(tmp_path):
    controller = _controller(tmp_path)
    config = controller.load().snapshot.config

    assert controller.matching_preset(config) == "Parakeet v3"
    whisper = controller.preset("Whisper Turbo")
    changed = type(config)(**{**config.__dict__, **whisper})
    assert controller.matching_preset(changed) == "Whisper Turbo"


def test_controller_load_save_and_personalization_crud(tmp_path):
    controller = _controller(tmp_path)
    initial = controller.load()
    assert initial.snapshot.config.asr_backend == "mlx-parakeet"

    updated = controller.save({"cleanup_level": "light"})
    assert updated.snapshot.config.cleanup_level == "light"
    updated = controller.save({"asr_language": "it"})
    assert updated.snapshot.config.asr_language == "it"

    assert controller.add_dictionary("JiSpr Flow")
    assert controller.update_dictionary("JiSpr Flow", "JiSpr", starred=True)
    controller.set_alias("jisper", "JiSpr")
    assert controller.update_alias("jisper", "juice per", "JiSpr")
    loaded = controller.load()
    assert loaded.dictionary_entries[0]["starred"] is True
    assert loaded.aliases == {"juice per": "JiSpr"}
    assert controller.remove_dictionary("JiSpr")
    assert controller.remove_alias("juice per")


def test_refresh_models_reports_loaded_and_empty_states(tmp_path):
    class Client:
        def __init__(self, **_kwargs):
            self.closed = False

        def list_models(self):
            return ["gemma-local", "qwen-local"]

        def close(self):
            self.closed = True

    controller = _controller(tmp_path, client_factory=Client)

    models, status = controller.refresh_models()

    assert models == ["gemma-local", "qwen-local"]
    assert "2 model" in status


def test_controller_import_does_not_import_appkit():
    assert "local_flow.settings.macos" not in sys.modules


def test_settings_command_is_actionable_off_macos(monkeypatch, capsys):
    monkeypatch.setattr(app_module.sys, "platform", "linux")

    code = main(["settings"])

    assert code == 1
    assert "only on macOS" in capsys.readouterr().err
