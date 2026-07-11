"""Headless tests for Settings provenance and atomic config persistence."""

from __future__ import annotations

import tomllib

import pytest

from local_flow.config import load_config_snapshot
from local_flow.errors import ConfigError
from local_flow.settings.service import SettingsService


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


def test_default_target_is_created_and_reloaded(tmp_path):
    path = tmp_path / "user" / "config.toml"
    service = SettingsService(env={}, default_path=path)

    snapshot = service.save({"floating_pill": False})

    assert path.is_file()
    assert snapshot.config.floating_pill is False


def test_service_rejects_non_ui_field(tmp_path):
    service = SettingsService(env={}, default_path=tmp_path / "config.toml")

    with pytest.raises(ConfigError, match="unsupported"):
        service.save({"history_max_entries": 1})
