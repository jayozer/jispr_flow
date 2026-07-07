"""JSON-file-backed store for dictionary terms, snippets, and style rules.

Everything lives under one data directory (``LOCAL_FLOW_DATA_DIR``) as three
hand-editable files that are created with commented defaults on first use:

- ``dictionary.json``: ``{"terms": ["JiSpr Flow", ...]}`` — canonical spellings
  enforced on output and fed to the polish prompt. Entries may be a plain
  string (legacy) or a rich object ``{"term": ..., "starred": bool, "uses":
  int, ...}``; unknown extra keys on rich entries are preserved verbatim.
- ``snippets.json``: ``{"snippets": {"sig block": "Best regards,\\nJay"}}`` —
  spoken trigger phrases expanded into stored text.
- ``styles.json``: ``{"active": "default", "styles": {name: rules_text}}`` —
  writing-style instructions injected into the polish prompt.
- ``transforms.json``: ``{"transforms": {name: prompt_text}}`` — named AI
  rewrites for ``local-flow transform`` (see
  ``local_flow.transforms.registry``); seeded with built-in **Polish** and
  **Prompt Engineer** transforms on first use only (unlike ``styles.json``,
  a pre-existing file is never backfilled with built-ins added later).
- ``app_styles.json``: ``{app_id_or_substring: "style" | {"style": ..., "insert": ...}}``
  — optional per-app overrides; unlike the other files this one is *never*
  created automatically (a missing file just means "no rules yet").
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from local_flow.errors import ConfigError

# Strips a trailing possessive so "Iva's"/"Iva's" fold to the same key as
# "Iva" for duplicate detection (curly and straight apostrophes both count).
_APOSTROPHE_S_SUFFIX = re.compile(r"['’]s$", re.IGNORECASE)


def fold_term(term: str) -> str:
    """Case-fold a term and drop a trailing possessive for dedup comparisons."""
    return _APOSTROPHE_S_SUFFIX.sub("", term.strip().lower())


_fold_term = fold_term  # backward-compat alias for existing importers


def _coerce_uses(value: object) -> int:
    """Tolerantly coerce a dictionary entry's ``uses`` field to ``int``.

    Hand-edited entries can carry ``null`` or other garbage; treat anything
    that doesn't cleanly convert as ``0`` instead of raising.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


DEFAULT_STYLES: dict[str, str] = {
    "default": (
        "Neutral: fix punctuation and casing, keep the speaker's wording and tone."
    ),
    "professional": (
        "Professional: complete sentences, no slang, concise business tone."
    ),
    "casual": (
        "Casual: relaxed tone, contractions are fine, keep it light and short."
    ),
    "email": (
        "structure as an email: greeting, short paragraphs, sign-off; formal tone"
    ),
    "chat": ("casual tone, concise, no greeting or sign-off"),
}


DEFAULT_TRANSFORMS: dict[str, str] = {
    "Polish": (
        "Rewrite the text for clarity and concision. Preserve meaning and tone. "
        "Return only the rewritten text."
    ),
    "Prompt Engineer": (
        "Restructure the text into a well-formed AI prompt: state the goal, "
        "context, constraints, and desired output format. Return only the prompt."
    ),
}


@dataclass(frozen=True)
class AppRule:
    """Per-app override: which style to polish with and how to insert text."""

    style: str = ""
    insert: str = ""


def match_app_rule(rules: dict[str, AppRule], app_id: str, title: str) -> AppRule | None:
    """Resolve the rule that applies to a frontmost app.

    Case-insensitive. An exact match on ``app_id`` always wins; otherwise the
    longest rule key that appears as a substring of ``app_id`` or ``title``
    wins (so a more specific key like "slackmacgap" beats "slack").
    """
    if not rules:
        return None
    app_id_l = (app_id or "").lower()
    title_l = (title or "").lower()
    normalized = {key.lower(): rule for key, rule in rules.items()}
    if app_id_l and app_id_l in normalized:
        return normalized[app_id_l]
    candidates = [
        key for key in normalized if key and (key in app_id_l or key in title_l)
    ]
    if not candidates:
        return None
    candidates.sort(key=len, reverse=True)
    return normalized[candidates[0]]


class PersonalizationStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._dictionary_path = self.data_dir / "dictionary.json"
        self._snippets_path = self.data_dir / "snippets.json"
        self._styles_path = self.data_dir / "styles.json"
        self._transforms_path = self.data_dir / "transforms.json"
        self._app_styles_path = self.data_dir / "app_styles.json"
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        if not self._dictionary_path.exists():
            self._write(self._dictionary_path, {"terms": []})
        if not self._snippets_path.exists():
            self._write(self._snippets_path, {"snippets": {}})
        if not self._styles_path.exists():
            self._write(self._styles_path, {"active": "default", "styles": DEFAULT_STYLES})
        else:
            self._merge_default_styles()
        if not self._transforms_path.exists():
            self._write(self._transforms_path, {"transforms": DEFAULT_TRANSFORMS})

    def _merge_default_styles(self) -> None:
        """Backfill built-in style names missing from an existing ``styles.json``.

        README promises ``email``/``chat`` (and the other built-ins) ship out
        of the box, but a pre-existing hand-edited file only ever got the
        defaults at first-creation time. This adds any ``DEFAULT_STYLES`` key
        that isn't already present, without ever touching an existing entry
        (including a user-customized ``email``/``chat``) or the ``active``
        selection, and only writes back when something was actually added.
        """
        try:
            data = self._read(self._styles_path)
        except ConfigError:
            return  # corrupt file: leave it for the caller to surface later
        styles = data.get("styles")
        if not isinstance(styles, dict):
            return
        added = False
        for name, rules in DEFAULT_STYLES.items():
            if name not in styles:
                styles[name] = rules
                added = True
        if added:
            data["styles"] = styles
            self._write(self._styles_path, data)

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    @staticmethod
    def _atomic_write(path: Path, data: dict) -> None:
        """Write ``data`` via a same-directory tmp file + ``os.replace``.

        The tmp file gets a unique name (via ``tempfile.NamedTemporaryFile``)
        so concurrent writers never collide on a shared ``<name>.tmp`` path.
        """
        payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        try:
            tmp.write(payload)
        finally:
            tmp.close()
        os.replace(tmp.name, path)

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(
                f"Could not read {path}: {exc}",
                hint="Fix or delete the file; it will be recreated with defaults.",
            ) from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path} must contain a JSON object.")
        return data

    # --- dictionary -----------------------------------------------------
    def _read_dictionary_entries(self) -> list[dict]:
        """Read ``dictionary.json`` normalized to a list of rich-entry dicts.

        Legacy plain-string entries become ``{"term": <str>}``; rich entries
        keep every key they had (including ones we don't understand) so
        hand-edited extra fields round-trip untouched. Anything else
        (numbers, lists, entries missing a usable "term") is skipped.
        """
        raw = self._read(self._dictionary_path).get("terms", [])
        entries: list[dict] = []
        for item in raw:
            if isinstance(item, str):
                term = item.strip()
                if term:
                    entries.append({"term": term})
            elif isinstance(item, dict):
                term = str(item.get("term", "")).strip()
                if term:
                    entry = dict(item)
                    entry["term"] = term
                    entries.append(entry)
        return entries

    def _write_dictionary_entries(self, entries: list[dict]) -> None:
        # Collapse entries that carry no rich data back to a plain string so
        # untouched/simple terms keep the minimal on-disk shape.
        terms_out = [
            entry["term"] if set(entry.keys()) <= {"term"} else entry for entry in entries
        ]
        self._atomic_write(self._dictionary_path, {"terms": terms_out})

    def dictionary_terms(self) -> list[str]:
        """Canonical terms ordered: starred first, then uses desc, then insertion order."""
        entries = self._read_dictionary_entries()

        def sort_key(indexed: tuple[int, dict]) -> tuple[int, int, int]:
            index, entry = indexed
            starred = bool(entry.get("starred", False))
            uses = _coerce_uses(entry.get("uses", 0))
            return (0 if starred else 1, -uses, index)

        ordered = sorted(enumerate(entries), key=sort_key)
        return [entry["term"] for _, entry in ordered]

    def add_dictionary_term(self, term: str) -> bool:
        """Add a new term; ``False`` when it (or an apostrophe variant) exists."""
        term = term.strip()
        if not term:
            return False
        entries = self._read_dictionary_entries()
        folded = fold_term(term)
        if any(fold_term(entry["term"]) == folded for entry in entries):
            return False
        entries.append({"term": term})
        self._write_dictionary_entries(entries)
        return True

    def record_term_uses(self, counts: dict[str, int]) -> None:
        """Increment per-term usage counts, upgrading legacy entries as needed.

        Unknown terms (not present in the dictionary) are silently ignored.
        """
        if not counts:
            return
        entries = self._read_dictionary_entries()
        changed = False
        for entry in entries:
            n = counts.get(entry["term"])
            if n:
                entry["uses"] = _coerce_uses(entry.get("uses", 0)) + int(n)
                changed = True
        if changed:
            self._write_dictionary_entries(entries)

    # --- snippets --------------------------------------------------------
    def snippets(self) -> dict[str, str]:
        raw = self._read(self._snippets_path).get("snippets", {})
        return {str(k): str(v) for k, v in raw.items() if str(k).strip()}

    def set_snippet(self, trigger: str, expansion: str) -> None:
        snippets = self.snippets()
        snippets[trigger.strip()] = expansion
        self._write(self._snippets_path, {"snippets": snippets})

    # --- styles ----------------------------------------------------------
    def styles(self) -> dict[str, str]:
        raw = self._read(self._styles_path).get("styles", {})
        return {str(k): str(v) for k, v in raw.items()}

    def style_rules(self, name: str | None = None) -> tuple[str, str]:
        """Return ``(style_name, rules_text)``; unknown names fall back to default."""
        data = self._read(self._styles_path)
        styles = self.styles()
        name = name or str(data.get("active", "default"))
        if name in styles:
            return name, styles[name]
        return "default", styles.get("default", DEFAULT_STYLES["default"])

    def set_active_style(self, name: str) -> None:
        data = self._read(self._styles_path)
        styles = self.styles()
        if name not in styles:
            raise ConfigError(
                f"Unknown style {name!r}.",
                hint=f"Known styles: {', '.join(sorted(styles)) or '(none)'}. "
                f"Edit {self._styles_path} to add one.",
            )
        data["active"] = name
        self._write(self._styles_path, data)

    # --- transforms --------------------------------------------------------
    def transforms(self) -> dict[str, str]:
        """Read ``transforms.json``'s name -> prompt mapping, insertion-ordered.

        Seeded with the built-in ``Polish``/``Prompt Engineer`` transforms on
        first store creation only (like ``dictionary.json``/``snippets.json``);
        once the file exists, whatever the user has there (added, removed, or
        edited transforms) is returned as-is -- there is no ``styles.json``-
        style backfill of built-ins added in a later version.
        """
        raw = self._read(self._transforms_path).get("transforms", {})
        return {str(k): str(v) for k, v in raw.items()}

    # --- per-app rules -----------------------------------------------------
    def app_rules(self) -> dict[str, AppRule]:
        """Read ``app_styles.json``, tolerant of it being absent or garbage.

        Unlike the other files, this one is never auto-created: a missing
        file, invalid JSON, or a non-dict top-level value all just mean
        "no per-app rules configured" and yield ``{}``.
        """
        try:
            raw = json.loads(self._app_styles_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        rules: dict[str, AppRule] = {}
        for key, value in raw.items():
            key_l = str(key).strip().lower()
            if not key_l:
                continue
            if isinstance(value, str):
                rules[key_l] = AppRule(style=value)
            elif isinstance(value, dict):
                rules[key_l] = AppRule(
                    style=str(value.get("style") or ""),
                    insert=str(value.get("insert") or ""),
                )
            # anything else (int, list, null, ...) is garbage; skip it
        return rules
