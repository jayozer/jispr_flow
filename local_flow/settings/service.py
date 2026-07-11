"""Pure, headlessly tested service behind the native macOS Settings UI."""

from __future__ import annotations

import os
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path

from local_flow.config import Config, ConfigSnapshot, load_config, load_config_snapshot
from local_flow.errors import ConfigError

SETTINGS_FIELDS: frozenset[str] = frozenset(
    {
        "asr_profile",
        "asr_backend",
        "asr_model",
        "asr_device",
        "asr_compute_type",
        "asr_language",
        "polish_backend",
        "lmstudio_model",
        "lmstudio_system_prompt",
        "cleanup_level",
        "style",
        "floating_pill",
        "pill_style",
    }
)


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n")
    return f'"{escaped}"'


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, Path):
        return _toml_string(str(value))
    return _toml_string(str(value))


def render_toml(values: Mapping[str, object]) -> str:
    return "".join(f"{key} = {_toml_value(value)}\n" for key, value in values.items())


def _environment_owned(source: str) -> bool:
    return source in {"dotenv", "environment"} or source.endswith(":dotenv") or source.endswith(
        ":environment"
    )


class SettingsService:
    """Load provenance and safely persist UI-owned values to active TOML."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        default_path: Path | None = None,
    ) -> None:
        self._env = env
        self._default_path_explicit = default_path is not None
        self._default_path = default_path or (
            Path.home() / ".config" / "local-flow" / "config.toml"
        )

    def load(self) -> ConfigSnapshot:
        if self._default_path_explicit:
            return load_config_snapshot(
                config_file=self._default_path if self._default_path.is_file() else None,
                env=self._env,
                discover_config=False,
            )
        snapshot = load_config_snapshot(env=self._env)
        if snapshot.config_path is None and self._default_path.is_file():
            return load_config_snapshot(config_file=self._default_path, env=self._env)
        return snapshot

    def target_path(self, snapshot: ConfigSnapshot | None = None) -> Path:
        snapshot = snapshot or self.load()
        return snapshot.config_path or self._default_path

    def save(self, changes: Mapping[str, object]) -> ConfigSnapshot:
        unknown = sorted(set(changes) - SETTINGS_FIELDS)
        if unknown:
            raise ConfigError(
                f"Settings cannot write unsupported fields: {', '.join(unknown)}"
            )
        snapshot = self.load()
        config_names = {field.name for field in fields(Config)}
        for name, value in changes.items():
            if name not in config_names:
                raise ConfigError(f"Unknown config field: {name}")
            current = getattr(snapshot.config, name)
            if value != current and _environment_owned(snapshot.sources[name]):
                raise ConfigError(
                    f"{name} is overridden by {snapshot.sources[name]}.",
                    hint="Move this non-secret value into TOML or remove the environment "
                    "override before changing it in Settings.",
                )

        target = self.target_path(snapshot)
        target.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, object] = {}
        if target.is_file():
            try:
                parsed = tomllib.loads(target.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise ConfigError(f"Could not read existing settings at {target}: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ConfigError(f"Settings file {target} must contain a TOML table.")
            existing = parsed
        merged = {**existing, **changes}
        content = render_toml(merged)

        temp = tempfile.NamedTemporaryFile(
            mode="w",
            dir=target.parent,
            prefix=f"{target.name}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        temp_path = Path(temp.name)
        try:
            try:
                temp.write(content)
            finally:
                temp.close()
            # Candidate validation deliberately excludes process/.env layers;
            # otherwise an override could mask an invalid TOML value.
            load_config(config_file=temp_path, env={})
            os.replace(temp_path, target)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        return self.load()
