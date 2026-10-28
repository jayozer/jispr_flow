"""Headless tests for Settings provenance and atomic config persistence."""

from __future__ import annotations

import tomllib

import pytest

from local_flow.config import load_config_snapshot
from local_flow.errors import ConfigError
from local_flow.settings.service import SettingsService, is_settings_editable


def test_snapshot_reports_toml_environment_and_profile_sources(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('asr_profile = "accuracy"\npill_style = "expanded"\n')

    snapshot = load_config_snapshot(
        config_file=path,
        env={"LOCAL_FLOW_CLEANUP_LEVEL": "light"},
    )

    assert snapshot.config_path == path
    assert snapshot.sources["pill_style"] == "toml"
    assert snapshot.sources["cleanup_level"] == "environment"
    assert snapshot.sources["asr_model"] == "profile:toml"
    assert snapshot.config.asr_model == "mlx-community/whisper-large-v3-turbo"
    assert snapshot.sources["history_enabled"] == "default"


def test_save_merges_ui_fields_and_preserves_unrelated_settings(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('vad_silence_ms = 777\npill_style = "compact"\n')
    service = SettingsService(env={"LOCAL_FLOW_CONFIG": str(path)})

    snapshot = service.save({"pill_style": "expanded", "cleanup_level": "light"})

    data = tomllib.loads(path.read_text())
    assert data["vad_silence_ms"] == 777
    assert data["pill_style"] == "expanded"
    assert snapshot.config.cleanup_level == "light"
    assert snapshot.sources["cleanup_level"] == "toml"


@pytest.mark.parametrize("profile", ["fast", "accuracy"])
@pytest.mark.parametrize("raw_backend", [None, "faster-whisper"])
def test_custom_profile_persists_explicit_mlx_backend_and_verified_local_path(
    tmp_path, profile, raw_backend
):
    path = tmp_path / "config.toml"
    path.write_text(
        f'asr_profile = "{profile}"\n'
        'pill_position = "caret"\n'
        'style = "professional"\n'
        + (f'asr_backend = "{raw_backend}"\n' if raw_backend is not None else "")
    )
    service = SettingsService(env={"LOCAL_FLOW_CONFIG": str(path)})
    before = service.load()
    assert before.config.asr_backend == "mlx-whisper"
    assert before.sources["asr_backend"] == "profile:toml"
    local_model = str(tmp_path / "models" / "whisper-small-en-mlx" / "verified-checkpoint")

    saved = service.save({
        "asr_profile": "custom",
        "asr_backend": "mlx-whisper",
        "asr_model": local_model,
        "asr_language": "en",
        "asr_device": "auto",
        "asr_compute_type": "int8",
        "style": "professional",
        "floating_pill": True,
    })

    assert tomllib.loads(path.read_text()) == {
        "asr_profile": "custom",
        "asr_backend": "mlx-whisper",
        "asr_model": local_model,
        "asr_language": "en",
        "asr_device": "auto",
        "asr_compute_type": "int8",
        "pill_position": "caret",
        "style": "professional",
    }
    reloaded = service.load()
    assert saved == reloaded
    assert reloaded.config.asr_backend == "mlx-whisper"
    assert reloaded.config.asr_model == local_model
    assert reloaded.sources["asr_backend"] == "toml"


@pytest.mark.parametrize("profile", ["fast", "accuracy"])
def test_custom_profile_persists_explicit_model_equal_to_old_effective_preset(tmp_path, profile):
    path = tmp_path / "config.toml"
    path.write_text(f'asr_profile = "{profile}"\nasr_model = "small.en"\n')
    service = SettingsService(env={"LOCAL_FLOW_CONFIG": str(path)})
    before = service.load().config

    saved = service.save({
        "asr_profile": "custom", "asr_backend": before.asr_backend,
        "asr_model": before.asr_model,
    })

    assert saved.config.asr_backend == before.asr_backend
    assert saved.config.asr_model == before.asr_model
    assert tomllib.loads(path.read_text()) == {
        "asr_profile": "custom", "asr_backend": before.asr_backend,
        "asr_model": before.asr_model,
    }


def test_custom_profile_does_not_persist_unsupplied_settings_or_matching_environment(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('asr_profile = "accuracy"\npill_position = "caret"\n')
    service = SettingsService(env={
        "LOCAL_FLOW_CONFIG": str(path), "LOCAL_FLOW_ASR_BACKEND": "mlx-whisper",
    })

    saved = service.save({"asr_profile": "custom", "asr_backend": "mlx-whisper"})

    assert saved.config.asr_backend == "mlx-whisper"
    assert saved.sources["asr_backend"] == "environment"
    assert tomllib.loads(path.read_text()) == {
        "asr_profile": "custom", "pill_position": "caret",
    }


@pytest.mark.parametrize("overrides", [
    {"LOCAL_FLOW_ASR_BACKEND": "faster-whisper"},
    {"LOCAL_FLOW_ASR_PROFILE": "accuracy"},
])
def test_custom_profile_cannot_bypass_environment_ownership(tmp_path, overrides):
    path = tmp_path / "config.toml"
    original = 'asr_profile = "accuracy"\npill_position = "caret"\n'
    path.write_text(original)
    service = SettingsService(env={"LOCAL_FLOW_CONFIG": str(path), **overrides})

    with pytest.raises(ConfigError, match="overridden by environment"):
        service.save({
            "asr_profile": "custom", "asr_backend": "mlx-whisper",
            "asr_model": str(tmp_path / "verified-model"),
        })

    assert path.read_text() == original
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("profile", ["custom", "accuracy"])
def test_unchanged_profile_and_effective_asr_values_do_not_rewrite_settings(tmp_path, profile):
    path = tmp_path / "config.toml"
    original = f'# Preserve an unchanged settings file.\nasr_profile = "{profile}"\n'
    path.write_text(original)
    service = SettingsService(env={"LOCAL_FLOW_CONFIG": str(path)})
    before = service.load().config

    service.save({
        "asr_profile": profile, "asr_backend": before.asr_backend,
        "asr_model": before.asr_model, "asr_language": before.asr_language,
    })

    assert path.read_text() == original


def test_invalid_candidate_leaves_original_byte_identical(tmp_path):
    path = tmp_path / "config.toml"
    original = 'pill_style = "compact"\n'
    path.write_text(original)
    service = SettingsService(env={"LOCAL_FLOW_CONFIG": str(path)})

    with pytest.raises(ConfigError, match="cleanup_level"):
        service.save({"cleanup_level": "dangerously-creative"})

    assert path.read_text() == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_environment_owned_field_cannot_pretend_to_save(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('pill_style = "compact"\n')
    service = SettingsService(
        env={
            "LOCAL_FLOW_CONFIG": str(path),
            "LOCAL_FLOW_PILL_STYLE": "expanded",
        }
    )

    with pytest.raises(ConfigError, match="overridden by environment"):
        service.save({"pill_style": "compact"})

    assert tomllib.loads(path.read_text())["pill_style"] == "compact"


def test_dotenv_owned_field_is_locked_until_migrated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# JiSpr local settings\n"
        "LOCAL_FLOW_PILL_STYLE=expanded  # current style\n"
        "UNRELATED_VALUE=keep-me\n"
    )
    service = SettingsService(default_path=tmp_path / "config.toml")

    before = service.load()
    assert before.sources["pill_style"] == "dotenv"
    assert not is_settings_editable(before.sources["pill_style"])
    with pytest.raises(ConfigError, match="overridden by dotenv"):
        service.save({"pill_style": "compact"})
    assert "LOCAL_FLOW_PILL_STYLE=expanded" in dotenv.read_text()


def test_default_target_is_created_and_reloaded(tmp_path):
    path = tmp_path / "user" / "config.toml"
    service = SettingsService(env={}, default_path=path)

    snapshot = service.save({"floating_pill": False})

    assert path.is_file()
    assert snapshot.config.floating_pill is False


def test_pill_position_round_trips_without_changing_style(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('pill_style = "expanded"\n')
    service = SettingsService(env={"LOCAL_FLOW_CONFIG": str(path)})

    snapshot = service.save({"pill_position": "caret"})

    assert snapshot.config.pill_position == "caret"
    assert snapshot.config.pill_style == "expanded"
    assert snapshot.sources["pill_position"] == "toml"
    assert service.load().config.pill_position == "caret"
    original = path.read_text()
    with pytest.raises(ConfigError, match="pill_position"):
        service.save({"pill_position": "mouse"})
    assert path.read_text() == original


def test_environment_owned_pill_position_is_locked(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('pill_position = "bottom"\n')
    service = SettingsService(
        env={
            "LOCAL_FLOW_CONFIG": str(path),
            "LOCAL_FLOW_PILL_POSITION": "caret",
        }
    )

    snapshot = service.load()
    assert snapshot.config.pill_position == "caret"
    assert snapshot.sources["pill_position"] == "environment"
    assert not is_settings_editable(snapshot.sources["pill_position"])
    with pytest.raises(ConfigError, match="overridden by environment"):
        service.save({"pill_position": "bottom"})
    assert tomllib.loads(path.read_text())["pill_position"] == "bottom"


def test_service_rejects_non_ui_field(tmp_path):
    service = SettingsService(env={}, default_path=tmp_path / "config.toml")

    with pytest.raises(ConfigError, match="unsupported"):
        service.save({"data_dir": tmp_path / "other"})
