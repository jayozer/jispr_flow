"""JSON-file-backed store for dictionary terms, snippets, and style rules.

Everything lives under one data directory (``LOCAL_FLOW_DATA_DIR``) as three
hand-editable files that are created with commented defaults on first use:

- ``dictionary.json``: ``{"terms": ["JiSpr Flow", ...]}`` — canonical spellings
  enforced on output and fed to the polish prompt.
- ``snippets.json``: ``{"snippets": {"sig block": "Best regards,\\nJay"}}`` —
  spoken trigger phrases expanded into stored text.
- ``styles.json``: ``{"active": "default", "styles": {name: rules_text}}`` —
  writing-style instructions injected into the polish prompt.
"""

from __future__ import annotations

import json
from pathlib import Path

from local_flow.errors import ConfigError

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
}


class PersonalizationStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._dictionary_path = self.data_dir / "dictionary.json"
        self._snippets_path = self.data_dir / "snippets.json"
        self._styles_path = self.data_dir / "styles.json"
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        if not self._dictionary_path.exists():
            self._write(self._dictionary_path, {"terms": []})
        if not self._snippets_path.exists():
            self._write(self._snippets_path, {"snippets": {}})
        if not self._styles_path.exists():
            self._write(self._styles_path, {"active": "default", "styles": DEFAULT_STYLES})

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

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
    def dictionary_terms(self) -> list[str]:
        terms = self._read(self._dictionary_path).get("terms", [])
        return [str(t) for t in terms if str(t).strip()]

    def add_dictionary_term(self, term: str) -> None:
        term = term.strip()
        if not term:
            return
        terms = self.dictionary_terms()
        if term not in terms:
            terms.append(term)
            self._write(self._dictionary_path, {"terms": terms})

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
