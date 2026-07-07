"""Selection capture: read/replace the current OS text selection via clipboard.

Highlighting text in any app and pressing a hotkey (see the Phase 6 transform
hotkey) needs a way to *read* that selection without any app-specific
integration: temporarily hijack the clipboard (save it, clear it, synthesize
the platform copy chord, poll for a change), then put a transform's result
back with the paste chord and restore the original clipboard afterward.

All OS interaction (reading/writing the clipboard, synthesizing key chords)
lives behind :class:`SelectionBackend` so :class:`SelectionCapture` itself is
pure state-machine logic, testable with :class:`MockSelectionBackend` and no
real keyboard/clipboard.
"""

from __future__ import annotations

import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from local_flow.errors import HotkeyBackendMissingError


class SelectionBackend(ABC):
    """The OS-touching part of selection capture/replace, injectable."""

    @abstractmethod
    def read_clipboard(self) -> str:
        """Return the current clipboard text."""

    @abstractmethod
    def write_clipboard(self, text: str) -> None:
        """Set the clipboard to ``text``."""

    @abstractmethod
    def send_copy(self) -> None:
        """Synthesize the platform copy chord (Cmd+C / Ctrl+C)."""

    @abstractmethod
    def send_paste(self) -> None:
        """Synthesize the platform paste chord (Cmd+V / Ctrl+V)."""


class PynputSelectionBackend(SelectionBackend):
    """Real desktop backend: pynput for key chords, pyperclip for the clipboard.

    Both are imported lazily (in ``__init__``, not at module scope) so the
    rest of the app -- and every test -- stays importable without the
    ``desktop`` extra installed.
    """

    def __init__(self) -> None:
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise HotkeyBackendMissingError(
                "The 'pynput' package is not installed.",
                hint="Install desktop extras: uv sync --extra desktop.",
            ) from exc
        try:
            import pyperclip
        except ImportError as exc:
            raise HotkeyBackendMissingError(
                "The 'pyperclip' package is not installed.",
                hint="Install desktop extras: uv sync --extra desktop.",
            ) from exc
        self._keyboard = keyboard
        self._pyperclip = pyperclip
        self._controller = keyboard.Controller()
        self._modifier = keyboard.Key.cmd if sys.platform == "darwin" else keyboard.Key.ctrl

    def read_clipboard(self) -> str:
        return self._pyperclip.paste()

    def write_clipboard(self, text: str) -> None:
        self._pyperclip.copy(text)

    def _tap(self, char: str) -> None:
        with self._controller.pressed(self._modifier):
            self._controller.tap(char)

    def send_copy(self) -> None:
        self._tap("c")

    def send_paste(self) -> None:
        self._tap("v")


class MockSelectionBackend(SelectionBackend):
    """Scripted clipboard content + an event log, for tests.

    ``selection_text`` (default ``None``) is what ``send_copy()`` reveals on
    the clipboard, simulating the OS's copy landing -- ``None`` simulates
    "nothing selected" (the clipboard is left exactly as ``write_clipboard``
    most recently set it, which is ``""`` right after :meth:`SelectionCapture
    <local_flow.transforms.selection.SelectionCapture>.capture` clears it).
    Real copies are asynchronous; that timing race is what
    ``SelectionCapture``'s poll loop (and its injected ``sleep``) exist to
    handle, so this mock models the *outcome*, not the async timing itself.
    """

    def __init__(self, clipboard: str = "", selection_text: str | None = None) -> None:
        self.clipboard = clipboard
        self.selection_text = selection_text
        self.events: list[str] = []

    def read_clipboard(self) -> str:
        return self.clipboard

    def write_clipboard(self, text: str) -> None:
        self.clipboard = text
        self.events.append(f"write:{text}")

    def send_copy(self) -> None:
        self.events.append("copy")
        if self.selection_text is not None:
            self.clipboard = self.selection_text

    def send_paste(self) -> None:
        self.events.append("paste")


class SelectionCapture:
    """Capture the current OS selection, and later replace it in place.

    ``capture()``/``replace()`` are meant to run back-to-back within one
    transform operation: ``capture()`` remembers the clipboard as it was
    *before* capture started, and ``replace()`` restores exactly that saved
    value afterward.

    Contract for callers (the ``transform --selection`` CLI branch and, per
    the Phase 6 plan, the hotkey callback): every call to ``capture()`` must
    be followed by exactly one of ``replace()`` or ``restore()`` before
    control leaves the caller, even on an exception -- ``capture()`` has
    already overwritten the OS clipboard by the time it returns (see its
    docstring), so skipping both would leave the user's original clipboard
    content lost. Wrap the capture in a ``try/except`` (or ``finally``) that
    calls ``restore()`` on any failure path; ``replace()`` already restores on
    the happy path. Both methods are idempotent with respect to each other:
    once one of them runs, a stray extra call to ``restore()`` is a no-op.
    """

    def __init__(
        self,
        backend: SelectionBackend,
        poll_timeout_s: float = 0.4,
        poll_interval_s: float = 0.02,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.backend = backend
        self.poll_timeout_s = poll_timeout_s
        self.poll_interval_s = poll_interval_s
        self._sleep = sleep
        self._saved_clipboard = ""
        self._captured = False

    def capture(self) -> str:
        """Return the current selection's text, or ``""`` when nothing is selected.

        Algorithm: save the clipboard, clear it (write ``""``), synthesize a
        copy, then poll until the clipboard changes away from ``""`` or the
        timeout elapses.

        Clearing the clipboard *first* is what makes "nothing selected"
        distinguishable from "the selection happens to be identical to the
        old clipboard content". Without the clear, copying a selection that
        is byte-identical to what was already on the clipboard would look
        exactly like a no-op copy (nothing to detect changing) -- and a real
        no-selection copy (many apps leave the clipboard untouched when
        there is no selection) would look identical too. Clearing first
        collapses both prior-content cases to the same starting state
        (``""``), so *any* non-empty clipboard content observed afterward
        must have come from this copy. The one case that remains genuinely
        ambiguous -- the selection itself being the empty string -- is
        indistinguishable from "no selection" by definition, and is treated
        as "no selection" (returns ``""``), which is the documented,
        harmless behavior.
        """
        self._saved_clipboard = self.backend.read_clipboard()
        self._captured = True
        self.backend.write_clipboard("")
        self.backend.send_copy()

        elapsed = 0.0
        current = self.backend.read_clipboard()
        while current == "" and elapsed < self.poll_timeout_s:
            self._sleep(self.poll_interval_s)
            elapsed += self.poll_interval_s
            current = self.backend.read_clipboard()
        return current

    def replace(self, text: str) -> None:
        """Write ``text`` to the clipboard, paste it, then restore the saved clipboard.

        Paste is asynchronous: the target application pulls from the
        clipboard on its own event loop rather than synchronously as
        ``send_paste()`` returns. Restoring the previous clipboard content
        immediately afterward risks a race where the app reads back the
        *restored* (old) content instead of ``text``. The short settle sleep
        before restoring gives the paste time to land first -- a best-effort
        delay, not a guarantee, but far better than no delay at all.
        """
        self.backend.write_clipboard(text)
        self.backend.send_paste()
        self._sleep(0.15)
        self.backend.write_clipboard(self._saved_clipboard)
        self._captured = False

    def restore(self) -> None:
        """Write the saved clipboard back, if ``capture()`` has run since the
        last ``replace()``/``restore()``.

        A no-op when nothing has been captured (before the first
        ``capture()``, or after a ``replace()``/``restore()`` already ran) --
        this is what makes it safe for callers to call unconditionally on any
        non-happy-path exit from a capture, without tracking whether a
        capture actually happened.
        """
        if not self._captured:
            return
        self.backend.write_clipboard(self._saved_clipboard)
        self._captured = False
