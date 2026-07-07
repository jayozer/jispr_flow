# local-flow TODO

Feature roadmap toward full single-user Wispr-Flow-Pro parity, fully local.
Detailed specs (files, interfaces, acceptance criteria) for every epic:
[docs/superpowers/plans/2026-07-06-flow-features-roadmap.md](docs/superpowers/plans/2026-07-06-flow-features-roadmap.md).

Scope: single user only. Team/enterprise features, cloud sync, mobile apps,
plan gating, and banking-app auto-pause are explicit non-goals.

## Phase 1 — Foundations

- [x] **E1 — Hotkeys: Fn or Space push-to-talk** *(user priority; live hardware checks = README manual checklist 11–13)*
  - [x] `LOCAL_FLOW_HOTKEY=fn` (default on macOS, via Quartz event tap) or `space` (hold-vs-tap with suppression) or any pynput key
  - [x] Rebindable cancel key (default Esc) to discard an utterance mid-recording
  - [ ] Stretch (not shipped): chord hotkeys (`ctrl+space`)
- [x] **E2 — Dictation history** — local JSONL log, `local-flow history --search/--clear`, retention `forever|24h|off`, records duration + replacement counts (fuels E5/E9/E14)
- [x] **E3 — Multilingual ASR** — `asr_language=auto` detection with multilingual Whisper models, config validation

## Phase 2 — Intelligence

- [x] **E4 — Per-app awareness** — style follows the frontmost app (Slack casual, Mail formal); per-app insert method (typing sink in Claude Code/terminals to avoid "[Pasted N lines]"); built-in email/chat formatting styles
- [x] **E5 — Auto-learning dictionary** — mines history for your recurring terms, `local-flow learn --add`, spoken "add X to dictionary"; starred terms, usage-based ranking, apostrophe dedup

## Phase 3 — UX shell

- [ ] **E6 — Tray app + setup wizard** — menu-bar icon with recording states, style + language quick-switch menus, desktop notifications, `local-flow setup` onboarding

## Phase 4 — Latency

- [ ] **E7 — Streaming insertion** — sentence-chunked insertion in hands-free mode, then live rough-text preview; `off` stays byte-identical to today

## Phase 5 — Wispr-Pro parity: quick wins

- [ ] **E9 — Auto-cleanup levels** — `none | light | medium | high`; spoken lists → real lists; spoken code syntax (camelCase/snake_case/ALL-CAPS); "undo AI edit" from history *(needs E2)*
- [ ] **E11 — Reliability & mics** — mic priority ranking with mid-session fallback, specific mic error diagnostics, whisper-mode VAD preset, crash-safe audio autosave + `local-flow recover`, failed-polish retry, long-session warning
- [ ] **E12 — Mouse Flow** — bind middle/x1/x2 mouse button to PTT (hold or toggle) and optionally Enter *(needs E1)*
- [ ] **E15 — File transcription** — `local-flow transcribe memo.m4a --polish`: transcribe existing audio files (WAV/MP3/M4A/FLAC) through the same local pipeline — a feature Wispr Flow itself doesn't have

## Phase 6 — Wispr-Pro parity: power features

- [ ] **E8 — Transforms + voice command mode** *(the flagship Pro feature)* — highlight text anywhere → hotkey → AI rewrite in place; built-in Polish & Prompt Engineer + unlimited custom transforms; optional auto-transform after every dictation; hold a second hotkey and *speak* an edit instruction *(needs E1)*
- [ ] **E14 — Personal insights** — `local-flow stats`: words, words/min, cleanup delta, smart replacements, top apps, streak heatmap *(needs E2)*

## Phase 7 — Deep platform work

- [ ] **E13 — Scratchpad** — floating always-on-top notepad (markdown files in your data dir), hotkey toggle, dictate straight into it
- [ ] **E10 — Context-aware dictation** — reads the text already in the focused field (macOS AX / Windows UIA) so polish continues sentences, matches tone, and spells nearby names correctly *(hardest platform work — last)*

## Already shipped (MVP)

- [x] Push-to-talk (F9) and hands-free VAD dictation
- [x] Local Whisper ASR + rule cleanup (fillers, "scratch that") + LM Studio polish with rules-only degradation
- [x] Personal dictionary, snippets, styles (hand-editable JSON)
- [x] Dictation commands ("new line", "press enter") and command mode (CLI)
- [x] Paste → type → clipboard insertion fallback chain; fully mocked headless test suite
