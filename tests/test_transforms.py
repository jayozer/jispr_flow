"""Selection capture, the transforms registry, and `local-flow transform`."""

import json

import pytest

from local_flow.app import main
from local_flow.errors import ConfigError, LMStudioConnectionError
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import DEFAULT_TRANSFORMS, PersonalizationStore
from local_flow.transforms.registry import apply_transform, build_transform_messages
from local_flow.transforms.selection import MockSelectionBackend, SelectionCapture


class TestSelectionCapture:
    def test_capture_happy_path_and_clipboard_restore(self):
        backend = MockSelectionBackend(clipboard="old clipboard", selection_text="selected text")
        sleeps = []
        capture = SelectionCapture(backend, sleep=sleeps.append)

        result = capture.capture()

        assert result == "selected text"
        # Clipboard is cleared before the copy chord is sent, so a selection
        # identical to the old clipboard content is still detectable.
        assert backend.events[0] == "write:"
        assert backend.events[1] == "copy"

        capture.replace("NEW TEXT")

        # write -> paste -> (settle sleep) -> restore, in that order.
        assert backend.events[-3:] == ["write:NEW TEXT", "paste", "write:old clipboard"]
        assert backend.clipboard == "old clipboard"
        assert sleeps[-1] == 0.15  # the settle sleep before restoring

    def test_nothing_selected_times_out_to_empty_string(self):
        backend = MockSelectionBackend(clipboard="", selection_text=None)
        sleeps = []
        capture = SelectionCapture(
            backend, poll_timeout_s=0.1, poll_interval_s=0.02, sleep=sleeps.append
        )

        result = capture.capture()

        assert result == ""
        assert sleeps  # it did poll (not zero-iteration)
        assert all(s == 0.02 for s in sleeps)

    def test_capture_does_not_sleep_when_selection_appears_immediately(self):
        backend = MockSelectionBackend(clipboard="x", selection_text="fast text")
        sleeps = []
        capture = SelectionCapture(backend, sleep=sleeps.append)

        result = capture.capture()

        assert result == "fast text"
        assert sleeps == []  # no polling needed: clipboard changed on the first read

    def test_replace_ordering_write_then_paste_then_restore(self):
        backend = MockSelectionBackend(clipboard="saved")
        capture = SelectionCapture(backend, sleep=lambda s: None)
        # No capture() call: replace() alone must still work off whatever
        # _saved_clipboard currently holds (constructor default "").
        capture.replace("hello")
        assert backend.events == ["write:hello", "paste", "write:"]


class TestBuildTransformMessages:
    def test_appends_return_only_suffix(self):
        messages = build_transform_messages("Rewrite for clarity.", "some text")
        assert messages == [
            {
                "role": "system",
                "content": "Rewrite for clarity. Return ONLY the transformed text.",
            },
            {"role": "user", "content": "some text"},
        ]


class TestApplyTransform:
    def test_calls_chat_client_and_returns_result(self):
        llm = MockChatClient(["Rewritten."])
        result = apply_transform(llm, "Rewrite for clarity.", "rough text")

        assert result == "Rewritten."
        assert llm.requests[0][0]["content"] == (
            "Rewrite for clarity. Return ONLY the transformed text."
        )
        assert llm.requests[0][1]["content"] == "rough text"

    def test_llm_failure_propagates(self):
        class FailingClient(MockChatClient):
            def chat(self, messages, *, temperature=0.2, max_tokens=None):
                raise LMStudioConnectionError("down")

        with pytest.raises(LMStudioConnectionError):
            apply_transform(FailingClient(), "Rewrite.", "text")


class TestPersonalizationStoreTransforms:
    def test_fresh_store_seeds_builtin_transforms(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        transforms = store.transforms()
        assert transforms == DEFAULT_TRANSFORMS
        assert list(transforms) == ["Polish", "Prompt Engineer"]

    def test_transforms_file_created_on_disk(self, tmp_path):
        PersonalizationStore(tmp_path)
        on_disk = json.loads((tmp_path / "transforms.json").read_text())
        assert on_disk["transforms"] == DEFAULT_TRANSFORMS

    def test_existing_file_is_left_untouched_no_backfill(self, tmp_path):
        (tmp_path / "transforms.json").write_text(json.dumps({"transforms": {"Mine": "just mine"}}))
        store = PersonalizationStore(tmp_path)
        assert store.transforms() == {"Mine": "just mine"}
        assert "Polish" not in store.transforms()  # unlike styles.json: no backfill

    def test_user_can_remove_a_builtin_transform_permanently(self, tmp_path):
        PersonalizationStore(tmp_path)  # seeds the file
        data = json.loads((tmp_path / "transforms.json").read_text())
        del data["transforms"]["Prompt Engineer"]
        (tmp_path / "transforms.json").write_text(json.dumps(data))

        store = PersonalizationStore(tmp_path)  # re-open: must not re-add it
        assert "Prompt Engineer" not in store.transforms()
        assert "Polish" in store.transforms()

    def test_corrupt_file_raises_actionable_error(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "transforms.json").write_text("{not json")
        with pytest.raises(ConfigError) as excinfo:
            store.transforms()
        assert "transforms.json" in str(excinfo.value)


class TestTransformCli:
    def test_list_prints_transform_names(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["transform", "--list"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Polish" in out
        assert "Prompt Engineer" in out

    def test_text_prints_transformed_result(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        import local_flow.app as app_module

        monkeypatch.setattr(
            app_module, "_build_chat_client", lambda config: MockChatClient(["REWRITTEN"])
        )
        code = main(["transform", "Polish", "--text", "rough text"])
        assert code == 0
        assert capsys.readouterr().out.strip() == "REWRITTEN"

    def test_unknown_name_fails_with_hint_listing_names(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["transform", "Nonexistent", "--text", "x"])
        assert code == 1
        err = capsys.readouterr().err
        assert "Unknown transform" in err
        assert "Polish" in err

    def test_missing_name_without_list_fails(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["transform"])
        assert code == 1
        err = capsys.readouterr().err
        assert "needs a name" in err

    def test_neither_text_nor_selection_fails(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["transform", "Polish"])
        assert code == 1
        err = capsys.readouterr().err
        assert "exactly one of --text or --selection" in err

    def test_both_text_and_selection_fails(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["transform", "Polish", "--text", "x", "--selection"])
        assert code == 1
        err = capsys.readouterr().err
        assert "exactly one of --text or --selection" in err

    def test_selection_replaces_in_place_with_monkeypatched_backend(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        import local_flow.app as app_module

        backend = MockSelectionBackend(clipboard="", selection_text="highlighted text")
        monkeypatch.setattr(
            app_module, "_build_chat_client", lambda config: MockChatClient(["POLISHED"])
        )
        monkeypatch.setattr(
            app_module,
            "_build_selection_capture",
            lambda config: SelectionCapture(backend, sleep=lambda s: None),
        )

        code = main(["transform", "Polish", "--selection"])

        assert code == 0
        assert backend.clipboard == ""  # restored to the saved (empty) clipboard
        assert "write:POLISHED" in backend.events
        err = capsys.readouterr().err
        assert "Polish" in err

    def test_selection_with_nothing_selected_fails_helpfully(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        import local_flow.app as app_module

        backend = MockSelectionBackend(clipboard="", selection_text=None)
        monkeypatch.setattr(
            app_module,
            "_build_selection_capture",
            lambda config: SelectionCapture(
                backend, poll_timeout_s=0.01, poll_interval_s=0.005, sleep=lambda s: None
            ),
        )
        # No _build_chat_client monkeypatch: must not be reached before the
        # "nothing selected" check fails.
        code = main(["transform", "Polish", "--selection"])

        assert code == 1
        err = capsys.readouterr().err
        assert "No text is selected" in err
