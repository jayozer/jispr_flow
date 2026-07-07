"""local-flow stats: local-only personal insights over dictation history."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from local_flow.app import main
from local_flow.history import HistoryRecord, HistoryStore
from local_flow.insights.stats import compute_stats, render_heatmap

# A fixed "now" for every compute_stats/render_heatmap test: 2026-07-06 is a
# Monday (verified), which makes the heatmap/streak math easy to reason
# about by hand.
NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)


def _record(
    timestamp: str,
    rough: str = "hello",
    final: str = "Hello.",
    duration_s: float = 0.0,
    replacements: int = 0,
    failed: bool = False,
    app: str = "",
) -> HistoryRecord:
    return HistoryRecord(
        timestamp=timestamp,
        rough=rough,
        final=final,
        duration_s=duration_s,
        replacements=replacements,
        failed=failed,
        app=app,
    )


class TestComputeStatsBasics:
    def test_empty_records(self):
        stats = compute_stats([], NOW)
        assert stats.total_dictations == 0
        assert stats.total_words == 0
        assert stats.words_per_minute == 0.0
        assert stats.cleaned_words_delta == 0
        assert stats.replacements == 0
        assert stats.failed == 0
        assert stats.top_apps == []
        assert stats.active_days == []
        assert stats.current_streak == 0
        assert stats.longest_streak == 0

    def test_total_dictations_and_words(self):
        records = [
            _record("2026-07-06T10:00:00Z", final="one two three"),
            _record("2026-07-06T11:00:00Z", final="four five"),
        ]
        stats = compute_stats(records, NOW)
        assert stats.total_dictations == 2
        assert stats.total_words == 5

    def test_words_per_minute_with_durations(self):
        records = [
            _record("2026-07-06T10:00:00Z", final="one two three four", duration_s=60.0),
            _record("2026-07-06T10:01:00Z", final="five six", duration_s=60.0),
        ]
        stats = compute_stats(records, NOW)
        # 6 words / (120s / 60) = 6 / 2.0 = 3.0 wpm
        assert stats.words_per_minute == pytest.approx(3.0)

    def test_words_per_minute_zero_without_duration(self):
        records = [_record("2026-07-06T10:00:00Z", final="one two three")]
        stats = compute_stats(records, NOW)
        assert stats.words_per_minute == 0.0


class TestCleanedWordsDelta:
    def test_clamped_at_zero_per_record(self):
        records = [
            # rough has 3 words, final has 2 -> +1
            _record("2026-07-06T10:00:00Z", rough="um one two", final="One two."),
            # final LONGER than rough -> clamped to 0, not negative
            _record("2026-07-06T10:01:00Z", rough="hi", final="Hi there now."),
        ]
        stats = compute_stats(records, NOW)
        assert stats.cleaned_words_delta == 1


class TestReplacementsAndFailed:
    def test_replacements_summed_honestly(self):
        records = [
            _record("2026-07-06T10:00:00Z", replacements=2),
            _record("2026-07-06T10:01:00Z", replacements=3),
        ]
        stats = compute_stats(records, NOW)
        assert stats.replacements == 5

    def test_failed_counted(self):
        records = [
            _record("2026-07-06T10:00:00Z", failed=True),
            _record("2026-07-06T10:01:00Z", failed=False),
            _record("2026-07-06T10:02:00Z", failed=True),
        ]
        stats = compute_stats(records, NOW)
        assert stats.failed == 2


class TestTopApps:
    def test_blank_app_bucketed_as_unknown(self):
        records = [
            _record("2026-07-06T10:00:00Z", app=""),
            _record("2026-07-06T10:01:00Z", app=""),
        ]
        stats = compute_stats(records, NOW)
        assert stats.top_apps == [("(unknown)", 2)]

    def test_top_5_by_count_excludes_the_rest(self):
        counts = [("a", 6), ("b", 5), ("c", 4), ("d", 3), ("e", 2), ("f", 1)]
        records = []
        minute = 0
        for app, n in counts:
            for _ in range(n):
                records.append(_record(f"2026-07-06T10:{minute:02d}:00Z", app=app))
                minute += 1
        stats = compute_stats(records, NOW)
        assert len(stats.top_apps) == 5
        assert all(app != "f" for app, _ in stats.top_apps)
        assert stats.top_apps[0] == ("a", 6)


class TestActiveDaysAndStreaks:
    def test_active_days_unique_sorted(self):
        records = [
            _record("2026-07-04T09:00:00Z"),
            _record("2026-07-04T15:00:00Z"),  # same day, duplicate
            _record("2026-07-06T09:00:00Z"),
        ]
        stats = compute_stats(records, NOW)
        assert stats.active_days == ["2026-07-04", "2026-07-06"]

    def test_timezone_offset_converted_to_utc_date(self):
        # 2026-07-07T02:00:00+05:00 == 2026-07-06T21:00:00Z: local date is
        # the 7th but the UTC date (what active_days must use) is the 6th.
        records = [_record("2026-07-07T02:00:00+05:00")]
        stats = compute_stats(records, NOW)
        assert stats.active_days == ["2026-07-06"]

    def test_unparseable_timestamp_excluded_everywhere(self):
        records = [
            _record("2026-07-06T09:00:00Z", final="one two", replacements=1),
            _record("not-a-timestamp", final="three four five", replacements=9),
        ]
        stats = compute_stats(records, NOW)
        assert stats.total_dictations == 1
        assert stats.total_words == 2
        assert stats.replacements == 1
        assert stats.active_days == ["2026-07-06"]

    def test_current_streak_active_today(self):
        records = [_record("2026-07-06T09:00:00Z")]  # NOW's date
        stats = compute_stats(records, NOW)
        assert stats.current_streak == 1

    def test_current_streak_active_yesterday_only(self):
        records = [_record("2026-07-05T09:00:00Z")]  # yesterday relative to NOW
        stats = compute_stats(records, NOW)
        assert stats.current_streak == 1

    def test_current_streak_broken_by_one_full_day_gap(self):
        # Active 2 days ago, but neither yesterday nor today -> streak is 0.
        records = [_record("2026-07-04T09:00:00Z")]
        stats = compute_stats(records, NOW)
        assert stats.current_streak == 0

    def test_current_streak_counts_consecutive_run_ending_yesterday(self):
        records = [
            _record("2026-07-03T09:00:00Z"),
            _record("2026-07-04T09:00:00Z"),
            _record("2026-07-05T09:00:00Z"),  # yesterday; today itself inactive
        ]
        stats = compute_stats(records, NOW)
        assert stats.current_streak == 3

    def test_longest_streak_across_gaps(self):
        records = [
            _record("2026-06-01T09:00:00Z"),
            _record("2026-06-02T09:00:00Z"),
            _record("2026-06-03T09:00:00Z"),
            _record("2026-06-05T09:00:00Z"),  # gap (06-04 missing)
            _record("2026-06-06T09:00:00Z"),
        ]
        stats = compute_stats(records, NOW)
        assert stats.longest_streak == 3
        assert stats.current_streak == 0  # nothing near NOW


class TestRenderHeatmap:
    def test_all_inactive_is_all_dots(self):
        result = render_heatmap([], NOW, weeks=2)
        expected = "\n".join(f"{label} .." for label in _WEEKDAYS())
        assert result == expected

    def test_active_mondays_light_up_the_monday_row(self):
        # this week's Monday (2026-07-06) is NOW's own date; the other two
        # are 1 and 2 Mondays earlier -- exactly the 3 columns of a 3-week grid.
        active_days = ["2026-07-06", "2026-06-29", "2026-06-22"]
        result = render_heatmap(active_days, NOW, weeks=3)
        lines = result.splitlines()
        assert len(lines) == 7
        assert lines[0] == "Mon ###"
        for label, line in zip(_WEEKDAYS()[1:], lines[1:], strict=True):
            assert line == f"{label} ..."

    def test_deterministic_for_same_inputs(self):
        active_days = ["2026-07-01", "2026-07-06"]
        assert render_heatmap(active_days, NOW) == render_heatmap(active_days, NOW)


def _WEEKDAYS() -> tuple[str, ...]:
    return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _stat_value(out: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}\s*:\s*(.+)", out)
    assert match, f"{label!r} not found in output:\n{out}"
    return match.group(1).strip()


class TestStatsCommand:
    def test_empty_history_prints_friendly_message_with_path(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["stats"])
        assert code == 0
        out = capsys.readouterr().out
        assert "no dictation history yet" in out
        assert str(tmp_path / "history.jsonl") in out

    def test_since_window_filters_records(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        now = datetime.now(UTC)
        old_ts = (now - timedelta(days=40)).isoformat().replace("+00:00", "Z")
        recent_ts = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        store.append(HistoryRecord(timestamp=old_ts, rough="old rough", final="Old final"))
        store.append(HistoryRecord(timestamp=recent_ts, rough="new rough", final="New final"))

        code = main(["stats", "--since", "30d"])
        assert code == 0
        out = capsys.readouterr().out
        assert _stat_value(out, "total dictations") == "1"

        code = main(["stats", "--since", "all"])
        assert code == 0
        out_all = capsys.readouterr().out
        assert _stat_value(out_all, "total dictations") == "2"

    def test_since_defaults_to_30d(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        now = datetime.now(UTC)
        old_ts = (now - timedelta(days=40)).isoformat().replace("+00:00", "Z")
        store.append(HistoryRecord(timestamp=old_ts, rough="x", final="y"))

        code = main(["stats"])
        assert code == 0
        out = capsys.readouterr().out
        assert "no dictations" in out.lower()

    def test_invalid_since_value_fails_helpfully(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        store.append_new(rough="x", final="y")

        code = main(["stats", "--since", "banana"])
        assert code == 1
        err = capsys.readouterr().err
        assert "banana" in err

    def test_disabled_history_shows_notice_but_still_reports(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        store.append_new(rough="x", final="hello world")

        monkeypatch.setenv("LOCAL_FLOW_HISTORY_ENABLED", "false")
        code = main(["stats", "--since", "all"])
        assert code == 0
        out = capsys.readouterr().out
        assert "disabled" in out.lower()
        assert "total dictations" in out

    def test_smart_replacements_label_is_honest(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        store.append_new(rough="x", final="hello world", replacements=3)

        code = main(["stats", "--since", "all"])
        assert code == 0
        out = capsys.readouterr().out
        assert "smart replacements applied" in out
        assert "words corrected" not in out.lower()
        assert _stat_value(out, "smart replacements applied") == "3"

    def test_heatmap_present_with_header(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        store.append_new(rough="x", final="hello world")

        code = main(["stats", "--since", "all"])
        assert code == 0
        out = capsys.readouterr().out
        assert "last 8 weeks" in out.lower()
        assert "Mon " in out
        assert "Sun " in out

    def test_empty_window_after_since_filter_suggests_since_all(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        now = datetime.now(UTC)
        old_ts = (now - timedelta(days=100)).isoformat().replace("+00:00", "Z")
        store.append(HistoryRecord(timestamp=old_ts, rough="x", final="y"))

        code = main(["stats", "--since", "7d"])
        assert code == 0
        out = capsys.readouterr().out
        assert "no dictations" in out.lower()
        assert "--since all" in out

    def test_unparseable_timestamp_is_noted(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        store.append_new(rough="x", final="hello world")
        with store.path.open("a", encoding="utf-8") as f:
            f.write('{"timestamp": "not-a-date", "rough": "bad", "final": "Bad."}\n')

        code = main(["stats", "--since", "all"])
        assert code == 0
        out = capsys.readouterr().out
        assert "unparseable" in out.lower()
        assert _stat_value(out, "total dictations") == "1"
