"""Explicitly approved local word corrections, separate from snippets.

Suggestions never persist themselves. A caller approves each suggestion before
calling ``learn``. Missing ``corrections.json`` means no rules and creates no
files. Every read validates the versioned document before a write can replace it.
"""

from __future__ import annotations

import json
import unicodedata
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

from local_flow.atomicio import atomic_write_text
from local_flow.errors import ConfigError

_MAX_PHRASE_CHARS = 120
_MAX_PHRASE_WORDS = 8
_MAX_RULES = 500
_MAX_SUGGESTIONS = 8
# The host's 32k-character cap alone does not bound SequenceMatcher's
# quadratic repeated-token work. Check both limits before constructing it.
MAX_REVIEW_CHARS = 32_000
MAX_REVIEW_WORDS = 1_024
_RULE_FIELDS = {"id", "source", "replacement", "app_id", "created_at"}
_REPAIR_HINT = (
    "Repair corrections.json or move it aside after making a backup. "
    "JiSpr leaves invalid correction files unchanged."
)


@dataclass(frozen=True)
class CorrectionSuggestion:
    source: str
    replacement: str


@dataclass(frozen=True)
class CorrectionRule:
    id: str
    source: str
    replacement: str
    app_id: str
    created_at: str


@dataclass(frozen=True)
class CorrectionChange:
    token: str
    before: CorrectionRule | None
    after: CorrectionRule | None


def _has_controls(value: str) -> bool:
    return any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029"
               for char in value)


def _phrase(value: object, name: str) -> str:
    if not isinstance(value, str) or _has_controls(value):
        raise ConfigError(f"Correction {name} must be text without controls or line breaks.")
    value = value.strip()
    if not value or len(value) > _MAX_PHRASE_CHARS or len(value.split()) > _MAX_PHRASE_WORDS:
        raise ConfigError(
            f"Correction {name} must contain 1–{_MAX_PHRASE_CHARS} characters "
            f"and at most {_MAX_PHRASE_WORDS} words.",
            hint="Teach a word or short phrase instead of a paragraph.",
        )
    if not any(char.isalpha() for char in value):
        raise ConfigError(
            f"Correction {name} must include letters; numbers alone cannot be learned."
        )
    # Two sentence endings are a paragraph rather than a reusable phrase.
    endings = sum(
        char in ".!?" and (index + 1 == len(value) or value[index + 1].isspace())
        for index, char in enumerate(value)
    )
    if endings >= 2:
        raise ConfigError(f"Correction {name} must be a word or phrase, not multiple sentences.")
    return value


def _app_id(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256 or _has_controls(value):
        raise ConfigError("Correction app_id must be an app identifier of at most 256 characters.")
    return value.strip()


def _key(rule: CorrectionRule) -> tuple[str, str]:
    return rule.source.casefold(), rule.app_id


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _validate_rule(data: object) -> CorrectionRule:
    if not isinstance(data, dict) or set(data) != _RULE_FIELDS:
        raise ValueError("each rule must contain id, source, replacement, app_id, and created_at")
    rule = CorrectionRule(**data)
    if (not isinstance(rule.id, str) or not rule.id.strip() or len(rule.id) > 128
            or _has_controls(rule.id)):
        raise ValueError("rule id must be non-empty text of at most 128 characters")
    if (_phrase(rule.source, "source") != rule.source
            or _phrase(rule.replacement, "replacement") != rule.replacement
            or _app_id(rule.app_id) != rule.app_id):
        raise ValueError("rule text must not have leading or trailing whitespace")
    if rule.source == rule.replacement:
        raise ValueError("rule source and replacement must differ")
    if not isinstance(rule.created_at, str):
        raise ValueError("created_at must be a timezone-aware ISO timestamp")
    if datetime.fromisoformat(rule.created_at).tzinfo is None:
        raise ValueError("created_at must include a timezone")
    return rule


class CorrectionStore:
    """Small, hand-editable rule store; reads always see the latest atomic save."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self._path = self.data_dir / "corrections.json"

    @property
    def path(self) -> Path:
        return self._path

    def list_rules(self) -> list[CorrectionRule]:
        try:
            data = json.loads(
                self.path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
            )
        except FileNotFoundError:
            return []
        except (OSError, ValueError, UnicodeError) as exc:
            raise ConfigError(f"Could not read {self.path}: {exc}", hint=_REPAIR_HINT) from exc
        try:
            if (not isinstance(data, dict) or set(data) != {"version", "rules"}
                    or type(data["version"]) is not int or data["version"] != 1):
                raise ValueError("expected format {\"version\": 1, \"rules\": [...]}")
            if not isinstance(data["rules"], list) or len(data["rules"]) > _MAX_RULES:
                raise ValueError(f"rules must be a list with at most {_MAX_RULES} entries")
            rules = [_validate_rule(row) for row in data["rules"]]
            if len({rule.id for rule in rules}) != len(rules):
                raise ValueError("rule IDs must be unique")
            if len({_key(rule) for rule in rules}) != len(rules):
                raise ValueError("sources must be unique within each app scope")
            return rules
        except (TypeError, ValueError, ConfigError) as exc:
            raise ConfigError(f"Invalid {self.path}: {exc}", hint=_REPAIR_HINT) from exc

    def _write(self, rules: list[CorrectionRule]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "rules": [asdict(rule) for rule in rules]}
        atomic_write_text(self.path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def learn(self, source: str, replacement: str, app_id: str = "") -> CorrectionChange | None:
        source = _phrase(source, "source")
        replacement = _phrase(replacement, "replacement")
        app_id = _app_id(app_id)
        rules = self.list_rules()
        if source == replacement:
            return None
        before = next((rule for rule in rules if _key(rule) == (source.casefold(), app_id)), None)
        if before is not None and before.replacement == replacement:
            return None
        if before is None and len(rules) >= _MAX_RULES:
            raise ConfigError(
                f"At most {_MAX_RULES} corrections can be stored.",
                hint="Forget an unused correction before teaching another.",
            )
        after = CorrectionRule(
            id=before.id if before else str(uuid4()),
            source=source,
            replacement=replacement,
            app_id=app_id,
            # Refresh on update as a revision marker for compare-and-swap undo.
            created_at=datetime.now(UTC).isoformat(),
        )
        self._write([after if rule == before else rule for rule in rules]
                    if before else [*rules, after])
        return CorrectionChange(token=str(uuid4()), before=before, after=after)

    def forget(self, rule_id: str) -> CorrectionChange | None:
        rules = self.list_rules()
        before = next((rule for rule in rules if rule.id == rule_id), None)
        if before is None:
            return None
        self._write([rule for rule in rules if rule != before])
        return CorrectionChange(token=str(uuid4()), before=before, after=None)

    def undo(self, change: CorrectionChange) -> bool:
        """Restore a caller-approved change only if its stored result is unchanged.

        The host owns the in-memory token registry and consumes each token once.
        This method intentionally works with a fresh store instance.
        """
        rules = self.list_rules()
        identity = change.after or change.before
        if identity is None or (change.before and change.after
                                and change.before.id != change.after.id):
            return False
        current = next((rule for rule in rules if rule.id == identity.id), None)
        if current != change.after:
            return False
        if change.before is not None:
            if any(rule.id != identity.id and _key(rule) == _key(change.before) for rule in rules):
                return False
            if current is None and len(rules) >= _MAX_RULES:
                return False
            _validate_rule(asdict(change.before))
        restored = [rule for rule in rules if rule.id != identity.id]
        if change.before is not None:
            restored.append(change.before)
        self._write(restored)
        return True

    def apply(self, text: str, app_id: str = "") -> tuple[str, int]:
        app_id = _app_id(app_id)
        rules = self.list_rules()
        local_sources = {rule.source.casefold() for rule in rules if rule.app_id == app_id}
        applicable = [rule for rule in rules
                      if rule.app_id == app_id
                      or (not rule.app_id and rule.source.casefold() not in local_sources)]
        return _apply_pairs(text, [(rule.source, rule.replacement) for rule in applicable])


def _apply_pairs(text: str, pairs: list[tuple[str, str]]) -> tuple[str, int]:
    if not text or not pairs:
        return text, 0
    tokens = _word_spans(text)
    token_starts = [start for start, _end in tokens]

    def token_boundary(position: int) -> bool:
        index = bisect_right(token_starts, position) - 1
        return (index < 0 or position == token_starts[index]
                or position >= tokens[index][1])

    folded_parts = [char.casefold() for char in text]
    folded = "".join(folded_parts)
    offsets = [index for index, part in enumerate(folded_parts) for _ in part]
    matches: list[tuple[int, int, str]] = []
    for source, replacement in pairs:
        source = source.casefold()
        search_at = 0
        while (start := folded.find(source, search_at)) != -1:
            end = start + len(source)
            original_start, original_end = offsets[start], offsets[end - 1] + 1
            if (text[original_start:original_end].casefold() == source
                    and token_boundary(original_start) and token_boundary(original_end)):
                matches.append((original_start, original_end, replacement))
            search_at = start + 1
    # At each position prefer the longest phrase. All matches refer to the
    # original text, so replacement output can never trigger another rule.
    matches.sort(key=lambda match: (match[0], -(match[1] - match[0])))
    pieces: list[str] = []
    cursor = count = 0
    for start, end, replacement in matches:
        if start < cursor:
            continue
        pieces.extend((text[cursor:start], replacement))
        count += text[start:end] != replacement
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), count


def _word_char(char: str) -> bool:
    return char == "_" or char.isalnum() or unicodedata.category(char).startswith("M")


def _word_spans(text: str) -> list[tuple[int, int]]:
    """Keep complete code, path, and email identifiers as indivisible tokens."""
    spans: list[tuple[int, int]] = []
    start: int | None = None

    def finish(end: int) -> None:
        if start is None:
            return
        first = start
        while first < end and text[first] in "'’":
            first += 1
        # Ordinary sentence punctuation is not part of a spelling correction.
        # Semantic identifier suffixes such as C++ and F# must remain intact.
        while end > first and text[end - 1] in "'’.:":
            end -= 1
        if first < end:
            spans.append((first, end))

    for index, char in enumerate(text):
        if _word_char(char) or char in "'’./\\+-@:#":
            if start is None:
                start = index
        elif start is not None:
            finish(index)
            start = None
    if start is not None:
        finish(len(text))
    return spans


def suggest_corrections(original: str, corrected: str) -> list[CorrectionSuggestion]:
    """Suggest short replacement spans only; never learn additions or deletions."""
    length_error = (
        "This dictation is too long to learn from in one review; dictate a shorter passage."
    )
    if max(len(original), len(corrected)) > MAX_REVIEW_CHARS:
        raise ConfigError(length_error)
    before = _word_spans(original)
    after = _word_spans(corrected)
    if max(len(before), len(after)) > MAX_REVIEW_WORDS:
        raise ConfigError(length_error)
    before_words = [original[start:end] for start, end in before]
    after_words = [corrected[start:end] for start, end in after]
    suggestions: list[CorrectionSuggestion] = []
    seen: set[tuple[str, str]] = set()
    replacements: dict[str, set[str]] = {}
    opcodes = SequenceMatcher(
        None, before_words, after_words, autojunk=False
    ).get_opcodes()
    for tag, i, j, k, end in opcodes:
        if tag != "replace":
            continue
        source = original[before[i][0]:before[j - 1][1]]
        replacement = corrected[after[k][0]:after[end - 1][1]]
        try:
            source = _phrase(source, "source")
            replacement = _phrase(replacement, "replacement")
        except ConfigError:
            continue
        key = (source.casefold(), replacement)
        if source != replacement and key not in seen:
            seen.add(key)
            suggestions.append(CorrectionSuggestion(source, replacement))
            replacements.setdefault(source.casefold(), set()).add(replacement)
    # A source corrected differently in two places is context-dependent and
    # cannot safely become one reusable exact replacement rule.
    suggestions = [suggestion for suggestion in suggestions
                   if len(replacements[suggestion.source.casefold()]) == 1][:_MAX_SUGGESTIONS]
    if len(suggestions) == 1 and sum(opcode[0] != "equal" for opcode in opcodes) == 1:
        suggestion = suggestions[0]
        if _apply_pairs(original, [(suggestion.source, suggestion.replacement)])[0] != corrected:
            return []
    return suggestions
