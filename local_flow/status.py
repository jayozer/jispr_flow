"""Status reporting seam for the dictation run loop.

``_run_loop`` (in :mod:`local_flow.app`) no longer prints directly; it emits
state transitions through a :class:`StatusReporter`. ``ConsoleReporter``
reproduces today's CLI output byte-for-byte. GUI reporters (tray icon and
floating recording pill) subscribe to the same transitions without changing
the transcription pipeline.
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

    wants_audio_level = False

    @abstractmethod
    def notify(self, state: State, detail: str = "") -> None:
        """Report a transition to ``state`` with an optional ``detail`` string."""

    def audio_level(self, level: float) -> None:
        """Report normalized live mic energy when a visual reporter wants it.

        The default is deliberately a no-op so console/tray/test reporters and
        third-party reporters remain source-compatible. ``_run_loop`` only
        asks the audio backend for levels when ``wants_audio_level`` is true.
        """
        return None


class CompositeReporter(StatusReporter):
    """Fan status and optional mic levels out to multiple reporters."""

    def __init__(self, *reporters: StatusReporter) -> None:
        self._reporters = reporters
        self.wants_audio_level = any(
            reporter.wants_audio_level for reporter in reporters
        )

    def notify(self, state: State, detail: str = "") -> None:
        for reporter in self._reporters:
            reporter.notify(state, detail)

    def audio_level(self, level: float) -> None:
        for reporter in self._reporters:
            if reporter.wants_audio_level:
                reporter.audio_level(level)


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
