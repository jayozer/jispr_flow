# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

local-flow: local-first desktop dictation (package `local_flow`, distribution name `local-flow`). Local ASR (faster-whisper) + VAD + LM Studio polish + clipboard/typing insertion. Python 3.11+, managed with uv, built with hatchling.

**The primary deliverable is the native macOS app (JiSpr)** — the SwiftUI menu-bar app under `macos/JiSpr`, built with full Xcode + `xcodegen`. The `local_flow` Python package described here is the dictation engine it drives, not the finished product. On a fresh machine: confirm you're on the latest `main` (the presence of `macos/JiSpr/` is the check — there are no tagged releases, so latest = tip of `origin/main`), then run `./script/bootstrap.sh` to build and launch the app. **Do not stop after `uv sync` / `uv run local-flow run`** — that gives you the engine only, not the app. See the README's "Native macOS app (JiSpr)" section for LM Studio, permissions, and model selection.

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

Composition happens in `app.py` (the `_build_*` functions wire adapters from `Config`); `pipeline.py` orchestrates a single utterance and holds no platform code. Post-MVP subsystems hang off the same seams: `context/` (frontmost-app style/sink routing + AX field-text context), `transforms/` (selection rewrite, voice command), `scratchpad/`, `history/`, `insights/` (stats), `tray/`, and `asr/streaming.py`.

Non-negotiable invariants:

- **CI is headless.** No mic, GPU, model, LM Studio server, clipboard, or display in tests. A new adapter or feature needs a mock and headless tests; `uv run pytest` and `uv run local-flow demo` must work on a bare machine.
- **Platform isolation.** OS-specific code (pynput, Quartz/pyobjc, sounddevice, pyperclip, pystray) lives in lazily-imported leaf modules gated behind the optional extras `audio`, `asr`, `desktop`, `tray` (core has only httpx). Core modules never import platform packages; a missing extra raises an actionable error, never an import crash.
- **Independent degradation.** LM Studio down → rules-only output plus a warning; paste fails → typing → clipboard-only. Error paths are tested behaviors, not afterthoughts.
- **Local-first.** Nothing leaves the machine except calls to the configured LM Studio server; the client refuses known cloud AI endpoints at construction time. LM Studio is never an ASR option.
- **Two dispatcher lanes.** Hotkey callbacks run on the fast lane; ASR+LLM+insert work is serialized FIFO on the `processor` lane — never run slow work on the hotkey lane (it drops the first words of the next dictation).
- **Atomic data-dir writes.** Anything under `LOCAL_FLOW_DATA_DIR` is written via tmp-file + `os.replace` (see `atomicio.py` / `PersonalizationStore._atomic_write`), never a bare `write_text`.

Configuration (`config.py`): frozen `Config` dataclass; precedence defaults < TOML file (`$LOCAL_FLOW_CONFIG`, `./local-flow.toml`, `~/.config/local-flow/config.toml`) < `LOCAL_FLOW_*` env vars; a `.env` file is read but never overrides real env. Any new setting must also be documented in `.env.example` and `local-flow.example.toml`. Personalization is hand-editable JSON in `LOCAL_FLOW_DATA_DIR` — dictionary, snippets, styles, `app_styles.json` (per-app style/insert routing), and `transforms.json`; history is append-only JSONL there and scratchpad notes are plain markdown under `notes/`.

## Roadmap / plans

`TODO.md` tracks the epic roadmap (E1–E15, all shipped), the code-review fix log (groups A–H, all fixed), and the post-1.0 backlog. `ROADMAP.md` is the feature roadmap, annotated with what shipped. Detailed per-epic specs and acceptance criteria live in `docs/superpowers/plans/`. The README's "Manual test checklist" section lists live-hardware checks that cannot be claimed as verified from tests — report them as manual steps for the user.
