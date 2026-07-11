"""One-time migration from legacy ``LOCAL_FLOW_*`` dotenv settings to TOML."""

from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import fields
from pathlib import Path

from local_flow.config import ENV_PREFIX, Config, _read_dotenv, load_config
from local_flow.errors import ConfigError
from local_flow.settings.service import render_toml


def dotenv_config_fields(dotenv_path: Path) -> dict[str, str]:
    """Return legacy dotenv values that correspond to real Config fields."""
    config_names = {field.name for field in fields(Config)}
    values = _read_dotenv(dotenv_path)
    migrated: dict[str, str] = {}
    for name in config_names:
        raw = values.get(f"{ENV_PREFIX}{name.upper()}")
        if raw is not None and raw != "":
            migrated[name] = raw
    return migrated


def _without_migrated_assignments(content: str, names: set[str]) -> str:
    env_names = {f"{ENV_PREFIX}{name.upper()}" for name in names}
    kept: list[str] = []
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").lstrip()
        key = stripped.partition("=")[0].strip()
        if key not in env_names:
            kept.append(line)
    return "".join(kept)


def migrate_dotenv_to_toml(dotenv_path: Path, target: Path) -> list[str]:
    """Move config assignments from dotenv into validated TOML atomically.

    Unrelated dotenv entries and comments are preserved. The TOML file is
    replaced first, so an interruption can at worst leave equivalent legacy
    overrides in place; it cannot lose the effective settings.
    """
    raw_changes = dotenv_config_fields(dotenv_path)
    if not raw_changes:
        return []

    existing: dict[str, object] = {}
    if target.is_file():
        try:
            existing = tomllib.loads(target.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Could not read existing settings at {target}: {exc}") from exc

    # Reuse the real config parser for types and validation. Only fields that
    # actually came from dotenv are copied into TOML; unrelated defaults stay
    # implicit and existing TOML-only values remain untouched.
    parsed = load_config(
        env={f"{ENV_PREFIX}{name.upper()}": value for name, value in raw_changes.items()}
    )
    migrated = {name: getattr(parsed, name) for name in raw_changes}
    toml_content = render_toml({**existing, **migrated})
    dotenv_content = _without_migrated_assignments(
        dotenv_path.read_text(encoding="utf-8"), set(migrated)
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    toml_temp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=target.parent,
        prefix=f"{target.name}.",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    )
    toml_temp_path: Path | None = Path(toml_temp.name)
    try:
        toml_temp.write(toml_content)
    finally:
        toml_temp.close()

    dotenv_temp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=dotenv_path.parent,
        prefix=f"{dotenv_path.name}.",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    )
    dotenv_temp_path: Path | None = Path(dotenv_temp.name)
    try:
        dotenv_temp.write(dotenv_content)
    finally:
        dotenv_temp.close()

    try:
        assert toml_temp_path is not None
        assert dotenv_temp_path is not None
        load_config(config_file=toml_temp_path, env={})
        os.replace(toml_temp_path, target)
        toml_temp_path = None
        os.replace(dotenv_temp_path, dotenv_path)
        dotenv_temp_path = None
    finally:
        if toml_temp_path is not None:
            toml_temp_path.unlink(missing_ok=True)
        if dotenv_temp_path is not None:
            dotenv_temp_path.unlink(missing_ok=True)

    return sorted(migrated)
