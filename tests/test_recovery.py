"""Crash-safe audio autosave (`PendingAudioStore`), `local-flow recover`, and
`history --retry` -- the E11 reliability trio.
"""

import pytest

from local_flow.app import _handle_utterance, main
from local_flow.asr.mock import MockTranscriber
from local_flow.audio.recovery import PendingAudioStore
from local_flow.history.store import HistoryStore
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.status import StatusReporter


def _pcm(n: int = 100) -> bytes:
    return b"\x00\x01" * n


class FakeReporter(StatusReporter):
    """Collects (state, detail) tuples in emission order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def notify(self, state, detail: str = "") -> None:
        self.events.append((state, detail))


def _make_pipeline(tmp_path, sink, transcriber=None):
    store = PersonalizationStore(tmp_path / "data")
    return DictationPipeline(
        transcriber=transcriber or MockTranscriber(["hello there"]),
        polisher=TranscriptPolisher(MockChatClient(["Hello there."]), store),
        store=store,
        sink=sink,
    )


class TestSaveLoadRoundTrip:
    def test_round_trips_bytes_and_sample_rate(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        path = store.save(_pcm(), 16000)
        pcm, sample_rate = store.load(path)
        assert pcm == _pcm()
        assert sample_rate == 16000

    def test_writes_under_pending_subdir_with_wav_suffix(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        path = store.save(_pcm(), 16000)
        assert path.parent == tmp_path / "pending"
        assert path.suffix == ".wav"

    def test_multiple_saves_get_unique_names(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        first = store.save(_pcm(), 16000)
        second = store.save(_pcm(), 16000)
        assert first != second


class TestPendingListingAndDelete:
    def test_empty_when_nothing_saved(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        assert store.pending() == []

    def test_lists_sorted_by_name(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        for _ in range(3):
            store.save(_pcm(), 16000)
        names = [p.name for p in store.pending()]
        assert names == sorted(names)
        assert len(names) == 3

    def test_delete_removes_file(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        path = store.save(_pcm(), 16000)
        store.delete(path)
        assert store.pending() == []

    def test_delete_is_idempotent_for_a_missing_file(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        # Must not raise even though nothing was ever saved.
        store.delete(store.pending_dir / "does-not-exist.wav")


class TestCorruptWav:
    def test_load_raises_value_error_on_garbage_bytes(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        store.pending_dir.mkdir(parents=True)
        bad = store.pending_dir / "garbage.wav"
        bad.write_bytes(b"not a wav file at all")
        with pytest.raises(ValueError):
            store.load(bad)


class TestHandleUtteranceAutosave:
    """`_handle_utterance`'s save-before/delete-after-success wiring."""

    def test_success_saves_then_deletes_the_file(self, tmp_path):
        pending = PendingAudioStore(tmp_path)
        sink = FakeTextSink()
        pipeline = _make_pipeline(tmp_path, sink)
        reporter = FakeReporter()

        _handle_utterance(pipeline, reporter, _pcm(), 16000, pending)

        assert pending.pending() == []
        assert sink.events

    def test_failure_leaves_the_saved_file_in_place(self, tmp_path):
        pending = PendingAudioStore(tmp_path)
        sink = FakeTextSink(fail=True)
        pipeline = _make_pipeline(tmp_path, sink)
        reporter = FakeReporter()

        _handle_utterance(pipeline, reporter, _pcm(), 16000, pending)

        assert len(pending.pending()) == 1
        assert ("error", "Fake sink was configured to fail.") in reporter.events
        # still reaches idle despite the failure (existing `_handle_utterance` contract)
        assert reporter.events[-1] == ("idle", "")

    def test_no_pending_store_is_byte_identical_to_before(self, tmp_path):
        """`pending_store=None` (the default) must not touch the filesystem."""
        sink = FakeTextSink()
        pipeline = _make_pipeline(tmp_path, sink)
        reporter = FakeReporter()

        _handle_utterance(pipeline, reporter, _pcm(), 16000)

        assert not (tmp_path / "pending").exists()
        assert reporter.events[-1] == ("idle", "")


class TestRecoverCommand:
    def test_empty_pending_prints_friendly_message(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["recover"])
        assert code == 0
        out = capsys.readouterr().out
        assert "no pending dictation audio" in out.lower()

    def test_recovers_and_empties_pending_on_success(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = PendingAudioStore(tmp_path)
        store.save(_pcm(), 16000)
        store.save(_pcm(), 16000)

        fake_sink = FakeTextSink()
        pipeline = _make_pipeline(tmp_path, fake_sink)

        import local_flow.app as app_module

        monkeypatch.setattr(
            app_module, "_build_run_dependencies", lambda config: (pipeline, None, None, None)
        )

        code = main(["recover"])
        assert code == 0
        assert store.pending() == []
        out = capsys.readouterr().out
        assert "2 recovered" in out

    def test_failure_keeps_the_file_and_reports_it(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = PendingAudioStore(tmp_path)
        store.save(_pcm(), 16000)

        failing_sink = FakeTextSink(fail=True)
        pipeline = _make_pipeline(tmp_path, failing_sink)

        import local_flow.app as app_module

        monkeypatch.setattr(
            app_module, "_build_run_dependencies", lambda config: (pipeline, None, None, None)
        )

        code = main(["recover"])
        assert code == 0
        assert len(store.pending()) == 1
        out = capsys.readouterr().out
        assert "failed" in out.lower()
        assert "0 recovered" in out

    def test_corrupt_wav_is_skipped_with_a_notice_and_kept(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = PendingAudioStore(tmp_path)
        store.pending_dir.mkdir(parents=True)
        (store.pending_dir / "garbage.wav").write_bytes(b"nope")

        pipeline = _make_pipeline(tmp_path, FakeTextSink())
        import local_flow.app as app_module

        monkeypatch.setattr(
            app_module, "_build_run_dependencies", lambda config: (pipeline, None, None, None)
        )

        code = main(["recover"])
        assert code == 0
        assert len(store.pending()) == 1
        out = capsys.readouterr().out
        assert "skip" in out.lower()


class TestHistoryRetry:
    def test_retry_reprocesses_rough_with_a_fresh_pipeline(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        history = HistoryStore(tmp_path)
        history.append_new(rough="the rough words", final="Old final.", used_llm=False)

        fake_sink = FakeTextSink()
        import local_flow.app as app_module

        monkeypatch.setattr(app_module, "_build_sink", lambda config: fake_sink)
        monkeypatch.setattr(
            app_module,
            "_build_chat_client",
            lambda config: MockChatClient(["Retried final."]),
        )

        code = main(["history", "--retry", "1"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Retried final." in out
        assert fake_sink.events == [("insert", "Retried final.")]

        # A NEW record is appended -- the old one is left untouched.
        records = history.recent()
        assert len(records) == 2
        assert records[0].final == "Retried final."
        assert records[1].final == "Old final."

    def test_retry_out_of_range_gives_friendly_error(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["history", "--retry", "1"])
        assert code == 1
        err = capsys.readouterr().err
        assert "no record #1" in err
        assert "hint" in err
