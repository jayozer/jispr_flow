"""Transform-in-place hotkey, voice command mode hotkey, and their `_run_loop`
wiring (Phase 6 E8). `TapListener`'s own press/injected-guard logic is
covered in test_hotkeys.py; this file covers `_run_voice_command`'s pure
routing logic and `_run_loop`'s daemon-thread wiring for both new hotkeys.
"""

import threading

from local_flow.app import (
    RunDependencies,
    _run_command_hotkey_listener,
    _run_loop,
    _run_transform_listener,
    _run_voice_command,
)
from local_flow.asr.mock import MockTranscriber
from local_flow.commands.command_mode import CommandMode
from local_flow.config import load_config
from local_flow.errors import HotkeyBackendMissingError, LMStudioConnectionError, LocalFlowError
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.status import StatusReporter
from local_flow.transforms.selection import MockSelectionBackend, SelectionCapture


def _config(**env):
    return load_config(env={f"LOCAL_FLOW_{k.upper()}": v for k, v in env.items()})


class FakeReporter(StatusReporter):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def notify(self, state, detail: str = "") -> None:
        self.events.append((state, detail))


def _pipeline(tmp_path, sink, llm=None, transcriber=None, with_command_mode=True):
    store = PersonalizationStore(tmp_path / "data")
    llm = llm if llm is not None else MockChatClient(["ok"])
    command_mode = (
        CommandMode(llm, dictionary_terms=store.dictionary_terms) if with_command_mode else None
    )
    return DictationPipeline(
        transcriber=transcriber or MockTranscriber(["make it formal"]),
        polisher=TranscriptPolisher(llm, store),
        store=store,
        sink=sink,
        command_mode=command_mode,
    )


class TestRunVoiceCommandWithSelection:
    """`selection` truthy: apply CommandMode directly and `capture.replace()`
    the result -- the pipeline's own sink is never touched.
    """

    def test_replaces_selection_with_command_mode_result(self, tmp_path):
        sink = FakeTextSink()
        llm = MockChatClient(["TRANSFORMED SELECTION"])
        pipeline = _pipeline(
            tmp_path, sink, llm=llm, transcriber=MockTranscriber(["make it formal"])
        )
        backend = MockSelectionBackend(clipboard="old clipboard", selection_text="highlighted")
        capture = SelectionCapture(backend, sleep=lambda s: None)
        reporter = FakeReporter()
        deps = RunDependencies(pipeline, None, None)

        _run_voice_command(deps, capture, b"pcm-bytes", 16000, reporter)

        assert "write:TRANSFORMED SELECTION" in backend.events
        assert backend.clipboard == "old clipboard"  # restored after replace
        assert sink.events == []  # never went through the pipeline sink
        assert reporter.events == []  # no warnings on the happy path
        user_message = llm.requests[-1][1]["content"]
        assert "Instruction: make it formal" in user_message
        assert "highlighted" in user_message

    def test_dictionary_enforcement_applied_to_replaced_selection(self, tmp_path):
        sink = FakeTextSink()
        llm = MockChatClient(["please contact jispr flow support"])
        store = PersonalizationStore(tmp_path / "data")
        store.add_dictionary_term("JiSpr Flow")
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["make it formal"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=sink,
            command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms),
        )
        backend = MockSelectionBackend(clipboard="", selection_text="contact support")
        capture = SelectionCapture(backend, sleep=lambda s: None)

        _run_voice_command(
            RunDependencies(pipeline, None, None), capture, b"pcm", 16000, FakeReporter()
        )

        assert "write:please contact JiSpr Flow support" in backend.events

    def test_llm_failure_restores_and_reports_warning(self, tmp_path):
        class FailingClient(MockChatClient):
            def chat(self, messages, *, temperature=0.2, max_tokens=None):
                raise LMStudioConnectionError("LM Studio is unreachable")

        sink = FakeTextSink()
        llm = FailingClient()
        pipeline = _pipeline(tmp_path, sink, llm=llm)
        backend = MockSelectionBackend(clipboard="precious", selection_text="highlighted")
        capture = SelectionCapture(backend, sleep=lambda s: None)
        reporter = FakeReporter()

        _run_voice_command(
            RunDependencies(pipeline, None, None), capture, b"pcm", 16000, reporter
        )

        assert backend.clipboard == "precious"  # restored, not left cleared
        assert sink.events == []
        assert len(reporter.events) == 1
        state, detail = reporter.events[0]
        assert state == "warning"
        assert "voice command failed" in detail
        assert "unreachable" in detail


class TestRunVoiceCommandNoSelection:
    """`selection` falsy: restore the (untouched) clipboard and fall back to
    ``pipeline.run_command``'s own target resolution + sink insertion.
    """

    def test_falls_back_to_last_transcript_and_inserts_via_sink(self, tmp_path):
        sink = FakeTextSink()
        llm = MockChatClient(["BULLET LIST"])
        pipeline = _pipeline(
            tmp_path, sink, llm=llm, transcriber=MockTranscriber(["turn it into bullets"])
        )
        pipeline.last_transcript = "some previously dictated text"
        backend = MockSelectionBackend(clipboard="untouched", selection_text=None)
        capture = SelectionCapture(
            backend, poll_timeout_s=0.01, poll_interval_s=0.005, sleep=lambda s: None
        )
        reporter = FakeReporter()

        _run_voice_command(
            RunDependencies(pipeline, None, None), capture, b"pcm", 16000, reporter
        )

        assert backend.clipboard == "untouched"  # restore()'d back, never replaced
        assert sink.events == [("insert", "BULLET LIST")]
        assert reporter.events == []
        assert "some previously dictated text" in llm.requests[-1][1]["content"]

    def test_no_target_at_all_reports_warning_instead_of_raising(self, tmp_path):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)  # last_transcript left at "" (default)
        backend = MockSelectionBackend(clipboard="", selection_text=None)
        capture = SelectionCapture(
            backend, poll_timeout_s=0.01, poll_interval_s=0.005, sleep=lambda s: None
        )
        reporter = FakeReporter()

        _run_voice_command(
            RunDependencies(pipeline, None, None), capture, b"pcm", 16000, reporter
        )

        assert sink.events == []
        assert len(reporter.events) == 1
        state, detail = reporter.events[0]
        assert state == "warning"
        assert "voice command failed" in detail
        assert "no target text" in detail


class TestRunVoiceCommandEdgeCases:
    def test_empty_transcription_warns_and_restores_without_calling_command_mode(
        self, tmp_path
    ):
        sink = FakeTextSink()
        llm = MockChatClient(["should never be requested"])
        pipeline = _pipeline(tmp_path, sink, llm=llm, transcriber=MockTranscriber([""]))
        backend = MockSelectionBackend(clipboard="precious", selection_text="highlighted")
        capture = SelectionCapture(backend, sleep=lambda s: None)
        reporter = FakeReporter()

        _run_voice_command(
            RunDependencies(pipeline, None, None), capture, b"pcm", 16000, reporter
        )

        assert backend.clipboard == "precious"
        assert llm.requests == []  # command_mode.run never reached
        assert reporter.events == [("warning", "voice command: nothing heard")]

    def test_no_command_mode_configured_warns_and_restores(self, tmp_path):
        sink = FakeTextSink()
        transcriber = MockTranscriber(["make it formal"])
        pipeline = _pipeline(tmp_path, sink, with_command_mode=False, transcriber=transcriber)
        backend = MockSelectionBackend(clipboard="precious", selection_text="highlighted")
        capture = SelectionCapture(backend, sleep=lambda s: None)
        reporter = FakeReporter()

        _run_voice_command(
            RunDependencies(pipeline, None, None), capture, b"pcm", 16000, reporter
        )

        assert backend.clipboard == "precious"
        assert transcriber.calls == []  # never even reached transcription
        assert reporter.events == [
            ("warning", "voice command mode needs LM Studio configured")
        ]

    def test_selection_capture_failure_restores_and_warns(self, tmp_path):
        class BoomBackend(MockSelectionBackend):
            def send_copy(self):
                raise RuntimeError("copy chord failed")

        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        backend = BoomBackend(clipboard="precious", selection_text="highlighted")
        capture = SelectionCapture(backend, sleep=lambda s: None)
        reporter = FakeReporter()

        _run_voice_command(
            RunDependencies(pipeline, None, None), capture, b"pcm", 16000, reporter
        )

        assert backend.clipboard == "precious"
        state, detail = reporter.events[0]
        assert state == "warning"
        assert "selection capture failed" in detail


class TestRunLoopTransformHotkey:
    """`_run_loop`'s transform-hotkey wiring: `TapListener` on a daemon
    thread, dispatcher-wrapped, disabled entirely when `transform_hotkey` is
    unset or `transform_default` is unknown.
    """

    def test_disabled_by_default_never_constructs_a_tap_listener(self, tmp_path, monkeypatch):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config()  # transform_hotkey unset
        reporter = FakeReporter()
        constructed = []

        class SpyTapListener:
            def __init__(self, key_name):
                constructed.append(key_name)

            def run(self, on_tap):
                pass

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                return

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", SpyTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        _run_loop(
            config, "push-to-talk", reporter,
            dependencies=RunDependencies(pipeline, None, None),
        )

        assert constructed == []
        assert not any("transform" in detail for _state, detail in reporter.events)

    def test_unknown_transform_default_disables_feature_with_startup_warning(
        self, tmp_path, monkeypatch
    ):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config(transform_hotkey="f6", transform_default="Nonexistent")
        reporter = FakeReporter()
        constructed = []

        class SpyTapListener:
            def __init__(self, key_name):
                constructed.append(key_name)

            def run(self, on_tap):
                pass

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                return

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", SpyTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        _run_loop(
            config, "push-to-talk", reporter,
            dependencies=RunDependencies(pipeline, None, None),
        )

        assert constructed == []  # feature disabled: listener never built
        warnings = [detail for state, detail in reporter.events if state == "warning"]
        assert any("unknown transform_default" in w for w in warnings)
        assert any("Nonexistent" in w for w in warnings)

    def test_end_to_end_tap_replaces_selection(self, tmp_path, monkeypatch):
        import local_flow.app as app_module

        sink = FakeTextSink()
        llm = MockChatClient(["POLISHED SELECTION"])
        pipeline = _pipeline(tmp_path, sink, llm=llm)
        config = _config(transform_hotkey="f6")
        reporter = FakeReporter()
        done = threading.Event()

        backend = MockSelectionBackend(clipboard="original clipboard", selection_text="highlighted")

        class SignalingCapture(SelectionCapture):
            def replace(self, text):
                super().replace(text)
                done.set()

        capture = SignalingCapture(backend, sleep=lambda s: None)
        monkeypatch.setattr(app_module, "_build_selection_capture", lambda config: capture)

        class FakeTapListener:
            def __init__(self, key_name):
                self.key_name = key_name

            def run(self, on_tap):
                on_tap()

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                done.wait(timeout=5)

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", FakeTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        _run_loop(
            config, "push-to-talk", reporter,
            dependencies=RunDependencies(pipeline, None, None),
        )

        assert done.wait(timeout=2), "the transform tap never replaced the selection"
        assert backend.clipboard == "original clipboard"
        assert "write:POLISHED SELECTION" in backend.events

    def test_end_to_end_no_selection_reports_warning(self, tmp_path, monkeypatch):
        import local_flow.app as app_module

        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config(transform_hotkey="f6")
        reporter = FakeReporter()
        done = threading.Event()

        backend = MockSelectionBackend(clipboard="precious", selection_text=None)

        class SignalingCapture(SelectionCapture):
            def restore(self):
                super().restore()
                done.set()

        capture = SignalingCapture(
            backend, poll_timeout_s=0.01, poll_interval_s=0.005, sleep=lambda s: None
        )
        monkeypatch.setattr(app_module, "_build_selection_capture", lambda config: capture)

        class FakeTapListener:
            def __init__(self, key_name):
                pass

            def run(self, on_tap):
                on_tap()

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                done.wait(timeout=5)

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", FakeTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        _run_loop(
            config, "push-to-talk", reporter,
            dependencies=RunDependencies(pipeline, None, None),
        )

        assert done.wait(timeout=2)
        assert backend.clipboard == "precious"
        assert ("warning", "no text selected") in reporter.events


class _FakeSource:
    def record_until(self, stop, frame_ms):
        stop.wait(timeout=5)
        return b"pcm-bytes"


class TestRunLoopCommandHotkey:
    """`_run_loop`'s voice-command-hotkey wiring: a second `PynputPushToTalk`
    with its own recorder state, routed through `_run_voice_command`.
    """

    def test_disabled_by_default_never_constructs_a_listener(self, tmp_path, monkeypatch):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config()  # command_hotkey unset
        constructed = []

        class SpyPynput:
            def __init__(self, key_name, cancel_key="esc", cancel_gate=None):
                constructed.append((key_name, cancel_key))

            def run(self, on_press, on_release):
                pass

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                return

        monkeypatch.setattr("local_flow.hotkeys.base.PynputPushToTalk", SpyPynput)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        _run_loop(
            config, "push-to-talk", FakeReporter(),
            dependencies=RunDependencies(pipeline, _FakeSource(), None),
        )

        assert constructed == []

    def test_end_to_end_no_selection_falls_back_and_inserts(self, tmp_path, monkeypatch):
        import local_flow.app as app_module

        sink = FakeTextSink()
        llm = MockChatClient(["BULLET LIST"])
        transcriber = MockTranscriber(["turn it into bullets"])
        pipeline = _pipeline(tmp_path, sink, llm=llm, transcriber=transcriber)
        pipeline.last_transcript = "earlier dictation"
        config = _config(command_hotkey="f7")
        reporter = FakeReporter()
        done = threading.Event()

        backend = MockSelectionBackend(clipboard="untouched", selection_text=None)
        capture = SelectionCapture(
            backend, poll_timeout_s=0.01, poll_interval_s=0.005, sleep=lambda s: None
        )
        monkeypatch.setattr(app_module, "_build_selection_capture", lambda config: capture)

        class FakePynput:
            def __init__(self, key_name, cancel_key="esc", cancel_gate=None):
                self.key_name = key_name
                self.cancel_key = cancel_key

            def run(self, on_press, on_release):
                on_press()
                on_release()

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                done.wait(timeout=5)

        monkeypatch.setattr("local_flow.hotkeys.base.PynputPushToTalk", FakePynput)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    done.set()

        _run_loop(
            config, "push-to-talk", SignalingReporter(),
            dependencies=RunDependencies(pipeline, _FakeSource(), None),
        )

        assert done.wait(timeout=2), "command hotkey finish() never reached 'idle'"
        assert sink.events == [("insert", "BULLET LIST")]
        assert [s for s, _d in reporter.events] == ["recording", "processing", "idle"]

    def test_no_cancel_key_configured(self):
        # `PynputPushToTalk(config.command_hotkey, cancel_key="")` disables
        # its cancel handling entirely (see PynputPushToTalk: `cancel_key`
        # falsy -> `self._cancel = None`) -- the command hotkey has no
        # cancel gesture of its own, same as mouse push-to-talk.
        from local_flow.hotkeys.base import PynputPushToTalk

        listener = PynputPushToTalk("f7", cancel_key="")
        assert listener._cancel is None


class TestRunTransformListenerErrorVisible:
    """An uncaught exception on the transform hotkey's daemon thread is
    silently swallowed by Python -- no traceback, no process exit --
    `_run_transform_listener` must catch `LocalFlowError` and print it in
    `_fail`'s format instead, same as `_run_mouse_listener`.
    """

    def test_local_flow_error_prints_formatted_message_with_hint(self, capsys):
        class FailingListener:
            def run(self, on_tap):
                raise HotkeyBackendMissingError("boom", hint="fix it")

        _run_transform_listener(FailingListener(), lambda: None)

        captured = capsys.readouterr()
        assert "error: transform hotkey stopped: boom" in captured.err
        assert "hint : fix it" in captured.err

    def test_clean_run_prints_nothing(self, capsys):
        class QuietListener:
            def run(self, on_tap):
                return

        _run_transform_listener(QuietListener(), lambda: None)

        assert capsys.readouterr().err == ""


class TestRunCommandHotkeyListenerErrorVisible:
    def test_local_flow_error_prints_formatted_message_with_hint(self, capsys):
        class FailingListener:
            def run(self, on_press, on_release):
                raise HotkeyBackendMissingError("boom", hint="fix it")

        _run_command_hotkey_listener(FailingListener(), lambda: None, lambda: None)

        captured = capsys.readouterr()
        assert "error: voice command hotkey stopped: boom" in captured.err
        assert "hint : fix it" in captured.err

    def test_error_without_hint_omits_hint_line(self, capsys):
        class FailingListener:
            def run(self, on_press, on_release):
                raise LocalFlowError("boom")

        _run_command_hotkey_listener(FailingListener(), lambda: None, lambda: None)

        captured = capsys.readouterr()
        assert "error: voice command hotkey stopped: boom" in captured.err
        assert "hint :" not in captured.err

    def test_clean_run_prints_nothing(self, capsys):
        class QuietListener:
            def run(self, on_press, on_release):
                return

        _run_command_hotkey_listener(QuietListener(), lambda: None, lambda: None)

        assert capsys.readouterr().err == ""
