"""Space as push-to-talk: hold to dictate, tap to type a normal space.

The state machine is pure and timer-agnostic: the platform glue schedules
``hold_elapsed(generation)`` after the hold threshold. Generations make a
timer that fires after the key was already released a no-op.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass

from local_flow.errors import HotkeyBackendMissingError
from local_flow.hotkeys.base import HotkeyListener, resolve_key

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
_WIN_KEYDOWN_MSGS = (0x0100, 0x0104)  # WM_KEYDOWN, WM_SYSKEYDOWN
_WIN_KEYUP_MSGS = (0x0101, 0x0105)  # WM_KEYUP, WM_SYSKEYUP
_LLKHF_INJECTED = 0x10  # low-level keyboard hook flag: event was synthesized


class SpacePushToTalk(HotkeyListener):
    """Hold Space to dictate; a quick tap still types a normal space."""

    def __init__(
        self,
        hold_ms: int = 250,
        cancel_key: str = "esc",
        cancel_gate: Callable[[], bool] | None = None,
    ) -> None:
        if sys.platform.startswith("linux"):
            # Same guard as create_hotkey_listener's space-on-Linux check, but
            # enforced here too so constructing this class directly (e.g. in
            # tests, or future callers) can't bypass it.
            raise HotkeyBackendMissingError(
                "Space push-to-talk needs per-event key suppression, which is "
                "not possible on Linux/X11.",
                hint="Use another key (LOCAL_FLOW_HOTKEY=f9) or hands-free "
                "mode (LOCAL_FLOW_MODE=hands-free).",
            )
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise HotkeyBackendMissingError(
                "The 'pynput' package is not installed.",
                hint="Install desktop extras: uv sync --extra desktop.",
            ) from exc
        self._keyboard = keyboard
        self.hold_ms = hold_ms
        self._cancel = resolve_key(keyboard, cancel_key) if cancel_key else None
        self._cancel_gate = cancel_gate
        self._machine = SpaceStateMachine()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._controller = keyboard.Controller()
        self._on_press: Callable[[], None] | None = None
        self._on_release: Callable[[], None] | None = None
        self._on_cancel: Callable[[], None] | None = None

    # -- actions ---------------------------------------------------------
    def _apply(self, actions: SpaceActions, generation: int) -> None:
        # Always called while holding self._lock: actions must be applied in
        # the order the machine produced them. Applying outside the lock lets
        # a hold-timer `start` and a release `stop` enqueue in inverted order
        # at the exact hold boundary. Safe under the lock: everything here is
        # sub-millisecond (queue.put via wrapped callbacks, Timer
        # create/cancel, one async event post) and nothing takes this lock.
        if actions.start_timer:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.hold_ms / 1000.0, self._fire_hold, args=[generation])
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
            self._apply(actions, self._machine.generation)

    def _replay_space(self) -> None:
        self._controller.tap(self._keyboard.Key.space)

    # -- event plumbing ---------------------------------------------------
    def _handle_press(self, key, injected=False) -> None:
        if key == self._keyboard.Key.space:
            if sys.platform == "win32":
                return  # win32_event_filter feeds the machine directly (never here)
            # Covers both our own replay tap and our TypingSink's typed
            # spaces (both are injected). Synthetic space events from other
            # software are therefore fully inert on darwin: the PID
            # interceptor swallows them (see _darwin_intercept) and this
            # guard ignores them if they ever reached here.
            if injected:
                return
            with self._lock:
                actions = self._machine.space_down()
                self._apply(actions, self._machine.generation)
        elif self._cancel is not None and key == self._cancel:
            # A TypingSink-typed cancel character (e.g. cancel key "x" inside
            # inserted text) must not spuriously cancel a recording.
            if injected:
                return
            with self._lock:
                actions = self._machine.cancel_down()
                self._apply(actions, self._machine.generation)
            # App-level gate: lets Esc discard a recording started by a
            # *different* listener (e.g. mouse push-to-talk) even though the
            # space machine itself produced no cancel action here. Only
            # checked when the machine did nothing, to avoid firing
            # `on_cancel` twice for a space-held cancel. The space machine's
            # own state is left untouched -- the space key isn't involved.
            if not actions.cancel and self._cancel_gate is not None and self._cancel_gate():
                if self._on_cancel is not None:
                    self._on_cancel()

    def _handle_release(self, key, injected=False) -> None:
        if key == self._keyboard.Key.space:
            if sys.platform == "win32":
                return  # win32_event_filter feeds the machine directly (never here)
            # See _handle_press: injected covers our replay tap and
            # TypingSink; other software's synthetic spaces are inert here.
            if injected:
                return
            with self._lock:
                actions = self._machine.space_up()
                self._apply(actions, self._machine.generation)

    def _darwin_intercept(self, event_type, event):
        import Quartz

        keycode = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode
        )
        if keycode != _MAC_SPACE_KEYCODE:
            return event  # non-space keys always pass through
        source_pid = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGEventSourceUnixProcessID
        )
        if source_pid == os.getpid():
            return event  # our own synthetic replay: let it reach the app
        # Every other space event is swallowed here (taps are replayed via
        # _replay_space, holds dictate). Accepted limitation: space events
        # synthesized by other software (e.g. Karabiner-Elements) carry that
        # software's PID rather than ours, so they are swallowed like real
        # hardware presses -- there is no way to tell "someone else's
        # synthetic tap" apart from "an actual keypress" at this layer.
        return None

    def run(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        """Block, driving the callbacks from space press/release/cancel events.

        Callbacks are invoked on OS event threads -- keep them cheap; wrap
        heavy work with ``CallbackDispatcher`` (as ``local-flow run`` does).
        """
        self._on_press, self._on_release, self._on_cancel = on_press, on_release, on_cancel
        keyboard = self._keyboard
        listener_box: list = []

        def win32_event_filter(msg, data):
            # listener.suppress_event() raises SuppressException inside the
            # hook *before* pynput posts the event to its message loop, so a
            # suppressed key never reaches on_press/on_release. The machine
            # has to be driven from here instead; _handle_press/_handle_release
            # bail out immediately on win32 (see Task 5 Amendment 2).
            if data.vkCode != _WIN_VK_SPACE:
                return True
            if data.flags & _LLKHF_INJECTED:
                return True  # our synthetic tap: let it reach the app untouched
            if msg in _WIN_KEYDOWN_MSGS:
                with self._lock:
                    actions = self._machine.space_down()
                    self._apply(actions, self._machine.generation)
            elif msg in _WIN_KEYUP_MSGS:
                with self._lock:
                    actions = self._machine.space_up()
                    self._apply(actions, self._machine.generation)
            if listener_box:
                listener_box[0].suppress_event()  # raises; nothing after this runs
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
