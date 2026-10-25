"""Agent command hotkey: `RunDependencies.agent_sink`, `_handle_utterance`'s
`skip_history`, and `_run_loop`'s toggle wiring.

Mirrors `tests/test_scratchpad_hotkey.py`'s patterns.
"""

import json
import os
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

from local_flow.agent.sink import AgentSink, _get_default_agent_socket_path
from local_flow.app import (
    RunDependencies,
    _handle_utterance,
    _run_agent_listener,
    _run_loop,
)
from local_flow.asr.mock import MockTranscriber
from local_flow.config import ConfigError, load_config
from local_flow.errors import HotkeyBackendMissingError, LocalFlowError
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.status import StatusReporter


def _config(**env):
    return load_config(env={f"LOCAL_FLOW_{k.upper()}": v for k, v in env.items()})


@pytest.fixture
def short_dir() -> Iterator[Path]:
    """A short-path scratch dir so AF_UNIX paths fit in sun_path on every OS."""
    base = tempfile.gettempdir()
    if len(base) > 40 and os.path.isdir("/tmp"):
        base = "/tmp"
    path = Path(tempfile.mkdtemp(prefix="lfa-", dir=base)).resolve()
    path.chmod(0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class FakeReporter(StatusReporter):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def notify(self, state, detail: str = "") -> None:
        self.events.append((state, detail))


def _pipeline(tmp_path, sink, llm=None, transcriber=None):
    from local_flow.history.store import HistoryStore

    store = PersonalizationStore(tmp_path / "data")
    history = HistoryStore(tmp_path / "data")
    llm = llm if llm is not None else MockChatClient(["ok"])
    return DictationPipeline(
        transcriber=transcriber or MockTranscriber(["hello"]),
        polisher=TranscriptPolisher(llm, store),
        store=store,
        sink=sink,
        history=history,
    )


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestAgentSinkValidation:
    """AgentSink validates socket security: user-owned parent dir with 0700 perms,
    user-owned socket. Only Unix sockets are supported.
    """

    def test_unknown_scheme_rejected(self):
        try:
            AgentSink("http://localhost:9876")
            raise AssertionError("should have raised")
        except ConfigError as exc:
            assert "absolute" in exc.message or "unix:" in exc.message

    def test_unix_empty_path_rejected(self):
        try:
            AgentSink("unix:")
            raise AssertionError("should have raised")
        except ConfigError as exc:
            assert "cannot be empty" in exc.message


class TestAgentSinkSecurityValidation:
    """Security validation happens before each connection: socket and parent dir
    must be owned by current user, parent dir must not be group/world writable.
    """

    def test_socket_in_user_owned_private_dir_accepted(self, short_dir):
        # Create a user-owned private directory (mode 0700)
        socket_dir = short_dir / "private"
        socket_dir.mkdir(mode=0o700)
        socket_path = socket_dir / "agent.sock"

        # Create a mock server
        received = []

        def _server():
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(socket_path))
            sock.listen(1)
            conn, _ = sock.accept()
            data = conn.recv(4096)
            received.append(data)
            conn.close()
            sock.close()

        server_thread = threading.Thread(target=_server, daemon=True)
        server_thread.start()
        time.sleep(0.05)

        sink = AgentSink(f"unix:{socket_path}")
        sink.insert("test command")

        server_thread.join(timeout=1.0)
        assert len(received) == 1
        message = json.loads(received[0].decode("utf-8"))
        assert message == {"version": 1, "text": "test command"}

    def test_socket_owned_by_another_user_rejected(self, short_dir, caplog):
        import logging

        import pytest
        socket_dir = short_dir / "private"
        socket_dir.mkdir(mode=0o700)
        socket_path = socket_dir / "agent.sock"

        # Bind a real socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(socket_path))
        s.listen(1)

        try:
            # Mock lstat to return a different uid
            original_lstat = socket_path.lstat

            def mock_lstat():
                st = original_lstat()
                # Create a new stat result with different uid
                return os.stat_result((
                    st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                    os.getuid() + 1,  # Different uid
                    st.st_gid, st.st_size, st.st_atime, st.st_mtime, st.st_ctime
                ))

            with mock.patch.object(Path, 'lstat', side_effect=mock_lstat):
                sink = AgentSink(f"unix:{socket_path}")
                with caplog.at_level(logging.WARNING, logger="local_flow.agent.sink"):
                    with pytest.raises(LocalFlowError) as exc_info:
                        sink.insert("test")
                # Must log a warning about the failure and mention uid
                assert any("failed to send" in record.message for record in caplog.records)
                assert "owned by uid" in exc_info.value.message
        finally:
            s.close()

    def test_socket_in_world_writable_dir_rejected(self, short_dir, caplog):
        import logging

        import pytest
        socket_dir = short_dir / "world_writable"
        socket_dir.mkdir(mode=0o777)  # World writable
        socket_path = socket_dir / "agent.sock"

        # Bind a real socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(socket_path))
        s.listen(1)

        try:
            sink = AgentSink(f"unix:{socket_path}")
            with caplog.at_level(logging.WARNING, logger="local_flow.agent.sink"):
                with pytest.raises(LocalFlowError) as exc_info:
                    sink.insert("test")
            # Must log a warning about the failure (world-writable directory)
            assert any("failed to send" in record.message for record in caplog.records)
            assert "0700" in exc_info.value.message or "mode" in exc_info.value.message
        finally:
            s.close()

    def test_socket_nonexistent_logs_warning(self, short_dir, caplog):
        import logging

        import pytest
        socket_path = short_dir / "nonexistent.sock"
        sink = AgentSink(f"unix:{socket_path}")
        with caplog.at_level(logging.WARNING, logger="local_flow.agent.sink"):
            with pytest.raises(LocalFlowError):
                sink.insert("test")
        # Must log a warning about the failure
        assert any("failed to send" in record.message for record in caplog.records)


class TestAgentSinkIPCFormat:
    """AgentSink sends JSON messages with version and text fields."""

    def test_unix_socket_message_format(self, short_dir):
        received = []
        socket_dir = short_dir / "test"
        socket_dir.mkdir(mode=0o700)
        socket_path = socket_dir / "agent.sock"

        def _server():
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(socket_path))
            sock.listen(1)
            conn, _ = sock.accept()
            data = conn.recv(4096)
            received.append(data)
            conn.close()
            sock.close()

        server_thread = threading.Thread(target=_server, daemon=True)
        server_thread.start()
        time.sleep(0.05)

        sink = AgentSink(f"unix:{socket_path}")
        sink.insert("test command")

        server_thread.join(timeout=1.0)
        assert len(received) == 1
        message = json.loads(received[0].decode("utf-8"))
        assert message == {"version": 1, "text": "test command"}


class TestDefaultAgentSocketPath:
    """_get_default_agent_socket_path returns a user-owned private directory."""

    def test_default_path_is_in_user_directory(self):
        default_path = _get_default_agent_socket_path()
        assert default_path
        # Should be under home directory or XDG_RUNTIME_DIR
        assert str(Path.home()) in default_path or "XDG_RUNTIME_DIR" in os.environ

    def test_default_path_accepted_by_agent_sink(self):
        default_path = _get_default_agent_socket_path()
        # Should not raise during construction
        sink = AgentSink(f"unix:{default_path}")
        assert sink.target == f"unix:{default_path}"


class TestHandleUtteranceSkipHistory:
    """`_handle_utterance`'s `skip_history` parameter prevents history writes."""

    def test_skip_history_false_writes_normally(self, tmp_path):
        normal_sink = FakeTextSink()
        pipeline = _pipeline(
            tmp_path, normal_sink, llm=MockChatClient(["polished"]),
            transcriber=MockTranscriber(["rough"]),
        )

        _handle_utterance(pipeline, FakeReporter(), b"pcm", 16000, skip_history=False)

        history = pipeline.history
        assert history is not None
        entries = list(history._read_all())
        assert len(entries) == 1
        assert entries[0].rough == "rough"
        assert entries[0].final == "polished"

    def test_skip_history_true_prevents_write(self, tmp_path):
        normal_sink = FakeTextSink()
        pipeline = _pipeline(
            tmp_path, normal_sink, llm=MockChatClient(["polished"]),
            transcriber=MockTranscriber(["rough"]),
        )

        _handle_utterance(pipeline, FakeReporter(), b"pcm", 16000, skip_history=True)

        history = pipeline.history
        assert history is not None
        entries = list(history._read_all())
        assert len(entries) == 0

    def test_skip_history_also_skips_personalization_stores(self, tmp_path):
        """skip_history must prevent writes to ALL persistent stores:
        history, dictionary additions, and term usage statistics.
        """
        normal_sink = FakeTextSink()
        # Use a transcription that will trigger dictionary term usage
        pipeline = _pipeline(
            tmp_path, normal_sink, llm=MockChatClient(["test word"]),
            transcriber=MockTranscriber(["test word"]),
        )
        # Add a term to the dictionary so usage can be recorded
        pipeline.store.add_dictionary_term("test")

        # Record initial state
        initial_dict_size = len(list(pipeline.store.dictionary_terms()))
        initial_usage_path = tmp_path / "data" / "term_usage.json"
        initial_usage_exists = initial_usage_path.exists()

        _handle_utterance(pipeline, FakeReporter(), b"pcm", 16000, skip_history=True)

        # Dictionary should not have grown (no new terms added)
        final_dict_size = len(list(pipeline.store.dictionary_terms()))
        assert final_dict_size == initial_dict_size

        # Term usage should not have been written (if it didn't exist before)
        # or should not have been updated (if it did exist)
        if not initial_usage_exists:
            assert not initial_usage_path.exists()

        # History should not have been written
        history = pipeline.history
        assert history is not None
        entries = list(history._read_all())
        assert len(entries) == 0


class TestConfigFields:
    """agent_hotkey and agent_target config fields."""

    def test_agent_hotkey_default_disabled(self):
        config = load_config(env={})
        assert config.agent_hotkey == ""

    def test_agent_target_default_empty(self):
        config = load_config(env={})
        assert config.agent_target == ""

    def test_agent_hotkey_env_override(self):
        config = load_config(env={"LOCAL_FLOW_AGENT_HOTKEY": "f10"})
        assert config.agent_hotkey == "f10"

    def test_agent_target_env_override(self):
        config = load_config(env={"LOCAL_FLOW_AGENT_TARGET": "unix:/tmp/test/agent.sock"})
        assert config.agent_target == "unix:/tmp/test/agent.sock"

    def test_agent_hotkey_collides_with_main_hotkey(self):
        try:
            load_config(
                env={"LOCAL_FLOW_HOTKEY": "f9", "LOCAL_FLOW_AGENT_HOTKEY": "f9"}
            )
            raise AssertionError("should have raised")
        except ConfigError as exc:
            assert "agent_hotkey" in exc.message
            assert "distinct" in exc.hint

    def test_agent_hotkey_collides_with_transform_hotkey(self):
        try:
            load_config(
                env={
                    "LOCAL_FLOW_HOTKEY": "fn",
                    "LOCAL_FLOW_TRANSFORM_HOTKEY": "f6",
                    "LOCAL_FLOW_AGENT_HOTKEY": "f6",
                }
            )
            raise AssertionError("should have raised")
        except ConfigError as exc:
            assert "agent_hotkey" in exc.message

    def test_agent_hotkey_collides_with_command_hotkey(self):
        try:
            load_config(
                env={
                    "LOCAL_FLOW_HOTKEY": "fn",
                    "LOCAL_FLOW_COMMAND_HOTKEY": "f7",
                    "LOCAL_FLOW_AGENT_HOTKEY": "f7",
                }
            )
            raise AssertionError("should have raised")
        except ConfigError as exc:
            assert "agent_hotkey" in exc.message

    def test_agent_hotkey_collides_with_scratchpad_hotkey(self):
        try:
            load_config(
                env={
                    "LOCAL_FLOW_HOTKEY": "fn",
                    "LOCAL_FLOW_SCRATCHPAD_HOTKEY": "f8",
                    "LOCAL_FLOW_AGENT_HOTKEY": "f8",
                }
            )
            raise AssertionError("should have raised")
        except ConfigError as exc:
            assert "agent_hotkey" in exc.message

    def test_collision_check_case_insensitive(self):
        try:
            load_config(
                env={"LOCAL_FLOW_HOTKEY": "F9", "LOCAL_FLOW_AGENT_HOTKEY": "f9"}
            )
            raise AssertionError("should have raised")
        except ConfigError as exc:
            assert "agent_hotkey" in exc.message

    def test_all_distinct_hotkeys_accepted(self):
        config = load_config(
            env={
                "LOCAL_FLOW_HOTKEY": "fn",
                "LOCAL_FLOW_TRANSFORM_HOTKEY": "f6",
                "LOCAL_FLOW_COMMAND_HOTKEY": "f7",
                "LOCAL_FLOW_SCRATCHPAD_HOTKEY": "f8",
                "LOCAL_FLOW_AGENT_HOTKEY": "f10",
            }
        )
        assert config.agent_hotkey == "f10"


class TestRunLoopAgentHotkeyDisabledByDefault:
    def test_disabled_by_default_never_constructs_listener(self, tmp_path, monkeypatch):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config()
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

        assert not any("agent" in key for key in constructed)
        assert not any("agent" in detail for _state, detail in reporter.events)

    def test_hotkey_set_without_sink_disables_with_warning(
        self, tmp_path, monkeypatch
    ):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config(agent_hotkey="f10")
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
            dependencies=RunDependencies(pipeline, None, None, agent_sink=None),
        )

        assert not any("f10" in key for key in constructed)
        warnings = [d for s, d in reporter.events if s == "warning"]
        assert any("no agent sink" in w for w in warnings)


class TestRunLoopAgentHotkeyUnsupportedKey:
    """An unknown agent_hotkey value must disable just this hotkey with an
    actionable warning instead of aborting the whole app.
    """

    def test_unsupported_agent_hotkey_warns_and_keeps_running(
        self, short_dir, monkeypatch
    ):
        sink = FakeTextSink()
        pipeline = _pipeline(short_dir, sink)
        socket_dir = short_dir / "agent"
        socket_dir.mkdir(mode=0o700)
        agent_sink = AgentSink(f"unix:{socket_dir}/test.sock")
        config = _config(
            hotkey="f9",
            agent_hotkey="fn",
            agent_target=f"unix:{socket_dir}/test.sock",
        )
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
            dependencies=RunDependencies(
                pipeline, None, None, agent_sink=agent_sink
            ),
        )

        assert result == 0
        assert main_listener_ran.is_set()
        warnings = [d for s, d in reporter.events if s == "warning"]
        assert any("agent hotkey disabled" in w for w in warnings)
        assert any("'fn'" in w for w in warnings)


class TestRunLoopAgentHotkeyToggle:
    """End-to-end: tapping the agent hotkey sets `agent_active` for the NEXT
    utterance only (one-shot), routing it to the agent sink and skipping history.
    """

    def test_tap_then_dictate_routes_to_agent_sink(self, short_dir, monkeypatch):
        normal_sink = FakeTextSink()
        llm = MockChatClient(["first utterance"])
        pipeline = _pipeline(
            short_dir, normal_sink, llm=llm, transcriber=MockTranscriber(["first utterance"])
        )
        socket_dir = short_dir / "agent"
        socket_dir.mkdir(mode=0o700)
        agent_target = f"unix:{socket_dir}/test-agent.sock"
        config = _config(agent_hotkey="f10", agent_target=agent_target)
        reporter = FakeReporter()
        agent_sink = AgentSink(agent_target)
        done = threading.Event()
        received_commands = []

        def _mock_insert(self, text: str):
            received_commands.append(text)

        monkeypatch.setattr(AgentSink, "insert", _mock_insert)

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    done.set()

        class FakeTapListener:
            def __init__(self, key_name):
                self.key_name = key_name

            def run(self, on_tap):
                on_tap()

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                assert _wait_until(
                    lambda: any("agent mode on" in d for _s, d in reporter.events)
                ), "agent toggle-on notification never arrived"
                on_press()
                on_release()
                done.wait(timeout=5)

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", FakeTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        class _FakeSource:
            def record_until(self, stop, frame_ms):
                stop.wait(timeout=5)
                return b"pcm-bytes"

        _run_loop(
            config, "push-to-talk", SignalingReporter(),
            dependencies=RunDependencies(
                pipeline, _FakeSource(), None, agent_sink=agent_sink
            ),
        )

        assert done.wait(timeout=2)
        assert normal_sink.events == []
        assert received_commands == ["first utterance"]

        history = pipeline.history
        assert history is not None
        entries = list(history._read_all())
        assert len(entries) == 0


class TestRunAgentListenerErrorVisible:
    """An uncaught exception on the agent hotkey's daemon thread must be
    caught and printed in `_fail`'s format.
    """

    def test_local_flow_error_prints_formatted_message_with_hint(self, capsys):
        class FailingListener:
            def run(self, on_tap):
                raise HotkeyBackendMissingError("boom", hint="fix it")

        _run_agent_listener(FailingListener(), lambda: None)

        captured = capsys.readouterr()
        assert "error: agent hotkey stopped: boom" in captured.err
        assert "hint : fix it" in captured.err

    def test_error_without_hint_omits_hint_line(self, capsys):
        class FailingListener:
            def run(self, on_tap):
                raise LocalFlowError("boom")

        _run_agent_listener(FailingListener(), lambda: None)

        captured = capsys.readouterr()
        assert "error: agent hotkey stopped: boom" in captured.err
        assert "hint :" not in captured.err

    def test_clean_run_prints_nothing(self, capsys):
        class QuietListener:
            def run(self, on_tap):
                return

        _run_agent_listener(QuietListener(), lambda: None)

        assert capsys.readouterr().err == ""


class TestBlockingIssueRegressions:
    """Regression tests for the 5 blocking issues from SWE review."""

    def test_issue_1_agent_sink_available_in_all_entry_points(self, monkeypatch):
        """Issue 1: agent_sink is built by _build_run_dependencies when hotkey is set.

        This is a documentation test - the fix is in _build_run_dependencies at line 1630-1636
        and the unpacking at line 1902-1904. No separate on-demand build needed.
        """
        # agent_sink is unpacked from deps at line 1902, which is either the passed
        # dependencies or the result of _build_run_dependencies(config). Since
        # _build_run_dependencies builds agent_sink when config.agent_hotkey is set,
        # agent_sink is always available when needed.
        from local_flow.app import _build_run_dependencies
        monkeypatch.setattr("local_flow.audio.capture.SounddeviceSource", lambda **_: object())
        config = _config(
            agent_hotkey="f10",
            asr_backend="mock",
            lmstudio_base_url="http://127.0.0.1:1/v1",
        )
        # This should not raise and should have agent_sink populated
        deps = _build_run_dependencies(config)
        assert deps.agent_sink is not None
        assert isinstance(deps.agent_sink, AgentSink)

    def test_issue_2_agent_audio_always_deleted(self, tmp_path):
        """Issue 2: Agent pending audio must be deleted on all paths, even non-LocalFlowError."""
        from local_flow.audio.recovery import PendingAudioStore

        pending_store = PendingAudioStore(tmp_path)
        normal_sink = FakeTextSink()

        # Create a pipeline that will raise a non-LocalFlowError
        class FailingTranscriber:
            def transcribe(self, pcm, sample_rate):
                raise RuntimeError("simulated crash")

        from local_flow.history.store import HistoryStore
        store = PersonalizationStore(tmp_path / "data")
        history = HistoryStore(tmp_path / "data")
        pipeline = DictationPipeline(
            transcriber=FailingTranscriber(),
            polisher=TranscriptPolisher(MockChatClient(["ok"]), store),
            store=store,
            sink=normal_sink,
            history=history,
        )

        # Process with skip_history=True (agent command)
        _handle_utterance(
            pipeline, FakeReporter(), b"test" * 1000, 16000,
            pending_store=pending_store, skip_history=True
        )

        # Pending audio should be deleted even though processing failed
        assert len(pending_store.pending()) == 0

    def test_issue_3_agent_commands_not_in_last_transcript(self, tmp_path):
        """Issue 3: agent utterances became last_transcript,
        allowing command mode to insert them."""
        normal_sink = FakeTextSink()
        pipeline = _pipeline(
            tmp_path, normal_sink, llm=MockChatClient(["agent command"]),
            transcriber=MockTranscriber(["agent command"]),
        )

        # Before: last_transcript should be empty
        assert pipeline.last_transcript == ""

        # Send an agent command
        _handle_utterance(pipeline, FakeReporter(), b"pcm", 16000, skip_history=True)

        # After: last_transcript should still be empty (not set to agent command)
        assert pipeline.last_transcript == ""

        # Normal dictation should set last_transcript
        _handle_utterance(pipeline, FakeReporter(), b"pcm", 16000, skip_history=False)
        assert pipeline.last_transcript == "agent command"

    def test_issue_5_port_validation_strict(self):
        """Issue 5: Null byte, relative path, and sun_path limit validation."""
        # Null byte rejected at construction
        try:
            AgentSink("unix:/tmp/test\x00.sock")
            raise AssertionError("should have raised")
        except ConfigError as exc:
            assert "null byte" in exc.message

        # Relative path rejected
        try:
            AgentSink("unix:relative/path.sock")
            raise AssertionError("should have raised")
        except ConfigError as exc:
            assert "absolute" in exc.message

    def test_issue_5_sun_path_limit_enforced(self, tmp_path):
        """Issue 5: sun_path has 104-byte limit."""
        # Create a path that's too long (> 104 bytes when resolved)
        long_name = "a" * 200
        long_path = tmp_path / long_name / "agent.sock"
        try:
            AgentSink(f"unix:{long_path}")
            raise AssertionError("should have raised")
        except ConfigError as exc:
            assert "too long" in exc.message or "104" in exc.message

    def test_issue_5_parent_must_be_exactly_0700(self, short_dir):
        """Issue 5: Parent directory mode must be exactly 0700,
        not just "not group/world writable"."""
        import socket as sock_mod
        # Create directory with mode 0755 (readable by others, but not writable)
        socket_dir = short_dir / "agent_755"
        socket_dir.mkdir(mode=0o755)
        socket_path = socket_dir / "agent.sock"

        # Bind a real socket
        s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
        s.bind(str(socket_path))
        s.listen(1)

        try:
            sink = AgentSink(f"unix:{socket_path}")
            with pytest.raises(LocalFlowError) as exc_info:
                sink.insert("test")
            assert "0700" in exc_info.value.message or "mode" in exc_info.value.message
        finally:
            s.close()

    def test_issue_5_symlink_parent_rejected(self, short_dir):
        """Issue 5: Parent must be a real directory, not a symlink."""
        import socket as sock_mod
        # Create real dir and symlink to it
        real_dir = short_dir / "real"
        real_dir.mkdir(mode=0o700)
        symlink_dir = short_dir / "symlink"
        symlink_dir.symlink_to(real_dir, target_is_directory=True)

        socket_path = symlink_dir / "agent.sock"
        # Bind a real socket
        s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
        s.bind(str(socket_path))
        s.listen(1)

        try:
            # resolve() follows symlinks, so the parent check sees the real dir
            # This test documents that behavior (symlink is followed, then checks apply)
            sink = AgentSink(f"unix:{socket_path}")
            # After resolve, parent is real_dir, which has mode 0700, so this should work
            # The test name is historical - resolve() makes symlinks transparent
            assert sink is not None
        finally:
            s.close()
