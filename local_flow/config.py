"""Configuration: defaults < TOML config file < environment variables.

Environment variables use the ``LOCAL_FLOW_`` prefix (see ``.env.example``).
A ``.env`` file in the working directory is read if present (values there do
not override real environment variables). The optional TOML config file is
looked up at ``$LOCAL_FLOW_CONFIG``, ``./local-flow.toml``, then
``~/.config/local-flow/config.toml``.
"""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

from local_flow.errors import ConfigError

ENV_PREFIX = "LOCAL_FLOW_"
DEFAULT_LMSTUDIO_BASE_URL = "http://localhost:1234/v1"
VALID_HISTORY_RETENTIONS = ("forever", "24h", "off")


def _default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "local-flow"


def _default_hotkey() -> str:
    # The Fn key is only observable on macOS (elsewhere keyboard firmware
    # swallows it), so the friendlier default is limited to darwin.
    return "fn" if sys.platform == "darwin" else "f9"


@dataclass(frozen=True)
class Config:
    # LM Studio (OpenAI-compatible local server)
    lmstudio_base_url: str = DEFAULT_LMSTUDIO_BASE_URL
    lmstudio_model: str = ""  # empty = auto-pick the first loaded model
    lmstudio_timeout: float = 60.0

    # ASR (local speech-to-text; never LM Studio)
    asr_backend: str = "faster-whisper"  # faster-whisper | mock
    asr_model: str = "small.en"  # name or path to a local model directory
    asr_device: str = "auto"  # auto | cpu | cuda
    asr_compute_type: str = "int8"
    asr_language: str = "en"  # ISO 639-1 code (e.g. "fr"), or "auto" to detect

    # Comma-separated ISO 639-1 codes for the tray app's Language quick-switch
    # menu (e.g. "en,de,fr"); empty hides the menu. Parsed by
    # `local_flow.tray.app.parse_languages`.
    languages: str = ""

    # VAD
    vad_backend: str = "energy"  # energy | webrtc | mock
    vad_aggressiveness: int = 2
    vad_frame_ms: int = 30
    vad_silence_ms: int = 600
    vad_energy_threshold: float = 500.0

    # Hotkey / capture mode
    mode: str = "push-to-talk"  # push-to-talk | hands-free
    hotkey: str = field(default_factory=_default_hotkey)  # fn | space | pynput key name
    hotkey_space_hold_ms: int = 250  # hold-vs-tap threshold for hotkey="space"
    cancel_hotkey: str = "esc"  # discards the in-flight dictation

    # Style / personalization
    style: str = "default"
    data_dir: Path = field(default_factory=_default_data_dir)

    # Per-app context awareness (frontmost app -> style/insert overrides)
    context_styles: bool = True

    # Text insertion
    insert_method: str = "auto"  # auto | paste | type | clipboard

    # Audio
    sample_rate: int = 16000

    # Dictation history (local JSONL log)
    history_enabled: bool = True
    history_max_entries: int = 5000
    history_retention: str = "forever"  # forever | 24h | off


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _discover_config_file(env: Mapping[str, str]) -> Path | None:
    explicit = env.get(ENV_PREFIX + "CONFIG")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(
                f"Config file {path} (from {ENV_PREFIX}CONFIG) does not exist.",
                hint="Fix the path or unset the variable to use defaults.",
            )
        return path
    for candidate in (
        Path.cwd() / "local-flow.toml",
        Path.home() / ".config" / "local-flow" / "config.toml",
    ):
        if candidate.is_file():
            return candidate
    return None


def _coerce(name: str, raw: object, target_type: type) -> object:
    try:
        if target_type is bool:
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
        if target_type is int:
            return int(str(raw))
        if target_type is float:
            return float(str(raw))
        if target_type is Path:
            return Path(str(raw)).expanduser()
        return str(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"Invalid value for {name!r}: {raw!r} ({exc})",
            hint=f"Expected a {target_type.__name__} value.",
        ) from exc


def load_config(
    config_file: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Config:
    """Build a :class:`Config` from the config file and environment.

    ``env`` may be passed explicitly for tests; when ``None`` the process
    environment is used, augmented with values from a local ``.env`` file.
    """
    if env is None:
        merged = _read_dotenv(Path.cwd() / ".env")
        merged.update(os.environ)
        env = merged

    field_types: dict[str, type] = {
        "lmstudio_timeout": float,
        "vad_aggressiveness": int,
        "vad_frame_ms": int,
        "vad_silence_ms": int,
        "vad_energy_threshold": float,
        "data_dir": Path,
        "sample_rate": int,
        "hotkey_space_hold_ms": int,
        "history_enabled": bool,
        "history_max_entries": int,
        "context_styles": bool,
    }
    names = [f.name for f in fields(Config)]
    values: dict[str, object] = {}

    if config_file is None:
        config_file = _discover_config_file(env)
    if config_file is not None:
        try:
            data = tomllib.loads(Path(config_file).read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                f"Could not parse config file {config_file}: {exc}",
                hint="The file must be valid TOML; see local-flow.example.toml.",
            ) from exc
        unknown = sorted(set(data) - set(names))
        if unknown:
            raise ConfigError(
                f"Unknown keys in {config_file}: {', '.join(unknown)}",
                hint=f"Valid keys: {', '.join(names)}",
            )
        for key, raw in data.items():
            values[key] = _coerce(key, raw, field_types.get(key, str))

    for name in names:
        raw = env.get(ENV_PREFIX + name.upper())
        if raw is not None and raw != "":
            values[name] = _coerce(name, raw, field_types.get(name, str))

    config = Config(**values)  # type: ignore[arg-type]

    if config.history_retention not in VALID_HISTORY_RETENTIONS:
        raise ConfigError(
            f"Invalid history_retention: {config.history_retention!r}",
            hint=f"Valid values: {', '.join(VALID_HISTORY_RETENTIONS)}.",
        )

    return config
