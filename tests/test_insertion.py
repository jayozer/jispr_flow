"""Text sinks: fake sink recording and fallback behaviour."""

import platform
import shutil
import subprocess
import sys

import pytest

from local_flow.errors import ClipboardError, PasteError
from local_flow.insertion import desktop
from local_flow.insertion.base import FakeTextSink, InsertionManager, TextSink


class BoomSink(TextSink):
    """Always fails, like a paste keystroke on Wayland."""

    name = "boom"

    def insert(self, text: str) -> None:
        raise PasteError("Sending the paste keystroke failed: synthetic input blocked.")

    def press_key(self, key: str) -> None:
        raise PasteError("Key press blocked.")


class TestFakeTextSink:
    def test_records_inserts_and_keys(self):
        sink = FakeTextSink()
        sink.insert("hello")
        sink.press_key("enter")
        assert sink.events == [("insert", "hello"), ("key", "enter")]
        assert sink.text == "hello\n"


class TestInsertionFallback:
    def test_falls_back_to_next_sink_when_paste_fails(self):
        fallback = FakeTextSink()
        manager = InsertionManager([BoomSink(), fallback])
        manager.insert("the text")
        assert fallback.events == [("insert", "the text")]
        assert manager.last_used == "fake"

    def test_first_sink_used_when_it_works(self):
        primary, fallback = FakeTextSink(), FakeTextSink()
        manager = InsertionManager([primary, fallback])
        manager.insert("hi")
        assert primary.events == [("insert", "hi")]
        assert fallback.events == []

    def test_key_actions_also_fall_back(self):
        fallback = FakeTextSink()
        manager = InsertionManager([BoomSink(), fallback])
        manager.press_key("enter")
        assert fallback.events == [("key", "enter")]

    def test_all_sinks_failing_raises_paste_error_listing_each(self):
        manager = InsertionManager([BoomSink(), FakeTextSink(fail=True)])
        with pytest.raises(PasteError) as excinfo:
            manager.insert("doomed")
        message = str(excinfo.value)
        assert "boom:" in message
        assert "fake:" in message
        assert "Accessibility" in message  # actionable hint present

    def test_empty_sink_list_rejected(self):
        with pytest.raises(ValueError):
            InsertionManager([])


class _FakeRun:
    """Stands in for ``subprocess.run``: records calls, fails chosen tools."""

    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.calls: list[tuple[list[str], bytes]] = []

    def __call__(self, cmd, input=None, check=True, timeout=None):
        self.calls.append((list(cmd), input))
        if cmd[0] in self.failing:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0)

    @property
    def tools_run(self) -> list[str]:
        return [cmd[0] for cmd, _ in self.calls]


class TestCopyToClipboardCliFallback:
    """The pyperclip-less CLI-tool path of ``copy_to_clipboard``.

    ``pyperclip`` is forced to ImportError via a ``None`` sys.modules entry
    (the same trick tests/test_field_context.py uses for
    ``ApplicationServices``), and ``platform.system``/``shutil.which``/
    ``subprocess.run`` are all monkeypatched -- nothing here ever touches a
    real clipboard, display, Wayland/X11 session, or Windows.
    """

    def _setup(self, monkeypatch, system: str, run: _FakeRun, installed=None):
        monkeypatch.setitem(sys.modules, "pyperclip", None)  # simulates ImportError
        monkeypatch.setattr(platform, "system", lambda: system)
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name: f"/usr/bin/{name}" if installed is None or name in installed else None,
        )
        monkeypatch.setattr(subprocess, "run", run)

    def test_wl_copy_failure_falls_through_to_xclip(self, monkeypatch):
        # wl-copy installed but failing (e.g. an X11 session): the chain must
        # continue to xclip instead of re-raising the first failure.
        run = _FakeRun(failing={"wl-copy"})
        self._setup(monkeypatch, "Linux", run)

        desktop.copy_to_clipboard("hello")  # must not raise

        assert run.tools_run == ["wl-copy", "xclip"]

    def test_wl_copy_and_xclip_failures_fall_through_to_xsel(self, monkeypatch):
        run = _FakeRun(failing={"wl-copy", "xclip"})
        self._setup(monkeypatch, "Linux", run)

        desktop.copy_to_clipboard("hello")

        assert run.tools_run == ["wl-copy", "xclip", "xsel"]

    def test_every_tool_failing_raises_after_trying_all_and_lists_each(self, monkeypatch):
        run = _FakeRun(failing={"wl-copy", "xclip", "xsel"})
        self._setup(monkeypatch, "Linux", run)

        with pytest.raises(ClipboardError) as excinfo:
            desktop.copy_to_clipboard("hello")

        assert run.tools_run == ["wl-copy", "xclip", "xsel"]
        message = str(excinfo.value)
        assert "wl-copy" in message
        assert "xclip" in message
        assert "xsel" in message

    def test_missing_tools_are_skipped_without_being_run(self, monkeypatch):
        # Pins existing behavior: absent tools never reach subprocess.run.
        run = _FakeRun()
        self._setup(monkeypatch, "Linux", run, installed={"xsel"})

        desktop.copy_to_clipboard("hello")

        assert run.tools_run == ["xsel"]

    def test_no_tool_installed_raises_not_found(self, monkeypatch):
        # Pins existing behavior: nothing installed -> the "not found" error,
        # not a tool-failure one.
        run = _FakeRun()
        self._setup(monkeypatch, "Linux", run, installed=set())

        with pytest.raises(ClipboardError, match="No clipboard mechanism found"):
            desktop.copy_to_clipboard("hello")

        assert run.tools_run == []

    def test_windows_clip_receives_utf16le_with_bom(self, monkeypatch):
        # clip.exe decodes its stdin as the OEM/ANSI code page unless the
        # bytes start with a UTF-16LE BOM; piping UTF-8 mojibakes non-ASCII.
        run = _FakeRun()
        self._setup(monkeypatch, "Windows", run)

        desktop.copy_to_clipboard("héllo \N{MICROPHONE}")

        assert run.tools_run == ["clip"]
        _, payload = run.calls[0]
        assert payload == b"\xff\xfe" + "héllo \N{MICROPHONE}".encode("utf-16-le")

    def test_non_windows_tools_receive_utf8(self, monkeypatch):
        # Pins existing behavior: pbcopy (and the Linux tools) keep UTF-8.
        run = _FakeRun()
        self._setup(monkeypatch, "Darwin", run)

        desktop.copy_to_clipboard("héllo \N{MICROPHONE}")

        assert run.tools_run == ["pbcopy"]
        _, payload = run.calls[0]
        assert payload == "héllo \N{MICROPHONE}".encode()
