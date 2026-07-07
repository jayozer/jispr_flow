# Phase 4: E7 Streaming / Low-Latency Insertion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Two tasks, in order. This is the only epic that changes pipeline shape — `off` must remain byte-identical to today, guarded by tests.

**Goal:** `LOCAL_FLOW_STREAMING=sentence` inserts each sentence while the next is still being spoken (hands-free); `live-preview` shows rough partial transcripts in the console/tray while speaking, with the final insert unchanged.

**Architecture:** Sentence mode is a parameterization of the existing hands-free segmentation (shorter pause threshold). Live preview tees mic frames through a `TranscriberStream` (windowed re-transcription) via a wrapping iterator, emitting a new `preview` reporter state. No true in-app diff-replace typing (explicitly out of scope).

## Global Constraints

Standard set. Specific to this epic:
- `streaming: str = "off"  # off | sentence | live-preview` — validated at `load_config` time like `history_retention`. `streaming_pause_ms: int = 300`.
- Streaming applies to HANDS-FREE mode only in this epic. `streaming != "off"` with push-to-talk mode: `_run_loop` prints a one-line notice `streaming requires hands-free mode; ignoring` and behaves as off (tested).
- `off` byte-identical: existing `tests/test_status.py` sequences and CLI outputs unchanged.
- Per the Phase-2 review carry-over: context routing (`router.resolve()`) stays once per CHUNK as today's once-per-utterance call — each sentence chunk is a pipeline utterance; do not add extra resolve calls beyond that.
- Documented limitation (README): in sentence mode, "scratch that" backtracking only reaches within the current chunk; history records one entry per chunk.

---

### Task 1: Sentence mode

**Files:** modify `local_flow/config.py` (two fields + `streaming` value validation with hint listing valid values), `local_flow/app.py` (`_run_loop` hands-free branch: when `config.streaming == "sentence"`, pass `silence_ms=config.streaming_pause_ms` into `segment_stream` instead of `config.vad_silence_ms`; the push-to-talk notice), README (Streaming section: what sentence mode does, latency tradeoff, the backtracking limitation), `.env.example`, `local-flow.example.toml`; tests: `tests/test_config.py` (fields + validation), `tests/test_status.py`-style run-loop tests.

**Key tests (TDD):**
- Config: defaults; `LOCAL_FLOW_STREAMING=banana` → ConfigError naming valid values.
- Byte-identical off: existing suite green without modification is the evidence; add one explicit test constructing the hands-free loop with `streaming="off"` asserting `segment_stream` received `config.vad_silence_ms` (monkeypatch/wrap `segment_stream` in app module to capture kwargs).
- Sentence mode: same capture shows `streaming_pause_ms`; ordering test — with a mock source yielding two speech bursts separated by a pause > streaming_pause_ms, assert the first chunk's sink insertion event occurs before the second chunk's transcribe call (instrument FakeTextSink + MockTranscriber with a shared event log list).
- Push-to-talk + streaming=sentence: notice printed once, behavior = off.

- [ ] TDD → `uv run pytest && uv run ruff check .` → commit `feat(streaming): sentence-chunked insertion for hands-free mode`

### Task 2: Live preview

**Files:** create `local_flow/asr/streaming.py`; modify `local_flow/asr/mock.py` (MockStream), `local_flow/status.py` (State literal gains `"preview"`; ConsoleReporter handles it), `local_flow/tray/state.py` (mapping for preview → processing icon, tooltip `… {detail[:40]}`), `local_flow/app.py` (`_with_preview` frame wrapper + wiring), README (+latency measurement note: utterance-end → insertion, how to eyeball before/after); tests: `tests/test_streaming.py` (new), extend `tests/test_tray_state.py`, `tests/test_status.py`.

**Interfaces (exact):**
```python
# local_flow/asr/streaming.py
class TranscriberStream(Protocol):
    def feed(self, frame: bytes) -> str | None: ...  # partial text when re-transcribed, else None
    def finish(self) -> str: ...                     # final text for the buffered audio; resets
    def reset(self) -> None: ...                     # drop buffered audio

class WindowedStream:
    """Re-transcribes the accumulated utterance every `interval_ms` of new audio.

    feed() runs the transcription synchronously on the calling thread; mic frames
    buffer in the source's queue meanwhile (documented tradeoff).
    """
    def __init__(self, transcriber: Transcriber, sample_rate: int,
                 interval_ms: int = 1000) -> None: ...
```
- `MockStream(partials: list[str])` in `asr/mock.py`: returns queued partials one per `interval` worth of fed bytes; `finish()` joins/returns the last.
- `_with_preview(frames, stream, reporter)` module-level in app.py: feeds each frame, notifies `preview` on partials, yields the frame through; `_run_loop` uses it to wrap the frame iterator (composing with `_interruptible`) when `config.streaming == "live-preview"`; on each yielded segment from `segment_stream`, call `stream.reset()` — the segment's final text still comes from the NORMAL pipeline transcribe (preview is display-only; final insert path unchanged).
- `ConsoleReporter` preview behavior: `print(f"\r… {detail[:70]}", end="", file=sys.stderr, flush=True)`; the reporter remembers a preview is on screen and, before the next non-preview printed state (`warning`/`inserted`/`error`), emits `"\n"` first so lines don't collide. States with no output (recording/processing/idle) do NOT clear the preview. All existing non-streaming output byte-identical (preview state simply never fires when streaming is off — but the clearing logic must be inert when no preview was shown; tested).
- `TrayStateMachine`: `preview` → icon `"processing"`, tooltip `… {detail[:40]}`, no flash.

**Key tests:** WindowedStream cadence with a mock transcriber (re-transcribes only after ≥ interval of new audio; finish returns full-buffer text; reset drops); `_with_preview` passes frames through unchanged and notifies partials (MockStream); ConsoleReporter preview + newline-before-next-print behavior via capsys; tray mapping; end-to-end hands-free run with MockStream injected: previews observed in FakeReporter sequence BEFORE the utterance's processing/inserted events, final inserted text unaffected.

- [ ] TDD → full suite → commit `feat(streaming): live rough-text preview while speaking`

## Manual checklist

1. `LOCAL_FLOW_STREAMING=sentence LOCAL_FLOW_MODE=hands-free uv run local-flow run` — speak two sentences with a short pause: the first appears in the editor while you speak the second.
2. `LOCAL_FLOW_STREAMING=live-preview ... local-flow run` — rough text updates on the terminal line while speaking; final polished insert lands as usual. Same in `local-flow tray` (tooltip).

## Self-Review (done)

`off` guarded byte-identical at three layers (config default, segment_stream kwargs test, existing suites). Preview is display-only — the insert path and history/context/dictionary flows are untouched by Task 2. Sentence mode reuses the utterance machinery wholesale, so router/history/uses semantics hold per chunk by construction.
