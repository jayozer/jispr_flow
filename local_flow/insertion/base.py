"""Text sink interface, the in-memory fake, and the fallback manager."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from local_flow.errors import LocalFlowError, PasteError


class TextSink(ABC):
    """Delivers final text into 'the active application'."""

    name: str = "sink"

    @abstractmethod
    def insert(self, text: str) -> None:
        """Insert ``text`` at the current cursor position."""

    def press_key(self, key: str) -> None:  # noqa: B027 - optional capability
        """Perform a key action ('enter', 'tab'). Default: no-op."""


class FakeTextSink(TextSink):
    """Records everything instead of touching the OS. Used in tests/demo."""

    name = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[tuple[str, str]] = []

    def insert(self, text: str) -> None:
        if self.fail:
            raise PasteError("Fake sink was configured to fail.", hint="Test-only failure.")
        self.events.append(("insert", text))

    def press_key(self, key: str) -> None:
        if self.fail:
            raise PasteError("Fake sink was configured to fail.", hint="Test-only failure.")
        self.events.append(("key", key))

    @property
    def text(self) -> str:
        """What the target document would contain (enter renders as newline)."""
        rendered: list[str] = []
        for kind, value in self.events:
            if kind == "insert":
                rendered.append(value)
            elif kind == "key" and value == "enter":
                rendered.append("\n")
            elif kind == "key" and value == "tab":
                rendered.append("\t")
        return "".join(rendered)


class InsertionManager(TextSink):
    """Tries sinks in order until one succeeds; explains every failure.

    Typical order: paste keystroke -> synthetic typing -> clipboard-only.
    """

    name = "auto"

    def __init__(self, sinks: Sequence[TextSink]) -> None:
        if not sinks:
            raise ValueError("InsertionManager needs at least one sink")
        self.sinks = list(sinks)
        self.last_used: str | None = None

    def _try_all(self, action: str, func_name: str, *args: str) -> None:
        failures: list[str] = []
        for sink in self.sinks:
            try:
                getattr(sink, func_name)(*args)
            except LocalFlowError as exc:
                failures.append(f"{sink.name}: {exc.message}")
                continue
            self.last_used = sink.name
            return
        raise PasteError(
            f"All text insertion methods failed while trying to {action}:\n  - "
            + "\n  - ".join(failures),
            hint="On macOS grant Accessibility permission to your terminal "
            "(System Settings -> Privacy & Security -> Accessibility). On Linux "
            "install xclip/xsel (X11) or wl-clipboard (Wayland); on Wayland "
            "synthetic keystrokes may be blocked, so use the clipboard method "
            "and paste manually with Ctrl+V.",
        )

    def insert(self, text: str) -> None:
        self._try_all("insert text", "insert", text)

    def press_key(self, key: str) -> None:
        self._try_all(f"press {key}", "press_key", key)
