"""local-flow command-line entry point.

Subcommands:
- ``demo``    headless pipeline demo with mocks (no permissions needed)
- ``run``     live dictation (microphone + hotkey/VAD + LM Studio)
- ``polish``  one-shot: clean/polish a rough transcript from the CLI
- ``command`` one-shot command mode: transform text per an instruction
- ``check``   diagnose the environment (LM Studio, ASR, audio, clipboard)
- ``history`` list/search/clear the local dictation history
- ``learn``   mine history for candidate dictionary terms, optionally add them
- ``tray``    menu-bar app with live states + style/language quick-switch
- ``setup``   interactive onboarding wizard that writes a validated config
"""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import TYPE_CHECKING

from local_flow import __version__
from local_flow.asr.streaming import TranscriberStream
from local_flow.config import Config, load_config
from local_flow.errors import ConfigError, LocalFlowError
from local_flow.personalization.store import PersonalizationStore
from local_flow.status import ConsoleReporter, StatusReporter

if TYPE_CHECKING:
    from local_flow.asr.base import Transcriber
    from local_flow.pipeline import DictationPipeline


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


def _build_vad(config: Config):
    from local_flow.audio.vad import EnergyVAD, MockVAD, WebRtcVAD

    if config.vad_backend == "webrtc":
        return WebRtcVAD(config.vad_aggressiveness)
    if config.vad_backend == "mock":
        return MockVAD([])
    return EnergyVAD(config.vad_energy_threshold)


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


def _build_pipeline(config: Config, chat_client, sink):
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
    return DictationPipeline(
        transcriber=_build_transcriber(config),
        polisher=polisher,
        store=store,
        sink=sink,
        command_mode=command_mode,
        history=history,
        router=_build_router(config, store),
    )


def _cmd_demo(_args: argparse.Namespace, _config: Config) -> int:
    from local_flow.demo import run_demo

    return run_demo()


def _cmd_polish(args: argparse.Namespace, config: Config) -> int:
    from local_flow.polish.polisher import TranscriptPolisher
    from local_flow.polish.rules import (
        apply_dictation_commands,
        enforce_dictionary,
        expand_snippets,
    )

    store = PersonalizationStore(config.data_dir)
    chat_client = None if args.no_llm else _build_chat_client(config)
    polisher = TranscriptPolisher(
        chat_client, store, style=config.style, level=config.cleanup_level
    )
    result = polisher.polish(args.text)
    text, _dict_count = enforce_dictionary(result.polished, store.dictionary_terms())
    text, _snippet_count = expand_snippets(text, store.snippets())
    text, actions = apply_dictation_commands(text)
    for warning in result.warnings:
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


def _handle_utterance(
    pipeline: DictationPipeline,
    reporter: StatusReporter,
    pcm: bytes,
    sample_rate: int,
) -> None:
    """Run one captured utterance's PCM through the pipeline.

    Reports ``processing`` before the pipeline call, ``warning`` per pipeline
    warning, ``inserted`` (with the final text's ``repr``) on success,
    ``error`` if the pipeline raises, and ``idle`` once the utterance is
    fully handled either way. Extracted from ``_run_loop`` so it can be
    exercised directly in tests without mocking audio capture or hotkeys.
    """
    reporter.notify("processing")
    try:
        result = pipeline.process_audio(pcm, sample_rate)
        for warning in result.warnings:
            reporter.notify("warning", warning)
        if result.final:
            reporter.notify("inserted", repr(result.final))
    except LocalFlowError as exc:
        reporter.notify("error", exc.message)
        if exc.hint:
            print(f"hint : {exc.hint}", file=sys.stderr)
    finally:
        reporter.notify("idle")


def _build_run_dependencies(config: Config):
    """Build the pipeline + audio source + VAD that ``_run_loop`` needs.

    Extracted from ``_run_loop`` so ``TrayApp`` can build these once, keep a
    reference to ``pipeline.polisher``/``pipeline.transcriber`` for its
    Style/Language menus, and hand the very same objects to ``_run_loop``
    running on its worker thread (rather than each rebuilding its own
    pipeline). ``_cmd_run`` still goes through ``_run_loop`` with
    ``dependencies=None``, so this call sequence (and its exception
    behavior) is unchanged for the CLI path.
    """
    from local_flow.audio.capture import SounddeviceSource

    chat_client = _build_chat_client(config)
    sink = _build_sink(config)
    pipeline = _build_pipeline(config, chat_client, sink)
    source = SounddeviceSource(sample_rate=config.sample_rate)
    vad = _build_vad(config)
    return pipeline, source, vad


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


def _run_loop(
    config: Config,
    mode: str,
    reporter: StatusReporter,
    stop_event: threading.Event | None = None,
    dependencies: tuple[DictationPipeline, object, object] | None = None,
) -> int:
    from local_flow.audio.vad import segment_stream

    try:
        pipeline, source, vad = (
            dependencies if dependencies is not None else _build_run_dependencies(config)
        )
    except LocalFlowError as exc:
        return _fail(exc)

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
                _handle_utterance(pipeline, reporter, segment, config.sample_rate)
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

            from local_flow.hotkeys.base import CallbackDispatcher, create_hotkey_listener

            listener = create_hotkey_listener(config)
            hint = "hold Space (a quick tap still types a space)" if (
                config.hotkey.lower() == "space"
            ) else f"hold {config.hotkey!r}"
            print(
                f"push-to-talk: {hint} to dictate; "
                f"press {config.cancel_hotkey!r} to discard. Ctrl+C to quit."
            )
            stop = threading.Event()
            recorder: dict[str, threading.Thread | None] = {"thread": None}
            captured: dict[str, bytes] = {}

            def start() -> None:
                stop.clear()
                reporter.notify("recording")

                def record() -> None:
                    captured["pcm"] = source.record_until(stop, config.vad_frame_ms)

                recorder["thread"] = threading.Thread(target=record, daemon=True)
                recorder["thread"].start()

            def finish() -> None:
                stop.set()
                thread = recorder["thread"]
                if thread is not None:
                    thread.join(timeout=5)
                pcm = captured.pop("pcm", b"")
                if pcm:
                    _handle_utterance(pipeline, reporter, pcm, config.sample_rate)

            def cancel() -> None:
                stop.set()
                thread = recorder["thread"]
                if thread is not None:
                    thread.join(timeout=5)
                captured.pop("pcm", None)
                print("dictation discarded")
                # Silent on the console (ConsoleReporter has no output for
                # "idle"); makes a tray reporter go back to its idle icon.
                reporter.notify("idle")

            dispatcher = CallbackDispatcher()
            listener.run(
                dispatcher.wrap(start), dispatcher.wrap(finish), dispatcher.wrap(cancel)
            )
    except KeyboardInterrupt:
        print("\nbye")
        return 0
    except LocalFlowError as exc:
        return _fail(exc)
    return 0


def _cmd_run(args: argparse.Namespace, config: Config) -> int:
    mode = args.mode or config.mode
    return _run_loop(config, mode, ConsoleReporter())


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

    polish_p = sub.add_parser("polish", help="clean/polish a rough transcript from the CLI")
    polish_p.add_argument("text", help="rough transcript text")
    polish_p.add_argument(
        "--no-llm",
        action="store_true",
        help="rule-based cleanup only; do not contact LM Studio",
    )

    command_p = sub.add_parser("command", help="transform text with an instruction (command mode)")
    command_p.add_argument("instruction", help="what to do, e.g. 'make this more formal'")
    command_p.add_argument("--text", required=True, help="the target text to transform")
    command_p.add_argument(
        "--mock",
        action="store_true",
        help="use a mock LLM (echoes input) instead of LM Studio",
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
        "polish": _cmd_polish,
        "command": _cmd_command,
        "check": _cmd_check,
        "history": _cmd_history,
        "learn": _cmd_learn,
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
