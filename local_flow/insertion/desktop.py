"""Real desktop text sinks: clipboard+paste keystroke, typing, clipboard-only.

All OS interaction is confined to this module and imported lazily so the rest
of the app (and the tests) stay headless.
"""

from __future__ import annotations

import codecs
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
    # clip.exe decodes its stdin as the OEM/ANSI code page unless the bytes
    # start with a UTF-16LE BOM, so UTF-8 mojibakes any non-ASCII text there;
    # every other tool reads UTF-8.
    if system == "Windows":
        payload = codecs.BOM_UTF16_LE + text.encode("utf-16-le")
    else:
        payload = text.encode("utf-8")

    failures: list[str] = []
    last_exc: Exception | None = None
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(cmd, input=payload, check=True, timeout=5)
            return
        except (subprocess.SubprocessError, OSError) as exc:
            # An installed tool can still fail (e.g. wl-copy under X11):
            # keep going down the chain instead of giving up here.
            failures.append(f"{cmd[0]}: {exc}")
            last_exc = exc
    if failures:
        raise ClipboardError(
            "Every available clipboard tool failed: " + "; ".join(failures),
            hint="On Linux install xclip or wl-clipboard; on servers there "
            "may be no clipboard at all.",
        ) from last_exc
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


def _macos_quartz_key_tap(keycode: int, flags: int = 0) -> None:
    """Post one macOS key tap without consulting the current input source.

    ``pynput.keyboard.Controller`` resolves the active keyboard layout through
    Carbon's Text Services Manager. On current macOS releases that lookup must
    run on a particular dispatch queue; constructing/using a controller on the
    dictation processor thread can therefore terminate the whole process with
    ``SIGTRAP``. Quartz key events are safe to post from that worker and are
    all we need for fixed shortcuts such as Cmd+V, Return, and Tab.
    """
    try:
        import Quartz
    except ImportError as exc:
        raise HotkeyBackendMissingError(
            "The macOS Quartz bindings are not installed.",
            hint="Install desktop extras: uv sync --extra desktop.",
        ) from exc

    down = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
    up = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)
    if down is None or up is None:
        raise PasteError("Creating a macOS keyboard event failed.")
    if flags:
        Quartz.CGEventSetFlags(down, flags)
        Quartz.CGEventSetFlags(up, flags)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


class ClipboardPasteSink(TextSink):
    """Copy to the clipboard, then send the platform paste keystroke."""

    name = "clipboard-paste"

    def insert(self, text: str) -> None:
        try:
            copy_to_clipboard(text)
        except ClipboardError as exc:
            raise PasteError(exc.message, hint=exc.hint) from exc
        try:
            if platform.system() == "Darwin":
                import Quartz

                # ANSI V is virtual keycode 9. A fixed keycode avoids
                # pynput's background-thread keyboard-layout lookup.
                _macos_quartz_key_tap(9, Quartz.kCGEventFlagMaskCommand)
            else:
                keyboard = _keyboard()
                controller = keyboard.Controller()
                with controller.pressed(keyboard.Key.ctrl):
                    controller.press("v")
                    controller.release("v")
        except HotkeyBackendMissingError:
            raise
        except Exception as exc:
            raise PasteError(
                f"Sending the paste keystroke failed: {exc}",
                hint="macOS needs Accessibility permission for your terminal; "
                "Wayland blocks synthetic keystrokes for most apps. The text is "
                "already on the clipboard, so you can paste manually.",
            ) from exc

    def press_key(self, key: str) -> None:
        if platform.system() == "Darwin":
            # Return=36, Tab=48 in the stable macOS virtual-key table.
            keycode = {"enter": 36, "tab": 48}.get(key)
            if keycode is None:
                raise PasteError(f"Unknown key action {key!r}.")
            try:
                _macos_quartz_key_tap(keycode)
            except HotkeyBackendMissingError:
                raise
            except Exception as exc:
                raise PasteError(f"Pressing {key} failed: {exc}") from exc
            return

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
