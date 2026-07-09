"""Transform-in-place hotkey, voice command mode hotkey, and their `_run_loop`
wiring (Phase 6 E8). `TapListener`'s own press/injected-guard logic is
covered in test_hotkeys.py; this file covers `_run_voice_command`'s pure
routing logic and `_run_loop`'s daemon-thread wiring for both new hotkeys.
"""

import threading
import time

import pytest

from local_flow.app import (
    RunDependencies,
    _build_pipeline,
    _run_command_hotkey_listener,
    _run_loop,
    _run_transform_listener,
    _run_voice_command,
    _transform_tap_debounced,
)
from local_flow.asr.mock import MockTranscriber
from local_flow.commands.command_mode import CommandMode
from local_flow.config import load_config
from local_flow.errors import (
    ConfigError,
    HotkeyBackendMissingError,
    LMStudioConnectionError,
    LocalFlowError,
)
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

    def test_empty_command_output_restores_selection_and_warns(self, tmp_path):
        # A whitespace-only completion must never be pasted over the user's
        # selection (the paste is unrecoverable -- restore() rewrites the
        # clipboard, not the selection): replace() is never called, the saved
        # clipboard comes back, and a warning says why.
        sink = FakeTextSink()
        llm = MockChatClient(["   "])
        pipeline = _pipeline(tmp_path, sink, llm=llm)
        backend = MockSelectionBackend(clipboard="precious", selection_text="highlighted")
        capture = SelectionCapture(backend, sleep=lambda s: None)
        reporter = FakeReporter()

        _run_voice_command(
            RunDependencies(pipeline, None, None), capture, b"pcm", 16000, reporter
        )

        assert "paste" not in backend.events  # replace() never ran
        assert backend.clipboard == "precious"  # restored, not overwritten
        assert sink.events == []
        assert reporter.events == [
            ("warning", "voice command returned no text; selection left unchanged")
        ]

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

    def test_end_to_end_empty_transform_output_restores_and_warns(
        self, tmp_path, monkeypatch
    ):
        # A whitespace-only completion must never be pasted over the user's
        # selection: replace() is never called, the saved clipboard comes
        # back via restore(), and a warning says why.
        import local_flow.app as app_module

        sink = FakeTextSink()
        llm = MockChatClient(["   "])
        pipeline = _pipeline(tmp_path, sink, llm=llm)
        config = _config(transform_hotkey="f6")
        reporter = FakeReporter()
        done = threading.Event()

        backend = MockSelectionBackend(clipboard="precious", selection_text="highlighted")
        replace_calls: list[str] = []

        class SpyCapture(SelectionCapture):
            def replace(self, text):
                replace_calls.append(text)
                super().replace(text)

            def restore(self):
                super().restore()
                done.set()

        capture = SpyCapture(backend, sleep=lambda s: None)
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
        assert replace_calls == []  # replace() never called with blank text
        assert backend.clipboard == "precious"  # restored, not overwritten
        assert (
            "warning",
            "transform returned no text; selection left unchanged",
        ) in reporter.events

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


class TestSecondaryHotkeyUnsupportedKeyDegrades:
    """Review item 14: a distinct-but-unsupported secondary hotkey value
    (e.g. `transform_hotkey=fn` -- pynput cannot observe Fn) used to raise
    out of listener construction on the main thread and abort the whole app.
    It must instead disable just that one hotkey with an actionable warning
    while the main push-to-talk loop keeps running.
    """

    def test_unsupported_transform_hotkey_warns_and_keeps_running(
        self, tmp_path, monkeypatch
    ):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config(hotkey="f9", transform_hotkey="fn")
        reporter = FakeReporter()
        main_listener_ran = threading.Event()

        class RaisingTapListener:
            def __init__(self, key_name):
                raise HotkeyBackendMissingError(
                    f"Unknown hotkey {key_name!r}.",
                    hint="Use a pynput key name such as f9, f8, scroll_lock, "
                    "or a single character.",
                )

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                main_listener_ran.set()

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", RaisingTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        result = _run_loop(
            config, "push-to-talk", reporter,
            dependencies=RunDependencies(pipeline, None, None),
        )

        assert result == 0  # the app did not abort
        assert main_listener_ran.is_set()  # the main hotkey still ran
        warnings = [d for s, d in reporter.events if s == "warning"]
        assert any("transform hotkey disabled" in w for w in warnings)
        assert any("'fn'" in w for w in warnings)  # names the bad value

    def test_unsupported_command_hotkey_warns_and_keeps_running(
        self, tmp_path, monkeypatch
    ):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config(hotkey="f9", command_hotkey="fn")
        reporter = FakeReporter()
        main_listener_ran = threading.Event()

        class RaisingPynput:
            def __init__(self, key_name, cancel_key="esc", cancel_gate=None):
                raise HotkeyBackendMissingError(
                    f"Unknown hotkey {key_name!r}.",
                    hint="Use a pynput key name such as f9, f8, scroll_lock, "
                    "or a single character.",
                )

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                main_listener_ran.set()

        monkeypatch.setattr("local_flow.hotkeys.base.PynputPushToTalk", RaisingPynput)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        result = _run_loop(
            config, "push-to-talk", reporter,
            dependencies=RunDependencies(pipeline, _FakeSource(), None),
        )

        assert result == 0
        assert main_listener_ran.is_set()
        warnings = [d for s, d in reporter.events if s == "warning"]
        assert any("voice command hotkey disabled" in w for w in warnings)
        assert any("'fn'" in w for w in warnings)


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


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    """Poll ``predicate`` until it's true or ``timeout`` elapses (test-only
    helper for synchronizing across the dispatcher's worker thread and any
    daemon listener threads without a fixed ``sleep``).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestTransformTapDebounced:
    """Pure unit tests for ``_transform_tap_debounced`` -- the guard
    ``_run_loop``'s transform-hotkey tap handler uses to reject a duplicate
    tap arriving too soon after the previous one *completed*. Extracted as a
    standalone function specifically so this threshold logic is testable
    with plain floats, no live clock or thread timing races (see its
    docstring for why a simple busy flag can't do this job on a serialized
    dispatcher).
    """

    def test_first_ever_tap_is_never_debounced(self):
        # 0.0 sentinel ("never yet"): a real `time.monotonic()` reading is
        # always large and positive, so `now - 0.0` always clears any
        # sane threshold.
        assert _transform_tap_debounced(0.0, now=100.0) is False

    def test_tap_immediately_after_completion_is_debounced(self):
        assert _transform_tap_debounced(100.0, now=100.3) is True

    def test_tap_well_after_the_threshold_is_not_debounced(self):
        assert _transform_tap_debounced(100.0, now=105.0) is False

    def test_tap_just_under_the_threshold_is_debounced(self):
        assert _transform_tap_debounced(100.0, now=100.999, threshold_s=1.0) is True

    def test_tap_at_or_past_the_threshold_is_not_debounced(self):
        assert _transform_tap_debounced(100.0, now=101.0, threshold_s=1.0) is False

    def test_custom_threshold_is_honored(self):
        assert _transform_tap_debounced(100.0, now=100.4, threshold_s=0.5) is True
        assert _transform_tap_debounced(100.0, now=100.6, threshold_s=0.5) is False


class TestRunLoopTransformHotkeyDebounce:
    """`_run_loop`'s transform-hotkey tap handler debounces a rapid duplicate
    tap (see ``_transform_tap_debounced``): a real key-*hold* is already
    fully suppressed by ``TapListener``'s own ``held`` guard (test_hotkeys.py),
    but two distinct tap events fired back-to-back -- a bouncy/stuck key, or
    the user mashing it -- must not both run the (slow, LLM-backed)
    transform.
    """

    def test_rapid_double_tap_second_one_is_debounced_with_a_warning(
        self, tmp_path, monkeypatch
    ):
        import local_flow.app as app_module

        sink = FakeTextSink()
        llm = MockChatClient(["POLISHED SELECTION", "POLISHED SELECTION 2"])
        pipeline = _pipeline(tmp_path, sink, llm=llm)
        config = _config(transform_hotkey="f6")
        reporter = FakeReporter()
        replace_done = threading.Event()
        warned_done = threading.Event()

        backend = MockSelectionBackend(
            clipboard="original clipboard", selection_text="highlighted"
        )
        replace_calls: list[str] = []

        class SignalingCapture(SelectionCapture):
            def replace(self, text):
                super().replace(text)
                replace_calls.append(text)
                replace_done.set()

        capture = SignalingCapture(backend, sleep=lambda s: None)
        monkeypatch.setattr(app_module, "_build_selection_capture", lambda config: capture)

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "warning" and "already running" in detail:
                    warned_done.set()

        class FakeTapListener:
            def __init__(self, key_name):
                self.key_name = key_name

            def run(self, on_tap):
                on_tap()
                on_tap()  # rapid duplicate: e.g. a bouncy key or a fast re-tap

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                warned_done.wait(timeout=5)

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", FakeTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        _run_loop(
            config, "push-to-talk", SignalingReporter(),
            dependencies=RunDependencies(pipeline, None, None),
        )

        assert replace_done.wait(timeout=2)
        assert warned_done.is_set()
        assert replace_calls == ["POLISHED SELECTION"]  # only the first tap ran
        assert backend.clipboard == "original clipboard"
        warnings = [detail for state, detail in reporter.events if state == "warning"]
        assert any("already running" in w for w in warnings)


class _ContentionSource:
    """A fake ``AudioSource`` that counts concurrent ``record_until`` calls,
    so tests can prove ``_run_loop``'s ``mic_owner`` guard never lets the
    main PTT recorder and the command-hotkey recorder open the (single,
    shared) microphone at the same time.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.active = 0
        self.max_concurrent = 0

    def record_until(self, stop, frame_ms):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_concurrent = max(self.max_concurrent, self.active)
        stop.wait(timeout=5)
        with self._lock:
            self.active -= 1
        return b"pcm-bytes"


class TestMicMutualExclusion:
    """`_run_loop`'s ``mic_owner`` guard: the main PTT recorder and the
    command-hotkey recorder both call ``source.record_until`` on the same
    ``SounddeviceSource`` (concurrent PortAudio opens are device contention,
    not two clean recordings), so the second one to start while the other is
    already recording is refused with a warning instead of racing it.
    """

    def test_command_start_refused_while_main_is_recording(self, tmp_path, monkeypatch):
        import local_flow.app as app_module

        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config(command_hotkey="f7")
        reporter = FakeReporter()
        source = _ContentionSource()
        done = threading.Event()
        cmd_attempted = threading.Event()

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    done.set()

        backend = MockSelectionBackend(clipboard="untouched", selection_text=None)
        capture = SelectionCapture(
            backend, poll_timeout_s=0.01, poll_interval_s=0.005, sleep=lambda s: None
        )
        monkeypatch.setattr(app_module, "_build_selection_capture", lambda config: capture)

        def _busy_warned() -> bool:
            return any(
                "microphone busy" in detail
                for state, detail in reporter.events
                if state == "warning"
            )

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                on_press()  # main start
                assert _wait_until(lambda: source.calls >= 1), "main recording never started"
                assert cmd_attempted.wait(timeout=2), "command hotkey never attempted"
                on_release()  # main finish
                done.wait(timeout=5)

        class FakePynput:
            def __init__(self, key_name, cancel_key="esc", cancel_gate=None):
                pass

            def run(self, on_press, on_release):
                assert _wait_until(lambda: source.calls >= 1), "main recording never started"
                on_press()  # cmd start attempt: must be refused (mic busy)
                assert _wait_until(_busy_warned), "command hotkey was not refused"
                on_release()  # cmd finish: guarded no-op, nothing was started
                cmd_attempted.set()

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )
        monkeypatch.setattr("local_flow.hotkeys.base.PynputPushToTalk", FakePynput)

        _run_loop(
            config, "push-to-talk", SignalingReporter(),
            dependencies=RunDependencies(pipeline, source, None),
        )

        assert done.is_set()
        assert source.calls == 1  # the command hotkey never actually opened the mic
        assert source.max_concurrent == 1

    def test_main_start_refused_while_command_hotkey_is_recording(
        self, tmp_path, monkeypatch
    ):
        import local_flow.app as app_module

        sink = FakeTextSink()
        llm = MockChatClient(["BULLET LIST"])
        transcriber = MockTranscriber(["turn it into bullets"])
        pipeline = _pipeline(tmp_path, sink, llm=llm, transcriber=transcriber)
        pipeline.last_transcript = "earlier dictation"
        config = _config(command_hotkey="f7")
        reporter = FakeReporter()
        source = _ContentionSource()
        done = threading.Event()
        cmd_started = threading.Event()
        main_attempted = threading.Event()

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    done.set()

        backend = MockSelectionBackend(clipboard="untouched", selection_text=None)
        capture = SelectionCapture(
            backend, poll_timeout_s=0.01, poll_interval_s=0.005, sleep=lambda s: None
        )
        monkeypatch.setattr(app_module, "_build_selection_capture", lambda config: capture)

        def _busy_warned() -> bool:
            return any(
                "microphone busy" in detail
                for state, detail in reporter.events
                if state == "warning"
            )

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                assert cmd_started.wait(timeout=2), "command recording never started"
                on_press()  # main start attempt: must be refused (mic busy)
                assert _wait_until(_busy_warned), "main hotkey was not refused"
                on_release()  # main finish: guarded no-op, nothing was started
                main_attempted.set()
                done.wait(timeout=5)

        class FakePynput:
            def __init__(self, key_name, cancel_key="esc", cancel_gate=None):
                pass

            def run(self, on_press, on_release):
                on_press()  # cmd start: succeeds, claims the mic
                assert _wait_until(
                    lambda: source.calls >= 1
                ), "command recording never started"
                cmd_started.set()
                assert main_attempted.wait(timeout=2)
                on_release()  # cmd finish: proceeds normally

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )
        monkeypatch.setattr("local_flow.hotkeys.base.PynputPushToTalk", FakePynput)

        _run_loop(
            config, "push-to-talk", SignalingReporter(),
            dependencies=RunDependencies(pipeline, source, None),
        )

        assert done.is_set()
        assert sink.events == [("insert", "BULLET LIST")]  # command hotkey's own path ran
        assert source.calls == 1  # the main hotkey never actually opened the mic
        assert source.max_concurrent == 1

    def test_sequential_use_is_unaffected(self, tmp_path, monkeypatch):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config()  # command_hotkey unset: plain main hotkey only
        reporter = FakeReporter()
        source = _ContentionSource()
        idle_count = [0]
        done = threading.Event()

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    idle_count[0] += 1
                    if idle_count[0] == 2:
                        done.set()

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                on_press()
                on_release()
                assert _wait_until(lambda: idle_count[0] >= 1), "first cycle never finished"
                on_press()
                on_release()
                done.wait(timeout=5)

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        _run_loop(
            config, "push-to-talk", SignalingReporter(),
            dependencies=RunDependencies(pipeline, source, None),
        )

        assert done.is_set()
        warnings = [detail for state, detail in reporter.events if state == "warning"]
        assert not any("microphone busy" in w for w in warnings)
        assert source.calls == 2
        assert source.max_concurrent == 1


class TestBuildPipelineUnknownAutoTransform:
    """Missing-test fix: `_build_pipeline` (via `_resolve_auto_transform_prompt`)
    must fail fast with a `ConfigError` listing the known transform names when
    `auto_transform` names something not in `transforms.json` -- the harsher,
    intentional counterpart to `transform_default`'s soft warn-and-disable
    (see `_resolve_auto_transform_prompt`'s docstring for why).
    """

    def test_unknown_auto_transform_raises_config_error_listing_known_names(
        self, tmp_path
    ):
        data_dir = tmp_path / "data"
        # `PersonalizationStore.__init__` seeds `transforms.json` with the
        # built-in Polish/Prompt Engineer transforms on first use; `_build_pipeline`
        # constructs its own store against the same `data_dir` below, so it
        # sees that same seeded file.
        config = load_config(
            env={
                "LOCAL_FLOW_DATA_DIR": str(data_dir),
                "LOCAL_FLOW_AUTO_TRANSFORM": "Nonexistent",
                "LOCAL_FLOW_ASR_BACKEND": "mock",
                "LOCAL_FLOW_LMSTUDIO_BASE_URL": "http://127.0.0.1:59999/v1",
            }
        )
        chat_client = MockChatClient(["ok"])  # headless-constructible, no network
        sink = FakeTextSink()

        with pytest.raises(ConfigError, match="Unknown auto_transform") as excinfo:
            _build_pipeline(config, chat_client, sink)

        message = str(excinfo.value)
        hint = excinfo.value.hint or ""
        assert "Nonexistent" in message
        assert "Polish" in hint
        assert "Prompt Engineer" in hint
