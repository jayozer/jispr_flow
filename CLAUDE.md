# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

local-flow: local-first desktop dictation (package `local_flow`, distribution name `local-flow`). Local ASR (faster-whisper) + VAD + LM Studio polish + clipboard/typing insertion. Python 3.11+, managed with uv, built with hatchling.

## Commands

```bash
uv sync --all-extras           # full dev setup; plain `uv sync` is enough for headless work
uv run pytest                  # all tests — fully headless (mocked ASR/VAD/LLM/hotkeys/sinks)
uv run pytest tests/test_config.py            # one file
uv run pytest -k "test_name"                  # one test
uv run ruff check .            # lint (line-length 100; rules E,F,W,I,UP,B)
uv run local-flow demo         # headless end-to-end pipeline proof with mocks, no permissions
```

CLI entry points: `uv run local-flow` or `uv run python -m local_flow`. Subcommands: `setup`, `check`, `run`, `recover`, `polish`, `transcribe`, `command`, `transform`, `demo`, `history`, `learn`, `stats`, `tray`, `pad`.

## Architecture

Full details in `docs/architecture.md` and the README's Architecture section — read those first. The short version:

The product is a pipeline of small adapters:

```
AudioSource ─► VAD ─► Transcriber ─► rule cleanup ─► LM Studio polish
                                                          │
TextSink ◄── dictation commands ◄── snippets ◄── dictionary enforcement
```

Every stage is an interface with real implementation(s) **and a mock**: `audio/capture.py` (sounddevice | mock), `audio/vad.py` (energy | webrtc | mock), `asr/` (faster-whisper | mock), `llm/` (LM Studio HTTP | mock), `insertion/` (clipboard+paste → typing → clipboard-only | fake). `polish/rules.py` is deterministic pure Python that runs before the LLM. Command mode (`commands/command_mode.py`) and named transforms reuse the same LLM client and TextSink. Hotkeys (`hotkeys/`) sit outside the pipeline — they only decide when to record.

Composition happens in `app.py` (the `_build_*` functions wire adapters from `Config`); `pipeline.py` orchestrates a single utterance and holds no platform code.

Non-negotiable invariants:

- **CI is headless.** No mic, GPU, model, LM Studio server, clipboard, or display in tests. A new adapter or feature needs a mock and headless tests; `uv run pytest` and `uv run local-flow demo` must work on a bare machine.
- **Platform isolation.** OS-specific code (pynput, Quartz/pyobjc, sounddevice, pyperclip, pystray) lives in lazily-imported leaf modules gated behind the optional extras `audio`, `asr`, `desktop`, `tray` (core has only httpx). Core modules never import platform packages; a missing extra raises an actionable error, never an import crash.
- **Independent degradation.** LM Studio down → rules-only output plus a warning; paste fails → typing → clipboard-only. Error paths are tested behaviors, not afterthoughts.
- **Local-first.** Nothing leaves the machine except calls to the configured LM Studio server; the client refuses known cloud AI endpoints at construction time. LM Studio is never an ASR option.

Configuration (`config.py`): frozen `Config` dataclass; precedence defaults < TOML file (`$LOCAL_FLOW_CONFIG`, `./local-flow.toml`, `~/.config/local-flow/config.toml`) < `LOCAL_FLOW_*` env vars; a `.env` file is read but never overrides real env. Any new setting must also be documented in `.env.example` and `local-flow.example.toml`. Personalization is three hand-editable JSON files (dictionary, snippets, styles) plus transforms in `LOCAL_FLOW_DATA_DIR`; history is append-only JSONL there.

## Roadmap / plans

`TODO.md` tracks the epic roadmap (E1–E15, all shipped; post-1.0 backlog at the end). Detailed per-epic specs and acceptance criteria live in `docs/superpowers/plans/`. The README's "Manual test checklist" section lists live-hardware checks that cannot be claimed as verified from tests — report them as manual steps for the user.
