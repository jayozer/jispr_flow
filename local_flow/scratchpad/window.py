"""ScratchpadWindow: a floating always-on-top tkinter window over a NoteStore.

Two-process design (see README "Scratchpad"): this window is NOT a thread
inside `local-flow run` -- tkinter needs its `Tk` instance driven from a
single main thread to be reliable, especially on macOS, and `local-flow run`
already has its own main thread doing hotkey/audio work. Instead, `local-flow
pad --window` runs this window as its OWN main program (blocking, exactly
like `local-flow tray`'s pystray icon), and the dictate-to-pad hotkey inside a
*separate* `local-flow run` process (or the same process via `--with-
dictation`, see `local_flow.app._cmd_pad`) writes to the very same note files
this window reads. The two stay in sync purely through the filesystem: this
window polls the active note's mtime every `_REFRESH_POLL_MS` and reloads
from disk when it changes -- no IPC, sockets, or new dependency needed.

`tkinter` is imported lazily, inside `__init__` only, so importing this
module (or `local_flow.scratchpad`) never requires a Tk-enabled Python build;
only constructing a `ScratchpadWindow` does. A missing/broken tkinter raises
`LocalFlowError` with a fix-it hint, the same adapter-boundary discipline as
every other optional backend in this project (pystray/Pillow for the tray,
pynput for hotkeys, ...).

GUI behavior itself (does the window actually appear, stay on top, redraw on
an external edit) is manual-verify only -- see the README's manual checklist.
Only construction, the tkinter-missing error path, and the pure decision
helpers (`_should_autosave`, `_should_abort_note_switch`) are exercised in
tests.
"""

from __future__ import annotations

from local_flow.errors import LocalFlowError
from local_flow.scratchpad.store import NoteStore

# How long (ms) to wait after the last keystroke before autosaving the Text
# widget's buffer to disk. A debounce, not a throttle: EVERY edit re-arms
# this timer (see `_on_modified`'s `edit_modified(False)` reset trick below),
# so continuous typing keeps pushing the save back until the user actually
# pauses -- avoiding a disk write on every keystroke.
_AUTOSAVE_DEBOUNCE_MS = 1000

# How often (ms) to check the active note file's mtime for external changes
# (e.g. another terminal's `pad --append`, or the dictate-to-pad hotkey in a
# separate `local-flow run` process). Deliberately a poll, not a filesystem
# watch (inotify/FSEvents) -- avoids a new dependency and keeps this portable.
_REFRESH_POLL_MS = 500


def _should_autosave(disk_mtime: float | None, known_mtime: float | None) -> bool:
    """Whether `_autosave` may safely overwrite the note file right now.

    True only when the file's current on-disk mtime still matches
    `known_mtime` -- the mtime this window last saw at its own most recent
    load or save of this note. A mismatch means the file changed on disk
    for some OTHER reason in between (an external `pad --append`, or the
    dictate-to-pad hotkey in a separate `local-flow run` process landing an
    utterance) -- see `_autosave`'s docstring for what happens instead of
    clobbering it. A pure function of the two mtimes so the decision itself
    is unit-testable without a real `Tk` instance.
    """
    return disk_mtime == known_mtime


def _should_abort_note_switch(*, was_dirty: bool, flush_succeeded: bool) -> bool:
    """Whether `_on_note_selected` must abort switching notes rather than
    proceed with the newly-picked note.

    True only when the OLD note's buffer had unsaved edits (`was_dirty`)
    AND the flush attempted before switching (`_autosave`) was refused due
    to an on-disk conflict (`not flush_succeeded`) -- i.e. exactly the case
    where proceeding would silently discard the user's unsaved text with no
    way to recover it. When the buffer was clean there was nothing to flush
    (always proceed); when the flush succeeded, the buffer is safely on
    disk under the OLD note (also always proceed). A pure function of the
    two booleans so the decision itself is unit-testable without a real
    `Tk` instance.
    """
    return was_dirty and not flush_succeeded


class ScratchpadWindow:
    """A small always-on-top editor over one `NoteStore`'s active note.

    Widgets: an `OptionMenu` note switcher (populated from
    `store.list_notes()` at construction time -- notes created by another
    process *after* the window opens won't appear in the menu until the
    window is restarted; a known, documented limitation given this is a
    thin, manual-verify GUI glue layer, not a full editor) above a `Text`
    widget showing the current note's content.

    Two independent, deliberately asymmetric sync mechanisms:
    - Local edits -> disk: debounced 1s autosave (`_on_modified`/`_autosave`).
    - Disk -> local view: 500ms mtime poll (`_poll_refresh`), SKIPPED
      entirely while the buffer has unsaved edits (`self._dirty`) so an
      external change (e.g. a dictated utterance landing via the hotkey)
      can never clobber text the user is actively mid-edit on. Once the
      debounce fires (or the user switches notes, which flushes
      immediately), the next poll tick sees the fresh mtime and catches up.

    A third rule guards the reverse race: `_autosave` itself checks the
    note file's mtime (`_should_autosave`) before writing, and refuses to
    overwrite when it has moved since this window's own last load/save --
    i.e. an external append landed WHILE the buffer had unsaved edits, so
    the "skip the poll while dirty" rule above didn't help. Rather than
    silently destroying that external write (the old behavior), the stale
    autosave is a no-op: the user's buffer stays in the widget untouched,
    the external content on disk stays untouched, and the window title
    flips to a visible conflict notice. Switching notes is ALSO refused
    while that conflict is unresolved (`_on_note_selected`/
    `_should_abort_note_switch`) -- previously switching still flushed and
    proceeded even on a refused autosave, silently dropping the buffer,
    exactly the data loss this whole mechanism exists to prevent. The way
    out is to copy the buffer's text somewhere safe, then reload (e.g.
    restart the window) to pick up the external content fresh -- no data
    lost on either side.
    """

    def __init__(self, store: NoteStore) -> None:
        try:
            import tkinter as tk
        except ImportError as exc:
            raise LocalFlowError(
                "tkinter unavailable; use local-flow pad --show",
                hint="Install a Python build with tkinter support (e.g. "
                "`brew install python-tk@3.12` on macOS, the `python3-tk` "
                "package on Debian/Ubuntu Linux), or use `local-flow pad "
                "--show`/`--append`/`--use` instead of the window.",
            ) from exc

        self._tk = tk
        self.store = store
        self._dirty = False
        self._autosave_job: str | None = None
        self._current_note = store.active_note()
        self._note_mtime: float | None = None

        self.root = tk.Tk()
        self.root.title(f"local-flow scratchpad — {self._current_note}")
        self.root.attributes("-topmost", True)
        self.root.geometry("480x360")

        names = store.list_notes()
        if self._current_note not in names:
            names = [self._current_note, *names]
        self._note_var = tk.StringVar(value=self._current_note)
        menu_frame = tk.Frame(self.root)
        menu_frame.pack(fill="x")
        tk.Label(menu_frame, text="Note:").pack(side="left", padx=(4, 2))
        self._option_menu = tk.OptionMenu(
            menu_frame, self._note_var, *names, command=self._on_note_selected
        )
        self._option_menu.pack(side="left", fill="x", expand=True)

        self.text = tk.Text(self.root, wrap="word", undo=True)
        self.text.pack(fill="both", expand=True)
        self.text.bind("<<Modified>>", self._on_modified)

        self._load_active(force=True)
        self.root.after(_REFRESH_POLL_MS, self._poll_refresh)

    def _note_path(self):
        return self.store.notes_dir / f"{self._current_note}.md"

    def _mtime(self) -> float | None:
        path = self._note_path()
        return path.stat().st_mtime if path.is_file() else None

    def _load_active(self, force: bool = False) -> None:
        """Reload the current note's content from disk into the Text widget.

        A no-op when the buffer has unsaved edits unless `force` (used for
        the initial load and right after switching notes) -- see the class
        docstring's "disk -> local view" sync rule.
        """
        if self._dirty and not force:
            return
        content = self.store.read(self._current_note)
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        # `insert`/`delete` themselves flip the widget's internal "modified"
        # flag -- reset it so this programmatic load is never mistaken for a
        # user edit (which would otherwise mark `_dirty` and schedule a
        # pointless autosave of content that's already on disk verbatim).
        self.text.edit_modified(False)
        self._dirty = False
        self._note_mtime = self._mtime()

    def _on_modified(self, _event=None) -> None:
        if not self.text.edit_modified():
            return
        # Reset immediately (not in `_autosave`) so every subsequent
        # keystroke re-flips False->True and re-fires `<<Modified>>` too --
        # without this, Tk only fires the event once per edit *burst*
        # (the flag stays True across further edits until something resets
        # it), which would debounce from the first keystroke instead of the
        # most recent one.
        self.text.edit_modified(False)
        self._dirty = True
        if self._autosave_job is not None:
            self.root.after_cancel(self._autosave_job)
        self._autosave_job = self.root.after(_AUTOSAVE_DEBOUNCE_MS, self._autosave)

    def _autosave(self) -> bool:
        """Write the Text widget's buffer to disk -- unless `_should_autosave`
        says the note file changed on disk since this window last loaded or
        saved it (see the class docstring's third sync rule). On a conflict,
        this is a silent-to-disk, visible-in-title no-op: `self._dirty` is
        left `True` (so `_poll_refresh` keeps refusing to reload and clobber
        the buffer from the widget's side), nothing is written (so the
        external content already on disk is untouched), and the window title
        gets a conflict notice so the stall isn't invisible to the user. The
        next edit re-arms the debounce timer as usual, so this re-checks
        (and can resolve) the next time the user types.

        Returns whether the write actually happened (`True`) or was refused
        due to a conflict (`False`) -- `_on_note_selected` uses this to
        decide whether it's safe to switch notes without losing the buffer
        (see `_should_abort_note_switch`).
        """
        self._autosave_job = None
        disk_mtime = self._mtime()
        if not _should_autosave(disk_mtime, self._note_mtime):
            self.root.title(
                f"local-flow scratchpad — {self._current_note} — conflict: "
                "note changed on disk; copy your text, then reload"
            )
            return False
        content = self.text.get("1.0", "end-1c")
        self.store.write(content, name=self._current_note)
        self._dirty = False
        self._note_mtime = self._mtime()
        return True

    def _on_note_selected(self, name: str) -> None:
        was_dirty = self._dirty
        flush_succeeded = True
        if was_dirty:
            # Flush pending edits to the OLD note before switching, so
            # picking a different note from the menu never silently drops
            # unsaved work.
            flush_succeeded = self._autosave()
        if _should_abort_note_switch(was_dirty=was_dirty, flush_succeeded=flush_succeeded):
            # The flush was conflict-refused (see `_autosave`): the OLD
            # note's buffer is still unsaved and switching now would discard
            # it with no way back. Abort -- reset the OptionMenu selection
            # back to the note that's still showing (tkinter's OptionMenu
            # already flipped `self._note_var` to `name` before invoking
            # this callback) and leave everything else untouched; the
            # conflict title `_autosave` just set explains what to do next.
            self._note_var.set(self._current_note)
            return
        self._current_note = name
        self.store.set_active(name)
        self.root.title(f"local-flow scratchpad — {name}")
        self._load_active(force=True)

    def _refresh_note_menu(self) -> None:
        """Sync the OptionMenu's entries to `store.list_notes()`.

        Cheap (a directory glob), so done on every poll tick alongside the
        mtime check -- lets a note created by another process (e.g. `pad
        --new`/`--use` in a different terminal) show up in the switcher
        without restarting the window, even though the window's own content
        view only follows the currently *selected* note.
        """
        names = self.store.list_notes()
        if self._current_note not in names:
            names = [self._current_note, *names]
        menu = self._option_menu["menu"]
        menu.delete(0, "end")
        for name in names:
            menu.add_command(
                label=name, command=lambda n=name: self._on_note_selected(n)
            )

    def _poll_refresh(self) -> None:
        if not self._dirty:
            mtime = self._mtime()
            if mtime != self._note_mtime:
                self._load_active(force=True)
        self._refresh_note_menu()
        self.root.after(_REFRESH_POLL_MS, self._poll_refresh)

    def run(self) -> None:
        """Block running the Tk event loop. Manual-verify only."""
        self.root.mainloop()
