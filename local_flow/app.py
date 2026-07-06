"""local-flow command-line entry point.

Subcommands:
- ``demo``    headless pipeline demo with mocks (no permissions needed)
- ``run``     live dictation (microphone + hotkey/VAD + LM Studio)
- ``polish``  one-shot: clean/polish a rough transcript from the CLI
- ``command`` one-shot command mode: transform text per an instruction
- ``check``   diagnose the environment (LM Studio, ASR, audio, clipboard)
"""

from __future__ import annotations

import argparse
import sys
import threading

from local_flow import __version__
from local_flow.config import Config, load_config
from local_flow.errors import LocalFlowError
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
    if config.asr_backend == "mock":
        from local_flow.asr.mock import MockTranscriber

        return MockTranscriber(["(mock transcription)"])
    from local_flow.asr.faster_whisper_asr import FasterWhisperTranscriber

    return FasterWhisperTranscriber(
        model=config.asr_model,
        device=config.asr_device,
        compute_type=config.asr_compute_type,
    )


def _build_vad(config: Config):
    from local_flow.audio.vad import EnergyVAD, MockVAD, WebRtcVAD

    if config.vad_backend == "webrtc":
        return WebRtcVAD(config.vad_aggressiveness)
    if config.vad_backend == "mock":
        return MockVAD([])
    return EnergyVAD(config.vad_energy_threshold)


def _build_sink(config: Config):
    from local_flow.insertion.base import InsertionManager
    from local_flow.insertion.desktop import (
        ClipboardOnlySink,
        ClipboardPasteSink,
        TypingSink,
    )

    sinks = {
        "paste": [ClipboardPasteSink()],
        "type": [TypingSink()],
        "clipboard": [ClipboardOnlySink()],
        "auto": [ClipboardPasteSink(), TypingSink(), ClipboardOnlySink()],
    }.get(config.insert_method)
    if sinks is None:
        raise LocalFlowError(
            f"Unknown insert method {config.insert_method!r}.",
            hint="Use one of: auto, paste, type, clipboard.",
        )
    return InsertionManager(sinks)


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
    return DictationPipeline(
        transcriber=_build_transcriber(config),
        polisher=polisher,
        store=store,
        sink=sink,
        command_mode=command_mode,
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
    text = enforce_dictionary(result.polished, store.dictionary_terms())
    text = expand_snippets(text, store.snippets())
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
                config.hotkey == "space"
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
    }
    try:
        return handlers[args.command](args, config)
    except LocalFlowError as exc:
        return _fail(exc)


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
