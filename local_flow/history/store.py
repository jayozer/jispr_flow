"""Append-only JSONL dictation history with rotation and retention control.

Every completed dictation is appended as one JSON object per line to
``data_dir/history.jsonl``. The file is hand-editable and tolerant of
corruption: unparseable lines (or lines missing required fields) are skipped
on read rather than raising, matching the spirit of the JSON personalization
stores.

All time handling is injectable via the ``now`` constructor argument so
retention pruning is deterministic under test; production code should never
need to pass it explicitly (it defaults to real UTC "now").
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from local_flow.atomicio import atomic_write_text


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_timestamp(value: str) -> datetime | None:
    """Parse an ISO 8601 UTC timestamp, tolerating a trailing ``Z``."""
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


@dataclass
class HistoryRecord:
    timestamp: str  # ISO 8601, UTC
    rough: str
    final: str
    used_llm: bool = False
    app: str = ""  # filled by E4 later
    duration_s: float = 0.0
    replacements: int = 0
    # LM Studio was configured for this utterance but never actually used
    # (raised/unreachable, cleanup_level != "none"); see
    # `local_flow.pipeline.DictationPipeline.process_transcript` and
    # `local-flow history --retry`.
    failed: bool = False


class HistoryStore:
    def __init__(
        self,
        data_dir: Path,
        max_entries: int = 5000,
        retention: str = "forever",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.max_entries = max_entries
        self.retention = retention
        self._now = now or _utc_now

    @property
    def path(self) -> Path:
        return self.data_dir / "history.jsonl"

    def append_new(self, **fields: object) -> None:
        """Build a :class:`HistoryRecord` timestamped with ``now()`` and append it.

        ``fields`` are the ``HistoryRecord`` fields other than ``timestamp``
        (``rough``, ``final``, ``used_llm``, ``app``, ``duration_s``,
        ``replacements``). Keeps timestamp generation behind the injectable
        ``now`` seam so callers never need to touch wall-clock time directly.
        """
        timestamp = self._now().astimezone(UTC).isoformat().replace("+00:00", "Z")
        self.append(HistoryRecord(timestamp=timestamp, **fields))  # type: ignore[arg-type]

    def append(self, record: HistoryRecord) -> None:
        if self.retention == "off":
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        if self.retention == "24h":
            self._prune_older_than_24h()
        self._rotate_if_needed()

    def recent(self, limit: int = 20) -> list[HistoryRecord]:
        records = list(self._read_all())
        records.reverse()
        return records[:limit]

    def search(self, query: str, limit: int = 20) -> list[HistoryRecord]:
        needle = query.lower()
        matches = [
            record
            for record in self._read_all()
            if needle in record.rough.lower() or needle in record.final.lower()
        ]
        matches.reverse()
        return matches[:limit]

    def all(self) -> Iterator[HistoryRecord]:
        return self._read_all()

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

    # --- internals ---------------------------------------------------

    def _read_all(self) -> Iterator[HistoryRecord]:
        if not self.path.is_file():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            try:
                yield HistoryRecord(
                    timestamp=str(data["timestamp"]),
                    rough=str(data["rough"]),
                    final=str(data["final"]),
                    used_llm=bool(data.get("used_llm", False)),
                    app=str(data.get("app", "")),
                    duration_s=float(data.get("duration_s", 0.0)),
                    replacements=int(data.get("replacements", 0)),
                    failed=bool(data.get("failed", False)),
                )
            except (KeyError, TypeError, ValueError):
                continue

    def _rewrite(self, records: list[HistoryRecord]) -> None:
        if not records:
            if self.path.exists():
                self.path.unlink()
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        lines = (json.dumps(asdict(record), ensure_ascii=False) for record in records)
        # Atomic (tmp file + rename): this rewrite runs on every append under
        # retention="24h" (and past the max_entries cap otherwise), so a
        # crash mid-write must never destroy the whole history file. The
        # file itself stays plain append-only JSONL; only this full-file
        # rewrite goes through the tmp-file indirection.
        atomic_write_text(self.path, "\n".join(lines) + "\n")

    def _prune_older_than_24h(self) -> None:
        cutoff = self._now() - timedelta(hours=24)
        kept = [
            record
            for record in self._read_all()
            if (parsed := _parse_timestamp(record.timestamp)) is not None and parsed >= cutoff
        ]
        self._rewrite(kept)

    def _rotate_if_needed(self) -> None:
        records = list(self._read_all())
        if len(records) > self.max_entries:
            self._rewrite(records[-self.max_entries :])
