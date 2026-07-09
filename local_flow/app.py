"""local-flow command-line entry point.

Subcommands:
- ``demo``    headless pipeline demo with mocks (no permissions needed)
- ``run``     live dictation (microphone + hotkey/VAD + LM Studio)
- ``recover`` reprocess dictation audio left behind by a crash mid-utterance
- ``polish``  one-shot: clean/polish a rough transcript from the CLI
- ``transcribe`` transcribe audio file(s) through the local ASR (+ polish)
- ``command`` one-shot command mode: transform text per an instruction
- ``transform`` apply a named AI rewrite to ``--text`` or the current selection
- ``check``   diagnose the environment (LM Studio, ASR, audio, clipboard)
- ``history`` list/search/clear the local dictation history
- ``pad``     markdown scratchpad notes: list/show/append/switch/create/window
- ``learn``   mine history for candidate dictionary terms, optionally add them
- ``stats``   local-only personal insights: words, streaks, top apps
- ``tray``    menu-bar app with live states + style/language quick-switch
- ``setup``   interactive onboarding wizard that writes a validated config
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterable, Iterator
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from local_flow import __version__
from local_flow.asr.base import Transcriber
from local_flow.asr.streaming import TranscriberStream
from local_flow.config import Config, load_config
from local_flow.errors import ConfigError, HotkeyBackendMissingError, LocalFlowError
from local_flow.personalization.store import PersonalizationStore
from local_flow.status import ConsoleReporter, StatusReporter

if TYPE_CHECKING:
    from local_flow.audio.capture import AudioSource
    from local_flow.audio.recovery import PendingAudioStore
    from local_flow.audio.vad import VoiceActivityDetector
    from local_flow.history.store import HistoryStore
    from local_flow.insertion.base import TextSink
    from local_flow.pipeline import DictationPipeline
    from local_flow.transforms.selection import SelectionCapture

# The energy VAD's dataclass default (see `Config.vad_energy_threshold`);
# `_build_vad` uses this to detect whether the user left the threshold at
# its default (in which case `vad_preset="whisper"` may lower it).
_DEFAULT_VAD_ENERGY_THRESHOLD = next(
    f.default for f in fields(Config) if f.name == "vad_energy_threshold"
)
_WHISPER_PRESET_ENERGY_THRESHOLD = 150.0

# Minimum time (monotonic seconds) that must elapse after one transform-hotkey
# tap COMPLETES before another is allowed to run -- see `_run_loop`'s
# `_transform_tap` and `_transform_tap_debounced` below.
_TRANSFORM_DEBOUNCE_S = 1.0

# How long `_Recording.finish` waits for a push-to-talk recorder thread to
# exit after its stop event is set. Module-level so tests can monkeypatch it
# down and exercise the stalled-recorder path without a real 5-second wait.
_RECORDER_JOIN_TIMEOUT_S = 5.0


def parse_mic_priority(raw: str) -> list[str]:
    """Parse ``config.mic_priority`` (e.g. ``"AirPods, USB"``) into a
    priority-ordered list of device-name substrings, mirroring
    ``local_flow.tray.app.parse_languages``: entries are stripped, blanks
    dropped, and duplicates removed (case-insensitively, since matching
    itself is case-insensitive) while preserving first-seen order.
    """
    seen: set[str] = set()
    names: list[str] = []
    for piece in raw.split(","):
        name = piece.strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(name)
    return names


def _fail(exc: LocalFlowError) -> int:
    print(f"error: {exc.message}", file=sys.stderr)
    if exc.hint:
        print(f"hint : {exc.hint}", file=sys.stderr)
    return 1


def _build_chat_client(config: Config):
    from local_flow.llm.lmstudio import LMStudioClient

    return LMStudioClient(
        base_url=config.lmstudio_base_url,
        model=config.lmstudio_model,
        timeout=config.lmstudio_timeout,
    )


def _build_selection_capture(config: Config):
    """Build the real (pynput/pyperclip-backed) :class:`SelectionCapture`.

    A standalone function -- rather than constructing it inline in
    ``_cmd_transform`` -- so tests can monkeypatch
    ``local_flow.app._build_selection_capture`` to inject a
    :class:`~local_flow.transforms.selection.MockSelectionBackend`, the same
    seam ``_build_chat_client``/``_build_sink`` provide for their adapters.
    ``config`` is unused today but kept for signature symmetry with those
    builders (and in case a future config knob tunes poll timing).
    """
    from local_flow.transforms.selection import PynputSelectionBackend, SelectionCapture

    return SelectionCapture(PynputSelectionBackend())


def _build_transcriber(config: Config):
    if config.asr_language != "en" and config.asr_model.endswith(".en"):
        raise ConfigError(
            f"asr_language={config.asr_language!r} is not compatible with "
            f"English-only model {config.asr_model!r}.",
            hint="Use a multilingual model such as `small`, not `small.en`.",
        )
    if config.asr_backend == "mock":
        from local_flow.asr.mock import MockTranscriber

        return MockTranscriber(["(mock transcription)"], language=config.asr_language)
    from local_flow.asr.faster_whisper_asr import FasterWhisperTranscriber

    return FasterWhisperTranscriber(
        model=config.asr_model,
        device=config.asr_device,
        compute_type=config.asr_compute_type,
        language=config.asr_language,
    )


class _NullTranscriber(Transcriber):
    """A transcriber for text-only pipelines that must never transcribe.

    ``history --retry`` reprocesses an already-saved transcript through
    :meth:`DictationPipeline.process_transcript`, which never touches the
    transcriber -- so building a real (model-loading) one just to satisfy the
    constructor would make retry fail on an unrelated ASR setup error
    (missing model or ``asr`` extra). This null object stands in for it and
    raises loudly if anything ever routes audio through such a pipeline.
    """

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        raise RuntimeError(
            "text-only pipeline has no transcriber (this pipeline was built for "
            "`history --retry`, which only reprocesses saved text)"
        )


def _build_vad(config: Config):
    """Build the configured VAD backend.

    ``vad_preset="whisper"`` lowers the energy VAD's RMS threshold from the
    dataclass default (500.0) to 150.0 for quieter/whispered speech --  but
    only when ``vad_energy_threshold`` still equals that default, i.e. the
    user hasn't set it explicitly. Limitation (documented, not fixed): an
    *explicit* ``vad_energy_threshold=500.0`` is indistinguishable from
    "unset" and is treated as unset, so the whisper preset would still apply.
    An explicitly-set non-default threshold always wins over the preset.
    """
    from local_flow.audio.vad import EnergyVAD, MockVAD, WebRtcVAD

    if config.vad_backend == "webrtc":
        return WebRtcVAD(config.vad_aggressiveness)
    if config.vad_backend == "mock":
        return MockVAD([])
    threshold = config.vad_energy_threshold
    if config.vad_preset == "whisper" and threshold == _DEFAULT_VAD_ENERGY_THRESHOLD:
        threshold = _WHISPER_PRESET_ENERGY_THRESHOLD
    return EnergyVAD(threshold)


def _insertion_sinks(method: str) -> list | None:
    """Return the ordered list of low-level sinks for one ``insert_method``.

    Shared by ``_build_sink`` (single sink for ``config.insert_method``) and
    ``_build_sinks_by_method`` (one :class:`InsertionManager` per method, used
    by the :class:`~local_flow.context.router.ContextRouter` for per-app
    insert overrides) so both stay in lockstep.
    """
    from local_flow.insertion.desktop import (
        ClipboardOnlySink,
        ClipboardPasteSink,
        TypingSink,
    )

    return {
        "paste": [ClipboardPasteSink()],
        "type": [TypingSink()],
        "clipboard": [ClipboardOnlySink()],
        "auto": [ClipboardPasteSink(), TypingSink(), ClipboardOnlySink()],
    }.get(method)


def _build_sink(config: Config):
    from local_flow.insertion.base import InsertionManager

    sinks = _insertion_sinks(config.insert_method)
    if sinks is None:
        raise LocalFlowError(
            f"Unknown insert method {config.insert_method!r}.",
            hint="Use one of: auto, paste, type, clipboard.",
        )
    return InsertionManager(sinks)


def _build_sinks_by_method() -> dict[str, object]:
    """Build one :class:`InsertionManager` per known insert method.

    Used by the context router so a per-app ``"insert": "type"`` rule can
    route to a different sink than the pipeline's configured default.
    """
    from local_flow.insertion.base import InsertionManager

    return {
        method: InsertionManager(sinks)
        for method in ("auto", "paste", "type", "clipboard")
        if (sinks := _insertion_sinks(method)) is not None
    }


def _build_history_store(config: Config):
    from local_flow.history.store import HistoryStore

    return HistoryStore(
        config.data_dir,
        max_entries=config.history_max_entries,
        retention=config.history_retention,
    )


def _build_note_store(config: Config):
    from local_flow.scratchpad.store import NoteStore

    return NoteStore(config.data_dir)


def _build_router(config: Config, store: PersonalizationStore):
    """Build the context router, or ``None`` when context styles are off/unused.

    Only constructed when ``config.context_styles`` is enabled AND
    ``app_styles.json`` actually has rules configured, so the common case
    (no per-app rules) never pays for a frontmost-app lookup per utterance.
    """
    if not config.context_styles:
        return None
    rules = store.app_rules()
    if not rules:
        return None
    from local_flow.context.frontmost import create_frontmost_provider
    from local_flow.context.router import ContextRouter

    return ContextRouter(
        provider=create_frontmost_provider(),
        rules=rules,
        sinks_by_method=_build_sinks_by_method(),
    )


def _build_field_text(config: Config):
    """Build the E10 field-text provider, or ``None`` when the feature is off.

    Mirrors ``_build_router``'s gating: skip constructing a provider
    entirely when ``config.context_awareness`` is off, rather than building
    a ``NullFieldText``/stub that would cost nothing per-call but is still
    pointless to construct -- and gives ``DictationPipeline`` a clean signal
    (``field_text is None``) to skip resolving context at all (see
    ``local_flow.pipeline.DictationPipeline.process_transcript``).
    """
    if not config.context_awareness:
        return None
    from local_flow.context.field_text import create_field_text_provider

    return create_field_text_provider()


def _build_pipeline(config: Config, chat_client, sink, transcriber: Transcriber | None = None):
    """Wire a :class:`DictationPipeline` from ``config``.

    ``transcriber`` defaults to :func:`_build_transcriber` (the real,
    model-loading backend) so live/file paths are unchanged; text-only
    callers (see :func:`_build_text_pipeline`) pass a :class:`_NullTranscriber`
    to avoid loading ASR they never use.
    """
    from local_flow.commands.command_mode import CommandMode
    from local_flow.pipeline import DictationPipeline
    from local_flow.polish.polisher import TranscriptPolisher

    store = PersonalizationStore(config.data_dir)
    _, style_rules = store.style_rules(config.style)
    polisher = TranscriptPolisher(
        chat_client, store, style=config.style, level=config.cleanup_level
    )
    command_mode = (
        CommandMode(
            chat_client,
            # Bound method, not a snapshot: new dictionary terms (e.g. from a
            # spoken "add X to the dictionary") reach the next command-mode
            # prompt without restarting the process.
            dictionary_terms=store.dictionary_terms,
            style_rules=style_rules,
        )
        if chat_client is not None
        else None
    )
    history = _build_history_store(config) if config.history_enabled else None
    auto_transform_prompt = _resolve_auto_transform_prompt(config, store)
    return DictationPipeline(
        transcriber=transcriber if transcriber is not None else _build_transcriber(config),
        polisher=polisher,
        store=store,
        sink=sink,
        command_mode=command_mode,
        history=history,
        router=_build_router(config, store),
        auto_transform_prompt=auto_transform_prompt,
        field_text=_build_field_text(config),
    )


def _build_text_pipeline(config: Config, *, transcriber: Transcriber | None = None):
    """Build a pipeline for the text-only / file-only commands.

    Everything ``run`` builds *except* the live audio source and VAD -- so
    ``history --retry`` and ``recover`` don't enumerate a microphone they
    don't need (a missing/denied mic, or recovering on another machine, would
    otherwise abort before any saved data is touched). ``recover`` genuinely
    transcribes saved WAVs, so it takes the default (real) transcriber;
    ``history --retry`` passes a :class:`_NullTranscriber`.
    """
    chat_client = _build_chat_client(config)
    sink = _build_sink(config)
    return _build_pipeline(config, chat_client, sink, transcriber=transcriber)


def _resolve_auto_transform_prompt(
    config: Config, store: PersonalizationStore
) -> str | None:
    """Resolve ``config.auto_transform`` (a transforms.json name) to its
    prompt text, once at pipeline-build time.

    ``""`` (the default) means the feature is off and this returns ``None``
    -- ``DictationPipeline`` treats that as a complete no-op. A non-empty
    name that isn't in ``store.transforms()`` fails fast here with a
    ``ConfigError`` (rather than silently disabling the feature or failing
    on every dictation), since an unresolvable auto-transform is almost
    certainly a typo the user would want to know about immediately.

    Deliberately harsher than an unknown ``transform_default`` (see
    ``_run_loop``'s transform-hotkey block, which only warns and disables
    that one hotkey): that failure loses one opt-in, per-tap hotkey, while
    this one would otherwise affect *every* dictation, so it is worth
    stopping the whole process for instead of degrading silently/repeatedly.
    """
    if not config.auto_transform:
        return None
    transforms = store.transforms()
    if config.auto_transform not in transforms:
        raise ConfigError(
            f"Unknown auto_transform {config.auto_transform!r}.",
            hint=f"Known transforms: {', '.join(transforms) or '(none)'}. "
            f"Edit {config.data_dir / 'transforms.json'} to add one, or set "
            "LOCAL_FLOW_AUTO_TRANSFORM to an existing name.",
        )
    return transforms[config.auto_transform]


def _cmd_demo(_args: argparse.Namespace, _config: Config) -> int:
    from local_flow.demo import run_demo

    return run_demo()


def _polish_text(
    config: Config, text: str, no_llm: bool = False
) -> tuple[str, list[str], list[str]]:
    """Run the polish pipeline's *text* half only -- no insertion, no history.

    Rules cleanup + optional LLM polish, then dictionary/snippet/dictation-
    command handling: the exact composition ``_cmd_polish`` has always used
    (as opposed to ``DictationPipeline.process_transcript``, which
    additionally runs ``apply_spoken_code_syntax`` and
    ``extract_dictionary_additions`` -- a known, documented drift between the
    one-shot CLI text path and live dictation; see the Phase 5 plan). Both
    ``_cmd_polish`` and ``_cmd_transcribe`` call this so the two one-shot
    text paths cannot drift *from each other*, even though both still drift
    from the live pipeline.

    Returns ``(final_text, key_actions, warnings)``.
    """
    from local_flow.polish.polisher import TranscriptPolisher
    from local_flow.polish.rules import (
        apply_dictation_commands,
        enforce_dictionary,
        expand_snippets,
    )

    store = PersonalizationStore(config.data_dir)
    chat_client = None if no_llm else _build_chat_client(config)
    polisher = TranscriptPolisher(
        chat_client, store, style=config.style, level=config.cleanup_level
    )
    result = polisher.polish(text)
    final, _dict_count = enforce_dictionary(result.polished, store.dictionary_terms())
    final, _snippet_count = expand_snippets(final, store.snippets())
    final, actions = apply_dictation_commands(final)
    return final, actions, list(result.warnings)


def _cmd_polish(args: argparse.Namespace, config: Config) -> int:
    text, actions, warnings = _polish_text(config, args.text, no_llm=args.no_llm)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(text)
    if actions:
        print(f"(key actions: {', '.join(actions)})", file=sys.stderr)
    return 0


def _cmd_command(args: argparse.Namespace, config: Config) -> int:
    from local_flow.commands.command_mode import CommandMode
    from local_flow.llm.mock import MockChatClient

    store = PersonalizationStore(config.data_dir)
    _, style_rules = store.style_rules(config.style)
    if args.mock:
        chat_client = MockChatClient()  # echoes the prompt; useful for wiring tests
    else:
        chat_client = _build_chat_client(config)
    command_mode = CommandMode(
        chat_client,
        dictionary_terms=store.dictionary_terms(),
        style_rules=style_rules,
    )
    print(command_mode.run(args.instruction, target_text=args.text))
    return 0


def _cmd_transform(args: argparse.Namespace, config: Config) -> int:
    """Apply a named transform (``transforms.json``) to ``--text`` or the
    current OS selection.

    ``--list`` short-circuits before any name/text/selection validation.
    Otherwise exactly one of ``--text``/``--selection`` is required; a
    selection capture that finds nothing highlighted fails with a hint
    rather than silently transforming an empty string. The chat client is
    only built once there is confirmed text to send it, so ``--list`` and a
    "nothing selected" failure never need LM Studio reachable.
    """
    store = PersonalizationStore(config.data_dir)
    transforms = store.transforms()

    if args.list:
        if not transforms:
            print("no transforms configured")
        else:
            for name in transforms:
                print(name)
        return 0

    if not args.name:
        raise LocalFlowError(
            "transform needs a name.",
            hint="Run `local-flow transform --list` to see available names.",
        )
    if args.name not in transforms:
        raise LocalFlowError(
            f"Unknown transform {args.name!r}.",
            hint=f"Known transforms: {', '.join(transforms) or '(none)'}. "
            f"Edit {config.data_dir / 'transforms.json'} to add one.",
        )

    text_given = args.text is not None
    selection_given = args.selection
    if text_given == selection_given:  # both or neither
        raise LocalFlowError(
            "transform needs exactly one of --text or --selection.",
            hint='Pass either --text "..." or --selection, not both/neither.',
        )

    from local_flow.transforms.registry import apply_transform

    prompt = transforms[args.name]

    if selection_given:
        capture = _build_selection_capture(config)
        try:
            # capture() itself is inside this try (not called before it): it
            # already overwrites the clipboard partway through (save -> clear
            # -> send_copy -> poll -- see SelectionCapture.capture's
            # docstring), so an exception raised *during* capture (e.g. the
            # backend's send_copy chord fails) must still hit the except
            # below and restore, exactly like a post-capture failure would.
            selected = capture.capture()
            if not selected:
                raise LocalFlowError(
                    "No text is selected.",
                    hint="Highlight some text in the frontmost app before running "
                    "`local-flow transform ... --selection`.",
                )
            chat_client = _build_chat_client(config)
            result = apply_transform(chat_client, prompt, selected)
            if not result.strip():
                # Never paste an empty completion over the user's selection
                # (the paste is unrecoverable -- restore() rewrites the
                # clipboard, not the selection). The except below restores.
                raise LocalFlowError(
                    "The transform returned no text; the selection was left unchanged.",
                    hint="Check the transform's prompt and the LM Studio model, then retry.",
                )
            capture.replace(result)
        except BaseException:
            # On any failure -- capture itself, "nothing selected", LM Studio
            # unreachable, or anything else -- restore the user's original
            # clipboard before the exception propagates. A no-op once
            # replace() above has already restored (SelectionCapture tracks
            # that internally).
            capture.restore()
            raise
        print(f"replaced selection with the {args.name!r} transform.", file=sys.stderr)
        return 0

    chat_client = _build_chat_client(config)
    result = apply_transform(chat_client, prompt, args.text)
    print(result)
    return 0


def _validate_audio_paths(raw_paths: list[str]) -> list[Path]:
    """Resolve every ``transcribe`` file argument and fail fast on the first
    one that doesn't exist, *before* the (expensive) ASR model loads.

    Validating one file at a time, interleaved with transcription, would let
    a slow model load succeed only to fail moments later on file #2 -- so
    every path is checked upfront instead.
    """
    paths = []
    for raw in raw_paths:
        path = Path(raw)
        if not path.is_file():
            raise LocalFlowError(
                f"Audio file not found: {path}",
                hint="Check the path. The real ASR backend accepts any container "
                "faster-whisper's bundled PyAV can decode (wav/mp3/m4a/flac/...); "
                "the mock backend (LOCAL_FLOW_ASR_BACKEND=mock) only reads plain WAV.",
            )
        paths.append(path)
    return paths


def _cmd_transcribe(args: argparse.Namespace, config: Config) -> int:
    """Transcribe one or more audio files through the local ASR, optionally
    polishing each transcript -- no insertion or history side effects.

    A feature Wispr Flow itself doesn't offer (it only transcribes live
    microphone input): point this at an existing voice memo, meeting
    recording, or any other audio file and get text back, straight to
    stdout, entirely on-device.
    """
    paths = _validate_audio_paths(args.files)

    transcriber_config = (
        replace(config, asr_language=args.language) if args.language else config
    )
    transcriber = _build_transcriber(transcriber_config)

    transcripts = []
    for path in paths:
        print(f"transcribing {path.name}...", file=sys.stderr)
        text = transcriber.transcribe_path(path)
        if args.polish:
            text, actions, warnings = _polish_text(config, text)
            for warning in warnings:
                print(f"warning: {warning}", file=sys.stderr)
            if actions:
                print(f"(key actions: {', '.join(actions)})", file=sys.stderr)
        if len(paths) > 1:
            print(f"== {path.name} ==")
        print(text)
        transcripts.append(text)

    if args.copy:
        from local_flow.insertion.desktop import ClipboardOnlySink

        # Every file's transcript, not just the last one's; blank-line
        # separated so multi-file output pastes as distinct paragraphs.
        ClipboardOnlySink().insert("\n\n".join(transcripts))

    return 0


def _describe_input_devices(
    devices: list[dict], default_index: int | None, chosen_index: int | None
) -> list[str]:
    """Format one ``check`` line per input-capable device, marking the OS
    default and the one ``mic_priority`` would select (independently -- they
    need not be the same device). Pure (no sounddevice import), so it is
    directly unit-testable with fake device dicts.
    """
    lines = []
    for index, device in enumerate(devices):
        if device.get("max_input_channels", 0) <= 0:
            continue
        markers = []
        if index == default_index:
            markers.append("default")
        if index == chosen_index:
            markers.append("selected by mic_priority")
        suffix = f" [{', '.join(markers)}]" if markers else ""
        lines.append(f"    [{index}] {device.get('name', '?')}{suffix}")
    if not lines:
        lines.append("    (no input devices found)")
    return lines


def _cmd_check(_args: argparse.Namespace, config: Config) -> int:
    print(f"local-flow {__version__} environment check")
    print(f"  data dir      : {config.data_dir}")
    print(f"  ASR model     : {config.asr_model} (language: {config.asr_language})")

    if config.context_styles:
        from local_flow.context.frontmost import create_frontmost_provider

        info = create_frontmost_provider().current()
        frontmost_label = info.app_id or info.title or "(unknown)"
        print(f"  frontmost app : {frontmost_label}")
    else:
        print("  frontmost app : (context styles disabled)")

    from local_flow.errors import LMStudioError

    try:
        client = _build_chat_client(config)
        models = client.list_models()
        print(f"  LM Studio     : OK at {config.lmstudio_base_url}, "
              f"models: {', '.join(models) or '(none loaded!)'}")
    except (LMStudioError, LocalFlowError) as exc:
        print(f"  LM Studio     : UNAVAILABLE - {exc.message}")
        if exc.hint:
            print(f"                  hint: {exc.hint}")

    print("  input devices :")
    try:
        import sounddevice

        from local_flow.audio.capture import pick_input_device

        devices = sounddevice.query_devices()
        try:
            default_index = sounddevice.default.device[0]
        except Exception:
            default_index = None
        preferred = parse_mic_priority(config.mic_priority)
        chosen_index = pick_input_device(devices, preferred) if preferred else None
        for line in _describe_input_devices(devices, default_index, chosen_index):
            print(line)
    except (ImportError, OSError) as exc:
        print(f"    sounddevice unavailable: {exc}")

    for label, module, extra in (
        ("faster-whisper", "faster_whisper", "asr"),
        ("sounddevice", "sounddevice", "audio"),
        ("webrtcvad", "webrtcvad", "audio"),
        ("pynput", "pynput", "desktop"),
        ("pyperclip", "pyperclip", "desktop"),
    ):
        try:
            __import__(module)
            print(f"  {label:<14}: installed")
        except (ImportError, OSError):
            print(f"  {label:<14}: missing (install with: uv sync --extra {extra})")
    print("check done (informational; run 'local-flow demo' for a full pipeline test)")
    return 0


def _truncate(text: str, limit: int = 80) -> str:
    flattened = " ".join(text.split())
    if len(flattened) <= limit:
        return flattened
    return flattened[:limit] + "..."


def _display_timestamp(raw: str) -> str:
    """Trim a stored ISO timestamp to whole seconds for display."""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_history_record(store: HistoryStore, n: int):
    """Resolve record ``n`` (1-based) against the plain newest-first listing.

    Used by ``--show``/``--reinsert-raw``: both operate on the same ordering
    as the unfiltered ``local-flow history`` listing, ignoring any
    ``--search``/``--limit`` given alongside them, so a number a user saw in
    a plain listing always resolves to the same record.
    """
    if n < 1:
        raise LocalFlowError(
            f"invalid record number {n}.",
            hint="record numbers start at 1, as shown by `local-flow history`.",
        )
    records = store.recent(limit=n)
    if len(records) < n:
        count = len(records)
        raise LocalFlowError(
            f"no record #{n} (history has {count} record{'s' if count != 1 else ''}).",
            hint="run `local-flow history` to see valid record numbers.",
        )
    return records[n - 1]


def _cmd_history(args: argparse.Namespace, config: Config) -> int:
    store = _build_history_store(config)

    if not config.history_enabled:
        print(
            "note: history recording is currently disabled "
            "(LOCAL_FLOW_HISTORY_ENABLED=false); showing existing records, if any."
        )

    if args.clear:
        path = store.path
        store.clear()
        print(f"cleared history: {path}")
        return 0

    if args.show is not None:
        record = _resolve_history_record(store, args.show)
        tag = "llm" if record.used_llm else "raw"
        print(f"{_display_timestamp(record.timestamp)}  [{tag}]  record #{args.show}")
        print(f"rough: {record.rough}")
        print(f"final: {record.final}")
        return 0

    if args.reinsert_raw is not None:
        record = _resolve_history_record(store, args.reinsert_raw)
        sink = _build_sink(config)
        sink.insert(record.rough)
        print(f"reinserted rough text from record #{args.reinsert_raw}: {record.rough!r}")
        return 0

    if args.retry is not None:
        record = _resolve_history_record(store, args.retry)
        # Text-only: `process_transcript` never transcribes, so build with a
        # null transcriber -- retrying saved text must not depend on a working
        # ASR model/extra.
        pipeline = _build_text_pipeline(config, transcriber=_NullTranscriber())
        result = pipeline.process_transcript(record.rough)
        print(f"retried record #{args.retry}: {result.final!r}")
        return 0

    records = (
        store.search(args.search, limit=args.limit)
        if args.search
        else store.recent(limit=args.limit)
    )
    if not records:
        print(f"no dictation history yet (file: {store.path})")
        return 0

    for record in records:
        tag = "llm" if record.used_llm else "raw"
        line = f'{_display_timestamp(record.timestamp)}  [{tag}]  "{_truncate(record.final)}"'
        if args.verbose:
            line += f' (rough: "{_truncate(record.rough)}")'
        print(line)
    return 0


_PAD_DICTATION_JOIN_TIMEOUT = 5.0


def _cmd_pad(args: argparse.Namespace, config: Config) -> int:
    """``local-flow pad``: headless CRUD over local markdown scratchpad notes,
    plus ``--window`` (the floating always-on-top editor).

    Exactly one of ``--list``/``--show``/``--append``/``--use``/``--new``/
    ``--window`` is accepted at a time (enforced by an argparse mutually
    exclusive group); with none given, behaves like a bare ``--show``
    (prints the active note) -- the friendliest default for `local-flow pad`
    on its own. ``--note`` only makes sense alongside ``--append`` (it names
    a note other than the active one to append to); given without
    ``--append`` it fails with a hint rather than silently doing nothing.
    Likewise ``--with-dictation`` only makes sense alongside ``--window``.

    ``--window`` runs :class:`~local_flow.scratchpad.window.ScratchpadWindow`
    as this process's own main program -- blocking on its Tk event loop,
    exactly like ``local-flow tray``'s pystray icon -- rather than as a
    thread inside `local-flow run` (see the module's docstring for why: Tk
    needs a single main thread). ``--with-dictation`` additionally starts the
    normal dictation run-loop on a worker thread (its own stop event +
    `ConsoleReporter`) for the lifetime of the window, so one process can
    both show the pad and dictate into it -- the loop is stopped and joined
    (best-effort) once the window closes.
    """
    store = _build_note_store(config)

    if args.note is not None and args.append is None:
        raise LocalFlowError(
            "--note only makes sense together with --append.",
            hint="Use `local-flow pad --append TEXT --note NAME`, or drop --note "
            "to target the active note.",
        )

    if args.with_dictation and not args.window:
        raise LocalFlowError(
            "--with-dictation only makes sense together with --window.",
            hint="Use `local-flow pad --window --with-dictation`.",
        )

    if args.window:
        from local_flow.scratchpad.window import ScratchpadWindow

        window = ScratchpadWindow(store)
        stop_event: threading.Event | None = None
        loop_thread: threading.Thread | None = None
        if args.with_dictation:
            stop_event = threading.Event()
            loop_thread = threading.Thread(
                target=_run_loop,
                args=(config, config.mode, ConsoleReporter(), stop_event),
                daemon=True,
            )
            loop_thread.start()
        try:
            window.run()
        finally:
            if stop_event is not None:
                stop_event.set()
            if loop_thread is not None:
                loop_thread.join(timeout=_PAD_DICTATION_JOIN_TIMEOUT)
        return 0

    if args.list:
        notes = store.list_notes()
        if not notes:
            print(f"no notes yet (dir: {store.notes_dir})")
            return 0
        active = store.active_note()
        for name in notes:
            print(f"{name}{' (active)' if name == active else ''}")
        return 0

    if args.append is not None:
        path = store.append(args.append, name=args.note)
        target = args.note or store.active_note()
        print(f"appended to '{target}': {path}")
        return 0

    if args.use is not None:
        store.set_active(args.use)
        store.create(args.use)
        print(f"active note: {args.use}")
        return 0

    if args.new is not None:
        path = store.create(args.new)
        print(f"note ready: {path}")
        return 0

    # Bare `--show`, `--show NAME`, or no flags at all: show a note (active
    # by default). `args.show` is `None` when the flag was never given and
    # `""` when given with no NAME -- both fall back to the active note.
    name = args.show if args.show else store.active_note()
    content = store.read(name)
    print(f"-- {name} --")
    print(content if content else "(empty)")
    return 0


def _cmd_learn(args: argparse.Namespace, config: Config) -> int:
    from local_flow.personalization.learn import suggest_terms

    store = PersonalizationStore(config.data_dir)
    history = _build_history_store(config)
    records = list(history.all())
    if not records:
        print(f"no dictation history yet (file: {history.path})")
        return 0

    suggestions = suggest_terms(
        records,
        store.dictionary_terms(),
        min_count=args.min_count,
        limit=args.limit,
    )
    if not suggestions:
        print(
            "no new terms to suggest yet "
            "(dictate more, or lower --min-count to see rarer candidates)."
        )
        return 0

    for i, suggestion in enumerate(suggestions, start=1):
        print(f'{i}. {suggestion.term} (x{suggestion.count}) — "{suggestion.sample}"')

    numbers: set[int] = set()
    if args.add_all:
        numbers = set(range(1, len(suggestions) + 1))
    elif args.add:
        numbers = set(args.add)

    for n in sorted(numbers):
        if not (1 <= n <= len(suggestions)):
            print(f"warning: no suggestion #{n}", file=sys.stderr)
            continue
        term = suggestions[n - 1].term
        if store.add_dictionary_term(term):
            print(f"added '{term}' to dictionary")
        else:
            print(f"'{term}' already in dictionary")
    return 0


def _parse_history_timestamp(raw: str) -> datetime | None:
    """Parse a stored history timestamp, tolerating a trailing ``Z``.

    Reuses the same implementation as ``local_flow.insights.stats._parse_timestamp``
    (avoiding duplication while maintaining consistency with
    ``local_flow.history.store._parse_timestamp``). Used by ``_cmd_stats`` for
    the ``--since`` window cutoff; a naive value is treated as UTC, matching
    how every timestamp in this project is generated (see
    ``HistoryStore.append_new``).
    """
    from local_flow.insights.stats import _parse_timestamp

    return _parse_timestamp(raw)


def _parse_since(raw: str) -> timedelta | None:
    """Parse ``stats --since``'s ``Nd`` / ``all`` grammar into a lookback
    window. ``None`` means ``all`` -- no cutoff, subject only to the
    unparseable-timestamp exclusion ``_cmd_stats`` always applies.

    Anything else must be a positive integer immediately followed by ``d``
    (days); anything that doesn't match fails fast with a hint rather than
    silently falling back to ``all`` or ``30d``.
    """
    if raw == "all":
        return None
    digits = raw[:-1]
    if raw.endswith("d") and digits.isdigit() and int(digits) > 0:
        return timedelta(days=int(digits))
    raise LocalFlowError(
        f"invalid --since value {raw!r}.",
        hint="Use e.g. `7d`, `30d` (the default), or `all`.",
    )


def _cmd_stats(args: argparse.Namespace, config: Config) -> int:
    """``local-flow stats``: a purely local personal-insights report.

    Mirrors ``_cmd_history``'s "history disabled" notice (stats reads the
    same store; a disabled history just means there's nothing NEW to report
    on, not that the command itself stops working). ``now`` is read once,
    right here, at the CLI boundary -- ``compute_stats``/``render_heatmap``
    themselves never touch the wall clock (see
    ``local_flow.insights.stats``).

    ``--since`` (``Nd``/``all``, see ``_parse_since``) filters records by
    timestamp *before* they ever reach ``compute_stats``. A record whose
    timestamp fails to parse is excluded from the report entirely (matching
    ``compute_stats``'s own "excluded everywhere" rule) rather than being
    arbitrarily included in one window or another; if any were skipped this
    way, a one-line note says how many.
    """
    from local_flow.insights.stats import compute_stats, render_heatmap

    store = _build_history_store(config)
    if not config.history_enabled:
        print(
            "note: history recording is currently disabled "
            "(LOCAL_FLOW_HISTORY_ENABLED=false); showing existing records, if any."
        )

    # Validate --since early, even before checking if history is empty,
    # so invalid values (e.g., "banana") always raise an error.
    cutoff = _parse_since(args.since)

    all_records = list(store.all())
    if not all_records:
        print(f"no dictation history yet (file: {store.path})")
        return 0

    now = datetime.now(UTC)
    since_at = now - cutoff if cutoff is not None else None

    windowed = []
    unparseable = 0
    for record in all_records:
        parsed = _parse_history_timestamp(record.timestamp)
        if parsed is None:
            unparseable += 1
            continue
        if since_at is not None and parsed < since_at:
            continue
        windowed.append(record)

    if not windowed:
        print(
            f"no dictations in the last {args.since} "
            "(try `local-flow stats --since all`)."
        )
        if unparseable:
            print(f"note: {unparseable} record(s) skipped: unparseable timestamp")
        return 0

    # tz=None: bucket active days/streaks/heatmap by the MACHINE's local
    # calendar (an evening dictation west of UTC belongs to the user's local
    # day, not the next UTC day). The --since cutoff above stays instant-based.
    stats = compute_stats(windowed, now, tz=None)

    top_apps = (
        ", ".join(f"{app} ({count})" for app, count in stats.top_apps)
        if stats.top_apps
        else "(none)"
    )
    rows = [
        ("total dictations", str(stats.total_dictations)),
        ("total words", str(stats.total_words)),
        ("words per minute", f"{stats.words_per_minute:.1f}"),
        ("cleaned words", str(stats.cleaned_words_delta)),
        # Honest label (see `local_flow.insights.stats.Stats.replacements`):
        # a count of substitutions applied, not confirmed corrections.
        ("smart replacements applied", str(stats.replacements)),
        ("failed (LM Studio skipped)", str(stats.failed)),
        ("top apps", top_apps),
        ("current streak", f"{stats.current_streak} day(s)"),
        ("longest streak", f"{stats.longest_streak} day(s)"),
    ]
    width = max(len(label) for label, _ in rows)

    print(f"local-flow stats -- since {args.since}")
    for label, value in rows:
        print(f"  {label:<{width}} : {value}")
    if unparseable:
        print(f"  (note: {unparseable} record(s) skipped: unparseable timestamp)")
    # Note when streaks are windowed by the --since cutoff (not when --since all)
    if cutoff is not None:
        print(
            "  (note: streaks are measured within the --since window; "
            "use --since all for all-time)"
        )

    print()
    print("last 8 weeks:")
    print(render_heatmap(stats.active_days, now, tz=None))  # same local-zone bucketing
    return 0


class RunDependencies(NamedTuple):
    """Everything ``_run_loop`` needs to run one dictation session.

    Built once by ``_build_run_dependencies`` from ``Config``; ``TrayApp``
    keeps the same instance across Start/Stop cycles, and tests construct one
    directly and positionally (``RunDependencies(pipeline, source, vad)``)
    since ``pending_store``/``normalize_audio``/``max_utterance_min``/
    ``scratchpad_sink`` all default -- replacing the old ad hoc 3-or-4-tuple
    ``dependencies`` parameter that ``_run_loop`` used to shape-sniff with
    ``*rest``.

    ``scratchpad_sink`` (E13): the :class:`~local_flow.scratchpad.sink.ScratchpadSink`
    that the scratchpad dictate-to-pad hotkey (``config.scratchpad_hotkey``)
    toggles routing into -- see ``_run_loop``'s ``pad_active`` holder and
    ``_handle_utterance``'s ``sink_override``. ``None`` (e.g. in most tests,
    which construct a ``RunDependencies`` directly without one) simply means
    the hotkey has nothing to route to; ``_run_loop`` warns and disables the
    hotkey rather than silently no-op'ing (see its scratchpad-hotkey block).
    """

    pipeline: DictationPipeline
    source: AudioSource
    vad: VoiceActivityDetector
    pending_store: PendingAudioStore | None = None
    normalize_audio: bool = False
    max_utterance_min: int = 20
    scratchpad_sink: TextSink | None = None


def _handle_utterance(
    pipeline: DictationPipeline,
    reporter: StatusReporter,
    pcm: bytes,
    sample_rate: int,
    pending_store: PendingAudioStore | None = None,
    normalize_audio: bool = False,
    max_utterance_min: int = 20,
    sink_override: TextSink | None = None,
) -> None:
    """Run one captured utterance's PCM through the pipeline.

    Reports ``processing`` before the pipeline call, ``warning`` per pipeline
    warning, ``inserted`` (with the final text's ``repr``) on success,
    ``error`` if the pipeline raises, and ``idle`` once the utterance is
    fully handled either way. Extracted from ``_run_loop`` so it can be
    exercised directly in tests without mocking audio capture or hotkeys.

    *Any* exception is caught, not just ``LocalFlowError``: this runs inside
    the hands-free segment loop (and the tray's), where an escaping
    full-disk ``OSError``, ctranslate2 ``RuntimeError``, or bad
    ``asr_language`` ``ValueError`` would silently kill the whole session --
    push-to-talk survives such errors only because ``CallbackDispatcher``'s
    worker happens to swallow them. One utterance may fail; the loop lives.

    When ``pending_store`` is given (see ``local_flow.audio.recovery`` and
    config ``audio_recovery``), the PCM is saved to disk *before*
    ``pipeline.process_audio`` runs and deleted only once that call returns
    normally -- if it raises (caught below as ``error``) or the process
    dies first, the saved WAV is left behind for ``local-flow recover``.
    The save itself sits inside the same guard, so a failing save (disk
    full) costs that one utterance, not the session.
    ``pending_store=None`` is byte-identical to before this existed.

    When ``normalize_audio`` is set (``vad_preset="whisper"``; see
    ``_build_vad``/``_build_run_dependencies``), the PCM is peak-normalized
    *before* both the pending-store save and ``pipeline.process_audio`` --
    so a crash-recovered whisper-mode WAV is already boosted too, and ASR
    always sees the same bytes that were persisted.

    ``sink_override`` (E13 scratchpad): resolved by the *caller* (``_run_loop``,
    from its ``pad_active`` toggle holder) rather than threaded through as a
    boolean flag here, so this function stays a plain, directly-testable
    "run PCM through a pipeline" call -- it doesn't need to know anything
    about hotkeys or toggles, just "here is the sink to force, if any". Only
    passed on to ``pipeline.process_audio`` when not ``None``, so existing
    callers/test doubles whose ``process_audio(pcm, sample_rate)`` doesn't
    accept the keyword are unaffected -- ``None`` (the default) is
    byte-identical to before this parameter existed.

    After processing, if the utterance's duration exceeds
    ``max_utterance_min`` minutes, an extra ``"warning"`` notification fires
    (informational only -- it does not truncate or otherwise change what was
    already processed and inserted).
    """
    reporter.notify("processing")
    try:
        if normalize_audio:
            from local_flow.audio.gain import normalize_peak

            pcm = normalize_peak(pcm)
        pending_path = (
            pending_store.save(pcm, sample_rate) if pending_store is not None else None
        )
        process_kwargs = {"sink_override": sink_override} if sink_override is not None else {}
        result = pipeline.process_audio(pcm, sample_rate, **process_kwargs)
        for warning in result.warnings:
            reporter.notify("warning", warning)
        if result.final:
            reporter.notify("inserted", repr(result.final))
        if result.duration_s > max_utterance_min * 60:
            minutes = result.duration_s / 60
            reporter.notify(
                "warning",
                f"utterance was {minutes:.0f} minutes long; consider shorter dictations",
            )
        if pending_path is not None:
            pending_store.delete(pending_path)
    except LocalFlowError as exc:
        reporter.notify("error", exc.message)
        if exc.hint:
            print(f"hint : {exc.hint}", file=sys.stderr)
    except Exception as exc:
        # Not a LocalFlowError, so there is no curated message/hint -- report
        # the class and keep the full traceback on stderr for diagnosis. The
        # saved pending WAV (if any) is deliberately left for `recover`.
        reporter.notify("error", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        reporter.notify("idle")


def _run_voice_command(
    deps: RunDependencies,
    capture: SelectionCapture,
    pcm: bytes,
    sample_rate: int,
    reporter: StatusReporter,
) -> None:
    """Route one voice-command-hotkey recording: transcribe, then apply it.

    Called from the command hotkey's ``finish()`` (see ``_run_loop``) right
    after the recording stops -- ``capture`` is a freshly built, not-yet-
    ``capture()``-ed :class:`~local_flow.transforms.selection.SelectionCapture`.
    ``capture()`` itself runs *inside* this function (not before it's
    called), first thing, so the selection reflects whatever was highlighted
    while the user was speaking, not whatever is highlighted by the time the
    (slower) transcription finishes -- and so a capture failure is caught by
    the same guard that restores the clipboard, same precision fix as
    ``_cmd_transform``'s residual review note.

    Two outcomes once a non-empty instruction is transcribed:
    - A selection was captured: run :class:`CommandMode` directly against it
      (bypassing ``DictationPipeline.run_command``, which always inserts via
      the sink) and ``capture.replace()`` the result in place.
    - Nothing was selected: restore the clipboard (nothing to replace) and
      fall back to ``pipeline.run_command``, which resolves its own target
      (explicit text, or ``last_transcript``) and inserts via the sink --
      the same path a typed ``local-flow command`` would take.

    Dictionary enforcement applies either way: explicitly here for the
    selection-replace path, and inside ``run_command`` itself for the
    fallback path. Every failure (capture, no command mode configured, empty
    transcription, LLM error) restores the clipboard and reports a
    ``"warning"`` instead of raising -- this runs on ``_run_loop``'s
    ``processor`` worker thread, so nothing here may crash the loop.
    """
    pipeline = deps.pipeline
    try:
        selection = capture.capture()
    except Exception as exc:
        capture.restore()
        reporter.notify(
            "warning", f"voice command: selection capture failed: {exc}"
        )
        return

    if pipeline.command_mode is None:
        capture.restore()
        reporter.notify("warning", "voice command mode needs LM Studio configured")
        return

    try:
        instruction = pipeline.transcriber.transcribe(pcm, sample_rate).strip()
        if not instruction:
            capture.restore()
            reporter.notify("warning", "voice command: nothing heard")
            return
        if selection:
            from local_flow.polish.rules import enforce_dictionary

            transformed = pipeline.command_mode.run(instruction, target_text=selection)
            transformed, _count = enforce_dictionary(
                transformed, pipeline.store.dictionary_terms()
            )
            if not transformed.strip():
                # Never paste an empty completion over the selection (the
                # paste is unrecoverable; restore() only rewrites the
                # clipboard) -- keep the user's text and warn instead.
                capture.restore()
                reporter.notify(
                    "warning", "voice command returned no text; selection left unchanged"
                )
                return
            capture.replace(transformed)
        else:
            capture.restore()  # nothing was selected; nothing to replace
            pipeline.run_command(instruction, target_text=None)
    except Exception as exc:
        capture.restore()
        message = getattr(exc, "message", str(exc))
        reporter.notify("warning", f"voice command failed: {message}")


def _build_pending_store(config: Config):
    """Build the crash-safe audio autosave store, or ``None`` when disabled.

    Gated on ``config.audio_recovery`` (default on): with it off, callers get
    ``None`` and every ``pending_store is not None`` check downstream (in
    ``_handle_utterance``) skips the save/delete entirely -- byte-identical
    to before this feature existed.
    """
    if not config.audio_recovery:
        return None
    from local_flow.audio.recovery import PendingAudioStore

    return PendingAudioStore(config.data_dir)


def _build_run_dependencies(config: Config) -> RunDependencies:
    """Build the pipeline + audio source + VAD + pending-audio store that
    ``_run_loop`` needs.

    Extracted from ``_run_loop`` so ``TrayApp`` can build these once, keep a
    reference to ``pipeline.polisher``/``pipeline.transcriber`` for its
    Style/Language menus, and hand the very same objects to ``_run_loop``
    running on its worker thread (rather than each rebuilding its own
    pipeline). ``_cmd_run`` still goes through ``_run_loop`` with
    ``dependencies=None``, so this call sequence (and its exception
    behavior) is unchanged for the CLI path.

    ``scratchpad_sink`` is always built (a ``NoteStore``/``ScratchpadSink``
    pair costs nothing until something is actually appended -- no file is
    created here) regardless of whether ``config.scratchpad_hotkey`` is set,
    so the hotkey can be toggled on/off freely without rebuilding
    dependencies; ``_run_loop`` only wires the hotkey listener itself when
    ``scratchpad_hotkey`` is non-empty.
    """
    from local_flow.audio.capture import SounddeviceSource
    from local_flow.scratchpad.sink import ScratchpadSink

    chat_client = _build_chat_client(config)
    sink = _build_sink(config)
    pipeline = _build_pipeline(config, chat_client, sink)
    source = SounddeviceSource(
        sample_rate=config.sample_rate,
        preferred=parse_mic_priority(config.mic_priority),
    )
    vad = _build_vad(config)
    pending_store = _build_pending_store(config)
    scratchpad_sink = ScratchpadSink(_build_note_store(config))
    return RunDependencies(
        pipeline=pipeline,
        source=source,
        vad=vad,
        pending_store=pending_store,
        normalize_audio=config.vad_preset == "whisper",
        max_utterance_min=config.max_utterance_min,
        scratchpad_sink=scratchpad_sink,
    )


def _interruptible(
    frames: Iterable[bytes], stop_event: threading.Event | None
) -> Iterator[bytes]:
    """Yield frames until the stop event is set (checked per ~30ms frame).

    Wraps the raw microphone-frame iterator so a hands-free Stop takes
    effect within one frame. Without this, ``segment_stream`` only hands
    control back to its caller when it yields a *completed* utterance --
    during continuous silence (no speech ever detected) it never yields, so
    the `stop_event` check between segments in ``_run_loop`` would never
    run and Stop would block indefinitely. ``stop_event=None`` (the
    ``_cmd_run`` console path) means passthrough: every frame is yielded.
    """
    for frame in frames:
        if stop_event is not None and stop_event.is_set():
            return
        yield frame


def _build_preview_stream(transcriber: Transcriber, sample_rate: int) -> TranscriberStream:
    """Build the :class:`TranscriberStream` used for ``streaming="live-preview"``.

    A standalone module-level function (rather than inlining ``WindowedStream``
    construction in ``_run_loop``) so tests can monkeypatch
    ``local_flow.app._build_preview_stream`` to inject a
    :class:`~local_flow.asr.mock.MockStream` without a real ASR model.
    """
    from local_flow.asr.streaming import WindowedStream

    return WindowedStream(transcriber, sample_rate)


def _with_preview(
    frames: Iterable[bytes], stream: TranscriberStream, reporter: StatusReporter
) -> Iterator[bytes]:
    """Tee mic frames through ``stream`` for a live rough-text preview.

    Every frame is fed to ``stream`` and then yielded through unchanged, so
    ``segment_stream`` downstream sees exactly the same audio as without
    preview. Whenever ``stream.feed()`` returns a re-transcribed partial,
    ``reporter.notify("preview", partial)`` fires. Preview text is
    display-only: the utterance's final, inserted text always comes from the
    normal per-segment ``transcriber.transcribe()`` call in
    ``_handle_utterance`` / ``DictationPipeline``, never from this stream.
    """
    for frame in frames:
        try:
            # Preview is display-only; a failing preview transcription must never
            # interrupt dictation. Catch all exceptions and continue yielding frames.
            partial = stream.feed(frame)
            if partial is not None:
                reporter.notify("preview", partial)
        except Exception:
            # Silently skip the partial on any error; the frame still flows through.
            pass
        yield frame


def _run_mouse_listener(
    mouse_listener,
    on_press: Callable[[], None],
    on_release: Callable[[], None],
) -> None:
    """Target for the mouse push-to-talk daemon thread (see ``_run_loop``).

    An uncaught exception on a daemon thread is silently swallowed by
    Python -- no traceback, no process exit -- so a startup failure (e.g.
    missing Accessibility/Input Monitoring permission) would otherwise
    vanish with no diagnostic at all. Catches ``LocalFlowError`` and prints
    it in the same format as ``_fail``, to stderr, instead.
    """
    try:
        mouse_listener.run(on_press, on_release)
    except LocalFlowError as exc:
        print(f"error: mouse push-to-talk stopped: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"hint : {exc.hint}", file=sys.stderr)


def _run_transform_listener(transform_listener, on_tap: Callable[[], None]) -> None:
    """Target for the transform hotkey's daemon thread (see ``_run_loop``).

    Same visible-failure wrapper as ``_run_mouse_listener``: an uncaught
    exception on a daemon thread is silently swallowed by Python -- no
    traceback, no process exit -- so a startup failure (e.g. missing
    Accessibility/Input Monitoring permission) is caught here and printed in
    ``_fail``'s format instead of vanishing.
    """
    try:
        transform_listener.run(on_tap)
    except LocalFlowError as exc:
        print(f"error: transform hotkey stopped: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"hint : {exc.hint}", file=sys.stderr)


def _run_scratchpad_listener(pad_listener, on_tap: Callable[[], None]) -> None:
    """Target for the scratchpad dictate-to-pad hotkey's daemon thread (see
    ``_run_loop``).

    Same visible-failure wrapper as ``_run_mouse_listener``/
    ``_run_transform_listener``/``_run_command_hotkey_listener``: an uncaught
    exception on a daemon thread is silently swallowed by Python -- no
    traceback, no process exit -- so a startup failure (e.g. missing
    Accessibility/Input Monitoring permission) is caught here and printed in
    ``_fail``'s format instead of vanishing.
    """
    try:
        pad_listener.run(on_tap)
    except LocalFlowError as exc:
        print(f"error: scratchpad hotkey stopped: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"hint : {exc.hint}", file=sys.stderr)


def _run_command_hotkey_listener(
    command_listener,
    on_press: Callable[[], None],
    on_release: Callable[[], None],
) -> None:
    """Target for the voice-command hotkey's daemon thread (see ``_run_loop``).

    Same visible-failure wrapper as ``_run_mouse_listener``/
    ``_run_transform_listener``.
    """
    try:
        command_listener.run(on_press, on_release)
    except LocalFlowError as exc:
        print(f"error: voice command hotkey stopped: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"hint : {exc.hint}", file=sys.stderr)


def _build_secondary_listener(
    factory: Callable[[], object],
    field_name: str,
    value: str,
    feature: str,
    reporter: StatusReporter,
):
    """Construct a secondary-hotkey listener, or warn and return ``None``.

    The transform/command/scratchpad hotkeys are optional extras: a value the
    backend can't observe (``fn`` needs a Quartz tap that only the *main*
    hotkey has, and pynput itself may be missing) must disable just that one
    feature with an actionable warning -- raising here would abort the whole
    ``run`` loop on the main thread for a hotkey the session can live
    without. Same warn-and-disable precedent as an unknown
    ``transform_default``.
    """
    try:
        return factory()
    except HotkeyBackendMissingError as exc:
        detail = exc.message + (f" {exc.hint}" if exc.hint else "")
        reporter.notify(
            "warning",
            f"{field_name} {value!r} is unusable ({detail}); {feature} disabled",
        )
        return None


def _transform_tap_debounced(
    last_completed_at: float, now: float, threshold_s: float = _TRANSFORM_DEBOUNCE_S
) -> bool:
    """Pure debounce check for the transform hotkey's tap handler (see
    `_run_loop`'s `_transform_tap`).

    `TapListener`'s own `held` flag already suppresses OS auto-repeat (a
    physical key-hold fires `on_tap` only once -- see `TapListener`'s
    docstring). But a user rapidly re-tapping the key, or a bouncy/stuck key
    that toggles press/release several times in a fraction of a second, can
    still enqueue more than one `_transform_tap` call on its dispatcher's
    worker queue (the `processor` lane -- see `_run_loop`). A `busy`-style
    flag (set at the start of the callback, cleared in a `finally`) can't
    catch this: that lane is a single `CallbackDispatcher` worker running
    its callbacks one at a time, so by the time a queued duplicate call
    actually runs, the first has already finished and cleared its own flag.
    A plain monotonic-clock debounce sidesteps that: a tap within
    `threshold_s` of the *previous tap's completion* (not its start) is
    rejected outright, extracted here as a standalone pure function so it
    is unit-testable with plain floats instead of a live clock/thread race.
    """
    return now - last_completed_at < threshold_s


class _Recording:
    """One in-flight push-to-talk recording: its worker thread, private stop
    event, and private capture box.

    A fresh instance per ``start()`` (main or command hotkey), never reused.
    That is the whole point: when :meth:`finish` times out waiting for the
    thread (a PortAudio stall -- macOS mic-permission prompt, Bluetooth
    dropout), the recording is *abandoned*. Whatever ``record_until``
    eventually returns lands in this instance's box, which nothing reads
    anymore -- it can never be popped by a later recording's finish and
    typed into whatever field happens to be focused by then. The stop event
    is per-instance for the same reason: the next recording must not
    ``clear()`` a stalled thread's stop signal and revive its capture loop.
    """

    def __init__(self, source: AudioSource, frame_ms: int) -> None:
        self._box: dict[str, bytes] = {}
        self.stop = threading.Event()

        def _record() -> None:
            self._box["pcm"] = source.record_until(self.stop, frame_ms)

        self.thread = threading.Thread(target=_record, daemon=True)
        self.thread.start()

    def finish(self) -> bytes | None:
        """Stop the recorder and return its PCM (``b""`` when nothing was
        captured). ``None`` means the thread is still alive after
        ``_RECORDER_JOIN_TIMEOUT_S``: the buffer is abandoned, and the
        caller must keep treating the microphone as busy until
        :attr:`thread` actually dies.
        """
        self.stop.set()
        self.thread.join(timeout=_RECORDER_JOIN_TIMEOUT_S)
        if self.thread.is_alive():
            return None
        return self._box.pop("pcm", b"")


def _run_loop(
    config: Config,
    mode: str,
    reporter: StatusReporter,
    stop_event: threading.Event | None = None,
    dependencies: RunDependencies | None = None,
) -> int:
    from local_flow.audio.vad import segment_stream

    try:
        deps = dependencies if dependencies is not None else _build_run_dependencies(config)
    except LocalFlowError as exc:
        return _fail(exc)
    pipeline, source, vad, pending_store, normalize_audio, max_utterance_min, scratchpad_sink = (
        deps
    )
    # Toggled by the scratchpad dictate-to-pad hotkey (push-to-talk mode
    # only, wired below alongside transform_hotkey/command_hotkey); a boxed
    # list (not a bare bool) so the hotkey's callback closure can flip it in
    # place. Read at both `_handle_utterance` call sites (hands-free and
    # push-to-talk) so this holder -- not a parameter threaded through every
    # call -- is the single source of truth for "is the pad active right
    # now". Always `[False]` when the hotkey is unset (the default), so
    # `sink_override` is always `None` below and behavior is unchanged.
    pad_active = [False]

    try:
        if mode == "hands-free":
            print("hands-free dictation: speak; pause to insert. Ctrl+C to quit.")
            reporter.notify("recording")
            # "sentence" streaming shortens the pause threshold that closes an
            # utterance so each sentence inserts while the next is still being
            # spoken; anything else (including "off") keeps today's
            # `vad_silence_ms` behavior byte-identical.
            silence_ms = (
                config.streaming_pause_ms
                if config.streaming == "sentence"
                else config.vad_silence_ms
            )
            # `_interruptible` wraps the raw mic frames first (closest to the
            # source), and `_with_preview` -- when live-preview is on -- wraps
            # *that*, not the other way around. This positions `_interruptible`
            # closest to the source: once `stop_event` fires, it stops yielding
            # frames, which ends `_with_preview`'s `for frame in frames` loop too,
            # so Stop cuts the live preview within one frame exactly like it does
            # the segmenter.
            frame_source: Iterable[bytes] = _interruptible(
                source.frames(config.vad_frame_ms), stop_event
            )
            preview_stream: TranscriberStream | None = None
            if config.streaming == "live-preview":
                preview_stream = _build_preview_stream(pipeline.transcriber, config.sample_rate)
                frame_source = _with_preview(frame_source, preview_stream, reporter)
            for i, segment in enumerate(
                segment_stream(
                    frame_source,
                    vad,
                    config.sample_rate,
                    frame_ms=config.vad_frame_ms,
                    silence_ms=silence_ms,
                )
            ):
                if stop_event is not None and stop_event.is_set():
                    break
                if i > 0:
                    # Re-arm "recording" for each subsequent utterance so a
                    # tray icon (or any other reporter) reflects that
                    # hands-free mode is listening again; ConsoleReporter is
                    # silent for "recording", so CLI output is unaffected.
                    reporter.notify("recording")
                _handle_utterance(
                    pipeline,
                    reporter,
                    segment,
                    config.sample_rate,
                    pending_store,
                    normalize_audio,
                    max_utterance_min,
                    scratchpad_sink if pad_active[0] else None,
                )
                if preview_stream is not None:
                    # `segment_stream` only reveals an utterance boundary by
                    # yielding it, and by then trailing silence frames (used
                    # to *detect* that boundary) -- and possibly a few frames
                    # of the next utterance -- have already been fed into
                    # `preview_stream`. Resetting here is a best-effort
                    # boundary, not an exact one; acceptable because preview
                    # is display-only and never affects the transcribed or
                    # inserted text.
                    preview_stream.reset()
        else:
            if config.streaming != "off":
                # Streaming (sentence-chunked insertion / live preview) only
                # applies to hands-free mode; push-to-talk behaves exactly
                # like `streaming=off` otherwise.
                print("streaming requires hands-free mode; ignoring")

            from local_flow.hotkeys.base import (
                CallbackDispatcher,
                PynputPushToTalk,
                TapListener,
                create_hotkey_listener,
                create_mouse_listener,
            )

            mouse_listener = create_mouse_listener(config)
            # Set while a recording (started by either the keyboard listener
            # below or the mouse listener, if any) is in flight. Threaded
            # into `create_hotkey_listener` as `cancel_gate` so the keyboard
            # cancel key can discard a mouse-started recording even though
            # no key of the keyboard listener's own is held; also used by
            # `cancel()` below to no-op on an idle cancel-key press instead
            # of printing a spurious "dictation discarded".
            recording_active = threading.Event()
            listener = create_hotkey_listener(config, cancel_gate=recording_active.is_set)
            hint = "hold Space (a quick tap still types a space)" if (
                config.hotkey.lower() == "space"
            ) else f"hold {config.hotkey!r}"
            print(
                f"push-to-talk: {hint} to dictate; "
                f"press {config.cancel_hotkey!r} to discard. Ctrl+C to quit."
            )
            if mouse_listener is not None:
                if config.mouse_button:
                    # Mouse push-to-talk runs alongside (not instead of) the
                    # keyboard listener -- using both at once is the user's
                    # own foot-gun (see README "Mouse push-to-talk"). It has
                    # no cancel gesture of its own: `esc` (or
                    # config.cancel_hotkey) via the keyboard listener above
                    # still discards a mouse-started recording.
                    print(
                        f"mouse push-to-talk also active: {config.mouse_mode} "
                        f"{config.mouse_button!r}"
                    )
                else:
                    # Enter-only config (`mouse_button` unset, only
                    # `mouse_enter_button` set): no mouse push-to-talk at all.
                    print(
                        f"mouse enter-key button active: "
                        f"{config.mouse_enter_button!r} (no mouse push-to-talk)"
                    )
            recorder: dict[str, _Recording | None] = {"current": None}
            # Which `_Recording` currently owns the microphone -- shared with
            # the command-hotkey recorder below (if configured): both
            # eventually call `source.record_until` on the very same
            # `SounddeviceSource`, and opening two concurrent PortAudio input
            # streams on one source is device contention, not a clean second
            # recording (see README "Voice command mode"). The mic counts as
            # busy while the owner's *thread* is alive, which also covers a
            # stalled recorder abandoned by `finish()`/`cancel()` (see
            # `_Recording.finish`): it keeps the mic busy until its thread
            # actually exits, then the next `start()` reclaims it. A plain
            # list box -- no lock -- is safe here because every callback that
            # reads or writes it (this hotkey's start/finish/cancel AND the
            # command hotkey's start/finish) is wrapped by the SAME
            # `dispatcher` below and therefore runs one at a time on its
            # single worker thread: there is never a moment where two of
            # these closures execute concurrently.
            mic_owner: list[_Recording | None] = [None]

            def _mic_busy() -> bool:
                owner = mic_owner[0]
                return owner is not None and owner.thread.is_alive()

            def start() -> None:
                if _mic_busy():
                    reporter.notify(
                        "warning", "microphone busy; finish the other recording first"
                    )
                    return
                recording_active.set()
                reporter.notify("recording")
                rec = _Recording(source, config.vad_frame_ms)
                recorder["current"] = rec
                mic_owner[0] = rec

            def finish() -> None:
                if not recording_active.is_set():
                    # `start()` refused above (mic busy) -- the key's own
                    # `PushToTalkCore.held` still flips true/false on
                    # physical press/release regardless, so `finish()` still
                    # runs. Nothing was actually started, and this callback
                    # never claimed the mic, so there is nothing to stop and
                    # nothing to release (releasing it here could steal it
                    # out from under whoever else does own it).
                    return
                recording_active.clear()
                rec = recorder["current"]
                recorder["current"] = None
                if rec is None:
                    return
                pcm = rec.finish()
                if pcm is None:
                    # The recorder thread outlived the join timeout (a
                    # PortAudio stall: mic-permission prompt, Bluetooth
                    # dropout). Its buffer is abandoned -- never typed into
                    # whatever field is focused once it finally returns --
                    # and `mic_owner` still holds it, so the mic reads busy
                    # until the thread actually exits (see `_mic_busy`).
                    reporter.notify(
                        "warning",
                        "microphone did not stop in time; recording discarded",
                    )
                    reporter.notify("idle")
                    return
                mic_owner[0] = None
                if pcm:
                    # Resolved here, not inside the processing task:
                    # `finish()` runs on the same dispatcher as
                    # `_toggle_pad`, so `pad_active` is read exactly when
                    # finish() executes (see `_toggle_pad`'s docstring).
                    sink_override = scratchpad_sink if pad_active[0] else None

                    def _process(pcm: bytes = pcm) -> None:
                        _handle_utterance(
                            pipeline,
                            reporter,
                            pcm,
                            config.sample_rate,
                            pending_store,
                            normalize_audio,
                            max_utterance_min,
                            sink_override,
                        )

                    processor.submit(_process)

            def cancel() -> None:
                if not recording_active.is_set():
                    # Nothing is actually recording: either an idle cancel-
                    # key press (now that the gate lets cancel reach here
                    # even without a held key of the keyboard listener's
                    # own), a race where `finish()` already completed the
                    # recording between the gate check and this callback
                    # running, or `start()` refused (mic busy). Either way,
                    # there is nothing to discard and (per `finish()` above)
                    # no mic claim of this callback's own to release.
                    return
                recording_active.clear()
                rec = recorder["current"]
                recorder["current"] = None
                if rec is not None:
                    if rec.finish() is None:
                        # Same abandonment as finish(); the cancelled audio
                        # was headed for the bin anyway, but the stalled
                        # thread keeps the mic marked busy until it exits.
                        reporter.notify(
                            "warning", "microphone did not stop in time"
                        )
                    else:
                        mic_owner[0] = None
                print("dictation discarded")
                # Silent on the console (ConsoleReporter has no output for
                # "idle"); makes a tray reporter go back to its idle icon.
                reporter.notify("idle")

            dispatcher = CallbackDispatcher()
            # The second lane (Group C item 10): everything slow -- ASR +
            # LLM + clipboard/typing insertion, i.e. dictation processing,
            # voice-command handling, transform taps, the mouse enter-key
            # press -- runs here, serialized FIFO among itself, while
            # `dispatcher` above stays free to run the *next* start()/
            # cancel() immediately. Without this split, a quick second
            # dictation's start() queued behind the previous utterance's
            # multi-second finish() and its first words were silently lost.
            # Keeping ALL slow work on one lane preserves the old mutual
            # exclusion: no two tasks ever touch the LLM client, clipboard,
            # or sink concurrently.
            processor = CallbackDispatcher()
            wrapped_start = dispatcher.wrap(start)
            wrapped_finish = dispatcher.wrap(finish)
            wrapped_cancel = dispatcher.wrap(cancel)

            if mouse_listener is not None:
                if config.mouse_enter_button:

                    def _press_enter() -> None:
                        try:
                            pipeline.sink.press_key("enter")
                        except LocalFlowError as exc:
                            print(f"error: {exc.message}", file=sys.stderr)
                            if exc.hint:
                                print(f"hint : {exc.hint}", file=sys.stderr)

                    # On the processor lane: it touches the sink, so it must
                    # never interleave with an in-flight insertion.
                    mouse_listener.on_enter = processor.wrap(_press_enter)
                # Daemon thread: started before the blocking keyboard
                # `listener.run()` below, sharing the very same
                # dispatcher-wrapped start/finish callbacks so a click and a
                # keypress both drive the same recording state machine. A
                # startup failure (e.g. missing permission) is caught and
                # printed by `_run_mouse_listener` instead of vanishing
                # silently, as an uncaught daemon-thread exception would.
                threading.Thread(
                    target=_run_mouse_listener,
                    args=(mouse_listener, wrapped_start, wrapped_finish),
                    daemon=True,
                ).start()

            if config.transform_hotkey:
                # Resolved once, at startup, rather than on every tap: an
                # unknown `transform_default` name disables the whole
                # feature with one warning instead of warning (or silently
                # no-op'ing) on every keypress. `store.transforms()` is the
                # very same PersonalizationStore the rest of the pipeline
                # uses (`pipeline.store`), so a user edit to transforms.json
                # is picked up the next time `local-flow run` starts.
                transform_prompt = pipeline.store.transforms().get(config.transform_default)
                if transform_prompt is None:
                    reporter.notify(
                        "warning",
                        f"unknown transform_default {config.transform_default!r}; "
                        "transform hotkey disabled",
                    )
                else:
                    from local_flow.transforms.registry import apply_transform

                    # Monotonic timestamp of the last tap's completion; 0.0
                    # sentinel ("never yet") so the very first tap always
                    # runs. Boxed in a list (not a bare float) so the closure
                    # below can rebind it -- see `_transform_tap_debounced`
                    # for the full rationale (dispatcher serialization means
                    # a plain busy flag can't catch a queued duplicate tap).
                    last_transform_at = [0.0]

                    def _transform_tap(prompt: str = transform_prompt) -> None:
                        now = time.monotonic()
                        if _transform_tap_debounced(last_transform_at[0], now):
                            reporter.notify(
                                "warning",
                                "transform already running; ignoring repeated tap",
                            )
                            return
                        capture = _build_selection_capture(config)
                        try:
                            # capture() runs inside this try (not before),
                            # same precision fix as `_cmd_transform`'s
                            # residual review note: a mid-capture failure
                            # must still restore the clipboard.
                            selected = capture.capture()
                            if not selected:
                                reporter.notify("warning", "no text selected")
                                capture.restore()
                                return
                            result = apply_transform(
                                pipeline.polisher.chat_client, prompt, selected
                            )
                            if not result.strip():
                                # Never paste an empty completion over the
                                # selection (unrecoverable; restore() only
                                # rewrites the clipboard).
                                capture.restore()
                                reporter.notify(
                                    "warning",
                                    "transform returned no text; selection left unchanged",
                                )
                                return
                            capture.replace(result)
                        except Exception as exc:
                            capture.restore()
                            message = getattr(exc, "message", str(exc))
                            reporter.notify("warning", f"transform failed: {message}")
                        finally:
                            last_transform_at[0] = time.monotonic()

                    transform_listener = _build_secondary_listener(
                        lambda: TapListener(config.transform_hotkey),
                        "transform_hotkey",
                        config.transform_hotkey,
                        "transform hotkey",
                        reporter,
                    )
                    if transform_listener is not None:
                        # On the processor lane (LLM + clipboard), so a tap
                        # can never run concurrently with an in-flight
                        # insertion -- and so it doesn't block
                        # start()/finish() while the (slow) transform runs.
                        threading.Thread(
                            target=_run_transform_listener,
                            args=(transform_listener, processor.wrap(_transform_tap)),
                            daemon=True,
                        ).start()

            if config.scratchpad_hotkey:
                # `scratchpad_sink` is always built by `_build_run_dependencies`
                # (see its docstring), so this only fires for a hand-built
                # `RunDependencies` (as in some tests) that sets the hotkey
                # without also providing a sink -- same "warn and disable
                # just this one feature" precedent as an unknown
                # `transform_default` above, rather than silently no-op'ing
                # every tap.
                if scratchpad_sink is None:
                    reporter.notify(
                        "warning",
                        "scratchpad_hotkey is set but no scratchpad sink was "
                        "built; scratchpad hotkey disabled",
                    )
                else:

                    def _toggle_pad(sink=scratchpad_sink) -> None:
                        """Flip `pad_active`. Wrapped by the very same
                        `dispatcher` as `start`/`finish` (and enqueued the
                        same way -- see `CallbackDispatcher.wrap`), so this
                        is serialized against them: the toggle applies to
                        any utterance whose `finish()` runs after the tap,
                        deterministically, even one already mid-recording
                        (started by an earlier `start()`) when the tap
                        lands, since `finish()` only reads `pad_active[0]`
                        at the moment IT executes.
                        """
                        pad_active[0] = not pad_active[0]
                        if pad_active[0]:
                            note = sink.store.active_note()
                            reporter.notify(
                                "warning", f"scratchpad on: dictating to {note!r}"
                            )
                        else:
                            reporter.notify(
                                "warning", "scratchpad off: dictating normally"
                            )

                    pad_listener = _build_secondary_listener(
                        lambda: TapListener(config.scratchpad_hotkey),
                        "scratchpad_hotkey",
                        config.scratchpad_hotkey,
                        "scratchpad hotkey",
                        reporter,
                    )
                    if pad_listener is not None:
                        threading.Thread(
                            target=_run_scratchpad_listener,
                            args=(pad_listener, dispatcher.wrap(_toggle_pad)),
                            daemon=True,
                        ).start()

            if config.command_hotkey:
                # A second, independent push-to-talk recorder: its own
                # `_Recording` holder, deliberately NOT shared with the main
                # hotkey's `recorder` above. It DOES share `mic_owner` with
                # the main hotkey, though: both ultimately call
                # `source.record_until` on the same `SounddeviceSource`, and
                # a concurrent second open is PortAudio device contention,
                # not a clean second recording -- so holding both hotkeys at
                # once has the second one refused (with a warning) rather
                # than corrupting either recording. See `mic_owner`'s
                # definition above for why a plain box (no lock) is safe.
                cmd_recorder: dict[str, _Recording | None] = {"current": None}
                cmd_recording_active = threading.Event()

                def cmd_start() -> None:
                    if _mic_busy():
                        reporter.notify(
                            "warning", "microphone busy; finish the other recording first"
                        )
                        return
                    cmd_recording_active.set()
                    reporter.notify("recording")
                    rec = _Recording(source, config.vad_frame_ms)
                    cmd_recorder["current"] = rec
                    mic_owner[0] = rec

                def cmd_finish() -> None:
                    if not cmd_recording_active.is_set():
                        # `cmd_start()` refused above (mic busy): this
                        # listener's own `PushToTalkCore.held` still flips on
                        # physical press/release regardless, so `finish()`
                        # still runs. Nothing was actually started here, and
                        # this callback never claimed the mic, so there is
                        # nothing to stop and nothing to release.
                        return
                    cmd_recording_active.clear()
                    rec = cmd_recorder["current"]
                    cmd_recorder["current"] = None
                    if rec is None:
                        return
                    pcm = rec.finish()
                    if pcm is None:
                        # Same stalled-recorder abandonment as the main
                        # hotkey's finish() above.
                        reporter.notify(
                            "warning",
                            "microphone did not stop in time; recording discarded",
                        )
                        reporter.notify("idle")
                        return
                    mic_owner[0] = None

                    def _process(pcm: bytes = pcm) -> None:
                        if pcm:
                            reporter.notify("processing")
                            # Captured (not just built) first thing in this
                            # task, before the -- slower -- transcription
                            # step inside `_run_voice_command`, so it
                            # reflects the selection as close to the key
                            # release as the processor lane allows.
                            capture = _build_selection_capture(config)
                            _run_voice_command(
                                deps, capture, pcm, config.sample_rate, reporter
                            )
                        reporter.notify("idle")

                    processor.submit(_process)

                # No cancel key: this listener has no cancel gesture of its
                # own (mirrors mouse push-to-talk -- see README).
                command_listener = _build_secondary_listener(
                    lambda: PynputPushToTalk(config.command_hotkey, cancel_key=""),
                    "command_hotkey",
                    config.command_hotkey,
                    "voice command hotkey",
                    reporter,
                )
                if command_listener is not None:
                    threading.Thread(
                        target=_run_command_hotkey_listener,
                        args=(
                            command_listener,
                            dispatcher.wrap(cmd_start),
                            dispatcher.wrap(cmd_finish),
                        ),
                        daemon=True,
                    ).start()

            listener.run(wrapped_start, wrapped_finish, wrapped_cancel)
    except KeyboardInterrupt:
        print("\nbye")
        return 0
    except LocalFlowError as exc:
        return _fail(exc)
    return 0


def _cmd_run(args: argparse.Namespace, config: Config) -> int:
    mode = args.mode or config.mode
    return _run_loop(config, mode, ConsoleReporter())


def _cmd_recover(_args: argparse.Namespace, config: Config) -> int:
    """Reprocess every WAV left behind under ``<data dir>/pending/``.

    Covers a crash/force-quit mid-dictation (see ``PendingAudioStore`` and
    config ``audio_recovery``): each file is run through the same pipeline
    ``local-flow run`` uses, deleted on success, and left in place on
    failure so a later ``recover`` can try again.
    """
    from local_flow.audio.recovery import PendingAudioStore

    store = PendingAudioStore(config.data_dir)
    pending = store.pending()
    if not pending:
        print(f"no pending dictation audio to recover (checked: {store.pending_dir})")
        return 0

    try:
        # File-only: recover reads saved WAVs, so it needs ASR + insertion but
        # NOT a live audio source -- don't enumerate a mic that may be absent,
        # unplugged, or permission-denied (e.g. recovering on another machine).
        pipeline = _build_text_pipeline(config)
    except LocalFlowError as exc:
        return _fail(exc)

    recovered = 0
    kept = 0
    for path in pending:
        try:
            pcm, sample_rate = store.load(path)
        except ValueError as exc:
            print(f"skip {path.name}: {exc}")
            kept += 1
            continue
        try:
            result = pipeline.process_audio(pcm, sample_rate)
        except LocalFlowError as exc:
            print(f"failed {path.name}: {exc.message}")
            kept += 1
            continue
        store.delete(path)
        recovered += 1
        print(f"recovered {path.name}: {result.final!r}")

    print(
        f"recover: {recovered} recovered, {kept} left in {store.pending_dir} "
        f"(of {len(pending)} total)"
    )
    return 0


def _cmd_tray(_args: argparse.Namespace, config: Config) -> int:
    from local_flow.tray.app import TrayApp

    try:
        app = TrayApp(config)
    except LocalFlowError as exc:
        return _fail(exc)
    app.run()
    return 0


def _cmd_setup(_args: argparse.Namespace, config: Config) -> int:
    from local_flow.setup_wizard import run_wizard

    try:
        run_wizard(config)
    except LocalFlowError as exc:
        return _fail(exc)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="local-flow",
        description="Local-first dictation: local ASR + VAD, LM Studio polish, "
        "clipboard/paste insertion. All processing stays on your machine.",
    )
    parser.add_argument("--version", action="version", version=f"local-flow {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("demo", help="headless pipeline demo with mocks (no permissions needed)")

    run_p = sub.add_parser("run", help="live dictation (mic + hotkey/VAD + LM Studio)")
    run_p.add_argument(
        "--mode",
        choices=["push-to-talk", "hands-free"],
        help="override the configured capture mode",
    )

    sub.add_parser(
        "recover",
        help="reprocess dictation audio left behind by a crash "
        "(<data dir>/pending/*.wav)",
    )

    polish_p = sub.add_parser("polish", help="clean/polish a rough transcript from the CLI")
    polish_p.add_argument("text", help="rough transcript text")
    polish_p.add_argument(
        "--no-llm",
        action="store_true",
        help="rule-based cleanup only; do not contact LM Studio",
    )

    transcribe_p = sub.add_parser(
        "transcribe", help="transcribe audio file(s) through the local ASR (+ optional polish)"
    )
    transcribe_p.add_argument(
        "files", nargs="+", metavar="FILE", help="one or more audio files to transcribe"
    )
    transcribe_p.add_argument(
        "--polish",
        action="store_true",
        help="run each transcript through the same polish pass as `local-flow polish`",
    )
    transcribe_p.add_argument(
        "--copy",
        action="store_true",
        help="copy the last file's final text to the clipboard",
    )
    transcribe_p.add_argument(
        "--language",
        metavar="XX",
        help="override the configured ASR language for this run (e.g. 'fr', 'auto')",
    )

    command_p = sub.add_parser("command", help="transform text with an instruction (command mode)")
    command_p.add_argument("instruction", help="what to do, e.g. 'make this more formal'")
    command_p.add_argument("--text", required=True, help="the target text to transform")
    command_p.add_argument(
        "--mock",
        action="store_true",
        help="use a mock LLM (echoes input) instead of LM Studio",
    )

    transform_p = sub.add_parser(
        "transform", help="apply a named AI rewrite to --text or the current OS selection"
    )
    transform_p.add_argument(
        "name", nargs="?", help="transform name, e.g. Polish (see --list)"
    )
    transform_p.add_argument(
        "--text", help="transform this text and print the result (headless)"
    )
    transform_p.add_argument(
        "--selection",
        action="store_true",
        help="capture the current OS selection, transform it, and replace it in place",
    )
    transform_p.add_argument(
        "--list", action="store_true", help="list available transform names and exit"
    )

    sub.add_parser("check", help="diagnose LM Studio / ASR / audio / clipboard setup")

    sub.add_parser(
        "tray",
        help="menu-bar tray app with live states and style/language quick-switch "
        "(requires: uv sync --extra tray)",
    )

    sub.add_parser(
        "setup", help="interactive onboarding wizard that writes a validated config"
    )

    history_p = sub.add_parser("history", help="list/search/clear the local dictation history")
    history_p.add_argument(
        "--search", help="only show records containing this text (case-insensitive)"
    )
    history_p.add_argument(
        "--limit", type=int, default=20, help="maximum number of records to show (default: 20)"
    )
    history_p.add_argument(
        "--clear", action="store_true", help="delete the local history file"
    )
    history_p.add_argument(
        "--verbose", action="store_true", help="also show the rough (pre-polish) transcript"
    )
    history_p.add_argument(
        "--show",
        type=int,
        metavar="N",
        help="print record N's full rough and final text (1-based, newest-first "
        "as in the plain listing; ignores --search/--limit)",
    )
    history_p.add_argument(
        "--reinsert-raw",
        type=int,
        metavar="N",
        help="undo a bad AI edit: re-insert record N's rough (pre-polish) transcript "
        "verbatim through the configured text sink (1-based, newest-first as in "
        "the plain listing; ignores --search/--limit)",
    )
    history_p.add_argument(
        "--retry",
        type=int,
        metavar="N",
        help="re-run record N's rough transcript through a freshly built pipeline "
        "(fresh polish + insert); appends a NEW history record rather than "
        "replacing the old one (1-based, newest-first as in the plain listing; "
        "ignores --search/--limit)",
    )

    pad_p = sub.add_parser(
        "pad",
        help="markdown scratchpad notes: list/show/append/switch/create/window",
    )
    pad_group = pad_p.add_mutually_exclusive_group()
    pad_group.add_argument(
        "--list", action="store_true", help="list note names (active one marked)"
    )
    pad_group.add_argument(
        "--show",
        nargs="?",
        const="",
        default=None,
        metavar="NAME",
        help="print a note's content (active note by default)",
    )
    pad_group.add_argument(
        "--append",
        metavar="TEXT",
        help="append TEXT to a note (active note by default; see --note)",
    )
    pad_group.add_argument(
        "--use", metavar="NAME", help="set the active note (creating it if missing)"
    )
    pad_group.add_argument(
        "--new", metavar="NAME", help="create an empty note (no-op if it already exists)"
    )
    pad_group.add_argument(
        "--window",
        action="store_true",
        help="open the floating always-on-top scratchpad window (blocks; "
        "requires a Tk-enabled Python)",
    )
    pad_p.add_argument(
        "--note", metavar="NAME", help="target note for --append (default: active note)"
    )
    pad_p.add_argument(
        "--with-dictation",
        action="store_true",
        help="alongside --window, also run the dictation loop on a worker "
        "thread for the window's lifetime",
    )

    learn_p = sub.add_parser(
        "learn", help="mine dictation history for candidate dictionary terms"
    )
    learn_p.add_argument(
        "--min-count",
        type=int,
        default=3,
        help="minimum occurrences before a term is suggested (default: 3)",
    )
    learn_p.add_argument(
        "--limit", type=int, default=20, help="maximum number of suggestions to show (default: 20)"
    )
    learn_p.add_argument(
        "--add",
        type=int,
        nargs="+",
        metavar="N",
        help="add suggestion number(s) N (as shown in the listing) to the dictionary",
    )
    learn_p.add_argument(
        "--add-all", action="store_true", help="add every suggestion shown to the dictionary"
    )

    stats_p = sub.add_parser(
        "stats", help="local-only personal insights: words, streaks, top apps"
    )
    stats_p.add_argument(
        "--since",
        default="30d",
        metavar="Nd|all",
        help="time window: `Nd` (e.g. `7d`, `30d` -- the default) or `all`",
    )

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    try:
        config = load_config()
    except LocalFlowError as exc:
        return _fail(exc)

    handlers = {
        "demo": _cmd_demo,
        "run": _cmd_run,
        "recover": _cmd_recover,
        "polish": _cmd_polish,
        "transcribe": _cmd_transcribe,
        "command": _cmd_command,
        "transform": _cmd_transform,
        "check": _cmd_check,
        "history": _cmd_history,
        "pad": _cmd_pad,
        "learn": _cmd_learn,
        "stats": _cmd_stats,
        "tray": _cmd_tray,
        "setup": _cmd_setup,
    }
    try:
        return handlers[args.command](args, config)
    except LocalFlowError as exc:
        return _fail(exc)


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
