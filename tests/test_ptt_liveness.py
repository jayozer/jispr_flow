"""Push-to-talk liveness (Group C items 6 and 10).

Item 6: a recorder thread that outlives ``_Recording.finish``'s join timeout
(a PortAudio stall: mic-permission prompt, Bluetooth dropout) is *abandoned*
-- its buffer is never processed or typed anywhere, the microphone stays
"busy" until the thread actually exits, and only then can a new recording
claim it. Before, ``finish()`` treated the join timeout as completion:
``mic_in_use`` was cleared (opening the door to a second concurrent
PortAudio stream) and the stalled recording's PCM was later popped by the
*next* dictation's finish and typed into whatever field was focused then.

Item 10: `_run_loop` runs recording state changes and utterance processing
on two separate ``CallbackDispatcher`` lanes (``dispatcher``/``processor``),
so a quick second dictation's ``start()`` begins recording immediately
instead of queueing behind the previous utterance's multi-second
ASR + LLM + insert -- which used to silently lose the first words.
"""

import threading
import time

import local_flow.app as app_module
from local_flow.app import RunDependencies, _Recording, _run_loop
from local_flow.asr.mock import MockTranscriber
from local_flow.config import load_config
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.status import StatusReporter


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    """Poll ``predicate`` until it's true or ``timeout`` elapses (same
    test-only synchronization helper as test_transform_command_hotkeys).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class FakeReporter(StatusReporter):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def notify(self, state, detail: str = "") -> None:
        self.events.append((state, detail))

    def states(self) -> list[str]:
        return [state for state, _detail in self.events]

    def warned(self, needle: str) -> bool:
        return any(
            needle in detail for state, detail in self.events if state == "warning"
        )


def _pipeline(tmp_path, sink, llm=None, transcriber=None):
    store = PersonalizationStore(tmp_path / "data")
    return DictationPipeline(
        transcriber=transcriber or MockTranscriber(["placeholder"]),
        polisher=TranscriptPolisher(llm or MockChatClient(["ok"]), store),
        store=store,
        sink=sink,
    )


class TestRecordingFinish:
    """`_Recording.finish` unit behavior: PCM on a clean exit, `None` (buffer
    abandoned, thread deliberately left running) on a stall.
    """

    class _WellBehavedSource:
        def record_until(self, stop, frame_ms):
            stop.wait(timeout=5)
            return b"pcm-bytes"

    class _StallingSource:
        """Ignores `stop` until the test releases it -- a PortAudio call that
        blocks past the join timeout.
        """

        def __init__(self) -> None:
            self.release = threading.Event()

        def record_until(self, stop, frame_ms):
            self.release.wait(timeout=10)
            return b"stalled-audio"

    def test_clean_exit_returns_the_pcm(self):
        rec = _Recording(self._WellBehavedSource(), frame_ms=30)
        assert rec.finish() == b"pcm-bytes"
        assert not rec.thread.is_alive()

    def test_stalled_thread_returns_none_and_stays_alive(self, monkeypatch):
        monkeypatch.setattr(app_module, "_RECORDER_JOIN_TIMEOUT_S", 0.05)
        source = self._StallingSource()
        rec = _Recording(source, frame_ms=30)

        assert rec.finish() is None
        assert rec.thread.is_alive()  # the caller must keep the mic "busy"

        source.release.set()
        rec.thread.join(timeout=5)
        assert not rec.thread.is_alive()


class _StallOnceSource:
    """First `record_until` stalls (ignores `stop`) until the test releases
    it; later calls behave normally. Records the thread of each call so the
    test can deterministically wait for the stalled one to die.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.release_stall = threading.Event()
        self.threads: list[threading.Thread] = []

    def record_until(self, stop, frame_ms):
        self.calls += 1
        self.threads.append(threading.current_thread())
        if self.calls == 1:
            self.release_stall.wait(timeout=10)
            return b"stalled-audio!!"  # must never reach the transcriber
        stop.wait(timeout=5)
        return b"fresh-pcm!"


class TestStalledRecorderIsAbandoned:
    """Item 6, end to end through `_run_loop`: finish() on a stalled recorder
    warns and discards; the mic stays busy while the stalled thread lives;
    once it dies the next dictation claims the mic and only ITS audio is
    transcribed and inserted.
    """

    def test_stall_discards_then_mic_recovers_once_the_thread_exits(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(app_module, "_RECORDER_JOIN_TIMEOUT_S", 0.05)
        sink = FakeTextSink()
        transcriber = MockTranscriber(["fresh words"])
        pipeline = _pipeline(
            tmp_path, sink, llm=MockChatClient(["fresh words"]), transcriber=transcriber
        )
        reporter = FakeReporter()
        config = load_config(env={})
        source = _StallOnceSource()

        class FakeListener:
            def run(self, on_press, on_release, on_cancel):
                on_press()  # recording 1 starts
                assert _wait_until(lambda: source.calls >= 1)
                on_release()  # finish(): join times out -> abandoned
                assert _wait_until(
                    lambda: reporter.warned("microphone did not stop in time")
                ), "the stalled recorder was not reported"

                on_press()  # stalled thread still alive: mic must read busy
                assert _wait_until(
                    lambda: reporter.warned("microphone busy")
                ), "a second stream was opened over the stalled recorder"
                on_release()  # nothing was started: guarded no-op

                source.release_stall.set()  # the stall clears...
                source.threads[0].join(timeout=5)  # ...and the thread exits

                on_press()  # now the mic is reclaimable
                assert _wait_until(
                    lambda: source.calls >= 2
                ), "the mic was never reclaimed after the stalled thread died"
                on_release()
                assert _wait_until(
                    lambda: any(state == "inserted" for state, _d in reporter.events)
                ), "the post-stall dictation never inserted"

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeListener(),
        )

        _run_loop(
            config, "push-to-talk", reporter,
            dependencies=RunDependencies(pipeline, source, None),
        )

        # Only the fresh recording's PCM was ever transcribed: the stalled
        # buffer (15 bytes of b"stalled-audio!!") was abandoned, not typed
        # into whatever field happened to be focused later.
        assert transcriber.calls == [(len(b"fresh-pcm!"), config.sample_rate)]
        assert sink.events == [("insert", "fresh words")]
        # The stall never opened a second concurrent stream: recording 2
        # started only after the stalled thread had exited.
        assert source.calls == 2


class _CountingSource:
    def __init__(self) -> None:
        self.calls = 0

    def record_until(self, stop, frame_ms):
        self.calls += 1
        stop.wait(timeout=5)
        return b"pcm" + str(self.calls).encode()


class _GatedPipeline(DictationPipeline):
    """`process_audio` blocks on `gate` -- a stand-in for the multi-second
    ASR + LLM + insert of a real utterance, releasable by the test.
    """

    def process_audio(self, pcm, sample_rate, **kwargs):
        self.processing_entered.set()
        assert self.gate.wait(timeout=5), "test never released the gate"
        return super().process_audio(pcm, sample_rate, **kwargs)


class TestSecondDictationStartsDuringProcessing:
    """Item 10: with recording state on `dispatcher` and slow work on
    `processor`, a second dictation's start() begins recording while the
    first utterance is still mid-processing -- and both utterances insert,
    in order. Before the split, start() sat queued behind finish()'s inline
    processing and the second dictation's first words were lost.
    """

    def test_start_runs_while_previous_utterance_is_processing(
        self, tmp_path, monkeypatch
    ):
        sink = FakeTextSink()
        transcriber = MockTranscriber(["first words", "second words"])
        llm = MockChatClient(["First words.", "Second words."])
        pipeline = _GatedPipeline(
            transcriber=transcriber,
            polisher=TranscriptPolisher(llm, PersonalizationStore(tmp_path / "data")),
            store=PersonalizationStore(tmp_path / "data"),
            sink=sink,
        )
        pipeline.gate = threading.Event()
        pipeline.processing_entered = threading.Event()
        reporter = FakeReporter()
        config = load_config(env={})
        source = _CountingSource()

        class FakeListener:
            def run(self, on_press, on_release, on_cancel):
                on_press()  # dictation 1
                assert _wait_until(lambda: source.calls >= 1)
                on_release()
                assert pipeline.processing_entered.wait(
                    timeout=5
                ), "utterance 1 never reached process_audio"

                on_press()  # dictation 2, while utterance 1 is mid-processing
                assert _wait_until(lambda: source.calls >= 2), (
                    "start() queued behind the previous utterance's "
                    "processing: the second dictation's audio was lost"
                )
                on_release()

                pipeline.gate.set()  # let both utterances process
                assert _wait_until(
                    lambda: reporter.states().count("inserted") == 2
                ), "both utterances should insert once the gate opens"

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeListener(),
        )

        _run_loop(
            config, "push-to-talk", reporter,
            dependencies=RunDependencies(pipeline, source, None),
        )

        # Neither start() was refused, and the processor lane kept the
        # utterances in order.
        assert not reporter.warned("microphone busy")
        assert reporter.states().count("recording") == 2
        assert sink.events == [
            ("insert", "First words."),
            ("insert", "Second words."),
        ]
        # Two distinct recordings, each with its own PCM: nothing was reused.
        assert transcriber.calls == [
            (len(b"pcm1"), config.sample_rate),
            (len(b"pcm2"), config.sample_rate),
        ]
