"""HistoryStore: append-only JSONL dictation history with rotation and retention."""

from datetime import UTC, datetime

import pytest

from local_flow.config import load_config
from local_flow.errors import ConfigError
from local_flow.history import HistoryRecord, HistoryStore


def _record(rough: str, final: str, timestamp: str = "2026-07-06T12:00:00Z") -> HistoryRecord:
    return HistoryRecord(timestamp=timestamp, rough=rough, final=final)


class TestAppendAndRecent:
    def test_roundtrip_newest_first(self, tmp_path):
        store = HistoryStore(tmp_path)
        store.append(_record("one", "One.", timestamp="2026-07-06T12:00:00Z"))
        store.append(_record("two", "Two.", timestamp="2026-07-06T12:01:00Z"))
        recent = store.recent()
        assert [r.final for r in recent] == ["Two.", "One."]

    def test_recent_limit(self, tmp_path):
        store = HistoryStore(tmp_path)
        for i in range(5):
            store.append(_record(f"r{i}", f"F{i}", timestamp=f"2026-07-06T12:0{i}:00Z"))
        assert [r.final for r in store.recent(limit=2)] == ["F4", "F3"]

    def test_path_property(self, tmp_path):
        store = HistoryStore(tmp_path)
        assert store.path == tmp_path / "history.jsonl"

    def test_append_creates_data_dir(self, tmp_path):
        data_dir = tmp_path / "nested" / "data"
        store = HistoryStore(data_dir)
        store.append(_record("hi", "Hi."))
        assert store.path.is_file()


class TestSearch:
    def test_case_insensitive_across_rough_and_final(self, tmp_path):
        store = HistoryStore(tmp_path)
        store.append(_record("hello WORLD", "Hello world.", timestamp="2026-07-06T12:00:00Z"))
        store.append(_record("goodbye", "Goodbye.", timestamp="2026-07-06T12:01:00Z"))
        results = store.search("world")
        assert [r.rough for r in results] == ["hello WORLD"]

    def test_matches_final_field(self, tmp_path):
        store = HistoryStore(tmp_path)
        store.append(_record("x", "JiSpr Flow rocks", timestamp="2026-07-06T12:00:00Z"))
        results = store.search("jispr")
        assert len(results) == 1

    def test_no_match_returns_empty(self, tmp_path):
        store = HistoryStore(tmp_path)
        store.append(_record("x", "y"))
        assert store.search("nonexistent") == []


class TestRotation:
    def test_rotation_keeps_newest_n(self, tmp_path):
        store = HistoryStore(tmp_path, max_entries=3)
        for i in range(5):
            store.append(_record(f"r{i}", f"F{i}", timestamp=f"2026-07-06T12:0{i}:00Z"))
        records = list(store.all())
        assert [r.final for r in records] == ["F2", "F3", "F4"]
        assert len(store.path.read_text().splitlines()) == 3


class TestRetention:
    def test_24h_retention_prunes_old_records(self, tmp_path):
        clock = {"now": datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)}
        store = HistoryStore(tmp_path, retention="24h", now=lambda: clock["now"])
        store.append(_record("old", "Old.", timestamp="2026-07-06T12:00:00Z"))
        clock["now"] = datetime(2026, 7, 7, 13, 0, 0, tzinfo=UTC)  # +25h later
        store.append(_record("new", "New.", timestamp="2026-07-07T13:00:00Z"))
        records = list(store.all())
        assert [r.final for r in records] == ["New."]

    def test_off_retention_writes_nothing(self, tmp_path):
        store = HistoryStore(tmp_path, retention="off")
        store.append(_record("x", "y"))
        assert not store.path.exists()
        assert store.recent() == []

    def test_rotation_and_24h_retention_together(self, tmp_path):
        """Rotation and 24h retention firing in same append call."""
        clock = {"now": datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)}
        store = HistoryStore(tmp_path, max_entries=2, retention="24h", now=lambda: clock["now"])
        # Append two old records (beyond 24h)
        store.append(_record("old1", "Old1.", timestamp="2026-07-05T10:00:00Z"))
        store.append(_record("old2", "Old2.", timestamp="2026-07-05T11:00:00Z"))
        # Advance clock and append three fresh records
        clock["now"] = datetime(2026, 7, 6, 14, 0, 0, tzinfo=UTC)
        store.append(_record("new1", "New1.", timestamp="2026-07-06T14:00:00Z"))
        store.append(_record("new2", "New2.", timestamp="2026-07-06T14:01:00Z"))
        store.append(_record("new3", "New3.", timestamp="2026-07-06T14:02:00Z"))
        # Should have 2 newest records (old pruned by age, new3 trimmed by rotation)
        recent = store.recent()
        assert [r.final for r in recent] == ["New3.", "New2."]
        assert len(list(store.all())) == 2

    def test_garbage_timestamp_silently_dropped_on_prune(self, tmp_path):
        """Line with invalid timestamp JSON is silently dropped during 24h prune."""
        clock = {"now": datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)}
        store = HistoryStore(tmp_path, retention="24h", now=lambda: clock["now"])
        # Write a valid record and a record with garbage timestamp directly to file
        store.append(_record("good", "Good.", timestamp="2026-07-06T12:00:00Z"))
        with store.path.open("a", encoding="utf-8") as f:
            f.write('{"timestamp": "not-a-date", "rough": "bad", "final": "Bad."}\n')
        # Advance clock and append a new record, triggering prune
        clock["now"] = datetime(2026, 7, 7, 13, 0, 0, tzinfo=UTC)  # +25h
        store.append(_record("newer", "Newer.", timestamp="2026-07-07T13:00:00Z"))
        # Should not crash, should drop the garbage timestamp line and keep valid records
        records = list(store.all())
        assert [r.final for r in records] == ["Newer."]


class TestCorruptLines:
    def test_corrupt_line_is_skipped(self, tmp_path):
        store = HistoryStore(tmp_path)
        store.append(_record("good", "Good."))
        with store.path.open("a", encoding="utf-8") as f:
            f.write("not json at all\n")
        records = list(store.all())
        assert [r.final for r in records] == ["Good."]


class TestClear:
    def test_clear_removes_file(self, tmp_path):
        store = HistoryStore(tmp_path)
        store.append(_record("x", "y"))
        assert store.path.exists()
        store.clear()
        assert not store.path.exists()
        assert store.recent() == []


class TestConfigFields:
    def test_history_defaults(self):
        config = load_config(env={})
        assert config.history_enabled is True
        assert config.history_max_entries == 5000
        assert config.history_retention == "forever"

    def test_invalid_retention_raises_config_error(self):
        with pytest.raises(ConfigError, match="history_retention") as excinfo:
            load_config(env={"LOCAL_FLOW_HISTORY_RETENTION": "weekly"})
        message = str(excinfo.value)
        assert "forever" in message
        assert "24h" in message
        assert "off" in message
