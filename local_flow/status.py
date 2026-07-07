"""Status reporting seam for the dictation run loop.

``_run_loop`` (in :mod:`local_flow.app`) no longer prints directly; it emits
state transitions through a :class:`StatusReporter`. ``ConsoleReporter``
reproduces today's CLI output byte-for-byte, so this is a pure plumbing
change with zero observable behavior change. Future reporters (e.g. a tray
icon in Phase 3 Task 3) can subscribe to the same transitions without
touching the run loop itself.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from typing import Literal

State = Literal[
    "idle", "recording", "processing", "inserted", "error", "warning", "preview"
]


class StatusReporter(ABC):
    """Receives state transitions from the dictation run loop."""

    @abstractmethod
    def notify(self, state: State, detail: str = "") -> None:
        """Report a transition to ``state`` with an optional ``detail`` string."""


class ConsoleReporter(StatusReporter):
    """Reproduces today's CLI output byte-for-byte (when preview never fires).

    ``"preview"`` overwrites the current console line with a rough partial
    transcript, ending without a newline (``\\r`` returns the cursor to
    column zero so the next preview overwrites it in place). This reporter
    remembers that a preview line is on screen and, the next time a state
    that actually prints something (``warning``/``inserted``/``error``)
    fires, emits a bare ``"\\n"`` to stderr first so that message doesn't
    collide with the dangling preview text. States with no output of their
    own (``recording``/``processing``/``idle``) do NOT clear the pending
    preview -- only the next *printed* state does. When ``"preview"`` never
    fires (every non-streaming run today), ``_preview_pending`` stays
    ``False`` forever and this class's output is unchanged, byte for byte.
    """

    def __init__(self) -> None:
        self._preview_pending = False

    def notify(self, state: State, detail: str = "") -> None:
        if state == "preview":
            print(f"\r… {detail[:70]}", end="", file=sys.stderr, flush=True)
            self._preview_pending = True
            return
        if state in ("warning", "inserted", "error"):
            if self._preview_pending:
                print(file=sys.stderr)
                self._preview_pending = False
            if state == "warning":
                print(f"warning: {detail}", file=sys.stderr)
            elif state == "inserted":
                print(f"inserted: {detail}")
            else:
                print(f"error: {detail}", file=sys.stderr)
        # "recording"/"processing"/"idle" produce no output today (silent),
        # and do not clear a pending preview line either.
