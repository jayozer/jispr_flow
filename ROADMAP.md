# local-flow Roadmap

Potential add-on features beyond the MVP, grounded in the adapter
architecture (see [docs/architecture.md](docs/architecture.md)): every item
below is either a new adapter behind an existing interface, a new stage in
the pipeline, or product surface around it. Nothing here may compromise the
core constraint: **everything runs locally; audio and text never leave the
machine.**

Legend: 🟢 near-term (high value, fits current architecture) ·
🟡 mid-term (needs new surface or platform work) ·
🔵 exploratory (research or major scope).

## 1. Capture & hotkeys

- [ ] 🟢 **Chord hotkeys** — support modifier combos (`cmd+shift+space`), not
      just a single key. Biggest known MVP limitation.
- [ ] 🟢 **Toggle mode** — tap once to start, tap again to stop, alongside
      hold-to-talk; friendlier for long dictations.
- [ ] 🟢 **Separate command-mode hotkey** — one key dictates, another treats
      the utterance as an instruction over the current selection.
- [ ] 🟡 **Double-tap modifier trigger** — e.g. double-tap Fn/Ctrl to start,
      like most commercial dictation tools.
- [ ] 🟡 **Audio cues** — soft start/stop chimes so you know recording state
      without looking.
- [ ] 🟡 **Mute/pause word** — a spoken keyword ("stop listening") that pauses
      hands-free mode.

## 2. ASR (Transcriber adapters)

- [ ] 🟢 **Multilingual models** — allow non-`.en` Whisper models, language
      pinning (`LOCAL_FLOW_ASR_LANGUAGE`) and auto-detection.
- [ ] 🟢 **Custom vocab boosting** — feed dictionary terms into Whisper's
      `initial_prompt` so canonical terms are recognized, not just enforced
      after the fact.
- [ ] 🟢 **whisper.cpp adapter** — alternative backend for machines where
      CTranslate2 is awkward; one new `Transcriber` class.
- [ ] 🟡 **MLX Whisper adapter** — Apple-Silicon-native backend (the primary
      dev machine is an M-series Mac; LM Studio there already runs MLX).
- [ ] 🟡 **Streaming transcription** — partial results while speaking instead
      of record-then-transcribe; needs incremental decoding and a preview UI.
- [ ] 🟡 **Parakeet / NVIDIA NeMo adapter** — faster-than-Whisper local ASR
      option on GPU machines.
- [ ] 🔵 **Local translation dictation** — speak one language, insert another
      (Whisper translate task or LM Studio translation step).

## 3. VAD & audio

- [ ] 🟢 **Silero VAD adapter** — ML-based VAD, much more robust than energy
      threshold in noisy rooms; one new `VoiceActivityDetector` class.
- [ ] 🟢 **Input device selection** — `LOCAL_FLOW_AUDIO_DEVICE` plus a
      `local-flow devices` listing command.
- [ ] 🟡 **Auto gain / noise calibration** — measure ambient noise on startup
      and set the energy threshold automatically.
- [ ] 🔵 **Wake-word activation** — "hey flow" to start hands-free capture,
      fully local (openWakeWord).

## 4. Polish & LLM

- [ ] 🟢 **Ollama / llama.cpp presets** — they're OpenAI-compatible, so mostly
      documentation plus tested defaults (`http://localhost:11434/v1`);
      keep the cloud-endpoint refusal list.
- [ ] 🟢 **Custom prompt templates** — user-editable polish/command prompts in
      the data dir, like styles.
- [ ] 🟢 **Polish intensity levels** — `none | light | full` per dictation
      (light = punctuation/casing only), selectable via config or spoken tag.
- [ ] 🟡 **Per-app styles** — detect the frontmost application and switch
      style automatically (formal in Mail, casual in Slack). Needs an
      active-window adapter per platform.
- [ ] 🟡 **Context-aware polish** — optionally include the current selection
      or clipboard as context so the LLM matches surrounding tone. Opt-in,
      local-only.
- [ ] 🔵 **Auto-learning dictionary** — notice words the user repeatedly
      corrects and suggest dictionary entries (all inference local).

## 5. Personalization

- [ ] 🟢 **CLI management commands** — `local-flow dict add/list/rm`,
      `snippet add`, `style set` instead of hand-editing JSON.
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
      around a paste insertion.
- [ ] 🟢 **Undo last insertion** — hotkey or `local-flow undo` that removes
      the last inserted text (send the right number of backspaces or
      platform undo).
- [ ] 🟡 **macOS AX API sink** — insert via Accessibility APIs directly at
      the cursor, no clipboard involved.
- [ ] 🟡 **Wayland sinks** — `wtype` / `ydotool` adapters so Linux Wayland
      gets real insertion instead of clipboard-only.
- [ ] 🟡 **Windows UIA sink** — direct text insertion via UI Automation.

## 7. Dictation commands

- [ ] 🟢 **Richer command set** — "delete that", "undo", "all caps",
      "quote ... end quote", spoken punctuation ("comma", "period") for
      polish-level *none*.
- [ ] 🟢 **Spelling mode** — "spell it: J-i-S-p-r" produces exact strings the
      LLM must not touch.
- [ ] 🟡 **Cursor/selection commands** — "select last sentence",
      "go to end of line" (needs the AX/UIA sinks above).
- [ ] 🔵 **App control commands** — "switch to browser", "save the file";
      deliberate scope expansion beyond dictation.

## 8. History & feedback

- [ ] 🟢 **Local history (opt-in)** — SQLite log of rough/final transcripts;
      `local-flow history`, re-insert by index, full-text search. Plain
      local file, easy to purge.
- [ ] 🟡 **Stats** — words dictated, session WPM, LM Studio latency; a
      `local-flow stats` command.
- [ ] 🟡 **Latency breakdown** — per-stage timing (record → ASR → polish →
      insert) surfaced after each dictation in verbose mode; doubles as a
      benchmark harness.

## 9. Product surface

- [ ] 🟡 **Menu bar / tray app** — recording indicator, mode toggle, style
      picker, last-transcript view (rumps on macOS, pystray elsewhere).
      Biggest single UX upgrade over the bare CLI.
- [ ] 🟡 **Floating recording pill** — small always-on-top indicator with
      mic level while recording.
- [ ] 🟡 **Onboarding wizard** — guided macOS permission setup (Microphone /
      Accessibility / Input Monitoring), first-run model download with
      progress, LM Studio connectivity check.
- [ ] 🟡 **Settings UI** — edit config/dictionary/snippets/styles visually
      (local web page served on localhost, or native).
- [ ] 🔵 **Packaged distribution** — signed .app / Homebrew formula /
      PyInstaller binaries; auto-start at login.

## 10. Engineering & quality

- [ ] 🟢 **Synthetic-speech e2e test** — CI-optional test that generates audio
      with macOS `say` and runs the real ASR pipeline (proven manually
      during MVP testing).
- [ ] 🟢 **HF_TOKEN & offline model docs** — document authenticated downloads
      and fully offline model installation paths.
- [ ] 🟡 **Latency benchmark suite** — track per-stage regressions across
      model/backend choices.
- [ ] 🟡 **Platform CI matrix** — macOS/Linux/Windows smoke tests for the
      import-level platform isolation guarantees.
- [ ] 🔵 **Plugin system** — third-party adapters (Transcriber/VAD/Sink)
      discoverable via entry points.

## Suggested sequencing

1. **Now** — chord hotkeys, toggle mode, Silero VAD, vocab boosting via
   `initial_prompt`, clipboard preservation, CLI personalization commands,
   polish intensity levels.
2. **Next** — menu bar app + recording indicator, local history, per-app
   styles, multilingual ASR, MLX Whisper, onboarding wizard.
3. **Later** — streaming transcription, Wayland/UIA sinks, settings UI,
   packaged distribution, wake word, plugin system.
