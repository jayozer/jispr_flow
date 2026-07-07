"""Tests for `local-flow transcribe` (E15): audio file(s) -> text (+ polish).

All headless: a tiny WAV is generated with the stdlib `wave` module and the
`mock` ASR backend (`LOCAL_FLOW_ASR_BACKEND=mock`) reads it back via
`MockTranscriber.transcribe_path`, so no real ASR model or audio hardware is
ever involved.
"""

from __future__ import annotations

import wave
from pathlib import Path

import local_flow.app as app_module
from local_flow.app import main
from local_flow.llm.mock import MockChatClient


def _write_wav(path: Path, pcm: bytes = b"\x00\x01" * 400, sample_rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm)


class TestTranscribeSingleFile:
    def test_transcribes_a_single_file_to_stdout(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        wav = tmp_path / "memo.wav"
        _write_wav(wav)

        code = main(["transcribe", str(wav)])
        assert code == 0
        out = capsys.readouterr().out
        assert "(mock transcription)" in out
        assert "==" not in out  # no header for a single file

    def test_progress_line_goes_to_stderr(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        wav = tmp_path / "memo.wav"
        _write_wav(wav)

        code = main(["transcribe", str(wav)])
        assert code == 0
        err = capsys.readouterr().err
        assert "transcribing memo.wav..." in err


class TestTranscribeMultiFile:
    def test_headers_shown_only_when_multiple_files(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        wav_a = tmp_path / "a.wav"
        wav_b = tmp_path / "b.wav"
        _write_wav(wav_a)
        _write_wav(wav_b)

        code = main(["transcribe", str(wav_a), str(wav_b)])
        assert code == 0
        out = capsys.readouterr().out
        assert "== a.wav ==" in out
        assert "== b.wav ==" in out
        # header for a.wav must come before header for b.wav
        assert out.index("== a.wav ==") < out.index("== b.wav ==")


class TestTranscribePolish:
    def test_polish_runs_text_through_mock_chat_client(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        wav = tmp_path / "memo.wav"
        _write_wav(wav)

        monkeypatch.setattr(
            app_module,
            "_build_chat_client",
            lambda config: MockChatClient(["Polished transcript."]),
        )

        code = main(["transcribe", str(wav), "--polish"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Polished transcript." in out
        assert "(mock transcription)" not in out

    def test_without_polish_llm_is_never_built(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        wav = tmp_path / "memo.wav"
        _write_wav(wav)

        def _boom(config):
            raise AssertionError("chat client should not be built without --polish")

        monkeypatch.setattr(app_module, "_build_chat_client", _boom)

        code = main(["transcribe", str(wav)])
        assert code == 0


class TestTranscribeCopy:
    def test_copy_puts_last_files_text_on_clipboard(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        wav_a = tmp_path / "a.wav"
        wav_b = tmp_path / "b.wav"
        _write_wav(wav_a)
        _write_wav(wav_b)

        copied: list[str] = []

        class FakeClipboardSink:
            def insert(self, text: str) -> None:
                copied.append(text)

        import local_flow.insertion.desktop as desktop_module

        monkeypatch.setattr(desktop_module, "ClipboardOnlySink", FakeClipboardSink)

        code = main(["transcribe", str(wav_a), str(wav_b), "--copy"])
        assert code == 0
        assert copied == ["(mock transcription)"]

    def test_no_copy_flag_leaves_clipboard_untouched(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        wav = tmp_path / "a.wav"
        _write_wav(wav)

        class ExplodingClipboardSink:
            def insert(self, text: str) -> None:
                raise AssertionError("clipboard should not be touched without --copy")

        import local_flow.insertion.desktop as desktop_module

        monkeypatch.setattr(desktop_module, "ClipboardOnlySink", ExplodingClipboardSink)

        code = main(["transcribe", str(wav)])
        assert code == 0


class TestTranscribeLanguageOverride:
    def test_language_override_reaches_the_transcriber(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        monkeypatch.setenv("LOCAL_FLOW_ASR_MODEL", "small")  # multilingual, allows non-en
        wav = tmp_path / "a.wav"
        _write_wav(wav)

        captured = []
        original = app_module._build_transcriber

        def spy(config):
            captured.append(config)
            return original(config)

        monkeypatch.setattr(app_module, "_build_transcriber", spy)

        code = main(["transcribe", str(wav), "--language", "fr"])
        assert code == 0
        assert captured[0].asr_language == "fr"

    def test_language_override_still_validated_against_english_only_model(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        # default asr_model is "small.en" (English-only)
        wav = tmp_path / "a.wav"
        _write_wav(wav)

        code = main(["transcribe", str(wav), "--language", "fr"])
        assert code == 1
        err = capsys.readouterr().err
        assert "not compatible" in err
        assert "hint" in err


class TestTranscribeMissingFile:
    def test_missing_file_fails_fast_before_building_the_transcriber(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")

        def _boom(config):
            raise AssertionError("transcriber should never be built for a missing file")

        monkeypatch.setattr(app_module, "_build_transcriber", _boom)

        missing = tmp_path / "nope.wav"
        code = main(["transcribe", str(missing)])
        assert code == 1
        err = capsys.readouterr().err
        assert "not found" in err
        assert "hint" in err

    def test_second_missing_file_fails_before_any_transcription_output(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        wav_a = tmp_path / "a.wav"
        _write_wav(wav_a)
        missing = tmp_path / "missing.wav"

        code = main(["transcribe", str(wav_a), str(missing)])
        assert code == 1
        out = capsys.readouterr().out
        assert "transcribing" not in out
        assert "(mock transcription)" not in out


class TestMockTranscribeNonWav:
    def test_non_wav_file_raises_a_friendly_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        bad = tmp_path / "bad.wav"
        bad.write_bytes(b"not a real wav file")

        code = main(["transcribe", str(bad)])
        assert code == 1
        err = capsys.readouterr().err
        assert "hint" in err
        assert "WAV" in err
