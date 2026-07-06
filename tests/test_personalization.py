"""Snippet expansion, dictionary preservation, and the JSON store."""

import json

import pytest

from local_flow.errors import ConfigError
from local_flow.personalization.store import PersonalizationStore
from local_flow.polish.rules import enforce_dictionary, expand_snippets


class TestSnippetExpansion:
    def test_expands_multiword_trigger(self):
        snippets = {"sig block": "Best regards,\nJay"}
        text, count = expand_snippets("add sig block here", snippets)
        assert text == "add Best regards,\nJay here"
        assert count == 1

    def test_case_insensitive_trigger(self):
        text, count = expand_snippets("My Addr please", {"addr": "12 Main St"})
        assert text == "My 12 Main St please"
        assert count == 1

    def test_no_partial_word_match(self):
        text, count = expand_snippets("the address book", {"addr": "12 Main St"})
        assert text == "the address book"
        assert count == 0

    def test_longer_trigger_wins(self):
        snippets = {"sig": "SHORT", "sig block": "LONG"}
        text, count = expand_snippets("use sig block now", snippets)
        assert text == "use LONG now"
        assert count == 1

    def test_expansion_with_backslashes_is_literal(self):
        snippets = {"winpath": "C:\\Users\\jay"}
        text, count = expand_snippets("open winpath now", snippets)
        assert text == "open C:\\Users\\jay now"
        assert count == 1


class TestDictionaryEnforcement:
    def test_canonical_casing_restored(self):
        text, count = enforce_dictionary("we use postgresql at work", ["PostgreSQL"])
        assert text == "we use PostgreSQL at work"
        assert count == 1

    def test_multiword_term_with_flexible_whitespace(self):
        text, count = enforce_dictionary("the jispr   flow launch", ["JiSpr Flow"])
        assert text == "the JiSpr Flow launch"
        assert count == 1

    def test_word_boundaries_respected(self):
        # 'postgresql' inside a longer token must not be rewritten
        text, count = enforce_dictionary("see postgresqlish docs", ["PostgreSQL"])
        assert text == "see postgresqlish docs"
        assert count == 0

    def test_survives_llm_style_rewrites(self):
        # Simulates an LLM lowercasing a term; the post-pass restores it.
        polished = "Jispr flow is ready."
        text, count = enforce_dictionary(polished, ["JiSpr Flow"])
        assert text == "JiSpr Flow is ready."
        assert count == 1


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
