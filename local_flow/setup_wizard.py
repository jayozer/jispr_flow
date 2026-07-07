"""``local-flow setup``: an interactive terminal wizard that writes a config.

Design goals, mirrored from ``docs/superpowers/plans/2026-07-06-phase3-e6-tray-setup.md``
(Task 4):

- Every side effect (reading input, printing, probing optional dependencies)
  goes through an injected seam (``ask``/``say``/``probe_import``/
  ``probe_lmstudio``) so the whole flow is testable without a terminal, the
  optional extras, or a running LM Studio server.
- The wizard never leaves a broken config behind: it writes to a temp file
  in the target directory, validates that with the real
  :func:`local_flow.config.load_config`, and only then swaps it into place
  with ``os.replace``. On validation failure the temp file is removed and
  any pre-existing config at the target path is left completely untouched.
- It never silently clobbers an existing config: overwriting requires an
  explicit "y".
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from local_flow.config import Config, load_config
from local_flow.errors import ConfigError, LocalFlowError

_MAX_ATTEMPTS = 3

# (module name, extra name) probed for `local-flow setup`'s dependency report.
_PROBED_MODULES: tuple[tuple[str, str], ...] = (
    ("faster_whisper", "asr"),
    ("sounddevice", "audio"),
    ("pynput", "desktop"),
    ("pyperclip", "desktop"),
    ("pystray", "tray"),
)

# Display label -> actual `asr_model` config value. The multilingual option
# is labeled for clarity; the underlying model name is plain "small".
_ASR_MODEL_CHOICES: dict[str, str] = {
    "small.en": "small.en",
    "small (multilingual)": "small",
    "base.en": "base.en",
}

_KEY_COMMENTS: dict[str, str] = {
    "hotkey": "push-to-talk key: fn | space | f9",
    "mode": "push-to-talk | hands-free",
    "asr_model": "small.en | small | base.en (small/base without .en are multilingual)",
    "asr_language": "ISO 639-1 code (e.g. en) or auto to detect the spoken language",
    "style": "active writing style; see styles.json in the data dir",
}


def _default_probe_import(module: str) -> bool:
    try:
        __import__(module)
        return True
    except (ImportError, OSError):
        return False


def _default_probe_lmstudio(config: Config) -> tuple[bool, str]:
    """Real default for the LM Studio probe: a short-timeout ``list_models()``."""
    from local_flow.app import _build_chat_client

    probe_config = replace(config, lmstudio_timeout=3.0)
    try:
        client = _build_chat_client(probe_config)
        models = client.list_models()
        return True, ", ".join(models) if models else "(no models loaded)"
    except LocalFlowError as exc:
        return False, exc.message


def _hotkey_options() -> tuple[list[str], str]:
    """Platform-appropriate hotkey choices, mirroring ``config._default_hotkey``.

    Linux keyboard/desktop stacks generally can't observe a bare "space" tap
    as a distinct hotkey the way macOS and Windows can (and the hotkey
    factory rejects it there), so Linux is only ever offered "f9".
    """
    if sys.platform == "darwin":
        return ["fn", "space", "f9"], "fn"
    if sys.platform.startswith("linux"):
        return ["f9"], "f9"
    return ["space", "f9"], "f9"


def _report_probes(
    say: Callable[[str], None],
    probe_import: Callable[[str], bool],
    probe_lmstudio: Callable[[], tuple[bool, str]],
) -> None:
    say("Checking optional dependencies...")
    for module, extra in _PROBED_MODULES:
        if probe_import(module):
            say(f"  {module:<15}: installed")
        else:
            say(f"  {module:<15}: missing (install with: uv sync --extra {extra})")

    say("Checking LM Studio...")
    reachable, detail = probe_lmstudio()
    if reachable:
        say(f"  LM Studio      : reachable, models: {detail}")
    else:
        say(f"  LM Studio      : unreachable - {detail}")
        say(
            "  hint: start LM Studio, load a model, then enable the local server "
            "(Developer tab -> Start Server)."
        )


def _ask_choice(
    ask: Callable[[str], str],
    say: Callable[[str], None],
    prompt: str,
    options: list[str],
    default: str,
) -> str:
    """Ask a numbered multiple-choice question; empty = default.

    Accepts either the option's number or its exact text (case-insensitive).
    An invalid answer re-prompts, up to ``_MAX_ATTEMPTS`` times, after which
    the default is used.
    """
    say(prompt)
    for i, option in enumerate(options, start=1):
        marker = "  [default]" if option == default else ""
        say(f"  {i}. {option}{marker}")

    for _attempt in range(_MAX_ATTEMPTS):
        answer = ask("> ").strip()
        if not answer:
            return default
        if answer.isdigit():
            index = int(answer)
            if 1 <= index <= len(options):
                return options[index - 1]
        for option in options:
            if answer.lower() == option.lower():
                return option
        say(f"Invalid choice: {answer!r}. Please try again.")

    say(f"Too many invalid attempts; using default ({default}).")
    return default


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_toml(values: dict[str, str]) -> str:
    lines = ["# Generated by `local-flow setup`."]
    for key, value in values.items():
        comment = _KEY_COMMENTS.get(key, "")
        line = f"{key} = {_toml_string(value)}"
        if comment:
            line += f"  # {comment}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def _print_next_steps(say: Callable[[str], None], path: Path) -> None:
    say("")
    say(f"Wrote config to {path}")
    say("Next steps:")
    if sys.platform == "darwin":
        say(
            "  - Grant your terminal Accessibility and Input Monitoring permissions "
            "(System Settings -> Privacy & Security) so hotkeys and text insertion work."
        )
    say(
        "  - Start LM Studio, load a model, and enable its local server "
        "(Developer tab -> Start Server)."
    )
    say("  - Run `local-flow check` to verify everything is wired up.")
    say("  - Run `local-flow run` to start dictating.")


def run_wizard(
    config: Config,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    target: Path | None = None,
    probe_import: Callable[[str], bool] | None = None,
    probe_lmstudio: Callable[[], tuple[bool, str]] | None = None,
) -> Path:
    """Run the interactive onboarding wizard and return the written config path.

    Writes only the keys the wizard actually asked about, to a temp file
    that is validated with :func:`local_flow.config.load_config` before
    being swapped into place; on validation failure the temp file is
    discarded (and the error re-raised) without touching any pre-existing
    config at the target path.
    """
    probe_import = probe_import or _default_probe_import
    probe_lmstudio = probe_lmstudio or (lambda: _default_probe_lmstudio(config))

    say("local-flow setup")
    say("================")
    _report_probes(say, probe_import, probe_lmstudio)

    say("")
    say("Answer the following (press Enter to accept the default marked below).")

    # Each question's default is the live config's current value when it's
    # among the offered options, else the hardcoded factory fallback -- this
    # makes re-running the wizard on an existing config a no-op on Enter.
    hotkey_options, hotkey_fallback = _hotkey_options()
    hotkey_default = (
        config.hotkey if config.hotkey in hotkey_options else hotkey_fallback
    )
    hotkey = _ask_choice(
        ask, say, "Push-to-talk hotkey:", hotkey_options, hotkey_default
    )

    mode_options = ["push-to-talk", "hands-free"]
    mode_default = config.mode if config.mode in mode_options else "push-to-talk"
    mode = _ask_choice(ask, say, "Capture mode:", mode_options, mode_default)

    model_labels = list(_ASR_MODEL_CHOICES)
    model_label_default = next(
        (
            label
            for label, value in _ASR_MODEL_CHOICES.items()
            if value == config.asr_model
        ),
        "small.en",
    )
    model_label = _ask_choice(ask, say, "ASR model:", model_labels, model_label_default)
    asr_model = _ASR_MODEL_CHOICES[model_label]

    values: dict[str, str] = {
        "hotkey": hotkey,
        "mode": mode,
        "asr_model": asr_model,
    }

    if asr_model == "small":  # the only multilingual option offered
        language_options = ["en", "auto"]
        language_default = (
            config.asr_language if config.asr_language in language_options else "en"
        )
        language = _ask_choice(
            ask, say, "ASR language:", language_options, language_default
        )
        values["asr_language"] = language

    from local_flow.personalization.store import PersonalizationStore

    store = PersonalizationStore(config.data_dir)
    style_names = sorted(store.styles()) or ["default"]
    active_style, _rules = store.style_rules()
    style_default = active_style if active_style in style_names else "default"
    style = _ask_choice(ask, say, "Writing style:", style_names, style_default)
    values["style"] = style

    write_path = target or (Path.home() / ".config" / "local-flow" / "config.toml")
    write_path.parent.mkdir(parents=True, exist_ok=True)

    if write_path.exists():
        say(f"Config already exists at {write_path}.")
        answer = ask("Overwrite? [y/N] > ")
        if answer.strip().lower() not in ("y", "yes"):
            say("keeping existing config")
            return write_path

    content = _render_toml(values)

    # Write to a temp file in the same directory first and validate that,
    # so a pre-existing config at write_path is never touched unless the
    # new one actually passes load_config.
    with tempfile.NamedTemporaryFile(
        dir=write_path.parent, suffix=".toml", delete=False
    ) as tmp_file:
        tmp_file.write(content.encode("utf-8"))
    tmp_path = Path(tmp_file.name)

    try:
        load_config(config_file=tmp_path)
    except ConfigError:
        tmp_path.unlink(missing_ok=True)
        raise

    os.replace(tmp_path, write_path)

    _print_next_steps(say, write_path)
    return write_path
