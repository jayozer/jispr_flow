"""Tests for the live-preview streaming machinery (Phase 4, Task 2):
`WindowedStream` cadence, `MockStream`, and the `_with_preview` frame tee.
"""

from local_flow.app import _with_preview
from local_flow.asr.mock import MockStream, MockTranscriber
from local_flow.asr.streaming import WindowedStream
from local_flow.status import StatusReporter


class RecordingReporter(StatusReporter):
    """Collects (state, detail) tuples in emission order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def notify(self, state, detail: str = "") -> None:
        self.events.append((state, detail))


class TestWindowedStreamCadence:
    """`WindowedStream` re-transcribes the accumulated buffer only once at
    least `interval_ms` of *new* audio has been fed since the last
    (re-)transcription.
    """

    def _stream(self, transcriber, interval_ms=100, sample_rate=1000):
        # sample_rate=1000, 16-bit mono -> 2 bytes/sample -> interval_bytes
        # = 1000 * 100 / 1000 * 2 = 200 bytes.
        return WindowedStream(transcriber, sample_rate, interval_ms=interval_ms)

    def test_feed_returns_none_until_interval_reached(self):
        transcriber = MockTranscriber(["partial one"])
        stream = self._stream(transcriber)

        assert stream.feed(b"x" * 100) is None
        assert stream.feed(b"x" * 99) is None
        assert transcriber.calls == []

    def test_feed_returns_partial_once_interval_bytes_accumulated(self):
        transcriber = MockTranscriber(["partial one", "partial two"])
        stream = self._stream(transcriber)

        stream.feed(b"x" * 100)
        result = stream.feed(b"x" * 100)  # 200 bytes total == interval_bytes

        assert result == "partial one"
        assert len(transcriber.calls) == 1
        assert transcriber.calls[0] == (200, 1000)  # full buffer so far

    def test_cadence_resets_after_each_transcription(self):
        transcriber = MockTranscriber(["p1", "p2"])
        stream = self._stream(transcriber)

        assert stream.feed(b"x" * 200) == "p1"
        # Counter reset to 0 after the first transcription; needs another
        # full interval of *new* bytes before the next one fires.
        assert stream.feed(b"x" * 100) is None
        assert stream.feed(b"x" * 100) == "p2"
        assert len(transcriber.calls) == 2
        # Second transcription still covers the whole buffer (400 bytes).
        assert transcriber.calls[1] == (400, 1000)

    def test_finish_transcribes_full_buffer_and_resets(self):
        transcriber = MockTranscriber(["ignored-partial", "final text"])
        stream = self._stream(transcriber)

        stream.feed(b"x" * 200)  # triggers one partial, consumes "ignored-partial"
        text = stream.finish()

        assert text == "final text"
        assert transcriber.calls[-1] == (200, 1000)
        # Buffer is reset: feeding a fresh interval starts counting from 0.
        assert stream.feed(b"x" * 199) is None

    def test_reset_drops_buffered_audio_without_transcribing(self):
        transcriber = MockTranscriber(["should not be called"])
        stream = self._stream(transcriber)

        stream.feed(b"x" * 150)
        stream.reset()
        assert transcriber.calls == []

        # After reset, a fresh interval is required again from zero.
        assert stream.feed(b"x" * 199) is None
        assert stream.feed(b"x" * 1) == "should not be called"


class TestWindowedStreamBounded:
    """Group C item 16: the preview buffer is bounded. It keeps only a
    trailing ``window_s`` of audio, and a re-transcription that comes back
    blank (the window was silence) drops the buffer -- so an utterance that
    never closes can't grow the buffer (and each synchronous re-run of
    Whisper over it) without limit.
    """

    def _stream(self, transcriber, interval_ms=100, sample_rate=1000, window_s=0.5):
        # interval_bytes = 200, window_bytes = 1000 * 0.5 * 2 = 1000.
        return WindowedStream(
            transcriber, sample_rate, interval_ms=interval_ms, window_s=window_s
        )

    def test_buffer_trimmed_to_trailing_window(self):
        transcriber = MockTranscriber(["still talking"])  # never blank: no reset
        stream = self._stream(transcriber)

        for _ in range(10):  # 2000 bytes fed, twice the 1000-byte window
            stream.feed(b"x" * 200)

        assert all(pcm_len <= 1000 for pcm_len, _rate in transcriber.calls)
        assert transcriber.calls[-1] == (1000, 1000)  # trailing window, not 2000

    def test_repeated_silent_intervals_never_grow_the_buffer(self):
        transcriber = MockTranscriber([""])  # scripted blank: pure silence
        stream = self._stream(transcriber)

        for _ in range(50):
            assert stream.feed(b"x" * 200) == ""

        # Every re-transcription saw exactly one interval's worth of audio:
        # the blank result dropped the buffer each time.
        assert transcriber.calls == [(200, 1000)] * 50
        assert len(stream._buffer) == 0

    def test_speech_after_silence_covers_only_post_silence_audio(self):
        transcriber = MockTranscriber(["", "hello"])
        stream = self._stream(transcriber)

        assert stream.feed(b"x" * 200) == ""  # silence: buffer dropped
        assert stream.feed(b"x" * 200) == "hello"

        assert transcriber.calls == [(200, 1000), (200, 1000)]

    def test_default_window_leaves_short_utterances_untrimmed(self):
        # The class default (30 s) is far above these tests' few hundred
        # bytes -- pinned here so the cadence tests above keep meaning "the
        # full buffer so far".
        transcriber = MockTranscriber(["partial"])
        stream = WindowedStream(transcriber, 1000, interval_ms=100)

        stream.feed(b"x" * 400)

        assert transcriber.calls == [(400, 1000)]


class TestMockStream:
    """`MockStream` scripts partials for tests without real audio/timing."""

    def test_returns_queued_partials_one_per_frame_by_default(self):
        stream = MockStream(["hello", "hello world"])

        assert stream.feed(b"frame-1") == "hello"
        assert stream.feed(b"frame-2") == "hello world"
        assert stream.feed(b"frame-3") is None  # queue exhausted
        assert stream.fed == [b"frame-1", b"frame-2", b"frame-3"]

    def test_frames_per_partial_cadence(self):
        stream = MockStream(["hello"], frames_per_partial=3)

        assert stream.feed(b"f1") is None
        assert stream.feed(b"f2") is None
        assert stream.feed(b"f3") == "hello"

    def test_finish_returns_last_partial_and_resets(self):
        stream = MockStream(["a", "b", "c"])
        stream.feed(b"f1")

        assert stream.finish() == "c"
        assert stream.fed == []

    def test_finish_with_no_partials_returns_empty_string(self):
        assert MockStream([]).finish() == ""

    def test_reset_clears_fed_frames_and_cadence(self):
        stream = MockStream(["a", "b"])
        stream.feed(b"f1")
        stream.reset()

        assert stream.fed == []
        assert stream.feed(b"f2") == "a"  # cadence restarted from the top


class TestWithPreview:
    """`_with_preview` tees frames through a `TranscriberStream`, yielding
    every frame through unchanged and notifying "preview" on partials.
    """

    def test_yields_every_frame_unchanged(self):
        stream = MockStream([])
        reporter = RecordingReporter()
        frames = [b"f1", b"f2", b"f3"]

        assert list(_with_preview(iter(frames), stream, reporter)) == frames

    def test_notifies_preview_on_partials_only(self):
        stream = MockStream(["rough one", "rough two"])
        reporter = RecordingReporter()
        frames = [b"f1", b"f2", b"f3"]

        list(_with_preview(iter(frames), stream, reporter))

        assert reporter.events == [
            ("preview", "rough one"),
            ("preview", "rough two"),
        ]

    def test_no_notifications_when_stream_never_yields_a_partial(self):
        stream = MockStream([])
        reporter = RecordingReporter()

        list(_with_preview(iter([b"f1", b"f2"]), stream, reporter))

        assert reporter.events == []

    def test_stream_feed_exception_does_not_interrupt_frame_flow(self):
        """Regression: preview transcription failures must never interrupt dictation.

        Even if stream.feed() raises an exception mid-utterance, all frames must
        still flow through and be processed by downstream (segment_stream, etc.).
        Preview is display-only; its failures are silent.
        """
        class FailingStream:
            """A mock stream that raises on the second feed."""
            def __init__(self):
                self.call_count = 0

            def feed(self, frame):
                self.call_count += 1
                if self.call_count == 2:
                    raise RuntimeError("preview transcription boom")
                return None

        stream = FailingStream()
        reporter = RecordingReporter()
        frames = [b"f1", b"f2", b"f3", b"f4"]

        # Should return all frames even though the second feed() raises.
        result = list(_with_preview(iter(frames), stream, reporter))

        assert result == frames, "all frames should pass through despite stream.feed() exception"
        assert reporter.events == [], "no notifications (preview is silent on failure)"
