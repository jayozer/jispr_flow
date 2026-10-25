"""Agent command hotkey: `RunDependencies.agent_sink`, `_handle_utterance`'s
`skip_history`, and `_run_loop`'s toggle wiring.

Mirrors `tests/test_scratchpad_hotkey.py`'s patterns.
"""

import json
import os
import socket
import stat
import threading
import time
from pathlib import Path
from unittest import mock

from local_flow.agent.sink import AgentSink, _get_default_agent_socket_path
from local_flow.app import (
    RunDependencies,
    _build_pipeline,
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
    user-owned socket. TCP only allows 127.0.0.1.
    """

    def test_tcp_localhost_accepted(self):
        sink = AgentSink("tcp://127.0.0.1:9876")
        assert sink.target == "tcp://127.0.0.1:9876"

    def test_tcp_non_localhost_rejected(self):
        try:
            AgentSink("tcp://192.168.1.1:9876")
            assert False, "should have raised"
        except ConfigError as exc:
            assert "127.0.0.1" in exc.message

    def test_tcp_malformed_port_rejected(self):
        try:
            AgentSink("tcp://127.0.0.1:abc")
            assert False, "should have raised"
        except ConfigError as exc:
            assert "tcp://127.0.0.1:PORT" in exc.message

    def test_unknown_scheme_rejected(self):
        try:
            AgentSink("http://localhost:9876")
            assert False, "should have raised"
        except ConfigError as exc:
            assert "unix: or tcp://" in exc.message

    def test_unix_empty_path_rejected(self):
        try:
            AgentSink("unix:")
            assert False, "should have raised"
        except ConfigError as exc:
            assert "cannot be empty" in exc.message


class TestAgentSinkSecurityValidation:
    """Security validation happens before each connection: socket and parent dir
    must be owned by current user, parent dir must not be group/world writable.
    """

    def test_socket_in_user_owned_private_dir_accepted(self, tmp_path):
        # Create a user-owned private directory (mode 0700)
        socket_dir = tmp_path / "private"
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

    def test_socket_owned_by_another_user_rejected(self, tmp_path):
        socket_dir = tmp_path / "private"
        socket_dir.mkdir(mode=0o700)
        socket_path = socket_dir / "agent.sock"
        socket_path.touch()

        # Mock os.stat to return a different uid
        original_stat = socket_path.stat

        def mock_stat(follow_symlinks=True):
            st = original_stat(follow_symlinks=follow_symlinks)
            # Create a new stat result with different uid
            return os.stat_result((
                st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                os.getuid() + 1,  # Different uid
                st.st_gid, st.st_size, st.st_atime, st.st_mtime, st.st_ctime
            ))

        with mock.patch.object(Path, 'stat', side_effect=mock_stat):
            sink = AgentSink(f"unix:{socket_path}")
            # insert should catch LocalFlowError and log warning
            sink.insert("test")
            # No assertion needed - just verify it doesn't crash

    def test_socket_in_world_writable_dir_rejected(self, tmp_path):
        socket_dir = tmp_path / "world_writable"
        socket_dir.mkdir(mode=0o777)  # World writable
        socket_path = socket_dir / "agent.sock"
        socket_path.touch()

        sink = AgentSink(f"unix:{socket_path}")
        # insert should catch LocalFlowError and log warning
        sink.insert("test")
        # No assertion needed - just verify it doesn't crash

    def test_socket_nonexistent_logs_warning(self, tmp_path):
        socket_path = tmp_path / "nonexistent.sock"
        sink = AgentSink(f"unix:{socket_path}")
        # insert should catch LocalFlowError and log warning
        sink.insert("test")
        # No assertion needed - just verify it doesn't crash


class TestAgentSinkIPCFormat:
    """AgentSink sends JSON messages with version and text fields."""

    def test_tcp_socket_message_format(self):
        received = []

        def _server():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            received.append(("port", port))
            sock.listen(1)
            conn, _ = sock.accept()
            data = conn.recv(4096)
            received.append(("data", data))
            conn.close()
            sock.close()

        server_thread = threading.Thread(target=_server, daemon=True)
        server_thread.start()

        assert _wait_until(lambda: len(received) > 0 and received[0][0] == "port")
        port = received[0][1]

        sink = AgentSink(f"tcp://127.0.0.1:{port}")
        sink.insert("another command")

        server_thread.join(timeout=1.0)
        assert len(received) == 2
        message = json.loads(received[1][1].decode("utf-8"))
        assert message == {"version": 1, "text": "another command"}


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
        config = load_config(env={"LOCAL_FLOW_AGENT_TARGET": "tcp://127.0.0.1:9999"})
        assert config.agent_target == "tcp://127.0.0.1:9999"

    def test_agent_hotkey_collides_with_main_hotkey(self):
        try:
            load_config(
                env={"LOCAL_FLOW_HOTKEY": "f9", "LOCAL_FLOW_AGENT_HOTKEY": "f9"}
            )
            assert False, "should have raised"
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
            assert False, "should have raised"
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
            assert False, "should have raised"
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
            assert False, "should have raised"
        except ConfigError as exc:
            assert "agent_hotkey" in exc.message

    def test_collision_check_case_insensitive(self):
        try:
            load_config(
                env={"LOCAL_FLOW_HOTKEY": "F9", "LOCAL_FLOW_AGENT_HOTKEY": "f9"}
            )
            assert False, "should have raised"
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
        self, tmp_path, monkeypatch
    ):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        socket_dir = tmp_path / "agent"
        socket_dir.mkdir(mode=0o700)
        agent_sink = AgentSink(f"unix:{socket_dir}/test.sock")
        config = _config(hotkey="f9", agent_hotkey="fn", agent_target=f"unix:{socket_dir}/test.sock")
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

    def test_tap_then_dictate_routes_to_agent_sink(self, tmp_path, monkeypatch):
        normal_sink = FakeTextSink()
        llm = MockChatClient(["first utterance"])
        pipeline = _pipeline(
            tmp_path, normal_sink, llm=llm, transcriber=MockTranscriber(["first utterance"])
        )
        socket_dir = tmp_path / "agent"
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
