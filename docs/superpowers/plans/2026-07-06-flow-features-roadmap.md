# Flow-Feature Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Scope note (per writing-plans scope check):** this roadmap spans seven
> independent subsystems. Each epic below is a separately plannable,
> separately shippable unit: when an epic is picked up, write its own
> detailed TDD plan (`superpowers:writing-plans`) using the scope,
> interfaces, and acceptance criteria locked in here. Do not implement two
> epics in one branch.

**Goal:** Close the gap between local-flow's MVP and a full Flow-style dictation app — configurable Fn/Space push-to-talk first, then history, multilingual ASR, per-app context styles, an auto-learning dictionary, a tray UI, and streaming insertion.

**Architecture:** Every epic is a new adapter behind an existing interface (`HotkeyListener`, `Transcriber`, `TextSink`) or a new small module wired into `DictationPipeline` / `app.py`, each with a mock so `uv run pytest` and `local-flow demo` stay fully headless. No epic rewrites the pipeline except Epic 7 (streaming), which is deliberately last.

**Tech Stack:** Python 3.13, uv, pytest, pynput, pyobjc-framework-Quartz (macOS Fn key), faster-whisper, pystray + Pillow (tray), LM Studio (OpenAI-compatible, localhost only).

## Global Constraints

- Everything runs locally; no network calls except the user's own LM Studio server (`LMStudioClient` refuses cloud endpoints — keep it that way).
- Every new stage/adapter gets a mock and headless tests; CI has no mic, GPU, display, clipboard, or LM Studio.
- OS-specific code lives in leaf modules, imported lazily; core modules never import platform packages.
- All new settings follow the existing pattern: `Config` dataclass field + `LOCAL_FLOW_<NAME>` env var + TOML key, precedence env > file > defaults (`local_flow/config.py`).
- Personalization data stays hand-editable JSON in `data_dir`.
- New third-party deps go in optional extras in `pyproject.toml`, never core deps.
- Missing platform capability must fail with an actionable `LocalFlowError` (message + hint), never a raw ImportError/crash.
- `uv run pytest` and `uv run ruff check .` pass at every commit.

## Phases

| Phase | Epics | Why this order |
|---|---|---|
| 1 — Foundations | E1 hotkeys (Fn/Space), E2 history, E3 multilingual | E1 is the user's explicit priority; E2 is a prerequisite for E5/E9/E14; E3 is a small, isolated win |
| 2 — Intelligence | E4 per-app styles, E5 auto-learning dictionary | E5 mines E2's history; E4 owns the frontmost-app adapter |
| 3 — UX shell | E6 tray/menu-bar app + setup wizard | Wraps whatever exists; later = wraps more |
| 4 — Latency | E7 streaming/partial insertion | Only Wave-1 epic that changes pipeline shape |
| 5 — Wispr-Pro parity, quick wins | E9 cleanup levels, E11 reliability & mics, E12 Mouse Flow, E15 file transcription | Small, self-contained, immediate daily-use value |
| 6 — Wispr-Pro parity, power features | E8 Transforms + voice command mode, E14 insights | E8 is the flagship Pro feature; E14 consumes E2/E4 data |
| 7 — Deep platform work | E13 Scratchpad, E10 context-aware dictation | E10 needs per-OS accessibility APIs — highest risk, do last |

**Non-goals (rejected, not deferred):** cloud sync / cross-device sync, team & collaboration features (shared dictionary/snippets, admin portal, billing), enterprise compliance (SSO, SOC 2, BAA — meaningless for a fully local app), mobile apps (iOS keyboard, Android bubble, Dynamic Island, shake-to-unsnooze), banking-app auto-pause (explicitly declined by the user — a local model makes it unnecessary), UI localization, word-count limits and plan gating (everything is enabled, always). Manual multi-machine use is covered by documenting that `data_dir` is plain files you can sync yourself.

---

### Epic 1: Configurable push-to-talk hotkey — Fn and Space (Phase 1, user priority)

**Decision (from user):** Fn is the preferred hotkey; the config must offer a choice between `fn` and `space` (existing pynput names like `f9` keep working). Default becomes `fn` on macOS, stays `f9` on Windows/Linux (the Fn key is handled by keyboard firmware on most non-Mac hardware and never reaches the OS).

**Files:**
- Modify: `local_flow/config.py` (extend `hotkey` docs; add `hotkey_space_hold_ms: int = 250`; platform-dependent default via `default_factory`)
- Modify: `local_flow/hotkeys/base.py` (add `create_hotkey_listener(config) -> HotkeyListener` factory; keep `PynputPushToTalk`)
- Create: `local_flow/hotkeys/macos_fn.py` (`QuartzFnListener(HotkeyListener)`)
- Create: `local_flow/hotkeys/space.py` (`SpacePushToTalk(HotkeyListener)`)
- Modify: `local_flow/app.py:229-254` (`_cmd_run` uses the factory instead of constructing `PynputPushToTalk` directly)
- Modify: `README.md`, `.env.example`, `local-flow.example.toml`
- Test: `tests/test_hotkeys.py`

**Interfaces:**
- Consumes: `HotkeyListener.run(on_press: Callable[[], None], on_release: Callable[[], None]) -> None` (unchanged, `local_flow/hotkeys/base.py:16`).
- Produces: `create_hotkey_listener(config: Config) -> HotkeyListener` — dispatches on `config.hotkey`: `"fn"` → `QuartzFnListener` (Darwin only; on other platforms raise `HotkeyBackendMissingError` with hint "the Fn key is only observable on macOS; use f9 or space"), `"space"` → `SpacePushToTalk(hold_ms=config.hotkey_space_hold_ms)`, anything else → `PynputPushToTalk(key_name)`.

**Key technical facts the implementer needs:**
- pynput has **no** `Key.fn`. On macOS the Fn key arrives as a `kCGEventFlagsChanged` Quartz event, keycode 63 (`kVK_Function`), flag mask `kCGEventFlagMaskSecondaryFn` (0x800000). `QuartzFnListener` runs a `CGEventTap` (listen-only) on `flagsChanged`, treating mask 0→1 as press and 1→0 as release. Dependency: `pyobjc-framework-Quartz` in the `desktop` extra (macOS marker: `; sys_platform == 'darwin'`). Needs the same Input Monitoring permission the README already documents.
- Space cannot be a naive global hotkey — holding it would type spaces into the focused app. `SpacePushToTalk` must **suppress and disambiguate**: swallow space key-down; if released before `hold_ms` (default 250 ms), replay a synthetic space keystroke so normal typing still works; if held past `hold_ms`, start recording and swallow everything until release (ignore OS auto-repeat events). Suppression mechanism: `darwin_intercept` (macOS) / `win32_event_filter` + `listener.suppress_event()` (Windows). On Linux/X11 pynput cannot suppress single events — `"space"` there must raise `HotkeyBackendMissingError` with hint to use hands-free mode or another key.

**Tasks:**
- [ ] Write per-epic detailed plan (`superpowers:writing-plans`) from this spec
- [ ] Config: `hotkey` default `"fn"` on Darwin / `"f9"` elsewhere; add `hotkey_space_hold_ms`; tests for both platforms via injected `sys.platform` seam
- [ ] `create_hotkey_listener` factory + unhappy-path tests (fn on Linux, space on X11 → actionable errors)
- [ ] `QuartzFnListener` with the tap loop isolated so press/release logic is unit-testable with fake events
- [ ] `SpacePushToTalk` hold-vs-tap state machine as a pure class (`feed(event, now) -> list[Action]`) + platform glue; unit-test tap-passthrough, hold-start, auto-repeat-ignore
- [ ] Wire into `_cmd_run`; update README permission notes, `.env.example`, example TOML; delete the "chord hotkeys are not supported" line only if the chord task below ships
- [ ] Stretch (separate task, may be dropped): chord hotkeys (`ctrl+space`) via pressed-set tracking in `PynputPushToTalk`
- [ ] *(Wispr §14.3)* Rebindable **cancel key** (`cancel_hotkey`, default `esc`): pressing it while recording discards the utterance instead of inserting it — plumb a `on_cancel` callback through `HotkeyListener.run` implementations and `_cmd_run`

**Acceptance:**
- `uv run pytest tests/test_hotkeys.py -v` passes headless.
- Manual (macOS): `LOCAL_FLOW_HOTKEY=fn uv run local-flow run` — hold Fn, speak, release → text inserted. Same with `space`; tapping space in an editor still types a space.

---

### Epic 2: Dictation history (Phase 1; prerequisite for Epic 5)

**Files:**
- Create: `local_flow/history/__init__.py`, `local_flow/history/store.py`
- Modify: `local_flow/pipeline.py:67-93` (append a record in `process_transcript` after insertion)
- Modify: `local_flow/config.py` (`history_enabled: bool = True`, `history_max_entries: int = 5000`)
- Modify: `local_flow/app.py` (new subcommand `history`)
- Test: `tests/test_history.py`

**Interfaces:**
- Produces: `HistoryStore(data_dir: Path, max_entries: int)` with `append(record: HistoryRecord) -> None`, `search(query: str, limit: int = 20) -> list[HistoryRecord]`, `recent(limit: int = 20) -> list[HistoryRecord]`, `all() -> Iterator[HistoryRecord]`; `HistoryRecord` dataclass: `timestamp: str` (ISO 8601), `rough: str`, `final: str`, `used_llm: bool`, `app: str` (empty until Epic 4 fills it).
- Storage: append-only JSONL at `data_dir/history.jsonl` (keeps the hand-editable-files ethos; rotate/truncate at `history_max_entries`).
- CLI: `local-flow history [--search TEXT] [--limit N] [--clear]`.

**Tasks:**
- [ ] Write per-epic detailed plan
- [ ] `HistoryStore` (JSONL append, search, rotation, `--clear`) — pure, fully unit-tested
- [ ] Pipeline wiring behind `history_enabled` (a disabled store writes nothing; `DictationPipeline` takes `history: HistoryStore | None = None`)
- [ ] `local-flow history` subcommand + README privacy note (local file, how to disable/clear)
- [ ] *(Wispr §8.9)* Retention policy: `history_retention: str = "forever"  # forever | 24h | off` — `24h` prunes on append; `off` behaves like `history_enabled=false`
- [ ] *(Wispr §12.1 prerequisite)* `HistoryRecord` gains `duration_s: float` (utterance audio length) and `replacements: int` (dictionary + snippet substitutions made) so E14 can compute words/min and smart-replacement stats

**Acceptance:** dictate twice, `uv run local-flow history` shows both with timestamps; `--search` filters; `LOCAL_FLOW_HISTORY_ENABLED=false` writes nothing.

---

### Epic 3: Multilingual ASR with auto language detection (Phase 1)

**Files:**
- Modify: `local_flow/config.py` (`asr_language: str = "en"`; `"auto"` = detect)
- Modify: `local_flow/asr/base.py`, `local_flow/asr/faster_whisper_asr.py`, `local_flow/asr/mock.py`
- Modify: `local_flow/app.py` (`_build_transcriber` passes language; `check` warns on `auto` + `*.en` model), `README.md`
- Test: extend `tests/test_config.py`, `tests/test_pipeline_integration.py`

**Interfaces:**
- `FasterWhisperTranscriber(model, device, compute_type, language: str | None = "en")` — `language=None` (from `"auto"`) lets faster-whisper detect per utterance.
- Config validation: `asr_language="auto"` or non-`en` with an `*.en` model raises `ConfigError` (hint: "use a multilingual model such as `small`, not `small.en`").

**Tasks:**
- [ ] Write per-epic detailed plan
- [ ] Thread `language` through the `Transcriber` constructor + config validation tests
- [ ] `check` subcommand reports resolved model/language combo; README model table gains multilingual rows (`small`, `medium`, `large-v3-turbo`)

**Acceptance:** `LOCAL_FLOW_ASR_LANGUAGE=auto LOCAL_FLOW_ASR_MODEL=small uv run local-flow run` transcribes a non-English utterance; misconfiguration fails with the hint above.

---

### Epic 4: Per-app context awareness (Phase 2)

Wispr-style: the polish tone follows the app you're dictating into (Slack casual, mail formal).

**Files:**
- Create: `local_flow/context/__init__.py`, `local_flow/context/frontmost.py` (`FrontmostAppProvider` ABC + `MacFrontmostApp` (NSWorkspace via pyobjc/AppKit), `WindowsFrontmostApp` (win32gui), `X11FrontmostApp` (xprop), `MockFrontmostApp`, `create_frontmost_provider()`)
- Create: `data_dir/app_styles.json` handling in `local_flow/personalization/store.py` (`app_style_map() -> dict[str, str]`, e.g. `{"com.tinyspeck.slackmacgap": "casual", "com.apple.mail": "formal"}`)
- Modify: `local_flow/polish/polisher.py` (`polish(rough, style: str | None = None)` — per-call style override; today style is fixed at construction, `local_flow/app.py:93`)
- Modify: `local_flow/pipeline.py` (resolve app → style before polishing; record app id into `HistoryRecord.app`)
- Test: `tests/test_context.py`

**Interfaces:**
- Produces: `FrontmostAppProvider.current() -> AppInfo` where `AppInfo = (app_id: str, title: str)`; empty strings when undetectable (Wayland) — pipeline then falls back to `config.style`.
- `TranscriptPolisher.polish(rough: str, style: str | None = None)`; `None` keeps constructor default, preserving every existing call site.

**Tasks:**
- [ ] Write per-epic detailed plan
- [ ] `FrontmostAppProvider` + three platform adapters (lazy imports, mock for CI)
- [ ] `app_styles.json` in `PersonalizationStore` (same read-tolerant JSON pattern as `dictionary.json`)
- [ ] Per-call style in polisher + pipeline lookup (provider consulted at utterance start, i.e. when recording stops)
- [ ] README section with example mapping; `check` prints detected frontmost app
- [ ] *(Wispr §2.16)* Per-app **insert-method override** in `app_styles.json` (entry value becomes `{"style": "casual", "insert": "type"}`, plain string still = style only): terminal/AI-coding apps map to the typing sink so long dictations stay visible and editable instead of collapsing into a "[Pasted N lines]" block in Claude Code/Codex
- [ ] *(Wispr §3.6)* App-category **formatting rules**: built-in style additions for email apps (greeting/body/sign-off structure) and chat apps (no salutation, casual) shipped as defaults in `styles.json`
- [ ] Stretch *(Wispr §2.17)*: IDE file tagging — when the frontmost app is an IDE/AI editor, dictionary terms that look like file names keep their exact casing/extension; full @-mention tagging is out of scope

**Acceptance:** with the Slack mapping present, dictating into Slack uses `casual` style rules in the LM Studio prompt (assert on prompt content with `MockChatClient`); apps with no mapping use `config.style`.

---

### Epic 5: Auto-learning dictionary (Phase 2; depends on Epic 2)

No keystroke spying — v1 learns from history mining plus an explicit review step, keeping the user in control.

**Files:**
- Create: `local_flow/personalization/learn.py` (`suggest_terms(history: Iterable[HistoryRecord], known: Iterable[str]) -> list[Suggestion]`)
- Modify: `local_flow/app.py` (new subcommand `learn`), `local_flow/personalization/store.py` (`add_dictionary_term(term: str)`)
- Modify: `local_flow/polish/rules.py` (voice command "add <term> to dictionary" recognized in `apply_dictation_commands`)
- Test: `tests/test_learning.py`

**Interfaces:**
- `Suggestion` dataclass: `term: str`, `count: int`, `sample: str` (one containing sentence). Heuristics: repeated capitalized tokens / CamelCase / dotted names appearing ≥3 times in `final` texts and absent from the dictionary and a small English stopword list.
- CLI: `local-flow learn` prints numbered suggestions; `local-flow learn --add N…` or `--add-all` writes them via `add_dictionary_term` (atomic JSON rewrite, preserves manual edits).

**Tasks:**
- [ ] Write per-epic detailed plan
- [ ] `suggest_terms` miner — pure function, table-driven tests (proper nouns, CamelCase, stopword exclusion, already-known exclusion)
- [ ] `learn` subcommand + `add_dictionary_term`
- [ ] Voice command "add … to dictionary" in the rules stage (runs even when LM Studio is down)
- [ ] *(Wispr §4.1)* Dictionary power features: **starred terms** (`"starred": true` in `dictionary.json`, listed first in the polish prompt so they win when prompt space is tight), **usage-based ranking** (increment a per-term `uses` counter each time `enforce_dictionary` fires; sort prompt terms by it), and **apostrophe-variant dedup** in `add_dictionary_term` ("Iva's" folds into "Iva")

**Acceptance:** seed history with "Kubernetes" ×3 → `local-flow learn` suggests it; `--add 1` puts it in `dictionary.json`; dictating "add jispr to dictionary" adds it live.

---

### Epic 6: Tray / menu-bar app + setup wizard (Phase 3)

**Files:**
- Create: `local_flow/tray/__init__.py`, `local_flow/tray/app.py` (`TrayApp` on pystray), `local_flow/tray/icons.py` (Pillow-drawn idle/recording/processing/error icons — no bundled assets)
- Create: `local_flow/setup_wizard.py` (`local-flow setup`: terminal wizard — check extras, LM Studio ping, mic permission dry-run, hotkey choice Fn/Space/F9, style choice; writes `~/.config/local-flow/config.toml`)
- Modify: `local_flow/app.py` (subcommands `tray`, `setup`; `_cmd_run` gains an injectable `StatusReporter` callback so the same run loop drives CLI prints or tray icon states)
- Modify: `pyproject.toml` (extra `tray = ["pystray", "pillow"]`)
- Test: `tests/test_tray_state.py`, `tests/test_setup_wizard.py` (state machine + wizard logic with mocks; no display in CI)

**Interfaces:**
- `StatusReporter.notify(state: Literal["idle","recording","processing","inserted","error"], detail: str = "") -> None`; `_cmd_run` calls it at each transition (default implementation = today's prints).
- Tray menu: Start/Stop dictation, mode toggle (PTT/hands-free), style submenu (from `styles.json`), "Open data folder", "Check setup", Quit.

**Tasks:**
- [ ] Write per-epic detailed plan
- [ ] `StatusReporter` seam in `_cmd_run` (pure refactor, no behavior change — commit separately)
- [ ] Icon renderer + tray state machine (unit-tested without a display)
- [ ] `TrayApp` wiring pystray menu ↔ a background run loop thread
- [ ] `setup` wizard writing a validated TOML; README quick-start updated to lead with `local-flow setup`
- [ ] *(Wispr §2.3)* Tray **language quick-switch** submenu (populated from a `languages` config list) that flips `asr_language` for the next utterances without restarting; *(Wispr §14.4)* desktop notifications for interrupted/failed dictations routed through `StatusReporter`

**Acceptance:** `uv run local-flow tray` shows an icon that turns red while recording and green on insert; menu style switch changes the next polish prompt; `local-flow setup` on a clean machine produces a working config.

---

### Epic 7: Streaming / low-latency insertion (Phase 4 — architectural, do last)

Today text lands only after the full utterance finishes ASR + LLM. Full Wispr-style live-typing-with-retroactive-correction requires diff-and-replace editing of already-inserted text — high risk in arbitrary apps. Staged approach; each stage is shippable and gated by config.

**Files:**
- Modify: `local_flow/asr/base.py` (+`TranscriberStream` protocol: `feed(pcm) -> str | None` partial, `finish() -> str`), `local_flow/asr/faster_whisper_asr.py` (windowed re-transcription stream), `local_flow/asr/mock.py`
- Modify: `local_flow/pipeline.py` (`process_stream(frames) -> DictationResult`), `local_flow/app.py` (hands-free path uses it), `local_flow/config.py` (`streaming: str = "off"  # off | sentence | live-preview`)
- Test: `tests/test_streaming.py`

**Stages:**
1. `sentence`: in hands-free mode, split at VAD pauses ≥ ~300 ms, run rules+polish per sentence, insert per sentence (works with existing sinks — biggest felt-latency win, no correction problem).
2. `live-preview`: stream rough partials to the console/tray only; final polished text inserted once, as today.
3. (Future, out of this roadmap) true in-app live typing with diff-replace — revisit only if 1+2 feel insufficient.

**Tasks:**
- [ ] Write per-epic detailed plan
- [ ] `TranscriberStream` protocol + mock; windowed faster-whisper implementation
- [ ] `process_stream` with per-sentence insertion (dictionary/snippets/commands still applied per chunk; "scratch that" only reaches back within the current chunk — document this limit)
- [ ] `live-preview` reporter via Epic 6's `StatusReporter`
- [ ] Latency measurement note in README (utterance-end → insertion, before/after)

**Acceptance:** with `LOCAL_FLOW_STREAMING=sentence` in hands-free mode, a two-sentence dictation inserts the first sentence while the second is still being spoken (assert ordering with mocks); `off` behaves byte-identical to today.

---

## Wave 2 — Wispr Pro single-user parity (added 2026-07-06)

> Source: full Wispr Flow paid-feature catalog reviewed with the user.
> Selection rule: single-user features only — no team/collaboration, no
> enterprise compliance/admin, no mobile-only features, no banking auto-pause
> (explicitly declined), no plan gating (everything here ships enabled).
> Already covered by Wave 1 and therefore *not* re-listed: unlimited words
> (inherent), command mode CLI, backtracking, filler removal, auto
> punctuation, per-app styles, personal dictionary/snippets, history,
> languages, streaming. Each epic below still gets its own detailed TDD plan
> when picked up.

### Epic 8: Transforms + voice command mode (Wispr §5, §6 — the flagship Pro features)

Highlight text anywhere → hotkey → AI rewrite in place; plus a spoken-instruction hotkey so command mode stops being CLI-only.

**Files:**
- Create: `local_flow/transforms/__init__.py`, `local_flow/transforms/selection.py` (`SelectionCapture`), `local_flow/transforms/registry.py` (`transforms.json` handling)
- Modify: `local_flow/personalization/store.py` (own `transforms.json` alongside the other JSON files), `local_flow/config.py` (`transform_hotkey: str = "f10"`, `command_hotkey: str = "f8"`, `auto_transform: str = ""`), `local_flow/app.py` (subcommand `transform`; run-loop listens for the two extra hotkeys), `local_flow/pipeline.py` (apply `auto_transform` between polish and insertion)
- Test: `tests/test_transforms.py`

**Interfaces:**
- `SelectionCapture.capture() -> str`: save clipboard → synthesize the platform copy chord (pynput) → poll clipboard ≤300 ms → return selection; `SelectionCapture.replace(text: str) -> None`: paste over the selection, then restore the saved clipboard. `MockSelection` for CI.
- `transforms.json`: `[{"name": "Polish", "prompt": "...", "hotkey": ""}]`; built-ins **Polish** and **Prompt Engineer** seeded on first run; custom transforms are just more entries (Wispr §5.2).
- Trigger paths: (a) select text + `transform_hotkey` → picker (last-used default) → rewrite in place; (b) `auto_transform: "<name>"` runs after every dictation before insertion; (c) `local-flow transform <name> --text ...` headless.
- Voice command mode: hold `command_hotkey`, speak the instruction; ASR transcribes it, `CommandMode.run(instruction, target_text=selection or last_transcript)` and the result replaces the selection (or inserts).

**Acceptance:** with mocked selection + `MockChatClient`, selection "hey can u fix" + Polish hotkey replaces it with the transformed text and restores the prior clipboard; `auto_transform` ordering covered by a pipeline test; `off`/unset changes nothing.

### Epic 9: Auto-cleanup levels & formatting (Wispr §3.1, §3.5, §2.18)

**Files:**
- Modify: `local_flow/config.py` (`cleanup_level: str = "medium"  # none | light | medium | high`), `local_flow/polish/prompting.py` (per-level system prompts + list-formatting instruction), `local_flow/polish/polisher.py`, `local_flow/polish/rules.py` (spoken code-syntax), `local_flow/app.py` (`history --show/--reinsert-raw`)
- Test: `tests/test_cleanup_levels.py`

**Interfaces:**
- Levels: `none` = verbatim (skip rules *and* LLM), `light` = rules + fillers/grammar-only prompt, `medium` = today's behavior, `high` = rewrite-for-brevity prompt. `TranscriptPolisher(..., level: str = "medium")`.
- List formatting: prompt rule turning spoken enumerations into numbered/bulleted lists (assert on prompt text with `MockChatClient`).
- Spoken code syntax in `rules.py` (deterministic, works with LM Studio down): "camel case order total" → `orderTotal`, "snake case user id" → `user_id`, "all caps api" → `API`.
- Undo AI edit (Wispr keeps the raw dictation): E2 already stores `rough` — `local-flow history --show N` prints rough vs final; `--reinsert-raw N` sends the rough text through the sink. **Blocked by E2.**

**Acceptance:** `cleanup_level=none` never calls the chat client and inserts the verbatim transcript; each level's prompt asserted; code-syntax conversions unit-tested table-driven.

### Epic 10: Context-aware dictation (Wispr §2.2, §2.5 — hardest platform work, do last)

Read the text already in the focused field so polish can continue sentences, match tone, avoid repetition, and spell names that appear nearby.

**Files:**
- Create: `local_flow/context/field_text.py` (`FieldTextProvider` ABC + `MacAXFieldText` (AXUIElement `AXValue`/`AXSelectedTextRange` via pyobjc-framework-ApplicationServices), `WindowsUIAFieldText` (UI Automation TextPattern via comtypes), `NullFieldText`, `MockFieldText`, `create_field_text_provider()`)
- Modify: `local_flow/polish/prompting.py` (context block, capped at 1,000 chars before cursor), `local_flow/pipeline.py`, `local_flow/config.py` (`context_awareness: bool = True`)
- Test: `tests/test_field_context.py`

**Interfaces:**
- `FieldTextProvider.current() -> FieldContext` where `FieldContext = (before_cursor: str, selected: str)`; empty strings whenever the platform can't read the field (Wayland, secure fields, terminals) — pipeline behaves exactly as today.
- Prompt addition: "The user is continuing this existing text: …. Continue naturally; do not repeat it; match its tone; reuse the spelling of any names appearing in it." Context goes only to the local LM Studio server — README privacy note required.

**Acceptance:** with `MockFieldText(before_cursor="Dear Dr. Adithya,")`, the polish prompt contains the context and the name; `NullFieldText` output is byte-identical to today; `context_awareness=false` skips the provider entirely.

### Epic 11: Reliability & microphone management (Wispr §2.8, §2.9, §2.12, §2.13, §2.4, §2.6)

**Files:**
- Create: `local_flow/audio/recovery.py` (`PendingAudioStore`)
- Modify: `local_flow/audio/capture.py` (mic ranking + mid-session fallback), `local_flow/audio/vad.py` (whisper preset), `local_flow/config.py` (`mic_priority: list[str] = []`, `vad_preset: str = "normal"  # normal | whisper`, `max_session_min: int = 20`), `local_flow/app.py` (subcommand `recover`; richer `check` mic diagnostics; `history --retry N`)
- Test: `tests/test_recovery.py`, extend `tests/test_vad.py`

**Interfaces:**
- Mic ranking: `mic_priority` is an ordered list of device-name substrings; `SounddeviceSource` picks the first available match (else system default). On a device error mid-recording it reopens the next-ranked mic and keeps the session's PCM contiguous. `check` lists devices and distinguishes unplugged / in-use / permission-blocked with per-case hints.
- Whisper mode: `vad_preset="whisper"` lowers the energy threshold (~150) and applies peak-normalization gain to each utterance before ASR so whispered speech transcribes.
- Recovery: `PendingAudioStore.save(pcm, sample_rate) -> Path` writes a WAV under `data_dir/pending/` *before* processing; deleted on successful insertion. `local-flow recover` reprocesses every pending WAV through the pipeline (covers crash/quit mid-dictation). Failed LM Studio polish marks the history entry `failed=true`; `history --retry N` re-runs polish+insert.
- Long sessions: warn via `StatusReporter` at `max_session_min - 1` minutes; no hard cap (local = no limit).

**Acceptance:** simulated crash (save → no delete) then `recover` inserts the text and empties `pending/`; mocked device-disappearance mid-record continues on the fallback mic; whisper-preset VAD detects a low-amplitude synthetic utterance that `normal` misses.

### Epic 12: Mouse Flow (Wispr §2.15 — blocked by E1's factory)

**Files:**
- Create: `local_flow/hotkeys/mouse.py` (`MousePushToTalk(HotkeyListener)`)
- Modify: `local_flow/config.py` (`mouse_button: str = ""  # middle | x1 | x2`, `mouse_mode: str = "hold"  # hold | toggle`, `mouse_enter_button: str = ""`), `local_flow/hotkeys/base.py` (factory runs keyboard and mouse listeners side by side when both configured)
- Test: `tests/test_mouse_hotkey.py`

**Interfaces:**
- `MousePushToTalk(button: str, mode: str)` on `pynput.mouse.Listener`; `hold` = press/release like PTT, `toggle` = click-on/click-off. Left/right buttons are rejected with a `ConfigError` (hint: only non-primary buttons). `mouse_enter_button` maps a button to `sink.press_key("enter")` for fully mouse-driven dictate-and-send.

**Acceptance:** with fake mouse events, x1-hold starts/stops recording; toggle mode alternates; configuring `mouse_button="left"` fails with the hint; keyboard hotkey keeps working alongside.

### Epic 13: Scratchpad (Wispr §11 — floating dictation notepad)

**Files:**
- Create: `local_flow/scratchpad/store.py` (`NoteStore`: markdown files under `data_dir/notes/`, one file per tab, autosave, list/create/rename), `local_flow/scratchpad/window.py` (tkinter always-on-top window, lazy import; tabs = note files), `local_flow/scratchpad/sink.py` (`ScratchpadSink(TextSink)` appends into the active note)
- Modify: `local_flow/app.py` (subcommand `pad`, `pad --append TEXT` headless; run loop toggles the window on `scratchpad_hotkey`), `local_flow/config.py` (`scratchpad_hotkey: str = "f7"`)
- Test: `tests/test_scratchpad_store.py` (store + sink headless; window is manual-test only)

**Interfaces:**
- `NoteStore.append(text: str) -> None` targets the active note; notes are plain markdown you can open in any editor (keeps the hand-editable ethos — this is our "tabs + history" answer without a rich-text engine).
- While the scratchpad window has focus, dictation routes to `ScratchpadSink` instead of the desktop sink (frontmost-app check reuses E4's provider when present, else window-focus flag).

**Acceptance:** `pad --append "idea"` creates/extends today's note file headless; hotkey toggles the window (manual); dictating with the pad focused lands in the note, not the previous app.

### Epic 14: Personal insights (Wispr §12.1 — blocked by E2; richer with E4)

**Files:**
- Create: `local_flow/insights/stats.py` (`compute_stats(records: Iterable[HistoryRecord], now: datetime) -> Stats`)
- Modify: `local_flow/app.py` (subcommand `stats [--since 30d]`)
- Test: `tests/test_insights.py`

**Interfaces:**
- `Stats`: total words, average words/min (words ÷ `duration_s` from the E2 addendum), words "cleaned up" (rough-vs-final word delta), smart replacements (`replacements` field), top-5 apps (`app` field, "(unknown)" bucket pre-E4), current/longest daily streak.
- Output: plain terminal report + an ASCII month heatmap for the streak. Purely local; no sharing/cards.

**Acceptance:** seeded records produce deterministic output (fixed `now` injected); empty history prints a friendly zero-state; missing `duration_s` (old records) degrades to word counts only.

### Epic 15: File transcription — `local-flow transcribe` (beyond Wispr: its catalog §18.1 lists this as unsupported)

Transcribe existing audio files (meeting recordings, voice memos, lectures) with the same local pipeline. No dependencies on other epics.

**Files:**
- Modify: `local_flow/app.py` (subcommand `transcribe`), `local_flow/asr/faster_whisper_asr.py` (`transcribe_path(path: Path) -> str`), `local_flow/asr/mock.py` (mock `transcribe_path` reads WAV via stdlib `wave` so tests stay headless)
- Test: `tests/test_transcribe.py`

**Interfaces:**
- CLI: `local-flow transcribe FILE... [--polish] [--copy] [--language XX]`. Output to stdout; `--copy` also places it on the clipboard (reuse `ClipboardOnlySink`); `--polish` runs the text half of the pipeline (`DictationPipeline.process_transcript` minus insertion — rules, LM Studio, dictionary, snippets) instead of printing the raw transcript.
- Format support comes free: faster-whisper's `model.transcribe()` accepts a file path and decodes via its bundled PyAV — WAV, MP3, M4A, FLAC, OGG, any sample rate — so the real adapter passes the path straight through rather than converting to PCM. Do **not** hand-roll resampling (stdlib `audioop` is gone in Python 3.13).
- Multiple files process sequentially with a `== filename ==` header per file; per-segment progress printed to stderr for long recordings.

**Tasks:**
- [ ] Write per-epic detailed plan
- [ ] `transcribe_path` on the real and mock adapters (mock: stdlib `wave` → existing `transcribe`)
- [ ] `transcribe` subcommand with `--polish` / `--copy` / `--language`; missing-file and unsupported-format failures raise `LocalFlowError` with hints
- [ ] README section ("transcribe a voice memo → polished notes")

**Acceptance:** headless test generates a tiny WAV, mock adapter returns its canned text through the subcommand; `--polish` asserted via `MockChatClient`; manual: `local-flow transcribe memo.m4a --polish > notes.md`.

---

## Self-Review (done)

1. **Spec coverage:** every gap from the architecture review maps to an epic — hotkeys E1 (Fn default + Space option + chord stretch), history/notes E2, multilingual E3, context awareness E4, auto-learning E5, GUI/onboarding E6, streaming E7; sync/team/mobile are explicit non-goals.
2. **Placeholder scan:** no TBDs; each epic names exact files, signatures, config keys, storage formats, and platform mechanisms. Full step-level code intentionally lives in the per-epic plans (scope-check decision recorded at top).
3. **Type consistency:** `HistoryRecord.app` (E2) is filled by E4's `AppInfo.app_id`; `StatusReporter` (E6) is consumed by E7's `live-preview` and E11's warnings; `HotkeyListener.run` signature unchanged across E1/E12. Dependencies: E5→E2, E9→E2 (undo only), E12→E1, E14→E2 (E4 soft), E7→E6 (soft); all else independent.
4. **Wave 2 coverage (Wispr Pro catalog):** every single-user catalog item maps to an epic, an epic addendum (marked *(Wispr §…)* in E1/E2/E4/E5/E6), or the non-goals list. Deliberately skipped: teams (§7), enterprise security/admin (§8–9), cloud sync & cross-device (§10), mobile-only UX (§2.10–2.14, §14.5–14.8), workflows gallery (§16), support tiers (§17), personalized speech models (§18.12 — research, not a feature).
