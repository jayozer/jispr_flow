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

State = Literal["idle", "recording", "processing", "inserted", "error", "warning"]


class StatusReporter(ABC):
    """Receives state transitions from the dictation run loop."""

    @abstractmethod
    def notify(self, state: State, detail: str = "") -> None:
        """Report a transition to ``state`` with an optional ``detail`` string."""


class ConsoleReporter(StatusReporter):
    """Reproduces today's CLI output byte-for-byte."""

    def notify(self, state: State, detail: str = "") -> None:
        if state == "warning":
            print(f"warning: {detail}", file=sys.stderr)
        elif state == "inserted":
            print(f"inserted: {detail}")
        elif state == "error":
            print(f"error: {detail}", file=sys.stderr)
        # "recording"/"processing"/"idle" produce no output today (silent).
