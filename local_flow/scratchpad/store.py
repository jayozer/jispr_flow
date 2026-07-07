"""NoteStore: plain markdown scratchpad notes, one file per note.

Notes live under ``data_dir/notes/<name>.md``, created lazily on first write
-- the same "just a file, hand-editable, tolerant of being poked at by the
user" idiom as ``HistoryStore``/the personalization stores elsewhere in this
project. Which note is "active" (the one dictation lands in via
``ScratchpadSink`` and CLI calls default to) is persisted in
``notes/.active`` as a plain-text name, so the choice survives across
separate process invocations (e.g. `local-flow pad --use work` now, `local-
flow pad --append ...` later, in a different process).

``now`` is accepted (matching ``HistoryStore``'s injectable-clock seam) but
unused today -- there is no timestamped data here yet. Kept for interface
parity and so a future feature (e.g. a timestamped heading per append)
doesn't need a constructor signature change.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from local_flow.errors import LocalFlowError

# Letters, digits, spaces, '.', '_', '-' only -- no '/' or other path
# separators, so a name can never escape ``notes_dir`` (rules out "../evil",
# "a/b", absolute paths, etc). 1-64 characters; empty names are rejected.
# Checked with ``fullmatch``, not ``match`` + a trailing "$": "$" alone
# matches just before a trailing newline, so a bare `match(r"...{1,64}$")`
# would wrongly accept "evil\n"; ``fullmatch`` requires the whole string
# (all 1-64 chars, no more) to fit the character class.
_NAME_RE = re.compile(r"[A-Za-z0-9._ -]{1,64}")

_DEFAULT_NOTE = "inbox"
_ACTIVE_FILE = ".active"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_valid_name(name: str) -> bool:
    """Whether ``name`` is a legal note name: see :data:`_NAME_RE`, plus a
    ban on a trailing '.' or ' ' -- both otherwise-legal characters here,
    but ones Windows strips (or rejects outright) from filenames, so a note
    created on one platform could silently resolve to a different file (or
    fail to open at all) on another."""
    return bool(_NAME_RE.fullmatch(name)) and name[-1] not in {".", " "}


def _validate_name(name: str) -> None:
    if not _is_valid_name(name):
        raise LocalFlowError(
            f"invalid note name {name!r}.",
            hint="Note names may only use letters, digits, spaces, '.', '_', '-' "
            "(1-64 characters), may never use a path separator like '/' or '..', "
            "and may not end in '.' or ' ' (Windows-hostile).",
        )


class NoteStore:
    def __init__(self, data_dir: Path, now: Callable[[], datetime] | None = None) -> None:
        self.data_dir = Path(data_dir)
        self._now = now or _utc_now

    @property
    def notes_dir(self) -> Path:
        return self.data_dir / "notes"

    @property
    def _active_path(self) -> Path:
        return self.notes_dir / _ACTIVE_FILE

    def _note_path(self, name: str) -> Path:
        return self.notes_dir / f"{name}.md"

    def list_notes(self) -> list[str]:
        """Sorted note names (no ``.md`` extension); ``[]`` if none yet."""
        if not self.notes_dir.is_dir():
            return []
        return sorted(path.stem for path in self.notes_dir.glob("*.md"))

    def active_note(self) -> str:
        """The persisted active note name, defaulting to ``"inbox"``.

        This is a degraded-read path, not a validating one: :meth:`set_active`
        already validates on write, but ``notes/.active`` is a plain-text
        file a user (or a bug) could put anything into directly. If its
        contents aren't a legal note name -- including a path-traversal
        string like ``"../../elsewhere/leak"`` -- we quietly fall back to
        ``"inbox"`` rather than raising, so a hand-corrupted marker file can
        never make a bare `pad --show`/`pad --append` (which both default
        through here) escape ``notes_dir`` or blow up the CLI outright.
        Callers that want a hard failure on an explicit bad name should go
        through :meth:`read`/:meth:`append`/:meth:`create`/:meth:`set_active`
        directly instead.
        """
        if not self._active_path.is_file():
            return _DEFAULT_NOTE
        name = self._active_path.read_text(encoding="utf-8").strip()
        return name if _is_valid_name(name) else _DEFAULT_NOTE

    def set_active(self, name: str) -> None:
        """Persist ``name`` as the active note. Does not create the note file
        itself -- callers that want it to exist should also call
        :meth:`create` (as ``local-flow pad --use`` does)."""
        _validate_name(name)
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self._active_path.write_text(name, encoding="utf-8")

    def read(self, name: str) -> str:
        """The note's full text, or ``""`` if it doesn't exist yet.

        Validates ``name`` first, same as :meth:`append`/:meth:`create`/
        :meth:`set_active` -- without this, a caller-supplied name like
        ``"../../elsewhere/leak"`` would resolve outside ``notes_dir`` and
        silently read an arbitrary ``*.md`` file on disk.
        """
        _validate_name(name)
        path = self._note_path(name)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def append(self, text: str, name: str | None = None) -> Path:
        """Append ``text`` to ``name`` (or the active note); creates the
        notes directory/file lazily. When the note already has content, the
        append is prefixed with a blank-line paragraph separator
        (``"\\n\\n" + text``); an empty/missing note just gets ``text``
        directly, with no leading blank line. Returns the note's path.

        Concurrency note: there is no cross-process locking here by design
        (this is a single-user, local-first tool) -- the read-to-decide-
        separator step and the ``"a"``-mode write are two separate
        operations. Append-mode writes are effectively line-atomic for
        realistic note sizes, so two processes appending won't interleave
        garbled text, but if they race on the very *first* append to the
        same currently-empty note, both may see it as empty and skip the
        blank-line separator, landing back-to-back with no blank line
        between them. A lone writer -- the normal case -- is unaffected.
        """
        target = name if name is not None else self.active_note()
        _validate_name(target)
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        path = self._note_path(target)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        addition = f"\n\n{text}" if existing else text
        with path.open("a", encoding="utf-8") as fh:
            fh.write(addition)
        return path

    def create(self, name: str) -> Path:
        """Create an empty note file; idempotent (an existing file is left
        untouched, never overwritten). Returns the note's path."""
        _validate_name(name)
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        path = self._note_path(name)
        if not path.is_file():
            path.touch()
        return path

    def write(self, text: str, name: str | None = None) -> Path:
        """Overwrite ``name`` (or the active note) with ``text`` verbatim --
        the "save the whole buffer" counterpart to :meth:`append`'s "add a
        paragraph" semantics.

        Used by :class:`~local_flow.scratchpad.window.ScratchpadWindow`'s
        debounced autosave: once a note is open for hand-editing, the Text
        widget's full buffer is the source of truth, not just appended
        dictation, so a plain overwrite (no blank-line separator logic) is
        the right operation. Creates the notes directory lazily; validates
        ``name`` first, same as every other method here.

        Concurrency note (mirrors :meth:`append`'s): no cross-process
        locking here either, and this is a stronger clobber risk than
        ``append``'s -- a whole-buffer overwrite has no way to merge with
        whatever else is on disk, so the last writer simply wins, full
        stop. ``ScratchpadWindow`` is the one caller today, and it guards
        the gap itself rather than pushing the guard down here: before
        calling this, it checks the note file's mtime against the mtime it
        last loaded/saved (``_should_autosave``), and skips the write
        entirely if they've diverged (an external append landed in
        between) rather than silently destroying that write. A caller that
        skips that check -- e.g. a future non-window caller of this method
        -- gets no such protection; it is purely a `NoteStore.write`-level
        "whole buffer overwrite, last writer wins" primitive.
        """
        target = name if name is not None else self.active_note()
        _validate_name(target)
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        path = self._note_path(target)
        path.write_text(text, encoding="utf-8")
        return path
