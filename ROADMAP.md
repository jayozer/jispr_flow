# local-flow Roadmap

Potential add-on features beyond the MVP, grounded in the adapter
architecture (see [docs/architecture.md](docs/architecture.md)): every item
below is either a new adapter behind an existing interface, a new stage in
the pipeline, or product surface around it. Nothing here may compromise the
core constraint: **everything runs locally; audio and text never leave the
machine.**

**Status (2026-07-09):** the Wispr-Pro parity build (epics E1–E15, tracked
in [TODO.md](TODO.md)) shipped a large slice of this roadmap, plus several
features it never listed — those are checked off below with the shape they
actually shipped in, and the extras are added as checked entries marked
*(beyond the original roadmap)*. The branch was then hardened by a
34-finding max-effort code review (TODO.md, groups A–H, all fixed).
Partially-shipped items stay unchecked with a note on what landed.

Legend: 🟢 near-term (high value, fits current architecture) ·
🟡 mid-term (needs new surface or platform work) ·
🔵 exploratory (research or major scope).

## 1. Capture & hotkeys

- [ ] 🟢 **Chord hotkeys** — support modifier combos (`cmd+shift+space`), not
      just a single key. Biggest known MVP limitation. *(Still open — E1
      stretch goal; also on the TODO.md backlog.)*
- [ ] 🟢 **Toggle mode** — tap once to start, tap again to stop, alongside
      hold-to-talk; friendlier for long dictations. *(Partly shipped:
      `mouse_mode = "toggle"` gives click-on/click-off for mouse buttons;
      keyboard hotkeys are still hold-to-talk.)*
- [x] ✅ **Separate command-mode hotkey** — shipped as `command_hotkey` (E8):
      hold a second key and *speak* an edit instruction, applied to the
      current selection or the last dictation.
- [x] ✅ **Mouse-button push-to-talk** *(beyond the original roadmap, E12)* —
      bind middle/x1/x2 to record (`mouse_button`, hold or toggle), plus an
      optional mouse Enter button (`mouse_enter_button`).
- [ ] 🟡 **Double-tap modifier trigger** — e.g. double-tap Fn/Ctrl to start,
      like most commercial dictation tools. (Hold-Fn is the macOS default
      hotkey; double-tap is still open.)
- [ ] 🟡 **Audio cues** — soft start/stop chimes so you know recording state
      without looking. (The tray icon shows recording state visually.)
- [ ] 🟡 **Mute/pause word** — a spoken keyword ("stop listening") that pauses
      hands-free mode.

## 2. ASR (Transcriber adapters)

### Completed goal — Near-term ASR milestone (2026-07-09)

```text
/goal Deliver the near-term ASR milestone on branch `asr-work`: add dynamic
custom-vocabulary boosting to faster-whisper, build a repeatable ASR benchmark
harness, and evaluate MLX Whisper on Apple Silicon, shipping an opt-in MLX
adapter only if the evidence justifies it. Done only when current dictionary
terms (including terms added without restarting) reach live and file
transcription through a bounded `initial_prompt`; the benchmark reports model
load time, per-run latency, median/p95 latency, real-time factor, transcript,
and WER when references are supplied in both human-readable and JSON forms;
and `docs/asr/MLX_EVALUATION.md` records reproducible commands, environment,
at least three representative local audio samples, results, and a clear
ship/defer decision. Ship MLX only if it improves median transcription latency
by at least 20% without worsening aggregate WER by more than 2 absolute points;
otherwise keep the evaluation and explicitly defer the adapter. Prove completion
by showing the real benchmark output plus `uv run pytest`,
`uv run ruff check .`, `uv run local-flow demo`, and `git diff --check` all
passing in the conversation. Constraints: preserve faster-whisper as the
default; keep every backend optional, lazy-imported, local-only, and mockable;
keep headless tests free of models, microphones, displays, and network; do not
implement the deferred Future ASR items or product-surface work; do not commit
user audio, secrets, model caches, or generated benchmark files; do not push,
merge, or deploy; make reasonable choices and record assumptions instead of
asking clarifying questions. Stop after 40 goal turns if not met and report
verified progress, blockers, and remaining work.
```

- [x] ✅ **Multilingual models** — shipped (E3): non-`.en` models with
      `asr_language` pinning or `"auto"` detection, config validation with
      actionable hints, and a tray Language quick-switch menu (`languages`).
- [x] ✅ **Custom vocab boosting** — current prioritized dictionary terms feed
      a bounded Whisper `initial_prompt` before every live or file call, so
      additions take effect without restarting; canonical enforcement remains
      as a post-transcription safety net.
- [x] ✅ **ASR benchmark harness** — `local-flow benchmark-asr` runs repeatable
      local corpora with model-load time, per-run and median/p95 latency,
      real-time factor, transcripts, optional reference/WER scoring, and
      human-readable plus JSON output.
- [x] ✅ **MLX Whisper evaluation** — the Apple-Silicon comparison cleared the
      controlled vocabulary-aware gate (89.3% lower aggregate median latency,
      no aggregate WER regression), so `mlx-whisper` shipped as an opt-in
      adapter while faster-whisper remains the default. See
      [the evaluation](docs/asr/MLX_EVALUATION.md).
- [x] ✅ **Streaming transcription** — shipped (E7): `streaming = "sentence"`
      inserts sentence chunks while you keep talking in hands-free mode;
      `"live-preview"` shows rough text as you speak; `"off"` stays
      byte-identical to the non-streaming pipeline.
- [x] ✅ **File transcription** *(beyond the original roadmap, E15)* —
      `local-flow transcribe memo.m4a --polish`: run existing audio files
      (WAV/MP3/M4A/FLAC) through the same local pipeline.

## 3. VAD & audio

- [ ] 🟢 **Silero VAD adapter** — ML-based VAD, much more robust than energy
      threshold in noisy rooms; one new `VoiceActivityDetector` class.
      *(A webrtcvad backend and a `vad_preset = "whisper"` low-volume preset
      shipped; Silero itself is still open.)*
- [x] ✅ **Input device selection** — shipped (E11) as `mic_priority` (ranked
      preferred-device names chosen at selection time) plus mic diagnostics
      in `local-flow check`.
- [ ] 🟡 **Auto gain / noise calibration** — measure ambient noise on startup
      and set the energy threshold automatically. *(Partly shipped: peak
      gain normalization boosts quiet audio under `vad_preset = "whisper"`;
      ambient-noise auto-thresholding is still open.)*
- [ ] 🔵 **Wake-word activation** — "hey flow" to start hands-free capture,
      fully local (openWakeWord).

## 4. Polish & LLM

**LM Studio is the primary and default backend.** Presets, prompts, and
testing target LM Studio first; other local servers are secondary
conveniences and must never dilute the LM Studio experience.

- [ ] 🟢 **LM Studio model presets** — curated defaults for recommended
      LM Studio models (Qwen, Llama, Phi) with tested polish prompts,
      timeouts, and context settings per model family.
- [ ] 🟡 **Ollama support (secondary)** — Ollama is OpenAI-compatible, so
      support is mostly documentation plus tested defaults
      (`http://localhost:11434/v1`); keep the cloud-endpoint refusal list.
- [ ] 🟢 **Custom prompt templates** — user-editable polish/command prompts in
      the data dir, like styles.
- [x] ✅ **Polish intensity levels** — shipped (E9) as
      `cleanup_level = none | light | medium | high` (`none` inserts the
      verbatim transcript with zero LLM calls), plus spoken-list formatting
      and spoken code syntax (camelCase / snake_case / ALL CAPS).
- [x] ✅ **Per-app styles** — shipped (E4): `app_styles.json` switches style
      *and* insert method by frontmost app (Slack casual, Mail formal,
      typing sink in terminals), with built-in email/chat styles.
- [x] ✅ **Context-aware polish** — shipped (E10): reads the text already in
      the focused field (macOS AX; Windows UIA is a documented stub) so
      polish continues sentences, matches tone, and spells nearby names
      correctly. On by default (`context_awareness`), local-only.
- [x] ✅ **Selection transforms + voice command mode** *(beyond the original
      roadmap, E8)* — highlight text anywhere → `transform_hotkey` → AI
      rewrite in place; built-in Polish & Prompt Engineer plus unlimited
      custom transforms in `transforms.json`; optional `auto_transform`
      after every dictation.
- [x] ✅ **Auto-learning dictionary** — shipped (E5): `local-flow learn`
      mines history for recurring terms, `--add` writes them, spoken
      "add X to dictionary" works mid-dictation; starred terms and
      usage-based ranking.

## 5. Personalization

- [ ] 🟢 **CLI management commands** — `local-flow dict add/list/rm`,
      `snippet add`, `style set` instead of hand-editing JSON. *(Partly
      shipped: `local-flow learn --add` and the spoken command cover
      dictionary additions; snippet/style CRUD is still hand-edited JSON.)*
- [ ] 🟢 **Import/export** — single-file backup/restore of dictionary,
      snippets, and styles.
- [ ] 🟡 **Dynamic snippet variables** — `{date}`, `{clipboard}`, cursor
      placement markers in expansions.
- [ ] 🟡 **espanso interop** — import triggers from an existing espanso
      config.
- [ ] 🔵 **Git-friendly sync** — data dir designed for syncing across
      machines via git/Syncthing (conflict-tolerant formats).

## 6. Insertion (TextSink adapters)

- [ ] 🟢 **Clipboard preservation** — save and restore the user's clipboard
      around a paste insertion. *(Partly shipped: transforms save/restore
      the clipboard, including non-text content via NSPasteboard; the
      dictation paste sink still overwrites it.)*
- [ ] 🟢 **Undo last insertion** — hotkey or `local-flow undo` that removes
      the last inserted text (send the right number of backspaces or
      platform undo). *(Related but different: `history --reinsert-raw N`
      undoes a bad AI edit by re-inserting the raw transcript.)*
- [ ] 🟡 **macOS AX API sink** — insert via Accessibility APIs directly at
      the cursor, no clipboard involved. (AX is currently used read-only
      for field context, E10.)
- [ ] 🟡 **Wayland sinks** — `wtype` / `ydotool` adapters so Linux Wayland
      gets real insertion instead of clipboard-only. (The clipboard-only
      fallback chain wl-copy → xclip → xsel is in place and hardened.)
- [ ] 🟡 **Windows UIA sink** — direct text insertion via UI Automation.

## 7. Dictation commands

- [ ] 🟢 **Richer command set** — "delete that", "undo", "all caps",
      "quote ... end quote", spoken punctuation ("comma", "period") for
      polish-level *none*. *(Partly shipped: "new line" / "new paragraph",
      trailing "press enter", "scratch that" backtracking, spoken lists,
      and "camel case / snake case / all caps X" all work; spoken
      punctuation and quote…end quote are still open.)*
- [ ] 🟢 **Spelling mode** — "spell it: J-i-S-p-r" produces exact strings the
      LLM must not touch.
- [ ] 🟡 **Cursor/selection commands** — "select last sentence",
      "go to end of line" (needs the AX/UIA sinks above).
- [ ] 🔵 **App control commands** — "switch to browser", "save the file";
      deliberate scope expansion beyond dictation.

## 8. History & feedback

- [x] ✅ **Local history (opt-in)** — shipped (E2) as append-only JSONL
      (not SQLite): `local-flow history --search/--show/--clear`, retention
      `forever | 24h | off`, records duration and replacement counts, and
      re-use by index via `--reinsert-raw N` / `--retry N`.
- [x] ✅ **Stats** — shipped (E14): `local-flow stats` reports words,
      words/min, cleanup delta, smart replacements, top apps, and a streak
      heatmap.
- [ ] 🟡 **Latency breakdown** — per-stage timing (record → ASR → polish →
      insert) surfaced after each dictation in verbose mode; doubles as a
      benchmark harness.

## 9. Product surface

**Prioritized:** the product surface comes first so the app can be tested
as a real desktop tool (not just a CLI) from early on.

- [x] ✅ **Menu bar / tray app** — shipped (E6, pystray): recording states on
      the icon, style and language quick-switch menus, desktop
      notifications, `local-flow tray`.
- [ ] 🟢 **Floating recording pill** — small always-on-top indicator with
      mic level while recording. (The tray icon covers recording state; a
      pill with live mic level is still open.)
- [x] ✅ **Onboarding wizard** — shipped (E6): `local-flow setup` writes a
      validated config interactively, probes LM Studio connectivity, and
      prints the macOS permission steps; `local-flow check` diagnoses
      LM Studio / ASR / audio / clipboard. *(First-run model download with
      a progress bar is still open.)*
- [x] ✅ **Scratchpad** *(beyond the original roadmap, E13)* — floating
      always-on-top markdown notepad (`local-flow pad --window`), plain
      files under the data dir, and a `scratchpad_hotkey` that routes live
      dictation into the active note.
- [ ] 🟡 **Settings UI** — edit config/dictionary/snippets/styles visually
      (local web page served on localhost, or native).
- [ ] 🟡 **Packaged distribution** — signed .app / Homebrew formula /
      PyInstaller binaries; auto-start at login.

## 10. Engineering & quality

- [x] ✅ **Crash-safe recovery** *(beyond the original roadmap, E11)* —
      audio autosave to `pending/`, `local-flow recover` replays saved
      WAVs in order without needing a microphone, failed-polish retry,
      long-utterance warning.
- [ ] 🟢 **Synthetic-speech e2e test** — CI-optional test that generates audio
      with macOS `say` and runs the real ASR pipeline (proven manually
      during MVP testing).
- [ ] 🟢 **HF_TOKEN & offline model docs** — document authenticated downloads
      and fully offline model installation paths. *(Partly shipped: the
      README documents pointing `asr_model` at a local CTranslate2 model
      dir for offline installs; HF_TOKEN flows are undocumented.)*
- [ ] 🟡 **Latency benchmark suite** — track per-stage regressions across
      model/backend choices. *(Partly shipped: `benchmark-asr` compares ASR
      backends; end-to-end pipeline stage timing remains open.)*
- [ ] 🟡 **Platform CI matrix** — macOS/Linux/Windows smoke tests for the
      import-level platform isolation guarantees. (No hosted CI is
      configured yet at all — the suite runs locally via `uv run pytest`.)
- [ ] 🔵 **Plugin system** — third-party adapters (Transcriber/VAD/Sink)
      discoverable via entry points.

## Future

Deferred ASR expansion stays out of the near-term milestone until benchmark
evidence or supported-platform demand justifies the added maintenance surface.

- [ ] **whisper.cpp adapter** — portable CPU/quantized alternative for
      machines where CTranslate2 or MLX is a poor fit.
- [ ] **Parakeet / NVIDIA NeMo adapter** — high-throughput local ASR for
      supported NVIDIA GPU systems.
- [ ] **Local translation dictation** — speak one language and insert another
      through a local Whisper translation task or LM Studio translation step.

## Suggested sequencing

1. **Now** — chord hotkeys (the one remaining headline gap), LM Studio model
   presets, clipboard
   preservation around the dictation paste sink, CLI personalization
   commands, Silero VAD, spoken punctuation for `cleanup_level = none`,
   keyboard toggle mode.
2. **Next** — settings UI, packaged distribution, Ollama docs, per-dictation
   latency breakdown, import/export, dynamic snippet variables, audio cues,
   floating recording pill, synthetic-speech e2e test.
3. **Later** — macOS AX / Wayland / Windows UIA sinks and the
   cursor/selection commands they unlock, wake word, spelling mode,
   app-control commands, platform CI matrix, plugin system, and Future items
   when their gates are met.
