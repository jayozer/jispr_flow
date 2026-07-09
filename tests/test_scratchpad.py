"""NoteStore, ScratchpadSink, ScratchpadWindow, and the `local-flow pad` CLI."""

import os
import sys
import types

import pytest

from local_flow.app import main
from local_flow.errors import LocalFlowError
from local_flow.scratchpad.sink import ScratchpadSink
from local_flow.scratchpad.store import NoteStore
from local_flow.scratchpad.window import (
    ScratchpadWindow,
    _should_abort_note_switch,
    _should_autosave,
)


class TestNoteStoreBasics:
    def test_notes_dir_property(self, tmp_path):
        store = NoteStore(tmp_path)
        assert store.notes_dir == tmp_path / "notes"

    def test_list_notes_empty_when_missing(self, tmp_path):
        store = NoteStore(tmp_path)
        assert store.list_notes() == []

    def test_read_missing_note_is_empty_string(self, tmp_path):
        store = NoteStore(tmp_path)
        assert store.read("inbox") == ""

    def test_active_note_defaults_to_inbox(self, tmp_path):
        store = NoteStore(tmp_path)
        assert store.active_note() == "inbox"

    def test_now_seam_is_injectable_but_optional(self, tmp_path):
        # `now` is accepted for interface parity with HistoryStore; not
        # currently consulted by any NoteStore behavior.
        calls = {"n": 0}

        def fake_now():
            calls["n"] += 1
            from datetime import UTC, datetime

            return datetime(2026, 7, 7, tzinfo=UTC)

        store = NoteStore(tmp_path, now=fake_now)
        store.append("hello")
        assert calls["n"] == 0


class TestAppend:
    def test_append_creates_dirs_and_file_lazily(self, tmp_path):
        store = NoteStore(tmp_path)
        path = store.append("first thought")
        assert path == tmp_path / "notes" / "inbox.md"
        assert path.is_file()
        assert path.read_text() == "first thought"

    def test_append_to_empty_note_has_no_leading_blank_line(self, tmp_path):
        store = NoteStore(tmp_path)
        store.append("hello", name="work")
        assert store.read("work") == "hello"

    def test_second_append_gets_blank_line_separator(self, tmp_path):
        store = NoteStore(tmp_path)
        store.append("first", name="work")
        store.append("second", name="work")
        assert store.read("work") == "first\n\nsecond"

    def test_append_defaults_to_active_note(self, tmp_path):
        store = NoteStore(tmp_path)
        store.set_active("work")
        store.append("hi")
        assert store.read("work") == "hi"
        assert store.read("inbox") == ""

    def test_append_returns_path(self, tmp_path):
        store = NoteStore(tmp_path)
        path = store.append("x", name="scratch")
        assert path == tmp_path / "notes" / "scratch.md"


class TestActiveNotePersistence:
    def test_set_active_persists_across_instances(self, tmp_path):
        NoteStore(tmp_path).set_active("meetings")
        assert NoteStore(tmp_path).active_note() == "meetings"

    def test_set_active_validates_name(self, tmp_path):
        store = NoteStore(tmp_path)
        with pytest.raises(LocalFlowError):
            store.set_active("../evil")

    def test_active_note_survives_process_restart_simulation(self, tmp_path):
        store1 = NoteStore(tmp_path)
        store1.set_active("project-x")
        store1.append("note one")
        store2 = NoteStore(tmp_path)
        assert store2.active_note() == "project-x"
        assert store2.read("project-x") == "note one"


class TestCreate:
    def test_create_makes_empty_file(self, tmp_path):
        store = NoteStore(tmp_path)
        path = store.create("todo")
        assert path.is_file()
        assert path.read_text() == ""

    def test_create_is_idempotent_no_overwrite(self, tmp_path):
        store = NoteStore(tmp_path)
        store.append("keep me", name="todo")
        path = store.create("todo")
        assert path.read_text() == "keep me"

    def test_create_validates_name(self, tmp_path):
        store = NoteStore(tmp_path)
        with pytest.raises(LocalFlowError):
            store.create("a/b")


class TestWrite:
    """`NoteStore.write` (T2): the whole-buffer overwrite `ScratchpadWindow`'s
    autosave uses -- distinct from `append`'s "add a paragraph" semantics.
    """

    def test_write_creates_dirs_and_file_lazily(self, tmp_path):
        store = NoteStore(tmp_path)
        store.write("hello", name="work")
        path = tmp_path / "notes" / "work.md"
        assert path.read_text() == "hello"

    def test_write_overwrites_existing_content_verbatim_no_blank_line(self, tmp_path):
        store = NoteStore(tmp_path)
        store.append("first", name="work")
        store.write("replaced entirely", name="work")
        assert store.read("work") == "replaced entirely"

    def test_write_defaults_to_active_note(self, tmp_path):
        store = NoteStore(tmp_path)
        store.set_active("work")
        store.write("hi")
        assert store.read("work") == "hi"
        assert store.read("inbox") == ""

    def test_write_validates_name(self, tmp_path):
        store = NoteStore(tmp_path)
        with pytest.raises(LocalFlowError):
            store.write("text", name="../evil")

    def test_write_returns_the_resulting_mtime(self, tmp_path):
        # `ScratchpadWindow._autosave` records this value as "the mtime of my
        # own save"; it is statted before the atomic rename publishes the
        # write, so it can never absorb a concurrent writer landing after.
        store = NoteStore(tmp_path)
        mtime = store.write("x", name="scratch")
        assert mtime == (tmp_path / "notes" / "scratch.md").stat().st_mtime

    def test_write_empty_string_clears_the_note(self, tmp_path):
        store = NoteStore(tmp_path)
        store.append("something", name="work")
        store.write("", name="work")
        assert store.read("work") == ""


class TestListNotes:
    def test_sorted_names_no_extension(self, tmp_path):
        store = NoteStore(tmp_path)
        store.create("zebra")
        store.create("alpha")
        store.create("mid")
        assert store.list_notes() == ["alpha", "mid", "zebra"]

    def test_active_marker_file_excluded(self, tmp_path):
        store = NoteStore(tmp_path)
        store.set_active("inbox")
        store.create("inbox")
        assert store.list_notes() == ["inbox"]


class TestNameValidation:
    @pytest.mark.parametrize(
        "bad_name",
        [
            "../evil",
            "a/b",
            "",
            "x" * 65,
            "/etc/passwd",
            "..\\evil",
            "evil\n",
            "trailing dot.",
            "trailing space ",
        ],
    )
    def test_rejects_invalid_names(self, tmp_path, bad_name):
        store = NoteStore(tmp_path)
        with pytest.raises(LocalFlowError) as exc_info:
            store.set_active(bad_name)
        assert exc_info.value.hint

    def test_rejects_invalid_name_on_append(self, tmp_path):
        store = NoteStore(tmp_path)
        with pytest.raises(LocalFlowError):
            store.append("text", name="../evil")

    def test_rejects_invalid_name_on_create(self, tmp_path):
        store = NoteStore(tmp_path)
        with pytest.raises(LocalFlowError):
            store.create("")

    def test_rejects_invalid_name_on_read(self, tmp_path):
        store = NoteStore(tmp_path)
        with pytest.raises(LocalFlowError):
            store.read("../evil")

    def test_rejects_trailing_newline_on_create(self, tmp_path):
        # A bare trailing "$" in the old regex admits a trailing "\n"
        # (`re.match` treats "$" as matching just before a final newline);
        # `re.fullmatch` (or an escaped "\Z") does not.
        store = NoteStore(tmp_path)
        with pytest.raises(LocalFlowError):
            store.create("evil\n")

    def test_accepts_boundary_length_name(self, tmp_path):
        store = NoteStore(tmp_path)
        name = "x" * 64
        store.create(name)
        assert store.list_notes() == [name]

    def test_accepts_spaces_dots_dashes_underscores(self, tmp_path):
        store = NoteStore(tmp_path)
        name = "My Notes_v2.draft-1"
        store.create(name)
        assert name in store.list_notes()


class TestActiveNoteCorruption:
    """``notes/.active`` is a hand-editable plain-text file; a corrupted or
    hand-crafted value (e.g. a path-traversal string) must never make
    ``active_note()`` (and callers defaulting through it) escape
    ``notes_dir`` -- it should degrade to ``"inbox"`` instead of raising or
    passing the bad value through."""

    def test_active_note_falls_back_to_inbox_when_traversal_string(self, tmp_path):
        store = NoteStore(tmp_path)
        store.notes_dir.mkdir(parents=True)
        (store.notes_dir / ".active").write_text("../../elsewhere/leak", encoding="utf-8")
        assert store.active_note() == "inbox"

    def test_active_note_falls_back_to_inbox_when_blank(self, tmp_path):
        store = NoteStore(tmp_path)
        store.notes_dir.mkdir(parents=True)
        (store.notes_dir / ".active").write_text("   ", encoding="utf-8")
        assert store.active_note() == "inbox"

    def test_append_after_corrupted_active_still_targets_inbox(self, tmp_path):
        store = NoteStore(tmp_path)
        store.notes_dir.mkdir(parents=True)
        (store.notes_dir / ".active").write_text("../../elsewhere/leak", encoding="utf-8")
        store.append("safe text")
        assert store.read("inbox") == "safe text"


class TestScratchpadSink:
    def test_insert_appends_to_store(self, tmp_path):
        store = NoteStore(tmp_path)
        sink = ScratchpadSink(store)
        sink.insert("hello world")
        assert store.read(store.active_note()) == "hello world"

    def test_press_key_enter_then_insert_yields_single_blank_line(self, tmp_path):
        store = NoteStore(tmp_path)
        sink = ScratchpadSink(store)
        sink.insert("a")
        sink.press_key("enter")
        sink.insert("b")
        assert store.read(store.active_note()) == "a\n\nb"

    def test_press_key_other_keys_are_no_ops(self, tmp_path):
        store = NoteStore(tmp_path)
        sink = ScratchpadSink(store)
        sink.insert("a")
        sink.press_key("tab")
        assert store.read(store.active_note()) == "a"

    def test_press_key_enter_with_no_prior_content_writes_nothing(self, tmp_path):
        store = NoteStore(tmp_path)
        sink = ScratchpadSink(store)
        sink.press_key("enter")
        assert store.read(store.active_note()) == ""

    def test_two_plain_inserts_also_paragraph_separate(self, tmp_path):
        # NoteStore.append's own blank-line-on-non-empty rule applies to
        # every append regardless of an intervening `press_key`, so two
        # consecutive dictated utterances land as separate paragraphs even
        # without an explicit "press enter".
        store = NoteStore(tmp_path)
        sink = ScratchpadSink(store)
        sink.insert("a")
        sink.insert("b")
        assert store.read(store.active_note()) == "a\n\nb"


class TestPadCli:
    def test_show_active_when_empty(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        assert main(["pad"]) == 0
        out = capsys.readouterr().out
        assert "-- inbox --" in out
        assert "(empty)" in out

    def test_append_then_show(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        assert main(["pad", "--append", "first thought"]) == 0
        capsys.readouterr()
        assert main(["pad", "--show"]) == 0
        out = capsys.readouterr().out
        assert "first thought" in out

    def test_append_with_note_targets_other_note(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        assert main(["pad", "--append", "todo item", "--note", "work"]) == 0
        capsys.readouterr()
        assert main(["pad", "--show", "work"]) == 0
        assert "todo item" in capsys.readouterr().out
        capsys.readouterr()
        assert main(["pad", "--show", "inbox"]) == 0
        assert "(empty)" in capsys.readouterr().out

    def test_note_flag_without_append_fails_helpfully(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["pad", "--show", "--note", "work"])
        assert code == 1

    def test_use_sets_active_and_creates(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        assert main(["pad", "--use", "meetings"]) == 0
        out = capsys.readouterr().out
        assert "meetings" in out
        assert (tmp_path / "notes" / "meetings.md").is_file()
        capsys.readouterr()
        assert main(["pad", "--append", "agenda"]) == 0
        assert (tmp_path / "notes" / "meetings.md").read_text() == "agenda"

    def test_new_creates_empty_note_without_switching_active(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        assert main(["pad", "--new", "someday"]) == 0
        assert (tmp_path / "notes" / "someday.md").is_file()
        capsys.readouterr()
        # active note is still inbox, unaffected by --new
        assert main(["pad", "--append", "x"]) == 0
        assert (tmp_path / "notes" / "inbox.md").read_text() == "x"

    def test_list_empty_state(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        assert main(["pad", "--list"]) == 0
        out = capsys.readouterr().out
        assert "no notes yet" in out

    def test_list_marks_active_note(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        main(["pad", "--new", "alpha"])
        main(["pad", "--use", "beta"])
        capsys.readouterr()
        assert main(["pad", "--list"]) == 0
        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert "alpha" in lines
        assert "beta (active)" in lines

    def test_show_unknown_note_is_friendly_empty(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        assert main(["pad", "--show", "nonexistent"]) == 0
        out = capsys.readouterr().out
        assert "-- nonexistent --" in out
        assert "(empty)" in out

    def test_invalid_note_name_fails_helpfully(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["pad", "--use", "../evil"])
        assert code == 1
        err = capsys.readouterr().err
        assert "invalid note name" in err
        assert "hint" in err

    def test_mutually_exclusive_flags_rejected_by_argparse(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        with pytest.raises(SystemExit):
            main(["pad", "--list", "--use", "work"])

    def test_show_rejects_path_traversal_and_never_reads_outside_file(
        self, capsys, tmp_path, monkeypatch
    ):
        # data dir is nested two levels under tmp_path, so
        # "../../elsewhere/leak" (relative to notes_dir = data_dir/notes)
        # resolves to tmp_path/elsewhere/leak.md -- a real file outside the
        # notes directory, seeded with a secret that must never surface.
        data_dir = tmp_path / "data"
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(data_dir))
        outside_dir = tmp_path / "elsewhere"
        outside_dir.mkdir()
        (outside_dir / "leak.md").write_text("TOP SECRET, DO NOT LEAK", encoding="utf-8")

        code = main(["pad", "--show", "../../elsewhere/leak"])

        assert code == 1
        captured = capsys.readouterr()
        assert "TOP SECRET" not in captured.out
        assert "TOP SECRET" not in captured.err
        assert "invalid note name" in captured.err
        assert "hint" in captured.err

    def test_show_falls_back_to_inbox_when_active_marker_corrupted(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        (notes_dir / ".active").write_text("../../elsewhere/leak", encoding="utf-8")

        code = main(["pad", "--show"])

        assert code == 0
        out = capsys.readouterr().out
        assert "-- inbox --" in out

    def test_append_falls_back_to_inbox_when_active_marker_corrupted(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        (notes_dir / ".active").write_text("../../elsewhere/leak", encoding="utf-8")

        code = main(["pad", "--append", "still works"])

        assert code == 0
        assert (notes_dir / "inbox.md").read_text(encoding="utf-8") == "still works"

    def test_with_dictation_without_window_fails_helpfully(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["pad", "--with-dictation"])
        assert code == 1

    def test_window_flag_is_mutually_exclusive_with_others(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        with pytest.raises(SystemExit):
            main(["pad", "--window", "--list"])

    def test_window_missing_tkinter_fails_with_hint(self, tmp_path, capsys, monkeypatch):
        # Mirrors TrayApp's pystray-missing test: simulate tkinter being
        # unavailable by making `import tkinter` raise ImportError.
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        monkeypatch.setitem(sys.modules, "tkinter", None)

        code = main(["pad", "--window"])

        assert code == 1
        captured = capsys.readouterr()
        assert "tkinter unavailable" in captured.err
        assert "pad --show" in captured.err
        assert "hint" in captured.err


class TestScratchpadWindowConstruction:
    """`ScratchpadWindow` (T2): tkinter is imported lazily, only inside
    `__init__`, so a missing/broken tkinter raises `LocalFlowError` with a
    fix-it hint -- the same adapter-boundary discipline as every other
    optional backend (pystray/Pillow, pynput, ...). GUI behavior itself
    (does the window actually render, stay on top, live-refresh) is
    manual-verify only; this is the one headless-testable path.
    """

    def test_missing_tkinter_raises_local_flow_error_with_hint(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "tkinter", None)  # simulates ImportError
        store = NoteStore(tmp_path)

        with pytest.raises(LocalFlowError) as exc_info:
            ScratchpadWindow(store)

        assert "tkinter unavailable" in exc_info.value.message
        assert "pad --show" in exc_info.value.message
        assert exc_info.value.hint
        assert "python3-tk" in exc_info.value.hint or "python-tk" in exc_info.value.hint


class TestShouldAutosave:
    """`_should_autosave` (pure helper): `ScratchpadWindow._autosave`'s
    conflict guard against clobbering an external write (e.g. the dictate-
    to-pad hotkey's `append`, or another terminal's `pad --append`) that
    landed while the window had unsaved edits in flight. See the class
    docstring's third sync rule; the actual title/UI change on a conflict is
    manual-verify only (needs a real `Tk` instance), so only the decision
    itself -- do the mtimes still match? -- is unit-tested here.
    """

    def test_matching_mtimes_is_safe_to_save(self):
        assert _should_autosave(100.0, 100.0) is True

    def test_both_none_is_safe_to_save(self):
        # A brand-new note that has never been loaded or saved from disk:
        # nothing has written to it, so there is nothing to conflict with.
        assert _should_autosave(None, None) is True

    def test_mismatched_mtimes_blocks_save(self):
        # The file's mtime moved since our last load/save -- something else
        # (an external append, the dictate-to-pad hotkey) wrote to it.
        assert _should_autosave(200.0, 100.0) is False

    def test_file_appeared_externally_since_last_known_state_blocks_save(self):
        # We last saw no file at all (`known_mtime=None`), but one now
        # exists on disk -- created by an external writer in the meantime.
        assert _should_autosave(100.0, None) is False

    def test_file_vanished_externally_since_last_known_state_blocks_save(self):
        # We last saw a real file, but it's gone now (deleted externally) --
        # still a change since our last known state, so still not safe to
        # blindly overwrite (recreate) without the user's awareness.
        assert _should_autosave(None, 100.0) is False


class TestShouldAbortNoteSwitch:
    """`_should_abort_note_switch` (pure helper, review fix): before this
    fix, `_on_note_selected` flushed via `_autosave()` and then switched
    notes UNCONDITIONALLY, even when `_autosave` had just refused to write
    because of an on-disk conflict (see `TestShouldAutosave`) -- silently
    dropping the user's unsaved buffer, exactly the data loss the README
    promises never happens ("neither side's text is ever silently
    destroyed"). `_on_note_selected` now aborts the switch (and resets the
    `OptionMenu` selection back to the current note) whenever the flush it
    just attempted was refused. This helper is the pure yes/no decision
    behind that; the actual `OptionMenu`/title side effects need a real
    `Tk` instance and are manual-verify only.
    """

    def test_clean_buffer_never_aborts(self):
        # Nothing was dirty, so nothing needed to flush -- the switch always
        # proceeds.
        assert _should_abort_note_switch(was_dirty=False, flush_succeeded=True) is False

    def test_dirty_buffer_with_successful_flush_does_not_abort(self):
        # The old note's edits made it to disk safely -- switching now loses
        # nothing.
        assert _should_abort_note_switch(was_dirty=True, flush_succeeded=True) is False

    def test_dirty_buffer_with_refused_flush_aborts(self):
        # The flush was conflict-refused (an external write landed on the
        # OLD note while it was dirty) -- switching now would discard the
        # user's unsaved edits with no way back.
        assert _should_abort_note_switch(was_dirty=True, flush_succeeded=False) is True


def _install_fake_tkinter(monkeypatch):
    """A minimal in-memory tkinter stand-in so the REAL `ScratchpadWindow`
    logic (`_load_active`/`_autosave` and their mtime bookkeeping) runs
    headless -- no display or Tk-enabled Python build needed. Implements only
    what `__init__` and the autosave path actually touch."""

    class _Widget:
        def __init__(self, *args, **kwargs):
            pass

        def pack(self, **kwargs):
            pass

    class _Text(_Widget):
        def __init__(self, *args, **kwargs):
            self._content = ""
            self._modified = False

        def bind(self, *args, **kwargs):
            pass

        def delete(self, start, end):
            self._content = ""

        def insert(self, index, text):
            self._content += text

        def get(self, start, end):
            return self._content

        def edit_modified(self, flag=None):
            if flag is None:
                return self._modified
            self._modified = bool(flag)

    class _Tk:
        def __init__(self):
            self._title = ""

        def title(self, text=None):
            if text is not None:
                self._title = text
            return self._title

        def attributes(self, *args):
            pass

        def geometry(self, spec):
            pass

        def after(self, ms, callback=None):
            # Never auto-fires: tests drive the window's methods directly.
            return "after#0"

        def after_cancel(self, job):
            pass

        def mainloop(self):
            pass

    class _StringVar:
        def __init__(self, value=""):
            self._value = value

        def set(self, value):
            self._value = value

        def get(self):
            return self._value

    class _Menu:
        def __init__(self):
            self.entries = []  # (label, command) in menu order

        def delete(self, first, last):
            self.entries = []

        def add_command(self, label=None, command=None, **kwargs):
            self.entries.append((label, command))

    class _OptionMenu(_Widget):
        def __init__(self, *args, **kwargs):
            self._menu = _Menu()

        def __getitem__(self, key):
            return self._menu

    fake = types.ModuleType("tkinter")
    fake.Tk = _Tk
    fake.StringVar = _StringVar
    fake.Frame = _Widget
    fake.Label = _Widget
    fake.OptionMenu = _OptionMenu
    fake.Text = _Text
    monkeypatch.setitem(sys.modules, "tkinter", fake)


def _bump_mtime(path):
    """Push the file's mtime one full second forward. A racing external
    append could otherwise land within the filesystem's mtime granularity of
    the window's own load/save, making the race invisible to a stat-based
    check -- this keeps the tests deterministic on any filesystem."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


class TestScratchpadAutosaveToctou:
    """Review item 8: the window must stat the note's mtime BEFORE reading
    content (and record its own save's pre-publish mtime), so the recorded
    "last seen" mtime is a lower bound. An external dictation append landing
    in the stat/read (or write/record) gap must then surface as a conflict on
    the next autosave instead of being stamped "already seen" and overwritten
    by the stale buffer."""

    def test_append_between_read_and_stat_survives_next_autosave(self, tmp_path, monkeypatch):
        _install_fake_tkinter(monkeypatch)
        store = NoteStore(tmp_path)
        store.write("hello", name="inbox")
        note_path = tmp_path / "notes" / "inbox.md"

        real_read = store.read

        def read_then_external_append(name):
            content = real_read(name)
            # lands in `_load_active`'s gap between reading the content and
            # recording the note's mtime
            store.append("external dictation", name=name)
            _bump_mtime(note_path)
            return content

        monkeypatch.setattr(store, "read", read_then_external_append)
        window = ScratchpadWindow(store)  # the initial load races the append
        monkeypatch.setattr(store, "read", real_read)

        # the user edits the (stale) buffer; the debounced autosave fires
        window.text.delete("1.0", "end")
        window.text.insert("1.0", "hello EDITED")
        window._dirty = True

        assert window._autosave() is False  # refused as a conflict...
        content = note_path.read_text()
        assert "external dictation" in content  # ...so the append survived
        assert "EDITED" not in content  # and the stale buffer never landed
        assert "conflict" in window.root.title()

    def test_append_right_after_autosave_write_survives_next_autosave(
        self, tmp_path, monkeypatch
    ):
        _install_fake_tkinter(monkeypatch)
        store = NoteStore(tmp_path)
        store.write("hello", name="inbox")
        note_path = tmp_path / "notes" / "inbox.md"
        window = ScratchpadWindow(store)

        real_write = store.write

        def write_then_external_append(content, name=None):
            result = real_write(content, name=name)
            # lands in `_autosave`'s gap between writing the buffer and
            # recording the resulting mtime
            store.append("external dictation", name=name)
            _bump_mtime(note_path)
            return result

        monkeypatch.setattr(store, "write", write_then_external_append)
        window.text.delete("1.0", "end")
        window.text.insert("1.0", "hello EDITED")
        window._dirty = True
        assert window._autosave() is True  # the save itself succeeds...
        monkeypatch.setattr(store, "write", real_write)

        # ...then the user keeps editing; the next autosave must treat the
        # external append as a conflict rather than overwrite it
        window.text.delete("1.0", "end")
        window.text.insert("1.0", "hello EDITED MORE")
        window._dirty = True
        assert window._autosave() is False
        content = note_path.read_text()
        assert "external dictation" in content
        assert "MORE" not in content


class TestNoteDropdownTracksActiveNote:
    """Review item 28: `_refresh_note_menu` rebuilds the `OptionMenu`'s
    entries as plain `add_command` items -- NOT the `tkinter._setit` wrapper
    the constructor originally wired -- so nothing set `_note_var` (the
    dropdown's button label) when a rebuilt entry was picked: after the
    first poll tick the label stopped tracking the active note.
    `_on_note_selected` must keep `_note_var` in sync itself.
    """

    def _window_with_two_notes(self, tmp_path, monkeypatch):
        _install_fake_tkinter(monkeypatch)
        store = NoteStore(tmp_path)
        store.write("inbox content", name="inbox")
        store.create("ideas")
        return ScratchpadWindow(store)

    def _rebuilt_command_for(self, window, name):
        """The `command` callback for `name` in the REBUILT menu entries."""
        window._refresh_note_menu()  # what every poll tick does
        menu = window._option_menu["menu"]
        return next(command for label, command in menu.entries if label == name)

    def test_selecting_from_a_rebuilt_menu_updates_the_dropdown_label(
        self, tmp_path, monkeypatch
    ):
        window = self._window_with_two_notes(tmp_path, monkeypatch)
        assert window._note_var.get() == "inbox"

        self._rebuilt_command_for(window, "ideas")()

        assert window._current_note == "ideas"
        assert window.store.active_note() == "ideas"
        assert window._note_var.get() == "ideas"

    def test_conflict_aborted_switch_keeps_the_label_on_the_current_note(
        self, tmp_path, monkeypatch
    ):
        window = self._window_with_two_notes(tmp_path, monkeypatch)

        # Dirty buffer + an external write to the note file underneath it:
        # the flush `_on_note_selected` attempts is conflict-refused, so the
        # switch aborts -- and the dropdown label must stay on the old note.
        window.text.delete("1.0", "end")
        window.text.insert("1.0", "unsaved edits")
        window._dirty = True
        window.store.append("external dictation", name="inbox")
        _bump_mtime(tmp_path / "notes" / "inbox.md")

        self._rebuilt_command_for(window, "ideas")()

        assert window._current_note == "inbox"
        assert window._note_var.get() == "inbox"
        assert "conflict" in window.root.title()
