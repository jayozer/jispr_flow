"""Space as push-to-talk: hold to dictate, tap to type a normal space.

The state machine is pure and timer-agnostic: the platform glue schedules
``hold_elapsed(generation)`` after the hold threshold. Generations make a
timer that fires after the key was already released a no-op.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from local_flow.errors import HotkeyBackendMissingError
from local_flow.hotkeys.base import HotkeyListener

_IDLE, _PENDING, _RECORDING, _CANCELLED = "idle", "pending", "recording", "cancelled"


@dataclass
class SpaceActions:
    start: bool = False  # begin recording
    stop: bool = False  # finish recording and insert
    cancel: bool = False  # discard the recording
    replay_space: bool = False  # synthesize the swallowed space (it was a tap)
    start_timer: bool = False  # schedule hold_elapsed(machine.generation)


class SpaceStateMachine:
    def __init__(self) -> None:
        self.state = _IDLE
        self.generation = 0

    def space_down(self) -> SpaceActions:
        if self.state == _IDLE:
            self.state = _PENDING
            self.generation += 1
            return SpaceActions(start_timer=True)
        return SpaceActions()  # OS auto-repeat while pending/recording/cancelled

    def space_up(self) -> SpaceActions:
        if self.state == _PENDING:
            self.state = _IDLE
            self.generation += 1  # invalidate the in-flight hold timer
            return SpaceActions(replay_space=True)
        if self.state == _RECORDING:
            self.state = _IDLE
            return SpaceActions(stop=True)
        if self.state == _CANCELLED:
            self.state = _IDLE  # physical release after a cancel: swallow silently
        return SpaceActions()

    def hold_elapsed(self, generation: int) -> SpaceActions:
        if self.state == _PENDING and generation == self.generation:
            self.state = _RECORDING
            return SpaceActions(start=True)
        return SpaceActions()

    def cancel_down(self) -> SpaceActions:
        if self.state == _RECORDING:
            self.state = _CANCELLED  # stay parked until the physical space release
            return SpaceActions(cancel=True)
        return SpaceActions()


_MAC_SPACE_KEYCODE = 49
_WIN_VK_SPACE = 0x20


class SpacePushToTalk(HotkeyListener):
    """Hold Space to dictate; a quick tap still types a normal space."""

    def __init__(self, hold_ms: int = 250, cancel_key: str = "esc") -> None:
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise HotkeyBackendMissingError(
                "The 'pynput' package is not installed.",
                hint="Install desktop extras: uv sync --extra desktop.",
            ) from exc
        self._keyboard = keyboard
        self.hold_ms = hold_ms
        self._cancel = getattr(keyboard.Key, cancel_key.lower(), None) if cancel_key else None
        self._machine = SpaceStateMachine()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._controller = keyboard.Controller()
        self._replaying = False
        self._on_press: Callable[[], None] | None = None
        self._on_release: Callable[[], None] | None = None
        self._on_cancel: Callable[[], None] | None = None

    # -- actions ---------------------------------------------------------
    def _apply(self, actions: SpaceActions) -> None:
        if actions.start_timer:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(
                self.hold_ms / 1000.0, self._fire_hold, args=[self._machine.generation]
            )
            self._timer.daemon = True
            self._timer.start()
        if actions.replay_space:
            self._replay_space()
        if actions.start and self._on_press is not None:
            self._on_press()
        if actions.stop and self._on_release is not None:
            self._on_release()
        if actions.cancel and self._on_cancel is not None:
            self._on_cancel()

    def _fire_hold(self, generation: int) -> None:
        with self._lock:
            actions = self._machine.hold_elapsed(generation)
        self._apply(actions)

    def _replay_space(self) -> None:
        self._replaying = True
        try:
            self._controller.tap(self._keyboard.Key.space)
        finally:
            self._replaying = False

    # -- event plumbing ---------------------------------------------------
    def _handle_press(self, key) -> None:
        if self._replaying:
            return
        if key == self._keyboard.Key.space:
            with self._lock:
                actions = self._machine.space_down()
            self._apply(actions)
        elif self._cancel is not None and key == self._cancel:
            with self._lock:
                actions = self._machine.cancel_down()
            self._apply(actions)

    def _handle_release(self, key) -> None:
        if self._replaying:
            return
        if key == self._keyboard.Key.space:
            with self._lock:
                actions = self._machine.space_up()
            self._apply(actions)

    def _darwin_intercept(self, event_type, event):
        if self._replaying:
            return event
        import Quartz

        keycode = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode
        )
        if keycode == _MAC_SPACE_KEYCODE:
            return None  # swallow: taps are replayed, holds dictate
        return event

    def run(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._on_press, self._on_release, self._on_cancel = on_press, on_release, on_cancel
        keyboard = self._keyboard
        listener_box: list = []

        def win32_event_filter(msg, data):
            if not self._replaying and data.vkCode == _WIN_VK_SPACE and listener_box:
                listener_box[0].suppress_event()
            return True

        try:
            listener = keyboard.Listener(
                on_press=self._handle_press,
                on_release=self._handle_release,
                darwin_intercept=self._darwin_intercept,
                win32_event_filter=win32_event_filter,
            )
            listener_box.append(listener)
            with listener:
                listener.join()
        except Exception as exc:
            raise HotkeyBackendMissingError(
                f"The space hotkey listener failed: {exc}",
                hint="macOS: grant Accessibility AND Input Monitoring permission "
                "to your terminal, then restart it.",
            ) from exc
        finally:
            if self._timer is not None:
                self._timer.cancel()
