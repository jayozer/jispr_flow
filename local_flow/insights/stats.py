"""Personal insights: local-only aggregate stats over dictation history.

Everything here is pure and injectable -- ``now`` is always passed in, never
read from the wall clock, and calendar bucketing uses the injected ``tz``
(default UTC) -- so :func:`compute_stats`/:func:`render_heatmap` are
deterministic under test. ``local_flow.app._cmd_stats`` is the only caller
that ever supplies a real ``datetime.now(UTC)``, and likewise the only one
that passes ``tz=None`` (bucket by the machine's local zone).

Timestamp tolerance and the "excluded everywhere" rule: this module
re-implements the same tolerant ISO-8601 parse idiom as
``local_flow.history.store._parse_timestamp`` / ``local_flow.app.
_display_timestamp`` (trailing ``Z`` -> ``+00:00``; a naive value is treated
as already UTC) rather than importing either -- consistent with how those
two already independently duplicate the idiom instead of sharing it. A
record whose ``timestamp`` fails to parse is excluded from *every* field of
:class:`Stats` computed by :func:`compute_stats`, not just the ones that
happen to need a date -- including ``total_dictations``. A record with an
unknown timestamp is data that cannot be placed in time in ANY way, and
counting it in some aggregates but not others (e.g. total words, but not the
heatmap) would be more confusing than a single all-or-nothing rule. This is
mostly a defense-in-depth safety net here: ``local_flow.app._cmd_stats``
already filters unparseable records out (and reports how many) before ever
calling :func:`compute_stats`, using its own copy of the same idiom for the
``--since`` window cutoff -- but it matters for any caller (directly, or in
tests) that hands :func:`compute_stats` unfiltered records.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

from local_flow.history.store import HistoryRecord

_WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _parse_timestamp(value: str) -> datetime | None:
    """Parse an ISO 8601 UTC timestamp, tolerating a trailing ``Z``.

    Mirrors ``local_flow.history.store._parse_timestamp`` byte-for-byte;
    kept as a separate copy rather than imported (see module docstring).
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _ensure_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC, treating a naive value as already UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class Stats:
    """Aggregate personal-insight numbers over one set of history records.

    Every field is computed from the SAME record set the caller passes to
    :func:`compute_stats` -- there is no separate "all time" vs. "windowed"
    split inside this dataclass. In particular both ``current_streak`` and
    ``longest_streak`` are measured *within whatever records were given*, not
    necessarily absolute all-time records: pass every record ever stored (the
    CLI's ``--since all``) to get true all-time streaks; a narrower window
    naturally caps what both can see, exactly like every other field here.

    The ``failed`` field (count of records where LLM polish was skipped) is
    informational only: failed records' text still counts in
    ``total_dictations``, ``total_words``, and ``words_per_minute``, since
    their rule-cleaned text was inserted into the document.
    """

    total_dictations: int
    total_words: int  # whitespace-split token count of `final`, summed
    words_per_minute: float  # total_words / (sum(duration_s)/60); 0.0 if no duration
    cleaned_words_delta: int  # sum(max(0, len(rough words) - len(final words)))
    # Honesty note (carried from the Phase-2 review): this counts
    # substitutions PERFORMED, including ones where the replacement text was
    # already identical to what was there -- it is NOT a count of words that
    # were actually wrong and got corrected. Any label built from this field
    # must say "applied" (or similar), never "corrected".
    replacements: int
    failed: int
    top_apps: list[tuple[str, int]]  # top 5 by dictation count; "" -> "(unknown)"
    active_days: list[str]  # sorted, unique ISO dates (in `tz`) with >=1 dictation
    current_streak: int
    longest_streak: int


def compute_stats(
    records: Iterable[HistoryRecord], now: datetime, tz: tzinfo | None = UTC
) -> Stats:
    """Aggregate ``records`` into a :class:`Stats` snapshot as of ``now``.

    Records whose ``timestamp`` does not parse are excluded from every
    field (see module docstring). ``now`` should be UTC (as
    ``datetime.now(UTC)``); a naive ``now`` is treated as already-UTC,
    matching the tolerance this module applies to record timestamps.

    ``tz`` is the zone whose calendar defines "a day" for ``active_days``
    and both streaks. It defaults to UTC so this function stays fully
    deterministic for direct callers and tests; pass ``None`` -- as
    ``local_flow.app._cmd_stats`` does -- to bucket by the machine's local
    zone (resolved per-instant via ``astimezone(None)``, so an evening
    dictation west of UTC counts toward the user's local day and DST
    transitions inside the record range still place each timestamp
    correctly).

    Failed records (where LLM polish was skipped) still count toward
    ``total_dictations``, ``total_words``, and ``words_per_minute``, since
    their rule-cleaned text was inserted; the ``failed`` field is
    informational only.

    Streak semantics (documented once here, since both fields share it):

    - ``current_streak``: the length of the run of consecutive active days
      ending at ``now``'s date in ``tz`` OR at that date minus one day -- i.e. a
      streak survives one full inactive "today" (you haven't dictated yet
      today, but did yesterday), and is broken only once a FULL calendar day
      passes with no activity at all (today AND yesterday both inactive).
    - ``longest_streak``: the longest run of consecutive active days
      anywhere in the resulting ``active_days`` (see the
      ``Stats.longest_streak`` docstring for the "over the given records,
      not literally all of history" caveat).
    """
    usable: list[tuple[HistoryRecord, datetime]] = []
    for record in records:
        parsed = _parse_timestamp(record.timestamp)
        if parsed is not None:
            usable.append((record, _ensure_utc(parsed)))

    total_dictations = len(usable)
    total_words = sum(len(record.final.split()) for record, _ in usable)
    total_duration_s = sum(record.duration_s for record, _ in usable)
    words_per_minute = (
        total_words / (total_duration_s / 60) if total_duration_s > 0 else 0.0
    )
    cleaned_words_delta = sum(
        max(0, len(record.rough.split()) - len(record.final.split()))
        for record, _ in usable
    )
    replacements = sum(record.replacements for record, _ in usable)
    failed = sum(1 for record, _ in usable if record.failed)

    app_counts: Counter[str] = Counter()
    for record, _ in usable:
        app_counts[record.app or "(unknown)"] += 1
    top_apps = app_counts.most_common(5)

    active_day_set = {ts.astimezone(tz).date().isoformat() for _, ts in usable}
    active_days = sorted(active_day_set)

    today = _ensure_utc(now).astimezone(tz).date()
    current_streak = _current_streak(active_day_set, today)
    longest_streak = _longest_streak(active_day_set)

    return Stats(
        total_dictations=total_dictations,
        total_words=total_words,
        words_per_minute=words_per_minute,
        cleaned_words_delta=cleaned_words_delta,
        replacements=replacements,
        failed=failed,
        top_apps=top_apps,
        active_days=active_days,
        current_streak=current_streak,
        longest_streak=longest_streak,
    )


def _current_streak(active_day_set: set[str], today: date) -> int:
    """See ``compute_stats``'s docstring for the "today or yesterday" rule."""
    if today.isoformat() in active_day_set:
        anchor = today
    elif (today - timedelta(days=1)).isoformat() in active_day_set:
        anchor = today - timedelta(days=1)
    else:
        return 0
    streak = 0
    day = anchor
    while day.isoformat() in active_day_set:
        streak += 1
        day -= timedelta(days=1)
    return streak


def _longest_streak(active_day_set: set[str]) -> int:
    if not active_day_set:
        return 0
    days = sorted(date.fromisoformat(d) for d in active_day_set)
    best = 1
    current = 1
    for prev, curr in zip(days, days[1:], strict=False):
        if (curr - prev).days == 1:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


def render_heatmap(
    active_days: list[str], now: datetime, weeks: int = 8, tz: tzinfo | None = UTC
) -> str:
    """Render an ASCII activity heatmap: one row per weekday (Mon..Sun), one
    column per week, oldest week first and the current week last.

    ``#`` marks a day present in ``active_days``, ``.`` marks any other day.
    The grid's last column is always the Monday-Sunday week containing
    ``now``'s date in ``tz`` (same semantics and default as
    :func:`compute_stats`'s ``tz`` -- pass the SAME zone used to compute
    ``active_days``, since the grid matches their ISO strings verbatim), so
    a handful of trailing cells in that column may represent dates still in
    the future relative to ``now`` (e.g. if ``now`` falls on a Tuesday,
    Wed-Sun of that same week haven't happened yet) -- those simply render
    as ``.`` like any other inactive day; no special-casing is needed since
    a future date can never appear in ``active_days``. Deterministic given
    ``(active_days, now, weeks, tz)`` for any non-``None`` ``tz``:
    no wall-clock reads.
    """
    active = set(active_days)
    today = _ensure_utc(now).astimezone(tz).date()
    this_monday = today - timedelta(days=today.weekday())
    grid_start = this_monday - timedelta(weeks=weeks - 1)

    lines = []
    for row, label in enumerate(_WEEKDAY_LABELS):
        cells = "".join(
            "#"
            if (grid_start + timedelta(weeks=col, days=row)).isoformat() in active
            else "."
            for col in range(weeks)
        )
        lines.append(f"{label} {cells}")
    return "\n".join(lines)
