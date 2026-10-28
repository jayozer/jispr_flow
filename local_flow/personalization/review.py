"""Bounded, in-memory dictation review; independent of saved history."""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from local_flow.errors import ConfigError
from local_flow.personalization.corrections import MAX_REVIEW_CHARS

if TYPE_CHECKING:
    from local_flow.pipeline import DictationResult

MAX_RECENT_REVIEWS = 20


@dataclass(frozen=True)
class DictationReview:
    id: str
    text: str
    app_id: str
    app_name: str
    corrections_applied: int


class DictationReviews:
    def __init__(self) -> None:
        self._records: OrderedDict[str, DictationReview] = OrderedDict()
        self._latest_id: str | None = None
        self._lock = threading.Lock()

    def add(self, result: DictationResult) -> DictationReview | None:
        if not result.inserted or not result.final.strip() or len(result.final) > MAX_REVIEW_CHARS:
            with self._lock:
                self._latest_id = None
            return None
        record = DictationReview(
            id=str(uuid4()),
            text=result.final,
            app_id=result.app_id,
            app_name=result.app_name,
            corrections_applied=result.corrections_applied,
        )
        with self._lock:
            self._records[record.id] = record
            self._latest_id = record.id
            while len(self._records) > MAX_RECENT_REVIEWS:
                self._records.popitem(last=False)
        return record

    def latest(self) -> DictationReview | None:
        with self._lock:
            return self._records.get(self._latest_id) if self._latest_id else None

    def get(self, record_id: str) -> DictationReview:
        with self._lock:
            record = self._records.get(record_id)
        if record is None:
            raise ConfigError(
                "This dictation is no longer available for learning.",
                hint="Load the latest dictation and review your correction again.",
            )
        return record
