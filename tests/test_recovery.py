"""Crash-safe audio autosave (`PendingAudioStore`), `local-flow recover`, and
`history --retry` -- the E11 reliability trio.
"""

import os
import wave

import pytest

from local_flow.app import _handle_utterance, main
from local_flow.asr.mock import MockTranscriber
from local_flow.audio.recovery import PendingAudioStore
from local_flow.errors import ASRModelMissingError, MicNotFoundError
from local_flow.history.store import HistoryStore
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.status import StatusReporter


def _pcm(n: int = 1600) -> bytes:
    return b"\x00\x10" * n


def _save_named(store: PendingAudioStore, name: str, mtime: float) -> None:
    """Save one pending WAV, then force its filename and mtime so a test can
    make name order and save-time order disagree deterministically."""
    path = store.save(_pcm(), 16000)
    target = path.with_name(name)
    path.rename(target)
    os.utime(target, (mtime, mtime))


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

    def test_lists_in_save_order_not_name_order(self, tmp_path):
        """Crashed utterances must replay in the order they were spoken.
        Filenames are uuid4 (no order), so `pending()` sorts by mtime --
        here the names are deliberately reverse-sorted vs. save time.
        """
        store = PendingAudioStore(tmp_path)
        save_order = ["zz-first.wav", "mm-second.wav", "aa-third.wav"]
        for i, name in enumerate(save_order):
            _save_named(store, name, mtime=1_700_000_000 + i)
        assert [p.name for p in store.pending()] == save_order

    def test_equal_mtimes_fall_back_to_name_order(self, tmp_path):
        """Coarse filesystem timestamps can collide; ties break by name so
        the order stays deterministic run to run."""
        store = PendingAudioStore(tmp_path)
        for name in ["bb.wav", "aa.wav", "cc.wav"]:
            _save_named(store, name, mtime=1_700_000_000)
        assert [p.name for p in store.pending()] == ["aa.wav", "bb.wav", "cc.wav"]

    def test_delete_removes_file(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        path = store.save(_pcm(), 16000)
        store.delete(path)
        assert store.pending() == []

    def test_delete_is_idempotent_for_a_missing_file(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        # Must not raise even though nothing was ever saved.
        store.delete(store.pending_dir / "does-not-exist.wav")


def _write_wav(path, *, channels: int, sampwidth: int) -> None:
    """Write a small but *valid* WAV whose format differs from the 16-bit
    mono PCM that `PendingAudioStore.save` produces."""
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(channels)
        fh.setsampwidth(sampwidth)
        fh.setframerate(16000)
        fh.writeframes(b"\x00" * (channels * sampwidth * 50))


class TestCorruptWav:
    def test_load_raises_value_error_on_garbage_bytes(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        store.pending_dir.mkdir(parents=True)
        bad = store.pending_dir / "garbage.wav"
        bad.write_bytes(b"not a wav file at all")
        with pytest.raises(ValueError):
            store.load(bad)

    def test_load_rejects_stereo_wav(self, tmp_path):
        """Only `save`'s own 16-bit mono format is replayable; a stereo file
        (e.g. dropped into pending/ by hand) would be misparsed as garbage
        PCM, so `load` must refuse it instead."""
        store = PendingAudioStore(tmp_path)
        store.pending_dir.mkdir(parents=True)
        bad = store.pending_dir / "stereo.wav"
        _write_wav(bad, channels=2, sampwidth=2)
        with pytest.raises(ValueError, match="16-bit mono"):
            store.load(bad)

    def test_load_rejects_8bit_wav(self, tmp_path):
        store = PendingAudioStore(tmp_path)
        store.pending_dir.mkdir(parents=True)
        bad = store.pending_dir / "8bit.wav"
        _write_wav(bad, channels=1, sampwidth=1)
        with pytest.raises(ValueError, match="16-bit mono"):
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

        monkeypatch.setattr(app_module, "_build_text_pipeline", lambda config: pipeline)

        code = main(["recover"])
        assert code == 0
        assert store.pending() == []
        out = capsys.readouterr().out
        assert "2 recovered" in out

    def test_quiet_pending_audio_is_not_rejected_by_frame_vad(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        quiet_pcm = (b"\x78\x00\x88\xff") * 2400  # peak 120; below old RMS gate
        store = PendingAudioStore(tmp_path)
        store.save(quiet_pcm, 16000)
        transcriber = MockTranscriber(["quiet recovered words"])
        sink = FakeTextSink()
        pipeline = _make_pipeline(tmp_path, sink, transcriber)

        import local_flow.app as app_module

        monkeypatch.setattr(app_module, "_build_text_pipeline", lambda config: pipeline)
        monkeypatch.setattr(
            app_module,
            "_build_vad",
            lambda config: (_ for _ in ()).throw(
                AssertionError("recover must not re-run frame VAD")
            ),
        )

        assert main(["recover"]) == 0
        assert transcriber.calls == [(len(quiet_pcm), 16000)]
        assert store.pending() == []
        assert "1 recovered" in capsys.readouterr().out

    def test_failure_keeps_the_file_and_reports_it(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = PendingAudioStore(tmp_path)
        store.save(_pcm(), 16000)

        failing_sink = FakeTextSink(fail=True)
        pipeline = _make_pipeline(tmp_path, failing_sink)

        import local_flow.app as app_module

        monkeypatch.setattr(app_module, "_build_text_pipeline", lambda config: pipeline)

        code = main(["recover"])
        assert code == 0
        assert len(store.pending()) == 1
        out = capsys.readouterr().out
        assert "failed" in out.lower()
        assert "0 recovered" in out

    def test_recover_does_not_require_a_microphone(self, capsys, tmp_path, monkeypatch):
        """`recover` only needs saved WAVs + ASR + insertion. Building a live
        audio source (which enumerates the mic) must not happen -- otherwise
        recovering on another machine, with the mic unplugged, or with mic
        permission denied would fail before touching a single saved file.
        """
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = PendingAudioStore(tmp_path)
        store.save(_pcm(), 16000)

        fake_sink = FakeTextSink()
        import local_flow.app as app_module
        import local_flow.audio.capture as capture_module

        monkeypatch.setattr(app_module, "_build_sink", lambda config: fake_sink)
        monkeypatch.setattr(
            app_module, "_build_chat_client", lambda config: MockChatClient(["Recovered."])
        )
        monkeypatch.setattr(
            app_module, "_build_transcriber", lambda config: MockTranscriber(["recovered text"])
        )

        def _no_mic(*args, **kwargs):
            raise MicNotFoundError("no microphone here", hint="plug one in")

        monkeypatch.setattr(capture_module, "SounddeviceSource", _no_mic)

        code = main(["recover"])
        assert code == 0
        assert store.pending() == []
        out = capsys.readouterr().out
        assert "1 recovered" in out

    def test_corrupt_wav_is_skipped_with_a_notice_and_kept(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = PendingAudioStore(tmp_path)
        store.pending_dir.mkdir(parents=True)
        (store.pending_dir / "garbage.wav").write_bytes(b"nope")

        pipeline = _make_pipeline(tmp_path, FakeTextSink())
        import local_flow.app as app_module

        monkeypatch.setattr(app_module, "_build_text_pipeline", lambda config: pipeline)

        code = main(["recover"])
        assert code == 0
        assert len(store.pending()) == 1
        out = capsys.readouterr().out
        assert "skip" in out.lower()

    def test_wrong_format_wav_is_skipped_with_a_notice_and_kept(
        self, capsys, tmp_path, monkeypatch
    ):
        """A non-mono/non-16-bit WAV in pending/ is skipped (with a notice)
        and left on disk -- never fed to the pipeline as garbage PCM, never
        deleted."""
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = PendingAudioStore(tmp_path)
        store.pending_dir.mkdir(parents=True)
        _write_wav(store.pending_dir / "stereo.wav", channels=2, sampwidth=2)

        pipeline = _make_pipeline(tmp_path, FakeTextSink())
        import local_flow.app as app_module

        monkeypatch.setattr(app_module, "_build_text_pipeline", lambda config: pipeline)

        code = main(["recover"])
        assert code == 0
        assert len(store.pending()) == 1
        out = capsys.readouterr().out
        assert "skip" in out.lower()
        assert "0 recovered" in out


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

    def test_retry_does_not_require_a_working_transcriber(self, capsys, tmp_path, monkeypatch):
        """`--retry` reprocesses saved *text* through `process_transcript`, which
        never transcribes, so a broken ASR setup -- missing model or `asr`
        extra -- must not block it.
        """
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        history = HistoryStore(tmp_path)
        history.append_new(rough="the rough words", final="Old final.", used_llm=False)

        fake_sink = FakeTextSink()
        import local_flow.app as app_module

        monkeypatch.setattr(app_module, "_build_sink", lambda config: fake_sink)
        monkeypatch.setattr(
            app_module, "_build_chat_client", lambda config: MockChatClient(["Retried final."])
        )

        def _no_model(config):
            raise ASRModelMissingError("model gone", hint="reinstall it")

        monkeypatch.setattr(app_module, "_build_transcriber", _no_model)

        code = main(["history", "--retry", "1"])
        assert code == 0
        assert fake_sink.events == [("insert", "Retried final.")]

    def test_retry_out_of_range_gives_friendly_error(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["history", "--retry", "1"])
        assert code == 1
        err = capsys.readouterr().err
        assert "no record #1" in err
        assert "hint" in err
