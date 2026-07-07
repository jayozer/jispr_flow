# local-flow TODO

Feature roadmap toward full single-user Wispr-Flow-Pro parity, fully local.
Detailed specs (files, interfaces, acceptance criteria) for every epic:
[docs/superpowers/plans/2026-07-06-flow-features-roadmap.md](docs/superpowers/plans/2026-07-06-flow-features-roadmap.md).

Scope: single user only. Team/enterprise features, cloud sync, mobile apps,
plan gating, and banking-app auto-pause are explicit non-goals.

## Phase 1 — Foundations

> **Goal:** E1, E2, and E3 are implemented and their TODO boxes checked: `tests/test_hotkeys.py` passes headless and covers the factory's unhappy paths (`fn` on Linux and `space` on X11 raise actionable `HotkeyBackendMissingError`s, not crashes); after two mocked dictations `uv run local-flow history` shows both entries with timestamps and `--search`, `--clear`, and the retention setting behave as specified; and `asr_language=auto` combined with an `*.en` model raises `ConfigError` with the multilingual-model hint. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0. Constraints: the `HotkeyListener.run` signature is unchanged, no core module imports a platform package, and history stays an append-only JSONL in `data_dir`.

- [x] **E1 — Hotkeys: Fn or Space push-to-talk** *(user priority; live hardware checks = README manual checklist 11–13)*
  - [x] `LOCAL_FLOW_HOTKEY=fn` (default on macOS, via Quartz event tap) or `space` (hold-vs-tap with suppression) or any pynput key
  - [x] Rebindable cancel key (default Esc) to discard an utterance mid-recording
  - [ ] Stretch (not shipped): chord hotkeys (`ctrl+space`)
- [x] **E2 — Dictation history** — local JSONL log, `local-flow history --search/--clear`, retention `forever|24h|off`, records duration + replacement counts (fuels E5/E9/E14)
- [x] **E3 — Multilingual ASR** — `asr_language=auto` detection with multilingual Whisper models, config validation

## Phase 2 — Intelligence

> **Goal:** E4 and E5 are implemented and their TODO boxes checked: a `MockChatClient` test asserts that with a Slack mapping in `app_styles.json` the polish prompt uses `casual` style rules while unmapped apps fall back to `config.style`; seeding history with a term appearing 3+ times makes `local-flow learn` suggest it, `--add 1` writes it into `dictionary.json` atomically, and the spoken command "add X to dictionary" adds it via the rules stage with LM Studio down. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraints: frontmost-app adapters are lazily imported leaf modules with a `MockFrontmostApp` for CI, and every existing `polish()` call site keeps working without changes.

- [x] **E4 — Per-app awareness** — style follows the frontmost app (Slack casual, Mail formal); per-app insert method (typing sink in Claude Code/terminals to avoid "[Pasted N lines]"); built-in email/chat formatting styles
- [x] **E5 — Auto-learning dictionary** — mines history for your recurring terms, `local-flow learn --add`, spoken "add X to dictionary"; starred terms, usage-based ranking, apostrophe dedup

## Phase 3 — UX shell

> **Goal:** E6 is implemented and its TODO box checked: the `StatusReporter` seam lands first as a pure refactor commit with `local-flow run` output unchanged, the tray icon state machine and setup wizard logic pass in `tests/test_tray_state.py` and `tests/test_setup_wizard.py` with no display, and `local-flow setup` writes a validated TOML config. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless, plus the wizard's generated TOML content. Constraints: pystray and Pillow live only in a new `tray` optional extra (never core deps), icons are Pillow-drawn with no bundled assets, and the live tray checks (icon turns red while recording, menu style switch changes the next polish prompt) are reported as manual steps for the user, not claimed as verified.

- [x] **E6 — Tray app + setup wizard** — menu-bar icon with recording states, style + language quick-switch menus, desktop notifications, `local-flow setup` onboarding *(live tray appearance = manual checklist in README)*

## Phase 4 — Latency

> **Goal:** E7 is implemented and its TODO box checked: `tests/test_streaming.py` asserts with mocks that under `LOCAL_FLOW_STREAMING=sentence` in hands-free mode a two-sentence dictation inserts the first sentence before the second finishes, and that `streaming=off` produces byte-identical output to the non-streaming pipeline. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraints: no existing pipeline test is edited to make it pass (off-mode equivalence is demonstrated, not redefined), the "scratch that only reaches back within the current chunk" limit is documented, and the README gains the before/after latency measurement note.

- [x] **E7 — Streaming insertion** — sentence-chunked insertion in hands-free mode, then live rough-text preview; `off` stays byte-identical to today

## Phase 5 — Wispr-Pro parity: quick wins

> **Goal:** E9, E11, E12, and E15 are implemented and their TODO boxes checked: `cleanup_level=none` inserts the verbatim transcript with zero chat-client calls and the spoken code-syntax conversions pass table-driven tests; a simulated crash (pending WAV saved, never deleted) followed by `local-flow recover` inserts the text and empties `data_dir/pending/`, and the whisper VAD preset detects a low-amplitude synthetic utterance that `normal` misses; fake x1-hold mouse events start and stop recording while the keyboard hotkey still works, and `mouse_button="left"` fails with the non-primary-buttons hint; `local-flow transcribe` on a generated WAV returns the mock's canned text and `--polish` is asserted via `MockChatClient`. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraint: each epic ships on its own branch — no two epics in one branch.

- [x] **E9 — Auto-cleanup levels** — `none | light | medium | high`; spoken lists → real lists; spoken code syntax (camelCase/snake_case/ALL-CAPS); "undo AI edit" from history *(needs E2)*
- [x] **E11 — Reliability & mics** — mic priority ranking *(selection-time; mid-session/open-failure fallback deferred)*, mic diagnostics in `check`, whisper-mode VAD preset, crash-safe audio autosave + `local-flow recover`, failed-polish retry, long-utterance warning
- [x] **E12 — Mouse Flow** — bind middle/x1/x2 mouse button to PTT (hold or toggle) and optionally Enter *(needs E1; x1/x2 not exposed by pynput on macOS — documented)*
- [x] **E15 — File transcription** — `local-flow transcribe memo.m4a --polish`: transcribe existing audio files (WAV/MP3/M4A/FLAC) through the same local pipeline — a feature Wispr Flow itself doesn't have

## Phase 6 — Wispr-Pro parity: power features

> **Goal:** E8 and E14 are implemented and their TODO boxes checked: with mocked selection and `MockChatClient`, selecting "hey can u fix" and pressing the transform hotkey replaces it with the transformed text and restores the prior clipboard; a pipeline test asserts `auto_transform` runs between polish and insertion and that leaving it unset changes nothing; voice command mode transcribes a spoken instruction and applies it to the selection; `local-flow stats` on seeded records with an injected `now` prints deterministic output (words, words/min, cleanup delta, replacements, top apps, streak heatmap) and a friendly zero-state on empty history. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraints: transforms live in hand-editable `transforms.json` with Polish and Prompt Engineer seeded on first run, and old history records missing `duration_s` degrade to word counts instead of crashing stats.

- [x] **E8 — Transforms + voice command mode** *(the flagship Pro feature)* — highlight text anywhere → hotkey → AI rewrite in place; built-in Polish & Prompt Engineer + unlimited custom transforms; optional auto-transform after every dictation; hold a second hotkey and *speak* an edit instruction *(needs E1; hotkeys active in push-to-talk mode)*
- [x] **E14 — Personal insights** — `local-flow stats`: words, words/min, cleanup delta, smart replacements, top apps, streak heatmap *(needs E2)*

## Phase 7 — Deep platform work

> **Goal:** E13 and E10 are implemented and their TODO boxes checked: `local-flow pad --append "idea"` creates or extends today's markdown note headless, and dictation with the pad focused routes to `ScratchpadSink` instead of the desktop sink; with `MockFieldText(before_cursor="Dear Dr. Adithya,")` the polish prompt contains the field context and the name, `NullFieldText` output is byte-identical to today, and `context_awareness=false` skips the provider entirely. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraints: notes remain plain markdown files under `data_dir/notes/`, field context goes only to the local LM Studio server with a README privacy note saying so, and unreadable fields (Wayland, secure inputs, terminals) silently fall back to today's behavior rather than erroring.

- [x] **E13 — Scratchpad** — floating always-on-top notepad (markdown files in your data dir), hotkey toggle, dictate straight into it *(window = `pad --window`, separate process from `run`; live window checks manual)*
- [x] **E10 — Context-aware dictation** — reads the text already in the focused field (macOS AX; Windows UIA ships as a documented stub) so polish continues sentences, matches tone, and spells nearby names correctly *(hardest platform work — last)*

## Backlog (post-1.0, from the final whole-branch review)

- [ ] Honor `stop_event` in `_run_loop`'s push-to-talk branch (closing `pad --window --with-dictation` burns the 5s join timeout)
- [ ] Voice-command recordings: apply `normalize_audio` + `pending_store`; honor `pad_active` on the no-selection fallback
- [ ] Re-enforce dictionary on `auto_transform` output (asymmetry with the voice-command path)
- [ ] Validate `mode` / `vad_backend` / `asr_backend` values at config load (typos currently fall back silently)
- [ ] Allow an empty env var to override a TOML value back to `""`; skip `.env` comment-stripping for quoted values
- [ ] Dispatcher head-of-line blocking: "busy" feedback when a press queues behind utterance processing
- [ ] Format mic open-failure inside the record thread as `error:/hint:` instead of a raw traceback
- [ ] `learn`: contraction stopwords ("I'm"); disabled-history notice; duplicate-branch test
- [ ] Docs staleness cluster: personalization-file inventories, "LM Studio only for polish and command mode", starred-terms/usage-ranking docs, stale code comments, `LOCAL_FLOW_CONFIG` mention
- [ ] Manual-checklist gaps: streaming modes, cleanup levels, `history --reinsert-raw/--retry`, `learn`
- [ ] Test gaps: `transcribe --copy` failure path; asr hint assertion ("multilingual")
- [ ] Hardening nice-to-haves: `NoteStore.write` tmp+rename; Fn-tap self-PID guard on cancel keyDown; `classify_win32_event` extraction; `HistoryStore` rotation without full re-read; `app_styles.json` hot-reload; `run_command` term-usage stats; mic open-failure fallback; chord hotkeys (stretch)

## Already shipped (MVP)

- [x] Push-to-talk (F9) and hands-free VAD dictation
- [x] Local Whisper ASR + rule cleanup (fillers, "scratch that") + LM Studio polish with rules-only degradation
- [x] Personal dictionary, snippets, styles (hand-editable JSON)
- [x] Dictation commands ("new line", "press enter") and command mode (CLI)
- [x] Paste → type → clipboard insertion fallback chain; fully mocked headless test suite
