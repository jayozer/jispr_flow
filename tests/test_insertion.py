"""Text sinks: fake sink recording and fallback behaviour."""

import pytest

from local_flow.errors import PasteError
from local_flow.insertion.base import FakeTextSink, InsertionManager, TextSink


class BoomSink(TextSink):
    """Always fails, like a paste keystroke on Wayland."""

    name = "boom"

    def insert(self, text: str) -> None:
        raise PasteError("Sending the paste keystroke failed: synthetic input blocked.")

    def press_key(self, key: str) -> None:
        raise PasteError("Key press blocked.")


class TestFakeTextSink:
    def test_records_inserts_and_keys(self):
        sink = FakeTextSink()
        sink.insert("hello")
        sink.press_key("enter")
        assert sink.events == [("insert", "hello"), ("key", "enter")]
        assert sink.text == "hello\n"


class TestInsertionFallback:
    def test_falls_back_to_next_sink_when_paste_fails(self):
        fallback = FakeTextSink()
        manager = InsertionManager([BoomSink(), fallback])
        manager.insert("the text")
        assert fallback.events == [("insert", "the text")]
        assert manager.last_used == "fake"

    def test_first_sink_used_when_it_works(self):
        primary, fallback = FakeTextSink(), FakeTextSink()
        manager = InsertionManager([primary, fallback])
        manager.insert("hi")
        assert primary.events == [("insert", "hi")]
        assert fallback.events == []

    def test_key_actions_also_fall_back(self):
        fallback = FakeTextSink()
        manager = InsertionManager([BoomSink(), fallback])
        manager.press_key("enter")
        assert fallback.events == [("key", "enter")]

    def test_all_sinks_failing_raises_paste_error_listing_each(self):
        manager = InsertionManager([BoomSink(), FakeTextSink(fail=True)])
        with pytest.raises(PasteError) as excinfo:
            manager.insert("doomed")
        message = str(excinfo.value)
        assert "boom:" in message
        assert "fake:" in message
        assert "Accessibility" in message  # actionable hint present

    def test_empty_sink_list_rejected(self):
        with pytest.raises(ValueError):
            InsertionManager([])
