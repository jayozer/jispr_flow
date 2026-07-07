"""NoteStore, ScratchpadSink, ScratchpadWindow, and the `local-flow pad` CLI."""

import sys

import pytest

from local_flow.app import main
from local_flow.errors import LocalFlowError
from local_flow.scratchpad.sink import ScratchpadSink
from local_flow.scratchpad.store import NoteStore
from local_flow.scratchpad.window import ScratchpadWindow


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
        path = store.write("hello", name="work")
        assert path == tmp_path / "notes" / "work.md"
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

    def test_write_returns_path(self, tmp_path):
        store = NoteStore(tmp_path)
        path = store.write("x", name="scratch")
        assert path == tmp_path / "notes" / "scratch.md"

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
