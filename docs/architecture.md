# Architecture

local-flow is a pipeline of small adapters. Each stage has one interface,
one or more real implementations, and a mock — so the entire product logic
runs headlessly in CI, and any stage can be swapped without touching the
others.

## The pipeline

```
AudioSource ──► VAD ──► Transcriber ──► rule cleanup ──► LM Studio polish
                                                             │
   TextSink ◄── dictation commands ◄── snippets ◄── dictionary enforcement
```

1. **AudioSource** (`local_flow/audio/capture.py`) yields 16-bit mono PCM
   frames. Real: `SounddeviceSource`. Test/demo: `MockAudioSource`.
2. **VAD** (`local_flow/audio/vad.py`) decides speech vs. silence per frame;
   `segment_stream` groups frames into utterances. Real: `EnergyVAD`
   (dependency-free RMS threshold) or `WebRtcVAD`. Test: `MockVAD`.
3. **Transcriber** (`local_flow/asr/`) turns PCM into rough text. Real:
   `FasterWhisperTranscriber`, `MlxWhisperTranscriber`, or Apple-Silicon
   `MlxParakeetTranscriber` (multilingual Parakeet v3 loaded directly through
   `parakeet-mlx`). Test: `MockTranscriber`. LM Studio is deliberately *not*
   an ASR option — it is only the local GGUF runtime for text polishing.
4. **Rule cleanup** (`local_flow/polish/rules.py`) is deterministic, pure
   Python: filler removal, backtracking ("scratch that"), whitespace repair.
   It runs before the LLM so dictation still works when LM Studio is down.
5. **LM Studio polish** (`local_flow/llm/lmstudio.py` +
   `polish/polisher.py`) sends the cleaned text with dictionary terms and
   style rules to the local OpenAI-compatible endpoint. On connection
   failure the polisher degrades to rules-only output and records a warning.
6. **Personalization pass** (`polish/rules.py` + `personalization/store.py`)
   re-enforces dictionary casing (in case the LLM rewrote a term), expands
   snippet triggers, and converts dictation commands ("new line", a trailing
   "press enter") into text and key actions.
7. **TextSink** (`local_flow/insertion/`) delivers the result. Real sinks:
   clipboard+paste keystroke, synthetic typing, clipboard-only; an
   `InsertionManager` tries them in order and reports every failure. Test:
   `FakeTextSink` records events.

**Command mode** (`local_flow/commands/command_mode.py`) is a second path
through stages 5–7: an instruction plus target text (explicit selection or
the last transcript) is sent to LM Studio, and the transformed result goes
through dictionary enforcement into the same TextSink.

**Hotkeys** (`local_flow/hotkeys/`) sit outside the pipeline: they only
decide *when* to record (push-to-talk) — hands-free mode replaces them with
the VAD's utterance segmentation.

## Why separate adapters?

- **Testability.** CI has no microphone, GPU, Whisper model, LM Studio
  server, clipboard, or display. Because ASR, VAD, LLM, hotkeys, and
  insertion are interfaces, every behavior — including error paths like
  "LM Studio is down" and "paste failed" — is exercised with mocks
  (`uv run pytest`, `uv run local-flow demo`).
- **Platform isolation.** All OS-specific code (PortAudio, pynput, clipboard
  tools) lives in leaf modules, imported lazily. The core never imports
  them, so headless environments work and missing extras fail with
  actionable errors instead of import crashes.
- **Independent failure modes.** ASR can work while LM Studio is down
  (degrade to rules-only); paste can fail while the clipboard works
  (fallback chain). Separation lets each stage degrade on its own.
- **Swappability.** whisper.cpp instead of faster-whisper, silero instead of
  energy/WebRTC VAD, or a different local LLM server: each is one new
  adapter class, no pipeline changes.

## Data & privacy

Settings come from env vars / TOML (`local_flow/config.py`); the native macOS
control center writes validated TOML through a provenance-aware pure service.
Personalization is stored in hand-editable JSON files in
`LOCAL_FLOW_DATA_DIR`. Nothing is sent anywhere except the configured
LM Studio server, which must not be a known cloud AI endpoint — the client
refuses those at construction time.
