"""Tests for migrating legacy dotenv configuration into Settings-owned TOML."""

from __future__ import annotations

import tomllib

from local_flow.config import load_config
from local_flow.config_migration import dotenv_config_fields, migrate_dotenv_to_toml


def test_migration_preserves_comments_and_unrelated_dotenv_entries(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# local notes\n"
        "LOCAL_FLOW_HOTKEY=f8\n"
        "export LOCAL_FLOW_HISTORY_ENABLED=false\n"
        "UNRELATED_SECRET=keep-me\n",
        encoding="utf-8",
    )
    target = tmp_path / "config.toml"
    target.write_text('style = "concise"\n', encoding="utf-8")

    migrated = migrate_dotenv_to_toml(dotenv, target)

    assert migrated == ["history_enabled", "hotkey"]
    data = tomllib.loads(target.read_text(encoding="utf-8"))
    assert data["hotkey"] == "f8"
    assert data["history_enabled"] is False
    assert data["style"] == "concise"
    assert dotenv.read_text(encoding="utf-8") == (
        "# local notes\nUNRELATED_SECRET=keep-me\n"
    )
    config = load_config(config_file=target, env={})
    assert config.hotkey == "f8"
    assert config.history_enabled is False


def test_migration_ignores_non_config_local_flow_variables(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "LOCAL_FLOW_CONFIG=/somewhere/config.toml\nLOCAL_FLOW_NOT_A_SETTING=value\n",
        encoding="utf-8",
    )

    assert dotenv_config_fields(dotenv) == {}
    assert migrate_dotenv_to_toml(dotenv, tmp_path / "config.toml") == []
    assert not (tmp_path / "config.toml").exists()


def test_profile_migration_preserves_effective_named_profile(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("LOCAL_FLOW_ASR_PROFILE=accuracy\n", encoding="utf-8")
    target = tmp_path / "config.toml"

    migrate_dotenv_to_toml(dotenv, target)

    data = tomllib.loads(target.read_text(encoding="utf-8"))
    assert data == {"asr_profile": "accuracy"}
    assert load_config(config_file=target, env={}).asr_backend == "mlx-whisper"
