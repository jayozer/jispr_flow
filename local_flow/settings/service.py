"""Pure, headlessly tested service behind the native macOS Settings UI."""

from __future__ import annotations

import os
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path

from local_flow.config import (
    Config,
    ConfigSnapshot,
    load_config,
    load_config_snapshot,
)
from local_flow.errors import ConfigError

SETTINGS_FIELDS: frozenset[str] = frozenset(
    {
        "mode",
        "hotkey",
        "hotkey_space_hold_ms",
        "cancel_hotkey",
        "mouse_button",
        "mouse_mode",
        "mouse_enter_button",
        "mic_priority",
        "asr_profile",
        "asr_backend",
        "asr_model",
        "asr_device",
        "asr_compute_type",
        "asr_language",
        "languages",
        "polish_backend",
        "lmstudio_model",
        "lmstudio_system_prompt",
        "cleanup_level",
        "style",
        "context_styles",
        "context_awareness",
        "insert_method",
        "streaming",
        "streaming_pause_ms",
        "transform_hotkey",
        "transform_default",
        "command_hotkey",
        "auto_transform",
        "scratchpad_hotkey",
        "floating_pill",
        "pill_style",
        "vad_backend",
        "vad_aggressiveness",
        "vad_frame_ms",
        "vad_silence_ms",
        "vad_energy_threshold",
        "vad_preset",
        "audio_recovery",
        "max_utterance_min",
        "history_enabled",
        "history_max_entries",
        "history_retention",
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


def is_dotenv_owned(source: str) -> bool:
    return source == "dotenv" or source.endswith(":dotenv")


def is_process_environment_owned(source: str) -> bool:
    return source == "environment" or source.endswith(":environment")


def is_environment_owned(source: str) -> bool:
    """Return whether an effective setting is controlled outside TOML.

    This is public because both the legacy AppKit window and the native app
    bridge need to render the same provenance/locked-field behavior.
    """
    return is_dotenv_owned(source) or is_process_environment_owned(source)


def is_settings_editable(source: str) -> bool:
    """Settings owns TOML; dotenv and parent-process overrides are locked."""
    return not is_environment_owned(source)


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
        changed: dict[str, object] = {}
        for name, value in changes.items():
            if name not in config_names:
                raise ConfigError(f"Unknown config field: {name}")
            current = getattr(snapshot.config, name)
            if value != current and is_environment_owned(snapshot.sources[name]):
                raise ConfigError(
                    f"{name} is overridden by {snapshot.sources[name]}.",
                    hint=(
                        "Migrate or remove the environment override before changing it "
                        "in Settings."
                    ),
                )
            if value != current:
                changed[name] = value

        if not changed:
            return snapshot

        toml_temp_path: Path | None = None
        target = self.target_path(snapshot)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            existing: dict[str, object] = {}
            if target.is_file():
                try:
                    parsed = tomllib.loads(target.read_text(encoding="utf-8"))
                except (OSError, tomllib.TOMLDecodeError) as exc:
                    raise ConfigError(
                        f"Could not read existing settings at {target}: {exc}"
                    ) from exc
                if not isinstance(parsed, dict):
                    raise ConfigError(f"Settings file {target} must contain a TOML table.")
                existing = parsed
            content = render_toml({**existing, **changed})
            toml_temp = tempfile.NamedTemporaryFile(
                mode="w",
                dir=target.parent,
                prefix=f"{target.name}.",
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            )
            toml_temp_path = Path(toml_temp.name)
            try:
                toml_temp.write(content)
            finally:
                toml_temp.close()

            # Exclude environment layers so they cannot mask an invalid
            # candidate TOML value.
            load_config(config_file=toml_temp_path, env={})
            os.replace(toml_temp_path, target)
            toml_temp_path = None
        except BaseException:
            if toml_temp_path is not None:
                toml_temp_path.unlink(missing_ok=True)
            raise
        return self.load()
