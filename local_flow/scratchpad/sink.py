"""ScratchpadSink: a TextSink that lands dictation in a NoteStore note."""

from __future__ import annotations

from local_flow.insertion.base import TextSink
from local_flow.scratchpad.store import NoteStore


class ScratchpadSink(TextSink):
    """Routes ``insert``/``press_key`` into a :class:`NoteStore` note instead
    of the desktop -- swap this in for the configured sink and dictation
    lands in the active scratchpad note rather than the frontmost app.

    ``press_key("enter")`` marks a pending paragraph break rather than
    writing anything itself: :meth:`NoteStore.append` already prefixes a
    blank line onto any append made to a non-empty note, so a bare "press
    enter" immediately followed by the next utterance's ``insert`` would
    otherwise double up that separator (two blank lines instead of one).
    The pending flag is bookkeeping only -- kept so the sink's contract
    honestly records that a break was requested, rather than silently
    dropping it -- and is cleared by the next ``insert``. Any other key is a
    no-op, matching ``TextSink``'s own default.
    """

    name = "scratchpad"

    def __init__(self, store: NoteStore) -> None:
        self.store = store
        self._pending_break = False

    def insert(self, text: str) -> None:
        self._pending_break = False
        self.store.append(text)

    def press_key(self, key: str) -> None:
        if key == "enter":
            self._pending_break = True
