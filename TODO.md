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

## Code review fixes (max-effort review — 2026-07-07)

34 verified correctness defects from a full-branch recall review, regrouped
into eight work groups, A–H in priority order (A first). Each group is one
coherent branch of work; its heading lists the findings by original severity
(P0 = silent data loss or crashes on normal macOS usage) and carries a
`/goal`-ready completion condition. Original finding numbers are kept. A few
refine existing Backlog entries (noted inline). Findings marked *(plausible)*
have a real mechanism but a timing/platform/edge-dependent trigger.

### Group A — Never destroy user text (P0: 1, 4, 5, 7)

> **Goal:** Items 1, 4, 5, and 7 are fixed and their boxes checked: a dictionary term containing a backslash (e.g. `AC\DC`) passes through `enforce_dictionary_detailed` without raising and lands verbatim in the output; in all three selection-replace paths (transform hotkey, `_cmd_transform`, `_run_voice_command`) a whitespace-only `MockChatClient` response leaves the user's selection untouched — tests assert `capture.replace` is never called with blank text and the selection is restored; and an empty `auto_transform` completion inserts the polished text with a warning instead of silently discarding the utterance. The transform clipboard path preserves non-text clipboard content or refuses the transform with a clear message, asserted against a fake pasteboard. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraints: guards mirror existing in-repo patterns (`expand_snippets`'s lambda replacement, `TranscriptPolisher`'s `if polished:`), non-blank-output behavior stays byte-identical to today, and any NSPasteboard code lives in a lazily-imported leaf module behind the `desktop` extra.

- [x] **1. Escape the dictionary replacement string** — `enforce_dictionary_detailed` passes the raw term as the `re.subn` replacement template, so a backslash term (e.g. `AC\DC`, `C:\Users`) raises `re.PatternError` on *every* dictation (crashes the hands-free session) or injects a control char. Guard the replacement with a lambda like `expand_snippets` does. `local_flow/polish/rules.py:123`
- [x] **4. Guard empty LLM output before replacing a selection** — all three selection-replace paths call `capture.replace(result)` with no empty-output guard, so a whitespace-only model response pastes `""` over the user's highlighted text (unrecoverable — restore rewrites the clipboard, not the selection). Skip the replace (and restore the selection) when output is blank. `local_flow/app.py:1700` (also `_cmd_transform` ~466, `_run_voice_command` ~1170)
- [x] **5. Guard empty auto-transform output before insertion** — the `auto_transform` block unconditionally sets `result.final` to the transform output, so an empty completion silently discards the whole utterance (no warning, `failed=False`). Mirror `TranscriptPolisher`'s `if polished:` guard. `local_flow/pipeline.py:193`
- [x] **7. Preserve non-text clipboard content in transforms** — `capture()` saves only text (pyperclip) then writes `""`, destroying any image/files/rich-text on the clipboard even on the restore path. Save/restore via NSPasteboard multi-type, or refuse the transform when the clipboard holds non-text. `local_flow/transforms/selection.py:182`

### Group B — Atomic persistence (P0: 3, 8, 9)

> **Goal:** Items 3, 8, and 9 are fixed and their boxes checked: `HistoryStore._rewrite` and every `PersonalizationStore._write` target (styles.json, snippets.json, active) go through tmp-file + `os.replace`, proven by tests that force the underlying write to fail mid-operation and assert the original file is still intact and parseable; and the scratchpad stats the note mtime *before* reading/writing, proven by a test where an external append landing between read and stat survives the next autosave instead of being overwritten. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraints: history stays an append-only JSONL in `data_dir`, on-disk formats are unchanged, and the existing `_atomic_write` helper is reused rather than duplicated.

- [x] **3. Make history rewrite atomic** — `_rewrite` uses a non-atomic `write_text` (unlike the sibling `PersonalizationStore._atomic_write`), running every append under `retention=24h` (and past the 5000-record cap on the default), so a crash mid-write destroys the entire history file. Use tmp-file + `os.replace`. `local_flow/history/store.py:153`
- [x] **8. Close the scratchpad autosave TOCTOU** *(plausible)* — `_load_active`/`_autosave` stat the note mtime *after* reading/writing content, so an external dictation append landing in the gap is stamped "already seen" and later overwritten by the stale buffer. Stat before read/write so the recorded mtime is a lower bound. `local_flow/scratchpad/window.py:185`
- [x] **9. Make the other personalization writes atomic** — `_write` (styles.json / snippets.json / active) uses plain `write_text` while only `dictionary.json` gets `_atomic_write`; a crash mid-write truncates the file and loses all styles. Route every write through `_atomic_write`. `local_flow/personalization/store.py:171`

### Group C — Keep the session alive (P0: 2, 6 · P1: 10, 16 · P2: 34)

> **Goal:** Items 2, 6, 10, 16, and 34 are fixed and their boxes checked: a mock transcriber raising `RuntimeError` mid-utterance leaves the hands-free loop running, reports through the `StatusReporter`, and still lands the WAV in `pending/` (`pending_store.save()` moved inside the guard); after `join(timeout=5)` a still-`is_alive()` recorder thread's buffer is abandoned instead of reused, asserted with a deliberately stalled mock recorder; a quick second dictation's `start()` no longer loses audio behind the previous utterance's processing (recording runs on its own thread, or at minimum a tested "busy" status is surfaced); `WindowedStream.feed` keeps a bounded trailing window and resets on silence, with a test asserting the buffer stays under the cap through repeated silent intervals; and `frames()` uses a timed `queue.get` so a simulated mid-session mic disconnect ends the loop instead of hanging it. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraints: no core module imports a platform package, normal-path push-to-talk behavior is unchanged, and every new seam ships with a mock.

- [x] **2. Broaden `_handle_utterance` error handling** — it catches only `LocalFlowError`, and `pending_store.save()` sits outside the `try`, so any other exception (full-disk `OSError`, ctranslate2 `RuntimeError`, bad `asr_language` `ValueError`) silently kills the hands-free/tray loop while push-to-talk survives. Catch `Exception`, report through the `StatusReporter`, and move `save()` inside the guard. `local_flow/app.py:1099`
- [x] **6. Don't treat a recorder join-timeout as completion** — `finish()`/`cancel()`/`cmd_finish` clear `mic_in_use` and pop `captured['pcm']` after `join(timeout=5)` with no `is_alive()` check, so a >5s PortAudio stall (mic-permission prompt, Bluetooth dropout) opens a second concurrent stream and later types a previous recording's audio into the wrong field. Check `is_alive()` and abandon the stalled thread's buffer. `local_flow/app.py:1585` *(fixed via per-recording `_Recording` state + `mic_owner` guard; a stalled recorder keeps the mic busy until its thread exits, then the next `start()` reclaims it)*
- [x] **10. Fix dispatcher head-of-line blocking** — all hotkey callbacks share one single-threaded `CallbackDispatcher` that runs ASR+LLM+insert inline, so a quick second dictation's `start()` queues behind the previous `finish()` and the first words are lost with no warning. Run recording on its own thread (or at least surface "busy"). `local_flow/app.py:1622` *(refines Backlog "Dispatcher head-of-line blocking"; fixed with a second `processor` dispatcher lane: state flips stay on the hotkey lane, all slow ASR+LLM+insert work is serialized FIFO on the processor lane)*
- [x] **16. Bound the live-preview stream** — `WindowedStream.feed()` re-transcribes the entire unbounded buffer every interval and only resets after a segment yields, so silence grows it ~115 MB/h and Whisper re-runs over it synchronously until dictation freezes. Trim to a trailing window and reset on silence. `local_flow/asr/streaming.py:60`
- [x] **34. Add liveness to the audio `frames()` loop** *(plausible)* — a blocking `queue.get()` with no timeout means a mid-session mic disconnect hangs the loop forever (tray Stop can't take effect). Use a timeout + finished/status handling. `local_flow/audio/capture.py:197`

### Group D — Polish rules & prompting correctness (P1: 11, 17, 18, 19, 22)

> **Goal:** Items 11, 17, 18, 19, and 22 are fixed and their boxes checked: table-driven rules tests assert `apply_spoken_code_syntax` no longer matches across a commanded newline ("… order total new line thanks" keeps "thanks" off the identifier) and that "email John, um, scratch that, email Sarah" resolves to "email Sarah" because fillers are removed before backtracking; `field_context.selected` is capped both at capture and in the prompt, asserted with an oversized synthetic selection whose prompt stays under the bound; `build_command_messages` wraps target/selection text in the same `<<<>>>` context-never-instructions framing the field-context prompt uses; and a mocked model-not-found response makes `resolve_model` invalidate its cache and re-list models instead of 404ing until restart. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraints: no existing rules test is edited to make new behavior pass (new cases are added instead), and no test requires a live LM Studio server.

- [x] **11. Cap `field_context.selected` length** — only `before_cursor` is capped at `MAX_BEFORE_CURSOR`; a Cmd+A selection (location 0 → empty `before_cursor`, whole doc as `selected`) embeds the entire document in the polish prompt (~51k chars for a 50k doc), stalling every utterance. Cap `selected` at capture (`field_text.py:133`) and in the prompt. `local_flow/polish/prompting.py:159`
- [x] **17. Stop spoken code-syntax eating commanded newlines** — `apply_spoken_code_syntax` runs after `apply_dictation_commands` and its `\s+` regex crosses the inserted newline, merging the next line's first word into the identifier ("… order total new line thanks" → "orderTotalThanks"). Don't match across newlines. `local_flow/polish/rules.py:229`
- [x] **18. Run backtracking after filler removal** — `clean_transcript` runs `apply_backtracking` before `remove_fillers`, so a filler segment between the retracted text and the marker gets popped instead ("email John, um, scratch that, email Sarah" → keeps "email John"). Swap the order. `local_flow/polish/rules.py:105`
- [x] **19. Re-list models on a 404** — `resolve_model` caches the first auto-picked model id forever, so after the user swaps models in LM Studio every call 404s and polish is silently downgraded until restart. Invalidate the cache and re-list on model-not-found. `local_flow/llm/lmstudio.py:97`
- [x] **22. Delimit selection text in command-mode prompts** — `build_command_messages` concatenates the target/selection text with no delimiters, unlike the field-context prompt's `<<<>>>` "context, never instructions" framing, so imperative text in a selection can hijack the command. Add the same anti-injection framing. `local_flow/commands/command_mode.py:34`

### Group E — Config & input validation (P1: 12, 13, 14, 20 · P2: 29)

> **Goal:** Items 12, 13, 14, 20, and 29 are fixed and their boxes checked: a typo in `mode`, `vad_backend`, `asr_backend`, or `asr_language` raises `ConfigError` at load naming the allowed values; the setup wizard's overwrite path reads the existing config first and preserves every key it didn't ask about, proven by a test where a customized `data_dir` survives a wizard re-run; a distinct-but-unsupported secondary-hotkey value (e.g. `transform_hotkey=fn`) disables just that hotkey with an actionable warning while `run` keeps working; an unknown per-app `insert` value in `app_styles.json` produces a warning instead of a silent default-sink fallback; and `_read_dotenv` preserves ` #` inside quoted values. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraints: config precedence (defaults < TOML < env) is unchanged, every new error message states the allowed values, and `.env.example` and `local-flow.example.toml` still document reality.

- [x] **12. Wizard overwrite must merge, not replace** — on overwrite the setup wizard writes only the 4–5 keys it asked about and `os.replace`s the whole file, silently dropping every other setting (relocating `data_dir`, orphaning styles/history). Read the existing config and preserve unasked keys. `local_flow/setup_wizard.py:291`
- [x] **13. Validate `mode` / `vad_backend` / `asr_backend` / `asr_language`** — none are validated, so a typo (`mode=handsfree`) silently runs push-to-talk and nothing records, or falls back to a different VAD. Validate against the allowed sets at load. `local_flow/config.py:310` *(refines Backlog "Validate mode/vad_backend/asr_backend")*
- [x] **14. Validate secondary-hotkey key values (degrade, don't abort)** — `transform/command/scratchpad` hotkeys are checked only for distinctness, so a distinct-but-unsupported value (`fn`, the macOS default) makes listener construction raise on the main thread and abort the whole app. Validate the value and disable just that one hotkey. `local_flow/app.py:1708`
- [x] **20. Warn on per-app `insert` override typos** — `ContextRouter` resolves the override with a case-sensitive `.get()` that silently returns the default sink on any miss, unlike `config.insert_method` which fails loudly. Validate/warn on unknown values in `app_styles.json`. `local_flow/context/router.py:46`
- [x] **29. Don't strip ` #` inside quoted `.env` values** — `_read_dotenv` truncates at the first ` #` even inside quotes, silently mangling values like a dir named `my #notes`. Strip comments only outside quotes. `local_flow/config.py:206` *(refines Backlog ".env comment-stripping for quoted values")*

### Group F — Recovery & file transcription (P1: 21 · P2: 25, 26, 33)

> **Goal:** Items 21, 25, 26, and 33 are fixed and their boxes checked: `local-flow recover` builds only the pipeline (no `SounddeviceSource`) and completes on a machine with no microphone or sounddevice, proven by a test that recovers a saved WAV with the audio extra absent/mocked; pending utterances replay ordered by save time (mtime or an embedded sequence), not uuid filename; a non-mono or non-16-bit WAV in `pending/` is skipped with a warning and left on disk instead of misparsed as garbage; and `transcribe --copy` over multiple files puts every transcript on the clipboard, joined. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraints: the recover path never imports a platform audio package, and skipped or failed pending files are never deleted.

- [x] **21. `recover` shouldn't require a microphone** — `_cmd_recover` builds full `RunDependencies` (incl. `SounddeviceSource`) just for the pipeline, so recovery of already-saved WAVs aborts when no mic/sounddevice is present — breaking the safety net for the P0 data-loss cases. Build only the pipeline. `local_flow/app.py:1864`
- [x] **25. Order `recover` by save time** — `pending()` sorts by uuid4 filename, so crashed utterances replay in scrambled order. Sort by mtime (or embed a sequence). `local_flow/audio/recovery.py:55`
- [x] **26. `transcribe --copy` should include every file** — with multiple files only the last file's text reaches the clipboard. Join all transcripts. `local_flow/app.py:540` *(also in Backlog test-gaps)*
- [x] **33. Validate WAV format in recovery `load()`** *(plausible)* — it ignores channel count/sample width, so a non-mono/non-16-bit file in `pending/` is misparsed as garbage instead of skipped. Check `getnchannels()`/`getsampwidth()`. `local_flow/audio/recovery.py:68`

### Group G — Tray & scratchpad UI (P1: 15 · P2: 27, 28)

> **Goal:** Items 15, 27, and 28 are fixed and their boxes checked: `TrayReporter.notify` marshals icon/title updates to the main thread and skips the redraw when the icon kind is unchanged, asserted with a fake tray backend that records the calling thread and update count; stopping hands-free dictation emits a terminal idle notification so the mock tray's final state is idle, not recording; and the note dropdown label tracks the active note across `_refresh_note_menu` rebuilds. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless with no display. Constraints: pystray/Pillow/tkinter stay behind the `tray`/`desktop` extras and out of core, and live tray/window appearance stays a README manual-checklist item reported as a manual step, never claimed as verified.

- [x] **15. Marshal tray UI updates to the main thread** — `TrayReporter.notify` sets the pystray icon/title from the dictation-loop thread; the darwin backend calls AppKit off-main-thread with no dispatch → intermittent corruption or native crash. Marshal to the main thread; also skip redraw when the icon kind is unchanged. `local_flow/tray/app.py:158`
- [x] **27. Emit a terminal "idle" on tray stop** — stopping hands-free dictation never reports idle, so the tray icon stays stuck on red "recording". Notify idle on stop. `local_flow/tray/app.py:309`
- [x] **28. Fix note dropdown desync** — `_refresh_note_menu` rebuilds `OptionMenu` entries without tkinter's `_setit`, so after the first poll the button label stops tracking the active note. Set `_note_var` in `_on_note_selected` (or rebuild via `_setit`). `local_flow/scratchpad/window.py:271`

### Group H — Cross-platform edges (P2: 23, 24, 30, 31, 32)

> **Goal:** Items 23, 24, 30, 31, and 32 are fixed and their boxes checked: `local-flow stats` buckets streaks and the heatmap by the user's local calendar date with an injected `now`/zone (a 23:30 UTC−8 dictation counts toward the local day, deterministically); AX field-text offsets are converted from UTF-16 code units to code points so an emoji before the cursor leaves `before_cursor`/`selected` uncorrupted; a tapped space followed by fast rollover typing replays in the typed order ("a b", not "ab "), with the pending space flushed on the next key press; a failing `wl-copy` falls through to `xclip`/`xsel` instead of re-raising; and text piped to `clip.exe` is UTF-16LE with BOM, asserted on a mocked subprocess. Prove it by showing `uv run pytest` and `uv run ruff check .` both exit 0 headless. Constraints: all platform-specific code stays in lazily-imported leaf modules, and every test runs on a bare darwin CI host with no display, clipboard, real Wayland/X11, or Windows.

- [x] **23. Bucket insights by local date, not UTC** — streaks/heatmap use UTC calendar dates, so evening dictations west of UTC land on the wrong day and break streaks. Convert to the user's local zone before `.date()`. `local_flow/insights/stats.py:148`
- [x] **24. Slice AX field text by UTF-16 offset** — the AX selection range is in UTF-16 code units but Python slices by code point, so a non-BMP char (emoji) before the cursor corrupts `before_cursor`/`selected`. Convert offsets. `local_flow/context/field_text.py:131`
- [x] **30. Flush the swallowed space on rollover** — a tapped space is replayed only on key-up, so fast rollover typing ("a b") reorders the space after the next char ("ab "). Flush the pending space when another key is pressed. `local_flow/hotkeys/space.py:47`
- [x] **31. Continue the Linux clipboard fallback chain** — `copy_to_clipboard` re-raises on the first installed-but-failing tool instead of trying the next, so `wl-copy` failing on X11 skips `xclip`/`xsel`. Continue the loop on failure. `local_flow/insertion/desktop.py:49`
- [x] **32. Encode for `clip.exe` correctly on Windows** — piping UTF-8 into `clip` yields mojibake for non-ASCII (it decodes OEM/ANSI). Pipe UTF-16LE + BOM. `local_flow/insertion/desktop.py:46`

## Backlog (post-1.0, from the final whole-branch review)

- [ ] Honor `stop_event` in `_run_loop`'s push-to-talk branch (closing `pad --window --with-dictation` burns the 5s join timeout)
- [ ] Voice-command recordings: apply `normalize_audio` + `pending_store`; honor `pad_active` on the no-selection fallback
- [ ] Re-enforce dictionary on `auto_transform` output (asymmetry with the voice-command path)
- [ ] Validate `mode` / `vad_backend` / `asr_backend` values at config load (typos currently fall back silently)
- [ ] Allow an empty env var to override a TOML value back to `""`; skip `.env` comment-stripping for quoted values
- [x] Dispatcher head-of-line blocking: resolved by Group C item 10 — utterance processing moved to its own `processor` dispatcher lane, so a press no longer queues behind it at all
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
