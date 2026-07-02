"""Snippet expansion, dictionary preservation, and the JSON store."""

import json

import pytest

from local_flow.errors import ConfigError
from local_flow.personalization.store import PersonalizationStore
from local_flow.polish.rules import enforce_dictionary, expand_snippets


class TestSnippetExpansion:
    def test_expands_multiword_trigger(self):
        snippets = {"sig block": "Best regards,\nJay"}
        assert (
            expand_snippets("add sig block here", snippets)
            == "add Best regards,\nJay here"
        )

    def test_case_insensitive_trigger(self):
        assert expand_snippets("My Addr please", {"addr": "12 Main St"}) == (
            "My 12 Main St please"
        )

    def test_no_partial_word_match(self):
        assert expand_snippets("the address book", {"addr": "12 Main St"}) == (
            "the address book"
        )

    def test_longer_trigger_wins(self):
        snippets = {"sig": "SHORT", "sig block": "LONG"}
        assert expand_snippets("use sig block now", snippets) == "use LONG now"

    def test_expansion_with_backslashes_is_literal(self):
        snippets = {"winpath": "C:\\Users\\jay"}
        assert expand_snippets("open winpath now", snippets) == "open C:\\Users\\jay now"


class TestDictionaryEnforcement:
    def test_canonical_casing_restored(self):
        assert (
            enforce_dictionary("we use postgresql at work", ["PostgreSQL"])
            == "we use PostgreSQL at work"
        )

    def test_multiword_term_with_flexible_whitespace(self):
        assert (
            enforce_dictionary("the jispr   flow launch", ["JiSpr Flow"])
            == "the JiSpr Flow launch"
        )

    def test_word_boundaries_respected(self):
        # 'postgresql' inside a longer token must not be rewritten
        assert (
            enforce_dictionary("see postgresqlish docs", ["PostgreSQL"])
            == "see postgresqlish docs"
        )

    def test_survives_llm_style_rewrites(self):
        # Simulates an LLM lowercasing a term; the post-pass restores it.
        polished = "Jispr flow is ready."
        assert enforce_dictionary(polished, ["JiSpr Flow"]) == "JiSpr Flow is ready."


class TestPersonalizationStore:
    def test_creates_default_files(self, tmp_path):
        store = PersonalizationStore(tmp_path / "data")
        assert (tmp_path / "data" / "dictionary.json").is_file()
        assert (tmp_path / "data" / "snippets.json").is_file()
        assert (tmp_path / "data" / "styles.json").is_file()
        assert store.dictionary_terms() == []
        assert store.snippets() == {}
        assert store.style_rules()[0] == "default"

    def test_dictionary_roundtrip_and_dedup(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        store.add_dictionary_term("JiSpr Flow")
        store.add_dictionary_term("JiSpr Flow")
        store.add_dictionary_term("PostgreSQL")
        assert store.dictionary_terms() == ["JiSpr Flow", "PostgreSQL"]
        # Persisted as plain JSON that a user can edit by hand.
        on_disk = json.loads((tmp_path / "dictionary.json").read_text())
        assert on_disk == {"terms": ["JiSpr Flow", "PostgreSQL"]}

    def test_snippets_roundtrip(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        store.set_snippet("sig block", "Best regards,\nJay")
        assert PersonalizationStore(tmp_path).snippets() == {
            "sig block": "Best regards,\nJay"
        }

    def test_style_fallback_for_unknown_name(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        name, rules = store.style_rules("nonexistent")
        assert name == "default"
        assert rules

    def test_set_active_style_validates(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        store.set_active_style("professional")
        assert store.style_rules()[0] == "professional"
        with pytest.raises(ConfigError):
            store.set_active_style("bogus")

    def test_corrupt_file_raises_actionable_error(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "dictionary.json").write_text("{not json")
        with pytest.raises(ConfigError) as excinfo:
            store.dictionary_terms()
        assert "dictionary.json" in str(excinfo.value)
