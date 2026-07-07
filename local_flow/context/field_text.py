"""Focused-field text adapters: macOS Accessibility, Windows stub, no-op else.

Same best-effort discipline as ``local_flow.context.frontmost``: nothing
here may ever raise out of ``current()``. A missing dependency, a denied
Accessibility permission, an unfocused/non-text element, or any other OS-call
failure all degrade to an empty :class:`FieldContext` so callers can always
ask "what's already in this field?" without a try/except of their own.

Platform-specific dependencies (``ApplicationServices``) are imported lazily
inside ``current()`` so importing this module stays headless, matching
``local_flow.context.frontmost``/``local_flow.insertion.desktop``.

Windows: v1 ships ``WindowsUIAFieldText``, a stub that always returns an
empty ``FieldContext``. Reading the focused control's text via Windows UI
Automation needs COM interop (the ``comtypes`` package) -- a new dependency
this project doesn't currently have, since plain ``ctypes`` cannot drive UI
Automation's COM interfaces. Rather than ship untested COM-interop code, this
is documented as a known platform gap (see README's "Context-aware
dictation"): dictation still works fully on Windows, polish just never sees
the focused field's existing text.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass

# Kept in sync with `local_flow.polish.prompting`'s defensive re-cap of the
# same value -- this is the "source of truth" cap; prompting re-applies it so
# the prompt is safe even if a future provider forgets to.
MAX_BEFORE_CURSOR = 1000


@dataclass(frozen=True)
class FieldContext:
    """Best-effort snapshot of the focused field's existing text.

    ``before_cursor``: the text immediately preceding the cursor/selection,
    tail-capped at ``MAX_BEFORE_CURSOR`` characters so a huge open document
    never balloons the polish prompt. ``selected``: whatever text (if any)
    is currently highlighted in that same field. All-empty when unknown --
    there's no separate "unknown" state to check for.
    """

    before_cursor: str = ""
    selected: str = ""


class FieldTextProvider(ABC):
    """Reports the focused field's existing text. Best-effort: never raises."""

    @abstractmethod
    def current(self) -> FieldContext:
        """Return the focused field's text, or an empty FieldContext if unknown."""


class MockFieldText(FieldTextProvider):
    """Test double: returns whatever ``.context`` is set to."""

    def __init__(self, context: FieldContext | None = None) -> None:
        self.context = context if context is not None else FieldContext()

    def current(self) -> FieldContext:
        return self.context


class NullFieldText(FieldTextProvider):
    """Always empty -- the fallback for platforms with no real adapter."""

    def current(self) -> FieldContext:
        return FieldContext()


class MacAXFieldText(FieldTextProvider):
    """Reads the focused UI element's text via the macOS Accessibility API.

    ``AXUIElementCreateSystemWide`` -> ``kAXFocusedUIElementAttribute`` ->
    from that element, ``kAXValueAttribute`` (the full text) and
    ``kAXSelectedTextRangeAttribute`` (an opaque ``AXValue``-wrapped
    ``CFRange``) via ``ApplicationServices`` (pyobjc). This module imports it
    directly, so it's declared as its own line in pyproject's ``desktop``
    extra (``pyobjc-framework-ApplicationServices``, darwin-only) alongside
    the Quartz/Cocoa lines -- matching this project's precedent (see E1's
    ``QuartzFnListener``/``local_flow.context.frontmost``) of declaring
    direct imports explicitly rather than riding another package's (here,
    ``pynput``'s) transitive dependency, even though ``pynput`` happens to
    depend on it too on darwin.

    The selection range comes back wrapped in an opaque ``AXValueRef``;
    ``AXValueGetValue`` with ``kAXValueCFRangeType`` unwraps it to an
    ``(ok, (location, length))`` pair. If that unwrap fails or the attribute
    simply isn't available (both observed in practice -- not every focused
    element exposes a selection range), this degrades to ``before_cursor``
    built from the *whole* value's tail with no cursor position, and an
    empty ``selected`` -- a documented, acceptable degradation rather than
    an all-or-nothing failure. Every step (missing module, no focused
    element, no/blank value, an unexpected type) is wrapped so any failure
    still degrades all the way to an empty ``FieldContext`` rather than
    raising.
    """

    def current(self) -> FieldContext:
        try:
            import ApplicationServices as AS

            system_wide = AS.AXUIElementCreateSystemWide()
            err, focused = AS.AXUIElementCopyAttributeValue(
                system_wide, AS.kAXFocusedUIElementAttribute, None
            )
            if err or focused is None:
                return FieldContext()

            err, value = AS.AXUIElementCopyAttributeValue(
                focused, AS.kAXValueAttribute, None
            )
            if err or not isinstance(value, str) or not value:
                return FieldContext()

            # Degraded default: no range -> the whole value's tail, no
            # selection. Overwritten below when the range unwraps cleanly.
            before_cursor = value[-MAX_BEFORE_CURSOR:]
            selected = ""

            err, ax_range = AS.AXUIElementCopyAttributeValue(
                focused, AS.kAXSelectedTextRangeAttribute, None
            )
            if not err and ax_range is not None:
                ok, cf_range = AS.AXValueGetValue(ax_range, AS.kAXValueCFRangeType, None)
                if ok and cf_range is not None:
                    location, length = int(cf_range[0]), int(cf_range[1])
                    before_cursor = value[:location][-MAX_BEFORE_CURSOR:]
                    selected = value[location : location + length]

            return FieldContext(before_cursor=before_cursor, selected=selected)
        except Exception:
            return FieldContext()


class WindowsUIAFieldText(FieldTextProvider):
    """v1 stub: always returns an empty ``FieldContext``.

    See the module docstring: reading the focused control's text needs
    ``comtypes``-based COM interop with Windows UI Automation, which this
    project does not depend on. Kept as its own class (rather than reusing
    ``NullFieldText`` directly in the factory) so the platform gap is
    explicit and self-documenting at the call site/import graph, and so a
    future real implementation has an obvious place to land.
    """

    def current(self) -> FieldContext:
        return FieldContext()


def create_field_text_provider() -> FieldTextProvider:
    """Build the platform field-text provider for the current OS.

    Construction never touches OS-specific dependencies (those are deferred
    to ``current()``), so this never raises either. darwin -> the real AX
    adapter; win32 -> the documented stub (behaviorally identical to
    ``NullFieldText``); anything else (Linux/BSD/...) -> ``NullFieldText``,
    since there's no accessibility-text adapter for those platforms yet.
    """
    if sys.platform == "darwin":
        return MacAXFieldText()
    if sys.platform == "win32":
        return WindowsUIAFieldText()
    return NullFieldText()
