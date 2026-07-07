"""local-flow command-line entry point.

Subcommands:
- ``demo``    headless pipeline demo with mocks (no permissions needed)
- ``run``     live dictation (microphone + hotkey/VAD + LM Studio)
- ``polish``  one-shot: clean/polish a rough transcript from the CLI
- ``command`` one-shot command mode: transform text per an instruction
- ``check``   diagnose the environment (LM Studio, ASR, audio, clipboard)
- ``history`` list/search/clear the local dictation history
"""

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime

from local_flow import __version__
from local_flow.config import Config, load_config
from local_flow.errors import ConfigError, LocalFlowError
from local_flow.personalization.store import PersonalizationStore


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
    polisher = TranscriptPolisher(chat_client, store, style=config.style)
    command_mode = (
        CommandMode(
            chat_client,
            dictionary_terms=store.dictionary_terms(),
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
    polisher = TranscriptPolisher(chat_client, store, style=config.style)
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

    from local_flow.context.frontmost import create_frontmost_provider

    info = create_frontmost_provider().current()
    frontmost_label = info.app_id or info.title or "(unknown)"
    print(f"  frontmost app : {frontmost_label}")

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


def _cmd_run(args: argparse.Namespace, config: Config) -> int:
    from local_flow.audio.capture import SounddeviceSource
    from local_flow.audio.vad import segment_stream

    try:
        chat_client = _build_chat_client(config)
        sink = _build_sink(config)
        pipeline = _build_pipeline(config, chat_client, sink)
        source = SounddeviceSource(sample_rate=config.sample_rate)
        vad = _build_vad(config)
    except LocalFlowError as exc:
        return _fail(exc)

    mode = args.mode or config.mode

    def handle(pcm: bytes) -> None:
        try:
            result = pipeline.process_audio(pcm, config.sample_rate)
            for warning in result.warnings:
                print(f"warning: {warning}", file=sys.stderr)
            if result.final:
                print(f"inserted: {result.final!r}")
        except LocalFlowError as exc:
            _fail(exc)

    try:
        if mode == "hands-free":
            print("hands-free dictation: speak; pause to insert. Ctrl+C to quit.")
            for segment in segment_stream(
                source.frames(config.vad_frame_ms),
                vad,
                config.sample_rate,
                frame_ms=config.vad_frame_ms,
                silence_ms=config.vad_silence_ms,
            ):
                handle(segment)
        else:
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
                    handle(pcm)

            def cancel() -> None:
                stop.set()
                thread = recorder["thread"]
                if thread is not None:
                    thread.join(timeout=5)
                captured.pop("pcm", None)
                print("dictation discarded")

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
    }
    try:
        return handlers[args.command](args, config)
    except LocalFlowError as exc:
        return _fail(exc)


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
