# E1: Configurable Fn/Space Push-to-Talk Hotkey Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `LOCAL_FLOW_HOTKEY` accepts `fn` (default on macOS, via a Quartz event tap) and `space` (hold-vs-tap with event suppression) alongside existing pynput key names, plus a rebindable cancel key (default `esc`) that discards an in-flight dictation.

**Architecture:** Three `HotkeyListener` implementations behind one new factory, `create_hotkey_listener(config)`. All press/release/cancel decision logic lives in pure, dependency-free classes (`PushToTalkCore`, `SpaceStateMachine`, `FnLogic`) that CI tests drive with fake events; pynput/Quartz glue stays in thin `run()` methods and lazy imports.

**Tech Stack:** Python 3.11+, pynput (existing `desktop` extra), pyobjc-framework-Quartz (new, darwin-only, for the Fn tap), pytest.

## Global Constraints

(Inherited from the roadmap's Global Constraints; repeated highlights.)
- CI has no display, no pynput, no pyobjc: every test in this plan must pass with core+dev deps only. Platform packages are imported lazily inside `__init__`/`run`, never at module level.
- Missing capability fails with `HotkeyBackendMissingError(message, hint=...)` — never a raw ImportError.
- New settings follow the existing pattern: `Config` field + `LOCAL_FLOW_<NAME>` env var + TOML key.
- `uv run pytest` and `uv run ruff check .` pass at every commit. Line length 100.
- Platform facts (do not re-derive): pynput has no `Key.fn`; on macOS the Fn key arrives as `kCGEventFlagsChanged` with keycode 63 and flag mask `kCGEventFlagMaskSecondaryFn`; macOS Space keycode is 49, Escape is 53; pynput can suppress single events via `darwin_intercept` (macOS) and `win32_event_filter` (Windows) but not on Linux/X11.

## File Structure

- `local_flow/config.py` — platform-dependent `hotkey` default, `hotkey_space_hold_ms`, `cancel_hotkey`
- `local_flow/hotkeys/base.py` — `HotkeyListener` (run gains `on_cancel`), `PushToTalkCore`, `PynputPushToTalk` (+cancel), `create_hotkey_listener`
- `local_flow/hotkeys/space.py` — NEW: `SpaceActions`, `SpaceStateMachine` (pure), `SpacePushToTalk` (pynput glue)
- `local_flow/hotkeys/macos_fn.py` — NEW: `FnLogic` (pure), `QuartzFnListener` (Quartz glue)
- `local_flow/app.py` — `_cmd_run` uses the factory + cancel handler
- `pyproject.toml`, `README.md`, `.env.example`, `local-flow.example.toml` — docs/deps
- `tests/test_hotkeys.py` — NEW: all logic + factory tests; `tests/test_config.py` — new fields

---

### Task 1: Config — platform default `fn`, `hotkey_space_hold_ms`, `cancel_hotkey`

**Files:**
- Modify: `local_flow/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.hotkey` defaults to `"fn"` on darwin, `"f9"` elsewhere (via `_default_hotkey()` read at construction time so tests can monkeypatch `sys.platform`); `Config.hotkey_space_hold_ms: int = 250`; `Config.cancel_hotkey: str = "esc"`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_config.py`:

```python
class TestHotkeyDefaults:
    def test_hotkey_defaults_to_fn_on_macos(self, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "darwin")
        config = load_config(env={})
        assert config.hotkey == "fn"

    def test_hotkey_defaults_to_f9_elsewhere(self, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "linux")
        config = load_config(env={})
        assert config.hotkey == "f9"

    def test_space_hold_ms_and_cancel_hotkey(self):
        config = load_config(
            env={
                "LOCAL_FLOW_HOTKEY_SPACE_HOLD_MS": "300",
                "LOCAL_FLOW_CANCEL_HOTKEY": "f12",
            }
        )
        assert config.hotkey_space_hold_ms == 300
        assert config.cancel_hotkey == "f12"

    def test_space_hold_ms_defaults(self):
        config = load_config(env={})
        assert config.hotkey_space_hold_ms == 250
        assert config.cancel_hotkey == "esc"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py::TestHotkeyDefaults -v`
Expected: FAIL — `hotkey == "f9"` on darwin / unknown env keys `hotkey_space_hold_ms`.

- [ ] **Step 3: Implement** — in `local_flow/config.py`:

Add `import sys` to the imports. Above the `Config` dataclass add:

```python
def _default_hotkey() -> str:
    # The Fn key is only observable on macOS (elsewhere keyboard firmware
    # swallows it), so the friendlier default is limited to darwin.
    return "fn" if sys.platform == "darwin" else "f9"
```

Replace the two hotkey config lines:

```python
    # Hotkey / capture mode
    mode: str = "push-to-talk"  # push-to-talk | hands-free
    hotkey: str = field(default_factory=_default_hotkey)  # fn | space | pynput key name
    hotkey_space_hold_ms: int = 250  # hold-vs-tap threshold for hotkey="space"
    cancel_hotkey: str = "esc"  # discards the in-flight dictation
```

In `load_config`, add to `field_types`:

```python
        "hotkey_space_hold_ms": int,
```

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_config.py -v` → all PASS (existing `TestConfigFile` still passes: env/TOML plumbing derives from `fields(Config)`).

- [ ] **Step 5: Commit**

```bash
git add local_flow/config.py tests/test_config.py
git commit -m "feat(config): fn default hotkey on macOS, space hold threshold, cancel key"
```

---

### Task 2: `PushToTalkCore` + cancel support in `PynputPushToTalk`

**Files:**
- Modify: `local_flow/hotkeys/base.py`
- Test: `tests/test_hotkeys.py` (create)

**Interfaces:**
- Produces: `PushToTalkCore(on_press, on_release, on_cancel=None)` with `key_down()`, `key_up()`, `cancel_down()` and a `held: bool` attribute — at-most-once semantics; cancel while held fires `on_cancel` and suppresses the later `key_up`'s release. `HotkeyListener.run(on_press, on_release, on_cancel=None)` (new optional arg on the ABC and all implementations). `PynputPushToTalk(key_name="f9", cancel_key="esc")`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_hotkeys.py`:

```python
"""Hotkey logic: shared press/release core, factory dispatch, space and fn machines."""

import sys

import pytest

from local_flow.hotkeys.base import PushToTalkCore


class Recorder:
    def __init__(self):
        self.events = []

    def press(self):
        self.events.append("press")

    def release(self):
        self.events.append("release")

    def cancel(self):
        self.events.append("cancel")


class TestPushToTalkCore:
    def test_press_release_cycle(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.key_down()
        core.key_up()
        assert rec.events == ["press", "release"]

    def test_auto_repeat_key_down_fires_press_once(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.key_down()
        core.key_down()
        core.key_down()
        core.key_up()
        assert rec.events == ["press", "release"]

    def test_key_up_without_down_is_ignored(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.key_up()
        assert rec.events == []

    def test_cancel_while_held_discards_and_swallows_release(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.key_down()
        core.cancel_down()
        core.key_up()  # physical key released afterwards: no stop
        assert rec.events == ["press", "cancel"]

    def test_cancel_while_idle_is_ignored(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.cancel_down()
        assert rec.events == []

    def test_cancel_without_handler_keeps_recording(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, None)
        core.key_down()
        core.cancel_down()
        core.key_up()
        assert rec.events == ["press", "release"]

    def test_auto_repeat_after_cancel_does_not_restart(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.key_down()
        core.cancel_down()
        core.key_down()  # OS auto-repeat: the key is still physically held
        core.key_up()
        core.key_down()  # a fresh press afterwards works again
        assert rec.events == ["press", "cancel", "press"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_hotkeys.py -v`
Expected: FAIL — `ImportError: cannot import name 'PushToTalkCore'`.

- [ ] **Step 3: Implement** — in `local_flow/hotkeys/base.py`:

Add after the imports (keep the existing module docstring and `HotkeyListener`, but change the ABC's `run` signature):

```python
class HotkeyListener(ABC):
    """Watches one push-to-talk key and reports press/release/cancel."""

    @abstractmethod
    def run(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        """Block, invoking callbacks when the hotkey is held/released/cancelled."""


class PushToTalkCore:
    """Held-state bookkeeping shared by every push-to-talk listener.

    Translates raw key events into at-most-once press/release/cancel
    callbacks. A cancel while held discards the recording: ``on_cancel``
    fires and the eventual physical key release is swallowed.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._on_cancel = on_cancel
        self.held = False
        self._suppressed = False  # key still physically down after a cancel

    def key_down(self) -> None:
        if not self.held and not self._suppressed:
            self.held = True
            self._on_press()

    def key_up(self) -> None:
        self._suppressed = False
        if self.held:
            self.held = False
            self._on_release()

    def cancel_down(self) -> None:
        if self.held and self._on_cancel is not None:
            self.held = False
            self._suppressed = True  # swallow auto-repeats until physical release
            self._on_cancel()
```

Rewrite `PynputPushToTalk.__init__` and `run` to use the core and a cancel key:

```python
class PynputPushToTalk(HotkeyListener):
    def __init__(self, key_name: str = "f9", cancel_key: str = "esc") -> None:
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise HotkeyBackendMissingError(
                "The 'pynput' package is not installed.",
                hint="Install desktop extras: uv sync --extra desktop.",
            ) from exc
        self._keyboard = keyboard
        self.key_name = key_name
        self._target = self._resolve_key(key_name)
        self._cancel = self._resolve_key(cancel_key) if cancel_key else None
```

(`_resolve_key` is unchanged; `esc` resolves via `keyboard.Key.esc`.)

```python
    def run(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        keyboard = self._keyboard
        core = PushToTalkCore(on_press, on_release, on_cancel)

        def handle_press(key) -> None:
            if key == self._target:
                core.key_down()
            elif self._cancel is not None and key == self._cancel:
                core.cancel_down()

        def handle_release(key) -> None:
            if key == self._target:
                core.key_up()

        try:
            with keyboard.Listener(
                on_press=handle_press, on_release=handle_release
            ) as listener:
                listener.join()
        except Exception as exc:
            raise HotkeyBackendMissingError(
                f"The global hotkey listener failed: {exc}",
                hint="macOS: grant Accessibility AND Input Monitoring permission "
                "to your terminal. Linux/Wayland: global key capture is blocked "
                "by the compositor - use hands-free mode "
                "(LOCAL_FLOW_MODE=hands-free) instead.",
            ) from exc
```

(The old nonlocal `held` bookkeeping is deleted — the core owns it now.)

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_hotkeys.py -v` → PASS; `uv run pytest` → full suite PASS.

- [ ] **Step 5: Commit** — `git add ... && git commit -m "feat(hotkeys): shared push-to-talk core with cancel-key support"`

---

### Task 3: `create_hotkey_listener` factory

**Files:**
- Modify: `local_flow/hotkeys/base.py`
- Test: `tests/test_hotkeys.py`

**Interfaces:**
- Consumes: `Config.hotkey`, `Config.hotkey_space_hold_ms`, `Config.cancel_hotkey` (Task 1); `QuartzFnListener(cancel_key)` (Task 6); `SpacePushToTalk(hold_ms, cancel_key)` (Task 5).
- Produces: `create_hotkey_listener(config: Config) -> HotkeyListener` — `"fn"` → `QuartzFnListener` (darwin only), `"space"` → `SpacePushToTalk` (not Linux), else `PynputPushToTalk`. Case-insensitive.
- Note: Tasks 5 and 6 don't exist yet — the factory imports `local_flow.hotkeys.space` / `local_flow.hotkeys.macos_fn` lazily inside the function, and this task's tests monkeypatch those attributes, so the factory tests need **stub modules** first: create the two files with a minimal class raising `NotImplementedError` if executing tasks in order (they are fully replaced by Tasks 5–6). Simpler: implement Tasks 4–6 module bodies before running this task's dispatch tests — the recommended execution order below handles this by creating real modules in Tasks 4–6 and keeping this task's tests limited to error paths plus monkeypatched dispatch.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_hotkeys.py`:

```python
from local_flow.config import load_config
from local_flow.errors import HotkeyBackendMissingError
from local_flow.hotkeys.base import create_hotkey_listener


def _config(**env):
    return load_config(env={f"LOCAL_FLOW_{k.upper()}": v for k, v in env.items()})


class TestFactory:
    def test_fn_rejected_off_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(HotkeyBackendMissingError, match="only be observed on macOS"):
            create_hotkey_listener(_config(hotkey="fn"))

    def test_space_rejected_on_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(HotkeyBackendMissingError, match="suppression"):
            create_hotkey_listener(_config(hotkey="space"))

    def test_fn_dispatches_to_quartz_listener_on_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        import local_flow.hotkeys.macos_fn as macos_fn

        created = {}

        class FakeFn:
            def __init__(self, cancel_key):
                created["cancel_key"] = cancel_key

        monkeypatch.setattr(macos_fn, "QuartzFnListener", FakeFn)
        listener = create_hotkey_listener(_config(hotkey="FN", cancel_hotkey="f12"))
        assert isinstance(listener, FakeFn)
        assert created["cancel_key"] == "f12"

    def test_space_dispatches_to_space_listener_on_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        import local_flow.hotkeys.space as space_mod

        created = {}

        class FakeSpace:
            def __init__(self, hold_ms, cancel_key):
                created.update(hold_ms=hold_ms, cancel_key=cancel_key)

        monkeypatch.setattr(space_mod, "SpacePushToTalk", FakeSpace)
        listener = create_hotkey_listener(
            _config(hotkey="space", hotkey_space_hold_ms="400")
        )
        assert isinstance(listener, FakeSpace)
        assert created == {"hold_ms": 400, "cancel_key": "esc"}

    def test_other_names_dispatch_to_pynput(self, monkeypatch):
        import local_flow.hotkeys.base as base_mod

        created = {}

        class FakePynput:
            def __init__(self, key_name, cancel_key="esc"):
                created.update(key_name=key_name, cancel_key=cancel_key)

        monkeypatch.setattr(base_mod, "PynputPushToTalk", FakePynput)
        listener = create_hotkey_listener(_config(hotkey="f9"))
        assert isinstance(listener, FakePynput)
        assert created["key_name"] == "f9"
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_hotkeys.py::TestFactory -v` → FAIL (`create_hotkey_listener` missing).

- [ ] **Step 3: Implement** — in `local_flow/hotkeys/base.py`, add `import sys` and `from local_flow.config import Config` to the imports, then append:

```python
def create_hotkey_listener(config: Config) -> HotkeyListener:
    """Build the push-to-talk listener for ``config.hotkey``.

    ``fn`` needs a macOS-only Quartz event tap (the Fn key never reaches
    other OSes); ``space`` needs per-event suppression, which pynput cannot
    do on Linux; anything else is a plain pynput key name.
    """
    name = config.hotkey.lower()
    if name == "fn":
        if sys.platform != "darwin":
            raise HotkeyBackendMissingError(
                "The Fn key can only be observed on macOS.",
                hint="On this platform Fn is handled by keyboard firmware and "
                "never reaches the OS. Set LOCAL_FLOW_HOTKEY to another key, "
                "e.g. f9 or space.",
            )
        import local_flow.hotkeys.macos_fn as macos_fn

        return macos_fn.QuartzFnListener(cancel_key=config.cancel_hotkey)
    if name == "space":
        if sys.platform.startswith("linux"):
            raise HotkeyBackendMissingError(
                "Space push-to-talk needs per-event key suppression, which is "
                "not possible on Linux/X11.",
                hint="Use another key (LOCAL_FLOW_HOTKEY=f9) or hands-free "
                "mode (LOCAL_FLOW_MODE=hands-free).",
            )
        import local_flow.hotkeys.space as space_mod

        return space_mod.SpacePushToTalk(
            hold_ms=config.hotkey_space_hold_ms, cancel_key=config.cancel_hotkey
        )
    return PynputPushToTalk(name, cancel_key=config.cancel_hotkey)
```

(Module-object imports + attribute access keep the monkeypatching in tests honest and the platform deps lazy. The `space`/`macos_fn` modules must exist by the time these dispatch tests run — execute Tasks 4–6 before running the two dispatch tests, or run only the two rejection tests until then. Recommended order: implement Task 4 + module skeletons first if running strictly sequentially; the task ordering below already places the pure machines before the glue.)

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_hotkeys.py -v`; the two dispatch tests will fail with ModuleNotFoundError until Tasks 4–6 create the modules — acceptable mid-plan state if committing per task; otherwise implement Tasks 4–6 first and run all together. Final state: PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(hotkeys): create_hotkey_listener factory with platform guards"`

---

### Task 4: `SpaceStateMachine` (pure hold-vs-tap logic)

**Files:**
- Create: `local_flow/hotkeys/space.py` (machine only; glue in Task 5)
- Test: `tests/test_hotkeys.py`

**Interfaces:**
- Produces: `SpaceActions` dataclass (`start`, `stop`, `cancel`, `replay_space`, `start_timer`: all bool) and `SpaceStateMachine` with `generation: int` and methods `space_down() -> SpaceActions`, `space_up() -> SpaceActions`, `hold_elapsed(generation: int) -> SpaceActions`, `cancel_down() -> SpaceActions`. Consumed by Task 5's glue: `start_timer=True` means "schedule `hold_elapsed(machine.generation)` after hold_ms".

- [ ] **Step 1: Write the failing tests** — append to `tests/test_hotkeys.py`:

```python
from local_flow.hotkeys.space import SpaceStateMachine


class TestSpaceStateMachine:
    def test_quick_tap_replays_a_space(self):
        m = SpaceStateMachine()
        down = m.space_down()
        assert down.start_timer and not down.start
        up = m.space_up()
        assert up.replay_space and not up.stop

    def test_hold_starts_then_release_stops(self):
        m = SpaceStateMachine()
        m.space_down()
        held = m.hold_elapsed(m.generation)
        assert held.start
        up = m.space_up()
        assert up.stop and not up.replay_space

    def test_stale_timer_after_tap_does_not_start(self):
        m = SpaceStateMachine()
        m.space_down()
        stale_gen = m.generation
        m.space_up()  # tap finished; timer not yet cancelled
        late = m.hold_elapsed(stale_gen)
        assert late == SpaceActions()  # no-op

    def test_auto_repeat_downs_are_ignored(self):
        m = SpaceStateMachine()
        m.space_down()
        assert m.space_down() == SpaceActions()  # repeat while pending
        m.hold_elapsed(m.generation)
        assert m.space_down() == SpaceActions()  # repeat while recording

    def test_cancel_while_recording_discards(self):
        m = SpaceStateMachine()
        m.space_down()
        m.hold_elapsed(m.generation)
        assert m.cancel_down().cancel
        assert m.space_down() == SpaceActions()  # auto-repeat while still held: no restart
        assert m.space_up() == SpaceActions()  # physical release: swallowed, no stop
        assert m.space_down().start_timer  # a fresh press afterwards works again

    def test_cancel_while_idle_or_pending_is_noop(self):
        m = SpaceStateMachine()
        assert m.cancel_down() == SpaceActions()
        m.space_down()
        assert m.cancel_down() == SpaceActions()

    def test_up_while_idle_is_noop(self):
        assert SpaceStateMachine().space_up() == SpaceActions()
```

Also add `SpaceActions` to that import line.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_hotkeys.py::TestSpaceStateMachine -v` → FAIL (module missing).

- [ ] **Step 3: Implement** — create `local_flow/hotkeys/space.py`:

```python
"""Space as push-to-talk: hold to dictate, tap to type a normal space.

The state machine is pure and timer-agnostic: the platform glue schedules
``hold_elapsed(generation)`` after the hold threshold. Generations make a
timer that fires after the key was already released a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass

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
```

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_hotkeys.py -v` → machine tests PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(hotkeys): pure hold-vs-tap state machine for space push-to-talk"`

---

### Task 5: `SpacePushToTalk` platform glue

**Files:**
- Modify: `local_flow/hotkeys/space.py`
- Test: `tests/test_hotkeys.py` (timer/replay-guard behavior via a fake-time seam is optional; the machine is already covered — glue tests here are limited to what runs headless)

**Interfaces:**
- Consumes: `SpaceStateMachine` (Task 4), `HotkeyListener`/`HotkeyBackendMissingError` (Task 2).
- Produces: `SpacePushToTalk(hold_ms: int = 250, cancel_key: str = "esc")`, a `HotkeyListener`.

**Key implementation facts:**
- Suppression: pass BOTH `darwin_intercept=` and `win32_event_filter=` to `keyboard.Listener` — pynput strips the prefix for the current platform and ignores the other, so one code path serves both OSes.
- macOS space keycode is 49 (`Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)`); pynput already depends on pyobjc Quartz on darwin. Windows: `data.vkCode == 0x20`, suppress via `listener.suppress_event()`.
- The synthetic replay tap passes through the same interceptor and the same on_press handler: a `self._replaying` flag must make both ignore it, or a tap would re-enter the machine and loop.
- The hold timer is a `threading.Timer(hold_ms / 1000, fire, args=[machine.generation])`; `fire` takes the lock, calls `machine.hold_elapsed(gen)`, applies actions. All machine access happens under one `threading.Lock`.

- [ ] **Step 1: Implement** — append to `local_flow/hotkeys/space.py`:

```python
import threading
from collections.abc import Callable

from local_flow.errors import HotkeyBackendMissingError
from local_flow.hotkeys.base import HotkeyListener

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
```

Move the `import threading` / `Callable` / errors / base imports to the top of the file with the existing imports (ruff `I` will enforce ordering).

- [ ] **Step 2: Run** — `uv run pytest && uv run ruff check .` → PASS (glue has no headless-runnable behavior beyond import; machine tests still green; the Task 3 space-dispatch test now passes).
- [ ] **Step 3: Commit** — `git commit -m "feat(hotkeys): space push-to-talk glue with suppression and tap replay"`

**Amendment (post-review, 2026-07-06).** Review found two defects in the code above; the implementation must use these corrections (the code blocks above are superseded where they conflict):

1. **Replay race.** `Controller.tap()` posts the synthetic down+up asynchronously; they arrive back on the listener thread *after* the current callback returns, when a transient `_replaying` boolean has already been reset — so the replayed space is swallowed again (tap types nothing, and the handler re-entry can self-sustain a replay loop). Replace the boolean with per-stage counters + deadline: `_replay_left = {"intercept": 0, "handler": 0}` and `_replay_deadline = 0.0`; `_replay_space()` sets both counters to 2 (one down + one up per stage), sets `_replay_deadline = time.monotonic() + 0.5`, then taps. A `_consume_replay(stage)` helper returns True (and decrements) while the counter is positive and the deadline unexpired, else zeroes both counters and returns False. The darwin interceptor *passes through* (`return event`) consumed replays and swallows other space events; the win32 filter suppresses space only when `_consume_replay("intercept")` is False; both handlers return early when `_consume_replay("handler")` is True. `import time` at module top. All replay state is touched only on the listener thread (the timer thread never emits `replay_space`), so no lock is needed on the counters.
2. **Cancel-key resolution.** `getattr(keyboard.Key, cancel_key.lower(), None)` silently disables cancel for single-character keys. Extract the existing `PynputPushToTalk._resolve_key` body into a module-level `resolve_key(keyboard, key_name)` in `base.py` (special name → `keyboard.Key` attr; single char → `KeyCode.from_char`; else `HotkeyBackendMissingError` with the existing hint); `PynputPushToTalk._resolve_key` becomes a thin delegate, and `SpacePushToTalk` uses `resolve_key(keyboard, cancel_key) if cancel_key else None`. Add headless tests for `resolve_key` driving it with a fake keyboard object (`Key` attrs + `KeyCode.from_char`).
3. **Lock hygiene (minor).** Capture `generation = self._machine.generation` inside the same `with self._lock:` block that produced the actions, and pass it to `_apply(actions, generation)`; `_apply` schedules the timer with that captured value, and `_fire_hold` re-locks to call `hold_elapsed(generation)` and capture the then-current generation for its own `_apply` call.

**Amendment 2 (post-re-review, 2026-07-06).** Reading pynput 1.8.2's source shows the counter design in Amendment 1 item 1 is still wrong on both platforms; it is superseded by exact synthetic-event identification:

- **macOS ordering fact:** pynput's darwin `ListenerMixin._handler` invokes `on_press`/`on_release` FIRST and consults `darwin_intercept` for the SAME event afterwards. Arming an interceptor counter inside the handler therefore mis-consumes on the very event that triggered the replay (real tap-up passes through; synthetic up gets swallowed — an unbalanced event stream on every tap).
- **macOS fix (stateless):** identify our synthetic events exactly by source PID. In `_darwin_intercept`: non-space keycodes pass; for space, `Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUnixProcessID) == os.getpid()` → `return event` (our replay reaches the app); otherwise `return None` (every real space swallowed — taps are replayed, holds dictate). No interceptor counters, no triggering-event flag. Known accepted limitation (comment it): space events synthesized by *other* software (e.g. Karabiner) carry that software's PID and are swallowed like hardware events.
- **macOS handlers:** keep a single handler-side counter (`_replay_handler_left = 2` + `_replay_deadline`, self-healing) so the synthetic echo does not re-enter the machine. Accepted limitation (comment it): a real second tap landing within the few-ms echo window may be ignored by the machine.
- **Windows ordering fact:** `listener.suppress_event()` raises `SuppressException` inside the hook *before* the event is posted to pynput's message loop — a suppressed event never reaches `on_press`/`on_release`. Feeding the machine from handlers therefore never happens: the class as written is inert on Windows.
- **Windows fix:** drive the machine from `win32_event_filter` itself: `data.vkCode == 0x20` and `data.flags & 0x10` (`LLKHF_INJECTED`) → `return True` untouched (our synthetic tap reaches the app, skips the machine); real space with `msg in (0x0100, 0x0104)` → locked `machine.space_down()`; `msg in (0x0101, 0x0105)` → locked `machine.space_up()`; after feeding the machine, call `listener_box[0].suppress_event()` LAST (it raises). `_handle_press`/`_handle_release` ignore space entirely on win32 (`sys.platform == "win32"`); the cancel key still flows through the normal handlers on both platforms (never suppressed).
- Module constants: `_WIN_KEYDOWN_MSGS = (0x0100, 0x0104)`, `_WIN_KEYUP_MSGS = (0x0101, 0x0105)`, `_LLKHF_INJECTED = 0x10`; `import os` at top. The `"intercept"` counter stage is deleted; `_replay_space` arms only the handler counter before tapping.

**Amendment 3 (post-re-review 2, 2026-07-06).** Two further defects from the end-to-end trace:

1. **User callbacks must not run inside OS event hooks.** `on_release` → `finish()` does seconds of work (ASR + LLM + insertion). Amendment 2 runs it inside the win32 `WH_KEYBOARD_LL` hook (Windows stalls all keyboard input, then silently kills hooks that overrun `LowLevelHooksTimeout`) and inside the darwin CGEventTap callback (an active/filtering tap — `darwin_intercept` forces `kCGEventTapOptionDefault` — that macOS disables via `kCGEventTapDisabledByTimeout` after ~1s; pynput never re-enables it). Fix at the wiring altitude so all three listeners benefit: add `CallbackDispatcher` to `local_flow/hotkeys/base.py` — single daemon worker thread + `queue.Queue`; `wrap(fn)` returns an enqueueing stand-in (`wrap(None)` → `None`); the worker catches exceptions (`print(f"hotkey callback failed: {exc}", file=sys.stderr)`) so a failing callback can't kill dispatch; single worker preserves press→release ordering. Task 7's `_cmd_run` builds one dispatcher and passes `listener.run(dispatcher.wrap(start), dispatcher.wrap(finish), dispatcher.wrap(cancel))`. Headless tests: ordering across two wrapped callbacks (threading.Event-synchronized), worker survives an exception and runs the next callback, callbacks execute off the caller's thread, `wrap(None) is None`. Docstring must state WHY (hook/tap timeout).
2. **Our own `TypingSink` doubles spaces on darwin.** `TypingSink` types via a pynput `Controller` in our PID, so each typed space passes the PID interceptor (correct — it types) but also reaches the darwin handlers, feeds the machine (down→PENDING, up→replay) and emits an extra synthetic space. Fix: pynput 1.8+ passes an `injected` flag to two-argument callbacks — declare the space handlers as `_handle_press(self, key, injected=False)` / `_handle_release(self, key, injected=False)` and for space events `if injected: return`. This replaces the `_replay_handler_left` counter/deadline mechanism entirely (our replay tap is itself injected): delete `_consume_replay`, `_replay_handler_left`, `_replay_deadline`, `_REPLAY_WINDOW_S`; `_replay_space()` is just the tap. Documented trade-off (comment): synthetic space events from *other* software are now fully inert on darwin (swallowed by the PID interceptor, ignored by the injected guard) — previously they would have dictated without typing. The `injected=False` default keeps compatibility if a pynput version calls handlers with one argument. Cancel-key branch behavior unchanged.

---

### Task 6: `FnLogic` + `QuartzFnListener` + pyproject dep

**Files:**
- Create: `local_flow/hotkeys/macos_fn.py`
- Modify: `pyproject.toml`
- Test: `tests/test_hotkeys.py`

**Interfaces:**
- Consumes: `PushToTalkCore` (Task 2).
- Produces: `FnLogic(core, cancel_keycode)` with `flags_changed(fn_active: bool)`, `key_down(keycode: int)`; `QuartzFnListener(cancel_key: str = "esc")`, a `HotkeyListener` (darwin-only construction).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_hotkeys.py`:

```python
from local_flow.hotkeys.macos_fn import ESCAPE_KEYCODE, FnLogic
from local_flow.hotkeys.base import PushToTalkCore


class TestFnLogic:
    def _logic(self, rec, cancel=ESCAPE_KEYCODE):
        return FnLogic(PushToTalkCore(rec.press, rec.release, rec.cancel), cancel)

    def test_fn_press_release(self):
        rec = Recorder()
        logic = self._logic(rec)
        logic.flags_changed(True)
        logic.flags_changed(False)
        assert rec.events == ["press", "release"]

    def test_repeated_flag_states_do_not_repeat_callbacks(self):
        rec = Recorder()
        logic = self._logic(rec)
        logic.flags_changed(True)
        logic.flags_changed(True)  # e.g. fn+arrow re-reports the same mask
        logic.flags_changed(False)
        logic.flags_changed(False)
        assert rec.events == ["press", "release"]

    def test_escape_while_held_cancels(self):
        rec = Recorder()
        logic = self._logic(rec)
        logic.flags_changed(True)
        logic.key_down(ESCAPE_KEYCODE)
        logic.flags_changed(False)
        assert rec.events == ["press", "cancel"]

    def test_other_keys_ignored(self):
        rec = Recorder()
        logic = self._logic(rec)
        logic.flags_changed(True)
        logic.key_down(0)  # kVK_ANSI_A
        logic.flags_changed(False)
        assert rec.events == ["press", "release"]

    def test_no_cancel_keycode_ignores_escape(self):
        rec = Recorder()
        logic = self._logic(rec, cancel=None)
        logic.flags_changed(True)
        logic.key_down(ESCAPE_KEYCODE)
        logic.flags_changed(False)
        assert rec.events == ["press", "release"]
```

- [ ] **Step 2: Run to verify failure** — module missing → FAIL.

- [ ] **Step 3: Implement** — create `local_flow/hotkeys/macos_fn.py`:

```python
"""Fn-key push-to-talk on macOS via a listen-only Quartz event tap.

pynput has no ``Key.fn``: the Fn key arrives as a ``flagsChanged`` event
(keycode 63) whose ``kCGEventFlagMaskSecondaryFn`` bit tracks the key
state. ``FnLogic`` is pure so CI can drive it with fake events; only
``QuartzFnListener.run`` touches Quartz.
"""

from __future__ import annotations

import sys
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
    def __init__(self, cancel_key: str = "esc") -> None:
        if sys.platform != "darwin":
            raise HotkeyBackendMissingError(
                "The Fn key can only be observed on macOS.",
                hint="Set LOCAL_FLOW_HOTKEY to another key, e.g. f9 or space.",
            )
        try:
            import Quartz
        except ImportError as exc:
            raise HotkeyBackendMissingError(
                "pyobjc-framework-Quartz is not installed.",
                hint="Install desktop extras: uv sync --extra desktop.",
            ) from exc
        self._quartz = Quartz
        self._cancel_keycode = _CANCEL_KEYCODES.get(cancel_key.lower()) if cancel_key else None

    def run(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        q = self._quartz
        logic = FnLogic(PushToTalkCore(on_press, on_release, on_cancel), self._cancel_keycode)
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
            elif event_type == q.kCGEventKeyDown:
                logic.key_down(keycode)
            return event

        mask = q.CGEventMaskBit(q.kCGEventFlagsChanged) | q.CGEventMaskBit(q.kCGEventKeyDown)
        tap = q.CGEventTapCreate(
            q.kCGSessionEventTap,
            q.kCGHeadInsertEventTap,
            q.kCGEventTapOptionListenOnly,
            mask,
            callback,
            None,
        )
        if tap is None:
            raise HotkeyBackendMissingError(
                "Could not create the macOS event tap for the Fn key.",
                hint="Grant Accessibility AND Input Monitoring permission to "
                "your terminal in System Settings > Privacy & Security, then "
                "restart the terminal.",
            )
        tap_holder.append(tap)
        source = q.CFMachPortCreateRunLoopSource(None, tap, 0)
        q.CFRunLoopAddSource(q.CFRunLoopGetCurrent(), source, q.kCFRunLoopCommonModes)
        q.CGEventTapEnable(tap, True)
        q.CFRunLoopRun()
```

In `pyproject.toml`, extend the desktop extra:

```toml
# Global hotkeys, keystroke paste/typing, clipboard.
desktop = [
    "pynput>=1.7.7",
    "pyperclip>=1.9.0",
    "pyobjc-framework-Quartz>=9.0; sys_platform == 'darwin'",
]
```

- [ ] **Step 4: Run tests** — `uv run pytest && uv run ruff check .` → PASS (fn-dispatch factory test from Task 3 now passes too).
- [ ] **Step 5: Commit** — `git commit -m "feat(hotkeys): Fn push-to-talk on macOS via Quartz event tap"`

---

### Task 7: Wire into `_cmd_run` + docs

**Files:**
- Modify: `local_flow/app.py:229-254`, `README.md`, `.env.example`, `local-flow.example.toml`
- Test: existing suite (`tests/test_demo_and_cli.py` must stay green)

**Interfaces:**
- Consumes: `create_hotkey_listener(config)` (Task 3).

- [ ] **Step 1: Implement app wiring** — in `_cmd_run`, replace the push-to-talk branch's listener block (`from local_flow.hotkeys.base import PynputPushToTalk` … `PynputPushToTalk(config.hotkey).run(start, finish)`) with:

```python
            from local_flow.hotkeys.base import CallbackDispatcher, create_hotkey_listener

            listener = create_hotkey_listener(config)
            hint = "hold Space (a quick tap still types a space)" if (
                config.hotkey == "space"
            ) else f"hold {config.hotkey!r}"
            print(
                f"push-to-talk: {hint} to dictate; "
                f"press {config.cancel_hotkey!r} to discard. Ctrl+C to quit."
            )
            stop = threading.Event()
            recorder: dict[str, threading.Thread | None] = {"thread": None}
            captured: dict[str, bytes] = {}

            def start() -> None:
                stop.clear()

                def record() -> None:
                    captured["pcm"] = source.record_until(stop, config.vad_frame_ms)

                recorder["thread"] = threading.Thread(target=record, daemon=True)
                recorder["thread"].start()

            def finish() -> None:
                stop.set()
                thread = recorder["thread"]
                if thread is not None:
                    thread.join(timeout=5)
                pcm = captured.pop("pcm", b"")
                if pcm:
                    handle(pcm)

            def cancel() -> None:
                stop.set()
                thread = recorder["thread"]
                if thread is not None:
                    thread.join(timeout=5)
                captured.pop("pcm", None)
                print("dictation discarded")

            dispatcher = CallbackDispatcher()
            listener.run(
                dispatcher.wrap(start), dispatcher.wrap(finish), dispatcher.wrap(cancel)
            )
```

(Construct the listener *before* printing so factory errors surface immediately with their hints; `start`/`finish` bodies are unchanged from today. The dispatcher — Amendment 3 — keeps seconds-long dictation processing out of the OS event hook/tap callbacks, which the OS would otherwise disable.)

- [ ] **Step 2: Update `.env.example`** — replace the hotkey block:

```bash
# --- Hotkey / capture mode ---
# Mode: push-to-talk | hands-free
LOCAL_FLOW_MODE=push-to-talk
# Push-to-talk key: fn (macOS only; the default there) | space (hold to
# dictate, tap types a space; macOS/Windows) | any pynput key name (f9, f8,
# scroll_lock, ...). Default: fn on macOS, f9 elsewhere.
LOCAL_FLOW_HOTKEY=fn
# Hold threshold (ms) separating "tap = type a space" from "hold = dictate".
LOCAL_FLOW_HOTKEY_SPACE_HOLD_MS=250
# Key that discards an in-flight dictation (pynput key name).
LOCAL_FLOW_CANCEL_HOTKEY=esc
```

- [ ] **Step 3: Update `local-flow.example.toml`** — replace the two mode/hotkey lines:

```toml
mode = "push-to-talk"          # push-to-talk | hands-free
hotkey = "fn"                  # fn (macOS) | space | pynput key name; default f9 off-macOS
hotkey_space_hold_ms = 250     # space only: tap-vs-hold threshold in ms
cancel_hotkey = "esc"          # discards the in-flight dictation
```

- [ ] **Step 4: Update `README.md`:**
  - Config table: change the Hotkey row to `| Hotkey | LOCAL_FLOW_HOTKEY | fn (macOS) / f9 |` and add rows for `LOCAL_FLOW_HOTKEY_SPACE_HOLD_MS` (250) and `LOCAL_FLOW_CANCEL_HOTKEY` (esc).
  - Replace the "Push-to-talk uses a single physical key (default F9); chord hotkeys are not supported in this MVP." bullet with:

```markdown
- Push-to-talk keys: **Fn** (macOS only — other OSes never see the Fn key;
  needs Input Monitoring permission), **Space** (hold to dictate, quick tap
  still types a space; macOS/Windows — Linux/X11 cannot suppress the key, use
  another key or hands-free mode there), or any single pynput key name
  (`f9`, `f8`, `scroll_lock`, …). Chord hotkeys are not supported yet.
  Press `esc` (configurable) to throw away a dictation mid-recording.
  Note: using Fn as a modifier (e.g. Fn+arrow) also triggers dictation
  start/stop — pick another key if you use Fn combos heavily.
```

  - Manual test checklist: add items 11–13: hold Fn → dictate → release inserts; tap Space in an editor → a normal space appears, hold Space → dictation; press Esc mid-dictation → nothing inserted, "dictation discarded" printed.

- [ ] **Step 5: Run everything**

Run: `uv run pytest && uv run ruff check .`
Expected: full suite PASS, lint clean.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: wire fn/space hotkey factory and cancel key into local-flow run"`

---

### Task 8: Manual verification (human + this Mac)

- [ ] `uv sync --all-extras`
- [ ] `uv run local-flow check` → pynput + pyperclip installed
- [ ] `LOCAL_FLOW_ASR_BACKEND=mock uv run local-flow run` (mock ASR avoids model download; LM Studio optional — polish degrades to rules): hold **Fn**, speak, release → "(mock transcription)" inserted into the focused editor. First run prompts for Accessibility + Input Monitoring; restart terminal after granting.
- [ ] `LOCAL_FLOW_HOTKEY=space LOCAL_FLOW_ASR_BACKEND=mock uv run local-flow run`: hold Space → dictation; quick-tap Space in an editor → normal space typed.
- [ ] Hold Fn, then press Esc before releasing → "dictation discarded", nothing inserted.

## Self-Review (done)

1. **Spec coverage:** fn (Quartz tap, darwin-only, default), space (hold-vs-tap, suppression, X11 rejection), pynput names, `hotkey_space_hold_ms`, cancel key addendum, factory, `_cmd_run` wiring, README/.env/toml/pyproject — all present. Chord hotkeys: explicitly deferred (stretch in roadmap), README wording kept honest.
2. **Placeholder scan:** none — every step has full code or exact text.
3. **Type consistency:** `run(on_press, on_release, on_cancel=None)` uniform across ABC + 3 listeners; factory kwargs match `SpacePushToTalk(hold_ms=, cancel_key=)` / `QuartzFnListener(cancel_key=)`; `SpaceActions` fields consumed by `_apply` exactly as produced. Task-3 dispatch tests depend on modules created in Tasks 4–6 — noted inline; run order or deferred assertion handles it.
