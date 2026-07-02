"""Real desktop text sinks: clipboard+paste keystroke, typing, clipboard-only.

All OS interaction is confined to this module and imported lazily so the rest
of the app (and the tests) stay headless.
"""

from __future__ import annotations

import platform
import shutil
import subprocess

from local_flow.errors import ClipboardError, HotkeyBackendMissingError, PasteError
from local_flow.insertion.base import TextSink


def copy_to_clipboard(text: str) -> None:
    """Copy text using pyperclip if present, else a platform CLI tool."""
    try:
        import pyperclip
    except ImportError:
        pyperclip = None
    if pyperclip is not None:
        try:
            pyperclip.copy(text)
            return
        except Exception as exc:  # pyperclip raises its own exception type
            raise ClipboardError(
                f"Copying to the clipboard failed: {exc}",
                hint="On Linux install xclip or wl-clipboard; on servers there "
                "may be no clipboard at all.",
            ) from exc

    system = platform.system()
    candidates: list[list[str]] = []
    if system == "Darwin":
        candidates = [["pbcopy"]]
    elif system == "Windows":
        candidates = [["clip"]]
    else:
        candidates = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]]
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=5)
            return
        except (subprocess.SubprocessError, OSError) as exc:
            raise ClipboardError(f"Clipboard tool {cmd[0]!r} failed: {exc}") from exc
    raise ClipboardError(
        "No clipboard mechanism found.",
        hint="Install desktop extras (uv sync --extra desktop) or a clipboard "
        "tool: xclip/xsel (X11) or wl-clipboard (Wayland).",
    )


def _keyboard():
    try:
        from pynput import keyboard
    except ImportError as exc:
        raise HotkeyBackendMissingError(
            "The 'pynput' package is not installed.",
            hint="Install desktop extras: uv sync --extra desktop.",
        ) from exc
    return keyboard


class ClipboardPasteSink(TextSink):
    """Copy to the clipboard, then send the platform paste keystroke."""

    name = "clipboard-paste"

    def insert(self, text: str) -> None:
        try:
            copy_to_clipboard(text)
        except ClipboardError as exc:
            raise PasteError(exc.message, hint=exc.hint) from exc
        keyboard = _keyboard()
        controller = keyboard.Controller()
        modifier = keyboard.Key.cmd if platform.system() == "Darwin" else keyboard.Key.ctrl
        try:
            with controller.pressed(modifier):
                controller.press("v")
                controller.release("v")
        except Exception as exc:
            raise PasteError(
                f"Sending the paste keystroke failed: {exc}",
                hint="macOS needs Accessibility permission for your terminal; "
                "Wayland blocks synthetic keystrokes for most apps. The text is "
                "already on the clipboard, so you can paste manually.",
            ) from exc

    def press_key(self, key: str) -> None:
        keyboard = _keyboard()
        controller = keyboard.Controller()
        key_obj = getattr(keyboard.Key, key, None)
        if key_obj is None:
            raise PasteError(f"Unknown key action {key!r}.")
        try:
            controller.press(key_obj)
            controller.release(key_obj)
        except Exception as exc:
            raise PasteError(f"Pressing {key} failed: {exc}") from exc


class TypingSink(TextSink):
    """Types the text with synthetic keystrokes (slower, but paste-proof apps)."""

    name = "typing"

    def insert(self, text: str) -> None:
        keyboard = _keyboard()
        try:
            keyboard.Controller().type(text)
        except Exception as exc:
            raise PasteError(
                f"Synthetic typing failed: {exc}",
                hint="macOS needs Accessibility permission; Wayland generally "
                "blocks synthetic input.",
            ) from exc

    def press_key(self, key: str) -> None:
        ClipboardPasteSink().press_key(key)


class ClipboardOnlySink(TextSink):
    """Last resort: put the text on the clipboard and tell the user."""

    name = "clipboard-only"

    def insert(self, text: str) -> None:
        try:
            copy_to_clipboard(text)
        except ClipboardError as exc:
            raise PasteError(exc.message, hint=exc.hint) from exc
        print("[local-flow] Text copied to clipboard - paste with Ctrl+V / Cmd+V.")

    def press_key(self, key: str) -> None:
        print(f"[local-flow] Please press {key} yourself (clipboard-only mode).")
