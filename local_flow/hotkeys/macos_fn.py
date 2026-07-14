"""Fn-key push-to-talk on macOS via a suppressing Quartz event tap.

pynput has no ``Key.fn``: the Fn key arrives as a ``flagsChanged`` event
(keycode 63) whose ``kCGEventFlagMaskSecondaryFn`` bit tracks the key
state. The tap consumes those Fn events while local-flow owns the hotkey so
macOS Dictation (or another global Fn listener) cannot transcribe and insert
the same utterance a second time. ``FnLogic`` is pure so CI can drive it with
fake events; only ``QuartzFnListener.run`` touches Quartz.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable

from local_flow.errors import HotkeyBackendMissingError
from local_flow.hotkeys.base import HotkeyListener, PushToTalkCore

FN_KEYCODE = 63  # kVK_Function
ESCAPE_KEYCODE = 53  # kVK_Escape
_CANCEL_KEYCODES = {"esc": ESCAPE_KEYCODE}


class FnLogic:
    """Derives press/release/cancel from flagsChanged + keyDown events."""

    def __init__(self, core: PushToTalkCore, cancel_keycode: int | None) -> None:
        self._core = core
        self._cancel_keycode = cancel_keycode
        self._fn_down = False

    def flags_changed(self, fn_active: bool) -> None:
        if fn_active and not self._fn_down:
            self._fn_down = True
            self._core.key_down()
        elif not fn_active and self._fn_down:
            self._fn_down = False
            self._core.key_up()

    def key_down(self, keycode: int) -> None:
        if self._cancel_keycode is not None and keycode == self._cancel_keycode:
            self._core.cancel_down()


class QuartzFnListener(HotkeyListener):
    def __init__(
        self,
        cancel_key: str = "esc",
        cancel_gate: Callable[[], bool] | None = None,
    ) -> None:
        if sys.platform != "darwin":
            raise HotkeyBackendMissingError(
                "The Fn key can only be observed on macOS.",
                hint="Set LOCAL_FLOW_HOTKEY to another key, e.g. f9 or space.",
            )
        # Validate before the Quartz import so a bad cancel key fails loudly
        # (and headlessly testable) instead of silently disabling cancel.
        if cancel_key and cancel_key.lower() not in _CANCEL_KEYCODES:
            raise HotkeyBackendMissingError(
                f"The fn hotkey backend only supports 'esc' as the cancel key, "
                f"not {cancel_key!r}.",
                hint="Set LOCAL_FLOW_CANCEL_HOTKEY=esc, or choose a different hotkey.",
            )
        try:
            import Quartz
        except ImportError as exc:
            raise HotkeyBackendMissingError(
                "pyobjc-framework-Quartz is not installed.",
                hint="Install desktop extras: uv sync --extra desktop.",
            ) from exc
        self._quartz = Quartz
        self._cancel_keycode = _CANCEL_KEYCODES[cancel_key.lower()] if cancel_key else None
        self._cancel_gate = cancel_gate
        self._run_loop = None
        self._stop_requested = threading.Event()

    def run(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        q = self._quartz
        core = PushToTalkCore(on_press, on_release, on_cancel, self._cancel_gate)
        logic = FnLogic(core, self._cancel_keycode)
        tap_holder: list = []

        def callback(_proxy, event_type, event, _refcon):
            if event_type in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
                if tap_holder:
                    q.CGEventTapEnable(tap_holder[0], True)
                return event
            keycode = q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode)
            if event_type == q.kCGEventFlagsChanged:
                if keycode == FN_KEYCODE:
                    flags = q.CGEventGetFlags(event)
                    logic.flags_changed(bool(flags & q.kCGEventFlagMaskSecondaryFn))
                    # An active event tap may suppress an event by returning
                    # None. Fn is the configured push-to-talk key, so letting
                    # the same flagsChanged event continue would also trigger
                    # macOS Dictation or any other global Fn-based dictation
                    # app, producing a second transcript after ours.
                    return None
            elif event_type == q.kCGEventKeyDown:
                logic.key_down(keycode)
            return event

        mask = q.CGEventMaskBit(q.kCGEventFlagsChanged) | q.CGEventMaskBit(q.kCGEventKeyDown)
        tap = q.CGEventTapCreate(
            q.kCGSessionEventTap,
            q.kCGHeadInsertEventTap,
            q.kCGEventTapOptionDefault,
            mask,
            callback,
            None,
        )
        if tap is None:
            raise HotkeyBackendMissingError(
                "Could not create the macOS event tap for the Fn key.",
                hint="Grant Accessibility AND Input Monitoring permission to "
                "JiSpr (or the terminal running local-flow) in System Settings "
                "> Privacy & Security, then restart JiSpr or the terminal.",
            )
        tap_holder.append(tap)
        # Positive ground-truth signal for the macOS app: the tap now exists, so
        # the Fn hotkey is live regardless of what a parent process's TCC preflight
        # reports. The app clears its "Fn hotkey needs permission" warning on this.
        print("Fn hotkey listener active.", file=sys.stderr, flush=True)
        source = q.CFMachPortCreateRunLoopSource(None, tap, 0)
        run_loop = q.CFRunLoopGetCurrent()
        self._run_loop = run_loop
        q.CFRunLoopAddSource(run_loop, source, q.kCFRunLoopCommonModes)
        q.CGEventTapEnable(tap, True)
        try:
            if self._stop_requested.is_set():
                q.CFRunLoopStop(run_loop)
            q.CFRunLoopRun()
        finally:
            self._run_loop = None

    def stop(self) -> None:
        """Wake and stop the Quartz loop from another thread."""
        self._stop_requested.set()
        run_loop = self._run_loop
        if run_loop is not None:
            self._quartz.CFRunLoopStop(run_loop)
            self._quartz.CFRunLoopWakeUp(run_loop)
