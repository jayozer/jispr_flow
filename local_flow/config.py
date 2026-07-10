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
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from local_flow.errors import ConfigError

ENV_PREFIX = "LOCAL_FLOW_"
DEFAULT_LMSTUDIO_BASE_URL = "http://localhost:1234/v1"
VALID_MODES = ("push-to-talk", "hands-free")
VALID_VAD_BACKENDS = ("energy", "webrtc", "mock")
VALID_ASR_BACKENDS = ("faster-whisper", "mlx-whisper", "mock")
VALID_ASR_PROFILES = ("custom", "fast", "accuracy")
ASR_PROFILE_MODELS = {
    "fast": "mlx-community/whisper-small.en-mlx",
    "accuracy": "mlx-community/whisper-large-v3-turbo",
}
VALID_HISTORY_RETENTIONS = ("forever", "24h", "off")
VALID_STREAMING_MODES = ("off", "sentence", "live-preview")
VALID_CLEANUP_LEVELS = ("none", "light", "medium", "high")
VALID_VAD_PRESETS = ("normal", "whisper")
VALID_MOUSE_BUTTONS = ("", "middle", "x1", "x2")
VALID_MOUSE_MODES = ("hold", "toggle")
VALID_PILL_STYLES = ("compact", "expanded")
VALID_POLISH_BACKENDS = ("lmstudio", "rules")


def _default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "local-flow"


def _default_hotkey() -> str:
    # The Fn key is only observable on macOS (elsewhere keyboard firmware
    # swallows it), so the friendlier default is limited to darwin.
    return "fn" if sys.platform == "darwin" else "f9"


def _default_floating_pill() -> bool:
    return sys.platform == "darwin"


@dataclass(frozen=True)
class Config:
    # LM Studio (OpenAI-compatible local server)
    lmstudio_base_url: str = DEFAULT_LMSTUDIO_BASE_URL
    lmstudio_model: str = ""  # empty = auto-pick the first loaded model
    lmstudio_timeout: float = 60.0
    # lmstudio = rules then local LLM; rules = deterministic cleanup only.
    polish_backend: str = "lmstudio"
    # Optional user instructions appended to JiSpr's protected polish prompt.
    lmstudio_system_prompt: str = ""

    # ASR (local speech-to-text; never LM Studio)
    asr_backend: str = "faster-whisper"  # faster-whisper | mlx-whisper | mock
    asr_model: str = "small.en"  # name or path to a local model directory
    asr_device: str = "auto"  # auto | cpu | cuda
    asr_compute_type: str = "int8"
    asr_language: str = "en"  # ISO 639-1 code (e.g. "fr"), or "auto" to detect
    # custom honors asr_backend/model; fast/accuracy select known MLX models.
    asr_profile: str = "custom"

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

    # Preset that biases the energy VAD toward quiet/whispered speech: "normal"
    # (default) leaves `vad_energy_threshold` alone; "whisper" lowers it to 150
    # -- but only when the user hasn't set `vad_energy_threshold` explicitly
    # (detected by comparing against the dataclass default 500.0; an explicit
    # 500.0 is indistinguishable from "unset" -- documented limitation). See
    # `local_flow.app._build_vad`.
    vad_preset: str = "normal"  # normal | whisper

    # Hotkey / capture mode
    mode: str = "push-to-talk"  # push-to-talk | hands-free
    hotkey: str = field(default_factory=_default_hotkey)  # fn | space | pynput key name
    hotkey_space_hold_ms: int = 250  # hold-vs-tap threshold for hotkey="space"
    cancel_hotkey: str = "esc"  # discards the in-flight dictation

    # Native, always-on-top recording state + live mic level. Enabled by
    # default on macOS; other platforms keep the console/tray surfaces until
    # they gain a native pill backend.
    floating_pill: bool = field(default_factory=_default_floating_pill)
    # compact = Apple/Wispr-inspired persistent line; expanded = the original
    # labeled 280x56 status pill.
    pill_style: str = "compact"

    # Comma-separated, priority-ordered microphone name substrings (case-
    # insensitive), e.g. "AirPods, USB". The first input device whose name
    # contains one of these wins; empty (default) means "system default".
    # Parsed by `local_flow.app.parse_mic_priority`.
    mic_priority: str = ""

    # Mouse-button push-to-talk (see README "Mouse push-to-talk"): an
    # optional second listener that runs alongside the keyboard hotkey.
    # Non-primary buttons only -- left/right are reserved for normal
    # clicking and are rejected at load. Empty (default) disables it.
    mouse_button: str = ""  # "" (disabled) | middle | x1 | x2
    mouse_mode: str = "hold"  # hold (press-and-hold) | toggle (click on/off)
    # A second, always-on click handler independent of mouse_mode: pressing
    # this button sends "enter" through the configured sink. Same allowed
    # values as mouse_button; empty (default) disables it.
    mouse_enter_button: str = ""

    # Style / personalization
    style: str = "default"
    data_dir: Path = field(default_factory=_default_data_dir)

    # Auto-cleanup level for the polish pass: none (verbatim, no LLM call) |
    # light (fillers/grammar only) | medium (today's default) | high
    # (rewrite for concision). See `local_flow.polish.prompting`.
    cleanup_level: str = "medium"

    # Per-app context awareness (frontmost app -> style/insert overrides)
    context_styles: bool = True

    # Field-text awareness (E10, see README "Context-aware dictation"):
    # best-effort reading of the focused field's existing text (the tail
    # before the cursor, plus any selection) so the polish pass continues
    # sentences, matches tone, and reuses nearby name spellings instead of
    # re-greeting or clashing with what's already there. Best-effort and
    # platform-limited (see `local_flow.context.field_text`) -- Windows
    # currently ships a stub that always returns empty context, so this flag
    # has no observable effect there beyond skipping a no-op provider
    # construction. Field text is sent only to the local LM Studio server
    # (`lmstudio_base_url`) as part of the polish prompt -- it is never
    # stored or sent anywhere else.
    context_awareness: bool = True

    # Text insertion
    insert_method: str = "auto"  # auto | paste | type | clipboard

    # Audio
    sample_rate: int = 16000

    # Crash-safe audio autosave: save each utterance's PCM under
    # <data dir>/pending/ before processing, delete it once handled. `local-flow
    # recover` replays anything left behind by a crash/force-quit. Set false to
    # skip the extra disk write entirely (byte-identical to before this existed).
    audio_recovery: bool = True

    # Utterances longer than this (minutes) trigger a "warning" status
    # notification after processing (a very long recording is usually an
    # accidental hands-free/stuck-hotkey capture rather than intended
    # dictation). Does not truncate or otherwise change processing.
    max_utterance_min: int = 20

    # Dictation history (local JSONL log)
    history_enabled: bool = True
    history_max_entries: int = 5000
    history_retention: str = "forever"  # forever | 24h | off

    # Streaming / low-latency insertion (hands-free mode only; see README
    # "Streaming"). "sentence" shortens the pause threshold that closes an
    # utterance so each sentence inserts while the next is still being
    # spoken; "live-preview" is reserved for a future rough-text preview.
    streaming: str = "off"  # off | sentence | live-preview
    streaming_pause_ms: int = 300

    # Transform-in-place hotkey (see README "Transform anywhere"): tap this
    # key to apply `transform_default` (a transforms.json name) to whatever
    # is currently selected in the frontmost app. Empty (default) disables
    # the feature entirely -- zero behavior change. See
    # `local_flow.hotkeys.base.TapListener` / `local_flow.app._run_loop`.
    transform_hotkey: str = ""
    transform_default: str = "Polish"

    # Voice command mode hotkey (see README "Voice command mode"): hold this
    # key and speak an edit instruction instead of typing one; applied to the
    # current selection (if any) or the last dictation. Empty (default)
    # disables it entirely.
    command_hotkey: str = ""

    # Auto-transform (see README "Auto-transform"): when set to a
    # transforms.json name, every dictation's final text is additionally
    # rewritten by that transform right before insertion. Empty (default, "")
    # disables it -- zero behavior change. Skipped at cleanup_level="none" or
    # when no chat client is configured; an unknown name fails fast with a
    # ConfigError when the pipeline is built (see `local_flow.app._build_pipeline`).
    auto_transform: str = ""

    # Dictate-to-pad hotkey (see README "Scratchpad"): tap this key (push-to-
    # talk mode only, like the transform/command hotkeys) to toggle routing
    # live dictation into the scratchpad's active note instead of the
    # configured insertion sink -- tap again to resume normal insertion.
    # Empty (default) disables the feature entirely -- zero behavior change.
    # Must be distinct from hotkey/transform_hotkey/command_hotkey. See
    # `local_flow.app._run_loop`.
    scratchpad_hotkey: str = ""


def resolve_asr_profile(config: Config) -> Config:
    """Resolve a friendly profile into the concrete ASR backend/model.

    ``custom`` preserves every existing setting. The named profiles are a
    single, UI-ready switch for the two MLX checkpoints JiSpr evaluates.
    """
    model = ASR_PROFILE_MODELS.get(config.asr_profile)
    if model is None:
        return config
    return replace(config, asr_backend="mlx-whisper", asr_model=model)


def _read_dotenv(path: Path) -> dict[str, str]:
    """Parse a minimal ``.env``-style file into a ``{KEY: value}`` dict.

    Blank lines and full-line ``#`` comments are skipped; surrounding
    single/double quotes on the value are stripped. A trailing `` #``
    -prefixed (space-then-hash) inline comment is also stripped from the
    value, e.g. ``KEY=value   # comment`` -> ``"value"``. Without this, a
    copy-pasted example line with an inline comment (see ``.env.example``)
    would silently become part of the value and fail whatever validation
    that field has. A `` #`` *inside* a quoted value is part of the value
    (``KEY="my #notes"`` -> ``"my #notes"``): a quoted value ends at its
    closing quote and anything after that is ignored as commentary.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ("'", '"'):
            closing = value.find(value[0], 1)
            if closing != -1:
                values[key.strip()] = value[1:closing]
                continue
            # No closing quote: fall through to the unquoted handling below.
        comment_at = value.find(" #")
        if comment_at != -1:
            value = value[:comment_at].rstrip()
        values[key.strip()] = value.strip("'\"")
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
        "floating_pill": bool,
        "history_enabled": bool,
        "history_max_entries": int,
        "context_styles": bool,
        "context_awareness": bool,
        "streaming_pause_ms": int,
        "audio_recovery": bool,
        "max_utterance_min": int,
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

    if config.mode not in VALID_MODES:
        raise ConfigError(
            f"Invalid mode: {config.mode!r}",
            hint=f"Valid values: {', '.join(VALID_MODES)}.",
        )

    if config.vad_backend not in VALID_VAD_BACKENDS:
        raise ConfigError(
            f"Invalid vad_backend: {config.vad_backend!r}",
            hint=f"Valid values: {', '.join(VALID_VAD_BACKENDS)}.",
        )

    if config.asr_backend not in VALID_ASR_BACKENDS:
        raise ConfigError(
            f"Invalid asr_backend: {config.asr_backend!r}",
            hint=f"Valid values: {', '.join(VALID_ASR_BACKENDS)}.",
        )

    if config.asr_profile not in VALID_ASR_PROFILES:
        raise ConfigError(
            f"Invalid asr_profile: {config.asr_profile!r}",
            hint=f"Valid values: {', '.join(VALID_ASR_PROFILES)}.",
        )

    # Whisper language codes are 2-3 lowercase ASCII letters (ISO 639-1 plus
    # a few 639-3 codes like "yue"/"haw"); "auto" means detect per utterance.
    lang = config.asr_language
    if lang != "auto" and not (
        2 <= len(lang) <= 3 and lang.isascii() and lang.isalpha() and lang.islower()
    ):
        raise ConfigError(
            f"Invalid asr_language: {lang!r}",
            hint='Valid values: "auto" (detect per utterance) or a lowercase '
            'ISO 639 language code of 2-3 letters, e.g. "en", "fr".',
        )

    if config.history_retention not in VALID_HISTORY_RETENTIONS:
        raise ConfigError(
            f"Invalid history_retention: {config.history_retention!r}",
            hint=f"Valid values: {', '.join(VALID_HISTORY_RETENTIONS)}.",
        )

    if config.streaming not in VALID_STREAMING_MODES:
        raise ConfigError(
            f"Invalid streaming: {config.streaming!r}",
            hint=f"Valid values: {', '.join(VALID_STREAMING_MODES)}.",
        )

    if config.cleanup_level not in VALID_CLEANUP_LEVELS:
        raise ConfigError(
            f"Invalid cleanup_level: {config.cleanup_level!r}",
            hint=f"Valid values: {', '.join(VALID_CLEANUP_LEVELS)}.",
        )

    if config.polish_backend not in VALID_POLISH_BACKENDS:
        raise ConfigError(
            f"Invalid polish_backend: {config.polish_backend!r}",
            hint=f"Valid values: {', '.join(VALID_POLISH_BACKENDS)}.",
        )

    if config.vad_preset not in VALID_VAD_PRESETS:
        raise ConfigError(
            f"Invalid vad_preset: {config.vad_preset!r}",
            hint=f"Valid values: {', '.join(VALID_VAD_PRESETS)}.",
        )

    if config.pill_style not in VALID_PILL_STYLES:
        raise ConfigError(
            f"Invalid pill_style: {config.pill_style!r}",
            hint=f"Valid values: {', '.join(VALID_PILL_STYLES)}.",
        )

    _mouse_hint = (
        "Only non-primary mouse buttons are supported: "
        f"{', '.join(b for b in VALID_MOUSE_BUTTONS if b)} "
        "(left/right are reserved for normal clicking); leave empty to disable."
    )
    if config.mouse_button not in VALID_MOUSE_BUTTONS:
        raise ConfigError(
            f"Invalid mouse_button: {config.mouse_button!r}", hint=_mouse_hint
        )

    if config.mouse_mode not in VALID_MOUSE_MODES:
        raise ConfigError(
            f"Invalid mouse_mode: {config.mouse_mode!r}",
            hint=f"Valid values: {', '.join(VALID_MOUSE_MODES)}.",
        )

    if config.mouse_enter_button not in VALID_MOUSE_BUTTONS:
        raise ConfigError(
            f"Invalid mouse_enter_button: {config.mouse_enter_button!r}", hint=_mouse_hint
        )

    if config.mouse_button and config.mouse_button == config.mouse_enter_button:
        raise ConfigError(
            f"mouse_button and mouse_enter_button cannot both be "
            f"{config.mouse_button!r}.",
            hint="Use a different button for each, or leave one of them empty.",
        )

    # Every configured keyboard hotkey (the main hotkey, cancel_hotkey,
    # transform_hotkey, command_hotkey, scratchpad_hotkey) must be distinct,
    # case-insensitively -- two listeners bound to the same key would race
    # for the same physical keypress with no defined winner. Checked
    # pairwise. cancel_hotkey has no "disabled" state (unlike the optional
    # trio below) so it always participates; transform_hotkey/command_hotkey/
    # scratchpad_hotkey are skipped when empty (disabled), so leaving them
    # unset (the default) never trips this. Mouse buttons (mouse_button/
    # mouse_enter_button) are a separate input device/namespace --
    # "middle"/"x1"/"x2" -- and are validated on their own above; they
    # can never collide with a keyboard key name, so they are not part of
    # this check.
    seen_hotkeys: dict[str, str] = {}
    for field_name, value in (
        ("hotkey", config.hotkey),
        ("cancel_hotkey", config.cancel_hotkey),
        ("transform_hotkey", config.transform_hotkey),
        ("command_hotkey", config.command_hotkey),
        ("scratchpad_hotkey", config.scratchpad_hotkey),
    ):
        if not value:
            continue
        key = value.lower()
        if key in seen_hotkeys:
            raise ConfigError(
                f"{field_name} and {seen_hotkeys[key]} cannot both be {value!r}.",
                hint="hotkey, cancel_hotkey, transform_hotkey, command_hotkey, "
                "and scratchpad_hotkey must all be distinct; leave "
                "transform_hotkey/command_hotkey/scratchpad_hotkey empty to "
                "disable that feature (cancel_hotkey has no disabled state, "
                "so it must be reassigned instead).",
            )
        seen_hotkeys[key] = field_name

    return resolve_asr_profile(config)
