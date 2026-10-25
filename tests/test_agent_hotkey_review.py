"""Review tests for the agent hotkey (PR #14, reviewed at a35969a).

Drop-in for ``tests/``. Tests whose name starts with ``test_bug_`` pin a
blocking bug and FAIL until it is fixed. ``test_nb_`` tests pin non-blocking
gaps and also FAIL until addressed. Every other test documents behavior that
already holds and should keep passing.

Bug IDs (see the PR review):
  B1  app.py:2434 discards the built agent sink when dependencies=None
  B2  agent audio left in pending/ on error -> `recover` pastes + records it
  B3  agent command becomes pipeline.last_transcript (command mode reuses it)
  B5  TCP port parsing: 0/>65535/non-ASCII digits accepted; OverflowError and
      NUL-byte ValueError escape insert()
  T1  TOCTOU: path checked, then re-walked by connect(); no post-connect check
  T2  only the immediate parent dir is checked, not its ancestors
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

import local_flow.agent.sink as sink_mod
from local_flow.agent.sink import AgentSink, _get_default_agent_socket_path
from local_flow.app import RunDependencies, _handle_utterance, _run_loop
from local_flow.asr.mock import MockTranscriber
from local_flow.audio.recovery import PendingAudioStore
from local_flow.config import load_config
from local_flow.errors import ConfigError
from local_flow.history.store import HistoryStore
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.status import StatusReporter

POSIX_ONLY = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs AF_UNIX")
NOT_ROOT = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses dir permissions"
)
# macOS sun_path is 104 bytes (incl. NUL); Linux is 108.
SUN_PATH_MAX = 104 if sys.platform == "darwin" else 108
PCM = b"\x01\x00" * 800


def _config(**env: str):
    return load_config(env={f"LOCAL_FLOW_{k.upper()}": v for k, v in env.items()})


class FakeReporter(StatusReporter):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def notify(self, state, detail: str = "") -> None:
        self.events.append((state, detail))


def _pipeline(data_dir: Path, sink, transcriber=None) -> DictationPipeline:
    store = PersonalizationStore(data_dir)
    return DictationPipeline(
        transcriber=transcriber or MockTranscriber(["open safari"]),
        polisher=TranscriptPolisher(MockChatClient(["open safari"]), store),
        store=store,
        sink=sink,
        history=HistoryStore(data_dir),
    )


def _history(pipeline: DictationPipeline) -> list:
    return list(pipeline.history._read_all())


@pytest.fixture
def short_dir() -> Iterator[Path]:
    """A short-path scratch dir so AF_UNIX paths fit in sun_path on every OS
    (pytest's tmp_path on macOS is ~130 bytes)."""
    base = tempfile.gettempdir()
    if len(base) > 40 and os.path.isdir("/tmp"):
        base = "/tmp"
    path = Path(tempfile.mkdtemp(prefix="lfa-", dir=base)).resolve()
    path.chmod(0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _private_dir(parent: Path, name: str = "p") -> Path:
    d = parent / name
    d.mkdir()
    d.chmod(0o700)
    return d


class _Server:
    """A one-shot AF_UNIX listener that records whatever bytes arrive."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = b""
        self.accepted = threading.Event()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(path))
        self._sock.listen(1)
        self._sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        self.accepted.set()
        with conn:
            conn.settimeout(1.0)
            try:
                while chunk := conn.recv(65536):
                    self.data += chunk
            except OSError:
                pass

    def close(self) -> bytes:
        self._thread.join(timeout=3)
        self._sock.close()
        return self.data


def _warned(caplog, needle: str) -> bool:
    return any(needle in r.getMessage() for r in caplog.records)


# --- AC2: agent utterances never reach history / personalization -----------


class TestSkipHistoryEveryPath:
    def test_process_transcript_skip_history_writes_no_history(self, tmp_path):
        p = _pipeline(tmp_path, FakeTextSink())
        p.process_transcript("open safari", sink_override=FakeTextSink(), skip_history=True)
        assert _history(p) == []

    def test_spoken_dictionary_addition_is_not_learned(self, tmp_path):
        p = _pipeline(tmp_path, FakeTextSink())
        before = list(p.store.dictionary_terms())
        p.process_transcript(
            "add Zyxwq to dictionary", sink_override=FakeTextSink(), skip_history=True
        )
        assert list(p.store.dictionary_terms()) == before

    @POSIX_ONLY
    def test_send_failure_skips_history_and_types_nothing(self, tmp_path, short_dir):
        normal = FakeTextSink()
        p = _pipeline(tmp_path, normal)
        agent = AgentSink(f"unix:{_private_dir(short_dir)}/missing.sock")
        _handle_utterance(p, FakeReporter(), PCM, 16000, sink_override=agent, skip_history=True)
        assert _history(p) == []
        assert normal.events == []

    def test_bug_B2_pipeline_error_leaves_agent_audio_for_recover(self, tmp_path):
        """B2 (app.py:1441, 1465-1470): on a non-LocalFlowError the pending WAV
        is kept, and `local-flow recover` replays it through the NORMAL sink
        (typed into the focused app) with history enabled."""

        class Boom:
            def transcribe(self, pcm, sample_rate):
                raise RuntimeError("asr blew up")

        p = _pipeline(tmp_path, FakeTextSink(), transcriber=Boom())
        pending = PendingAudioStore(tmp_path)
        _handle_utterance(
            p,
            FakeReporter(),
            PCM,
            16000,
            pending,
            sink_override=FakeTextSink(),
            skip_history=True,
        )
        assert pending.pending() == [], "agent audio must never be left for recover"

    def test_bug_B3_agent_command_becomes_last_transcript(self, tmp_path):
        """B3 (pipeline.py:247-248): command mode with no selection transforms
        `last_transcript` and inserts it into the focused app via self.sink."""
        p = _pipeline(tmp_path, FakeTextSink())
        p.last_transcript = "previous normal dictation"
        p.process_transcript("open safari", sink_override=FakeTextSink(), skip_history=True)
        assert p.last_transcript == "previous normal dictation"


# --- AC1: production wiring --------------------------------------------------


class _NoopTap:
    def __init__(self, key: str) -> None:
        pass

    def run(self, on_tap) -> None:
        pass


class _NoopKeyboard:
    def run(self, on_press, on_release, on_cancel) -> None:
        pass


class _SilentSource:
    def record_until(self, stop, frame_ms):
        return b""


class TestProductionWiring:
    def test_bug_B1_run_loop_discards_built_agent_sink(self, tmp_path, monkeypatch):
        """B1 (app.py:2434): `_run_loop` re-reads `dependencies.agent_sink`,
        which is None for `local-flow run` / pill / --with-dictation, so the
        sink built by `_build_run_dependencies` is thrown away."""
        agent = AgentSink(f"unix:{_private_dir(tmp_path)}/test.sock")
        p = _pipeline(tmp_path, FakeTextSink())
        monkeypatch.setattr(
            "local_flow.app._build_run_dependencies",
            lambda config: RunDependencies(p, _SilentSource(), None, agent_sink=agent),
        )
        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", _NoopTap)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: _NoopKeyboard(),
        )
        reporter = FakeReporter()
        _run_loop(_config(agent_hotkey="f10"), "push-to-talk", reporter)
        assert not any("no agent sink was built" in d for _s, d in reporter.events)

    def test_build_run_dependencies_uses_platform_default_target(self, tmp_path, monkeypatch):
        from local_flow.app import _build_run_dependencies

        monkeypatch.setattr("local_flow.audio.capture.SounddeviceSource", lambda **_: object())
        config = _config(
            data_dir=str(tmp_path),
            asr_backend="mock",
            lmstudio_base_url="http://127.0.0.1:1/v1",
            agent_hotkey="f10",
        )
        sink = _build_run_dependencies(config).agent_sink
        assert isinstance(sink, AgentSink)
        assert sink.target == f"unix:{_get_default_agent_socket_path()}"

    def test_nb_invalid_agent_target_not_rejected_at_config_load(self):
        """NB (config.py:225): a bad target is only caught when the engine
        builds the sink, so Settings can save it with no error."""
        with pytest.raises(ConfigError):
            _config(agent_hotkey="f10", agent_target="relative/path.sock")


# --- AC3: target parsing -----------------------------------------------------


@pytest.mark.parametrize("target", ["unix:../../x", "unix:relative/agent.sock"])
def test_nb_relative_unix_path_accepted(target):
    """NB (sink.py:86-93): a relative path resolves against the CWD at send
    time. Require an absolute path (after expanduser)."""
    with pytest.raises(ConfigError):
        AgentSink(target)


def test_bug_B5_nul_byte_path_escapes_quiet_path(caplog):
    """B5 (sink.py:169): a NUL byte makes Path/stat raise ValueError, which
    insert() does not catch. Reject it in _validate_target_format."""
    with pytest.raises(ConfigError):
        AgentSink("unix:/nonexistent-lfa/x\x00y")


# --- Default path, ~ expansion, spaces, sun_path -----------------------------


class TestDefaultPath:
    def test_macos_default_is_application_support(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setenv("HOME", "/Users/someone")
        assert _get_default_agent_socket_path() == (
            "/Users/someone/Library/Application Support/poppy-desk/agent.sock"
        )

    def test_linux_default_prefers_xdg_runtime_dir(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/501")
        assert _get_default_agent_socket_path() == "/run/user/501/poppy-desk/agent.sock"

    def test_macos_default_fits_sun_path_for_a_long_username(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setenv("HOME", "/Users/" + "u" * 32)
        assert len(_get_default_agent_socket_path().encode()) < 104

    @POSIX_ONLY
    def test_tilde_and_space_in_path_deliver(self, short_dir, monkeypatch):
        home = short_dir / "h"
        home.mkdir()
        sock_dir = _private_dir(home, "App Support")
        monkeypatch.setenv("HOME", str(home))
        server = _Server(sock_dir / "a.sock")
        AgentSink("unix:~/App Support/a.sock").insert("open safari")
        assert b'"text": "open safari"' in server.close()

    @POSIX_ONLY
    def test_over_long_socket_path_is_quiet(self, short_dir, caplog):
        """Over-long paths are now rejected at construction time (fail-fast)."""
        deep = _private_dir(short_dir, "d" * (SUN_PATH_MAX + 10))
        # Now raises ConfigError at construction instead of logging at send time
        with pytest.raises(ConfigError):
            AgentSink(f"unix:{deep}/agent.sock")


# --- Unix socket ownership, permissions, symlinks, TOCTOU ---------------------


@POSIX_ONLY
class TestUnixSocketSecurity:
    def test_private_dir_delivers(self, short_dir):
        server = _Server(_private_dir(short_dir) / "agent.sock")
        AgentSink(f"unix:{server.path}").insert("open safari")
        assert b"open safari" in server.close()

    @pytest.mark.parametrize("mode", [0o777, 0o770, 0o730, 0o703], ids=["777", "770", "730", "703"])
    def test_group_or_world_writable_dir_rejected(self, short_dir, caplog, mode):
        d = _private_dir(short_dir)
        server = _Server(d / "agent.sock")
        d.chmod(mode)
        with caplog.at_level(logging.WARNING, logger="local_flow.agent.sink"):
            AgentSink(f"unix:{server.path}").insert("open safari")
        assert server.close() == b""
        assert _warned(caplog, "0700") or _warned(caplog, "mode")

    def test_socket_owned_by_other_uid_rejected(self, short_dir, caplog, monkeypatch):
        server = _Server(_private_dir(short_dir) / "agent.sock")
        real_uid = os.getuid()
        monkeypatch.setattr(sink_mod.os, "getuid", lambda: real_uid + 1)
        with caplog.at_level(logging.WARNING, logger="local_flow.agent.sink"):
            AgentSink(f"unix:{server.path}").insert("open safari")
        assert server.close() == b""
        assert _warned(caplog, "owned by uid")

    def test_nb_dir_mode_0755_is_accepted_despite_0700_claim(self, short_dir, caplog):
        """NB (sink.py:149-150): only group/other WRITE bits are rejected, so a
        0755 dir passes. Either enforce `mode & 0o077 == 0` as documented, or
        fix the docs/PR description to say "not group/world writable"."""
        d = _private_dir(short_dir)
        server = _Server(d / "agent.sock")
        d.chmod(0o755)
        AgentSink(f"unix:{server.path}").insert("open safari")
        assert server.close() == b""

    def test_symlinked_socket_into_world_writable_dir_rejected(self, short_dir, caplog):
        evil = short_dir / "evil"
        evil.mkdir()
        server = _Server(evil / "agent.sock")
        evil.chmod(0o777)
        link = _private_dir(short_dir) / "agent.sock"
        link.symlink_to(server.path)
        AgentSink(f"unix:{link}").insert("open safari")
        assert server.close() == b""

    def test_symlinked_dir_component_into_world_writable_dir_rejected(self, short_dir):
        evil = short_dir / "evil"
        evil.mkdir()
        server = _Server(evil / "agent.sock")
        evil.chmod(0o777)
        (short_dir / "looks-private").symlink_to(evil, target_is_directory=True)
        AgentSink(f"unix:{short_dir}/looks-private/agent.sock").insert("open safari")
        assert server.close() == b""

    @NOT_ROOT
    def test_bug_T2_writable_ancestor_not_checked(self, short_dir):
        """T2 (sink.py:135-154): only `socket_path.parent` is checked. If any
        ancestor is writable by others (and not sticky), another user can
        rename the private dir away and plant their own. Check every ancestor
        (like ssh StrictModes) or verify the peer after connect."""
        shared = short_dir / "shared"
        shared.mkdir()
        server = _Server(_private_dir(shared) / "agent.sock")
        shared.chmod(0o777)  # world-writable, no sticky bit
        AgentSink(f"unix:{server.path}").insert("open safari")
        assert server.close() == b""

    def test_bug_T1_path_swapped_after_check_is_followed(self, short_dir, monkeypatch):
        """T1 (sink.py:179-187): validation stats the path, then connect()
        re-walks the same string. A swap in between (here: the private dir is
        replaced with a symlink to a world-writable dir) sends the command to
        the impostor. Fix: verify the peer after connect and before sendall
        (getpeereid / LOCAL_PEERCRED on macOS, SO_PEERCRED on Linux) and/or
        re-validate the path post-connect."""
        private = _private_dir(short_dir)
        real = _Server(private / "agent.sock")
        evil = short_dir / "evil"
        evil.mkdir()
        impostor = _Server(evil / "agent.sock")
        evil.chmod(0o777)
        original = AgentSink._validate_socket_security

        def validate_then_swap(self, socket_path):
            original(self, socket_path)
            private.rename(short_dir / f"moved-{uuid.uuid4().hex[:6]}")
            private.symlink_to(evil, target_is_directory=True)

        monkeypatch.setattr(AgentSink, "_validate_socket_security", validate_then_swap)
        AgentSink(f"unix:{private}/agent.sock").insert("open safari")
        real.close()
        assert impostor.close() == b"", "command was delivered to a swapped-in socket"


# --- AC5: quiet failure on every path -----------------------------------------


class TestQuietFailure:
    @staticmethod
    def _assert_quiet(sink: AgentSink, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="local_flow.agent.sink"):
            sink.insert("open safari")
        assert _warned(caplog, "failed to send")

    @POSIX_ONLY
    def test_socket_missing(self, short_dir, caplog):
        self._assert_quiet(AgentSink(f"unix:{_private_dir(short_dir)}/none.sock"), caplog)

    @POSIX_ONLY
    def test_stale_socket_file_connect_refused(self, short_dir, caplog):
        path = _private_dir(short_dir) / "stale.sock"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(path))
        s.close()
        self._assert_quiet(AgentSink(f"unix:{path}"), caplog)

    @POSIX_ONLY
    def test_peer_closes_immediately(self, short_dir):
        path = _private_dir(short_dir) / "slam.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(path))
        srv.listen(1)

        def accept_and_close() -> None:
            conn, _ = srv.accept()
            conn.close()

        t = threading.Thread(target=accept_and_close, daemon=True)
        t.start()
        try:
            AgentSink(f"unix:{path}").insert("x" * 1_000_000)
        finally:
            t.join(timeout=2)
            srv.close()

    @POSIX_ONLY
    def test_nb_failed_send_reported_as_inserted(self, tmp_path, short_dir):
        """NB (app.py:1452): `inserted` fires even though nothing was delivered;
        the failure only goes to `logging`, so the pill/tray show success."""
        normal = FakeTextSink()
        p = _pipeline(tmp_path, normal)
        reporter = FakeReporter()
        agent = AgentSink(f"unix:{_private_dir(short_dir)}/none.sock")
        _handle_utterance(p, reporter, PCM, 16000, sink_override=agent, skip_history=True)
        assert normal.events == []
        assert not any(state == "inserted" for state, _ in reporter.events)
