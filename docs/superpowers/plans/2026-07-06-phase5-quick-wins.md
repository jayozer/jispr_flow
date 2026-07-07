# Phase 5: Quick Wins — E9 Cleanup Levels, E11 Reliability & Mics, E12 Mouse Flow, E15 File Transcription

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Six tasks; order T1→T6 (T1/T2 = E9, T3/T4 = E11, T5 = E12, T6 = E15). Independent epics — a task failure doesn't block the others beyond its pair.

**Goal:** Four self-contained Wispr-parity features: auto-cleanup levels with undo, crash-safe audio recovery + mic management + whisper mode, mouse-button push-to-talk, and audio-file transcription (a feature Wispr itself lacks).

## Global Constraints

Standard set (local-only, lazy platform imports, Config/env/TOML pattern with validation-and-hint like `streaming`, mocks + headless tests, suite+ruff green per commit, ≤100 cols, docs updated per task: README + `.env.example` + `local-flow.example.toml` whenever a config key is added).

---

### Task 1 (E9): Cleanup levels

**Files:** `local_flow/config.py` (`cleanup_level: str = "medium"`, validated none|light|medium|high), `local_flow/polish/prompting.py` (per-level system prompts + a list-formatting instruction in all LLM levels), `local_flow/polish/polisher.py` (`TranscriptPolisher(..., level: str = "medium")`, settable property like `style`), `local_flow/app.py` (builders pass level), README/env/toml; tests `tests/test_cleanup_levels.py`.

**Semantics (binding):**
- `none`: verbatim — `polish()` returns the raw text untouched: NO rule cleanup, NO chat-client call (assert the client is never called). Dictionary/snippets/dictation-commands/spoken-adds still run in the pipeline (they are personalization, not cleanup) — document this in README.
- `light`: rule cleanup + LLM with a fillers/grammar-only prompt ("fix grammar and remove fillers; do not rephrase").
- `medium`: today's behavior byte-identical (existing prompt).
- `high`: rewrite-for-brevity prompt ("rewrite for concision and polish while preserving meaning").
- All LLM prompts (light/medium/high) additionally instruct: spoken enumerations become proper numbered/bulleted lists.
- LLM-down degradation unchanged: light/medium/high fall back to rules-only.

**Tests:** level=none → chat client not called, output == input verbatim; per-level prompt content asserted via MockChatClient; medium prompt unchanged from the current constant (pin it); property setter affects next polish; config validation.

- [ ] TDD → commit `feat(polish): cleanup levels none/light/medium/high with list formatting`

### Task 2 (E9): Spoken code syntax + undo AI edit

**Files:** `local_flow/polish/rules.py` (`apply_spoken_code_syntax(text) -> tuple[str, int]`: "camel case order total" → `orderTotal`, "snake case user id" → `user_id`, "all caps api" → `API`; phrase followed by 1–4 words; case-insensitive trigger; count returned), `local_flow/pipeline.py` (runs in the rules stage — inside/right after rule cleanup so it works LLM-down; skipped entirely when `cleanup_level == "none"`), `local_flow/app.py` (`history --show N` prints the full rough AND final of record N (1-based from the listing order, same ordering contract as `--add` in learn); `--reinsert-raw N` sends record N's rough through `_build_sink(config)` and reports), README; tests in `tests/test_polish_rules.py` + `tests/test_demo_and_cli.py`.

**Tests:** table-driven conversions (multi-word, single word, mixed case input, trigger absent → unchanged, count); pipeline integration LLM-down; `--show`/`--reinsert-raw` against a seeded history (incl. out-of-range N friendly error).

- [ ] TDD → commit `feat(polish): spoken code syntax and history undo/reinsert-raw`

### Task 3 (E11): Crash-safe audio + retry

**Files:** create `local_flow/audio/recovery.py` (`PendingAudioStore(data_dir)`: `save(pcm: bytes, sample_rate: int) -> Path` writes a WAV under `data_dir/pending/` with a unique timestamp-free name (uuid4 hex — no clock dependence), `delete(path)`, `pending() -> list[Path]`, `load(path) -> tuple[bytes, int]` via stdlib `wave`); modify `local_flow/history/store.py` (`HistoryRecord.failed: bool = False` — tolerant like other fields), `local_flow/pipeline.py` (record `failed=True` when a chat client is configured but `used_llm` is False), `local_flow/app.py` (`_handle_utterance` saves before `process_audio`, deletes on normal return — wire the store only when built (`history_enabled` irrelevant; gate on a new config `audio_recovery: bool = True`); subcommand `recover` reprocessing every pending WAV through the pipeline (delete each on success, keep on failure, print summary); `history --retry N` re-runs record N's rough through `process_transcript` (fresh polish + insert)), README/env/toml; tests `tests/test_recovery.py`.

**Tests:** save/load WAV round-trip (bytes + rate); simulated crash (save, no delete) → `recover` with mock pipeline inserts and empties pending/; failure keeps the file; `failed` flag set when LLM configured-but-down (MockChatClient raising vs `chat_client=None` — None means not configured → failed stays False); `--retry` happy + out-of-range.

- [ ] TDD → commit `feat(reliability): crash-safe audio autosave, recover command, polish retry`

### Task 4 (E11): Mic priority + whisper preset + long-utterance warning

**Files:** `local_flow/config.py` (`mic_priority: str = ""` comma-separated name substrings; `vad_preset: str = "normal"` validated normal|whisper; `max_utterance_min: int = 20`), `local_flow/audio/capture.py` (`SounddeviceSource(..., preferred: list[str] | None = None)`: pick the first input device whose name contains a preferred substring (case-insensitive, priority order), else default; expose `chosen_device_name` for check; on open failure of the chosen device, fall back down the priority list then default before erroring — mid-stream fallback is OUT of scope v1, document), `local_flow/audio/vad.py` or the builder (`vad_preset="whisper"` → energy threshold 150 unless explicitly overridden by env/file — implement in `_build_vad`: preset applies only when the user hasn't set `vad_energy_threshold` explicitly; simplest honest rule: preset multiplies default only, document precedence), peak normalization helper `normalize_peak(pcm: bytes, target=0.9) -> bytes` in `local_flow/audio/vad.py` or new `audio/gain.py` applied to utterance PCM before ASR when preset=whisper (wire in `_handle_utterance`/pipeline), `local_flow/app.py` (`check` lists input devices marking the selected one; parse_mic_priority helper; long-utterance warning: after processing, if `result` duration exceeds `max_utterance_min` minutes → `reporter.notify("warning", ...)`), README/env/toml; tests: pure helpers (device pick from a fake device list, normalize_peak math incl. silence and clipping, preset threshold resolution), warning trigger with a fake long duration.

- [ ] TDD → commit `feat(audio): mic priority, whisper-mode preset, long-utterance warning`

### Task 5 (E12): Mouse Flow

**Files:** create `local_flow/hotkeys/mouse.py` (`MousePushToTalk(HotkeyListener)`: `button: str` middle|x1|x2, `mode: str` hold|toggle; pure `MouseToggleMachine` for toggle state (click → start, click → stop; pressed/released of OTHER buttons ignored); pynput.mouse lazy import; hold mode uses `PushToTalkCore`; cancel key NOT handled here (keyboard cancel still works via the keyboard listener — document)); `local_flow/config.py` (`mouse_button: str = ""`, `mouse_mode: str = "hold"` validated, `mouse_enter_button: str = ""`); `local_flow/hotkeys/base.py` (factory-level helper `create_mouse_listener(config) -> HotkeyListener | None`, rejecting left/right with ConfigError + hint); `local_flow/app.py` (`_run_loop` PTT branch: when `mouse_button` set, run the mouse listener on a DAEMON THREAD alongside the blocking keyboard listener, sharing the same dispatcher-wrapped callbacks — both feed the same start/finish/cancel, and simultaneous use is the user's own foot-gun, document; `mouse_enter_button` maps a click of that button to `sink.press_key("enter")` via a dispatcher-wrapped callback), README/env/toml; tests `tests/test_mouse_hotkey.py` (toggle machine pure; button resolution incl. left/right rejection; factory returns None when unset).

- [ ] TDD → commit `feat(hotkeys): mouse-button push-to-talk with hold and toggle modes`

### Task 6 (E15): `local-flow transcribe`

**Files:** `local_flow/asr/faster_whisper_asr.py` (`transcribe_path(self, path: Path) -> str` passing the path straight to `model.transcribe` — faster-whisper's bundled PyAV decodes WAV/MP3/M4A/FLAC at any sample rate; do NOT hand-roll resampling), `local_flow/asr/mock.py` (`transcribe_path` reads WAV via stdlib `wave` and delegates to `transcribe`), `local_flow/app.py` (subcommand `transcribe FILE... [--polish] [--copy] [--language XX]`: per-file `== name ==` header when multiple files; output to stdout; `--copy` additionally puts the LAST file's text on the clipboard via `ClipboardOnlySink` (lazy import); `--polish` runs the text half — polisher + dictionary + snippets + commands — WITHOUT insertion (reuse the `_cmd_polish` composition, not `process_transcript`, to avoid sink/history side effects; note the known `_cmd_polish` drift backlog item — mirror whatever `_cmd_polish` does today); `--language` overrides `config.asr_language` for this invocation (still validated against `.en` models); missing file / unsupported extension → `LocalFlowError` with hint), README ("transcribe a voice memo → polished notes" example); tests `tests/test_transcribe.py` (headless: generate a tiny WAV with `wave`, mock backend; `--polish` asserted via MockChatClient; multi-file headers; missing-file error).

- [ ] TDD → commit `feat(transcribe): transcribe audio files through the local pipeline`

## Manual checklist

1. `LOCAL_FLOW_CLEANUP_LEVEL=none` — dictation inserts verbatim (fillers kept).
2. "camel case order total" → `orderTotal` inserted (LM Studio stopped).
3. Kill the app mid-dictation → `local-flow recover` inserts the lost utterance.
4. `LOCAL_FLOW_MIC_PRIORITY="AirPods"` with AirPods connected → check names them selected.
5. `LOCAL_FLOW_VAD_PRESET=whisper` — whispered dictation transcribes in hands-free mode.
6. `LOCAL_FLOW_MOUSE_BUTTON=x1` with a side-button mouse — hold-to-talk works alongside Fn.
7. `local-flow transcribe memo.m4a --polish` produces polished notes.

## Self-Review (done)

E9's `none` semantics documented (personalization still applies); E11's recovery hooks sit in `_handle_utterance` so tray + CLI both benefit; E12 reuses `PushToTalkCore`/dispatcher contracts from E1; E15 reuses `_cmd_polish` composition and flags the known drift backlog. No cross-epic interfaces beyond `HistoryRecord.failed` (E11-T3) which the tolerant reader already accommodates.
