"""Snippet expansion, dictionary preservation, and the JSON store."""

import json

import pytest

from local_flow.errors import ConfigError
from local_flow.personalization.store import AppRule, PersonalizationStore, match_app_rule
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


class TestBuiltinStyles:
    def test_fresh_store_includes_email_and_chat_styles(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        styles = store.styles()
        assert "email" in styles
        assert "chat" in styles
        assert "email" in styles["email"].lower()
        assert "casual" in styles["chat"].lower()
        # active style is unaffected by seeding the new named styles
        assert store.style_rules()[0] == "default"

    def test_existing_styles_file_is_not_overwritten(self, tmp_path):
        (tmp_path / "styles.json").write_text(
            json.dumps({"active": "mine", "styles": {"mine": "just mine"}})
        )
        store = PersonalizationStore(tmp_path)
        assert store.styles() == {"mine": "just mine"}
        assert "email" not in store.styles()
        assert "chat" not in store.styles()


class TestAppRules:
    def test_plain_string_value_is_style_only(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "app_styles.json").write_text(
            json.dumps({"com.tinyspeck.slackmacgap": "casual"})
        )
        assert store.app_rules() == {
            "com.tinyspeck.slackmacgap": AppRule(style="casual"),
        }

    def test_dict_value_reads_style_and_insert(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "app_styles.json").write_text(
            json.dumps({"com.apple.mail": {"style": "email", "insert": "paste"}})
        )
        assert store.app_rules() == {
            "com.apple.mail": AppRule(style="email", insert="paste"),
        }

    def test_unknown_keys_in_dict_value_are_ignored(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "app_styles.json").write_text(
            json.dumps({"claude": {"insert": "type", "bogus": "ignored"}})
        )
        assert store.app_rules() == {"claude": AppRule(insert="type")}

    def test_keys_are_lowercased(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "app_styles.json").write_text(json.dumps({"Com.Apple.Mail": "email"}))
        assert store.app_rules() == {"com.apple.mail": AppRule(style="email")}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        assert store.app_rules() == {}

    def test_invalid_json_returns_empty_dict(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "app_styles.json").write_text("{not json")
        assert store.app_rules() == {}

    def test_non_dict_top_level_returns_empty_dict(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "app_styles.json").write_text(json.dumps(["a", "b"]))
        assert store.app_rules() == {}

    def test_garbage_values_are_skipped(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "app_styles.json").write_text(
            json.dumps({"good": "casual", "bad": 42, "also_bad": [1, 2]})
        )
        assert store.app_rules() == {"good": AppRule(style="casual")}


class TestMatchAppRule:
    def test_exact_app_id_match_wins(self):
        rules = {
            "com.apple.mail": AppRule(style="email"),
            "mail": AppRule(style="casual"),
        }
        assert match_app_rule(rules, "com.apple.mail", "Mail") == AppRule(style="email")

    def test_longest_substring_key_wins(self):
        rules = {
            "slack": AppRule(style="casual"),
            "slackmacgap": AppRule(style="chat"),
        }
        result = match_app_rule(rules, "com.tinyspeck.slackmacgap", "Slack")
        assert result == AppRule(style="chat")

    def test_substring_matches_against_title_too(self):
        rules = {"claude": AppRule(insert="type")}
        result = match_app_rule(rules, "com.unknown.app", "Claude Code")
        assert result == AppRule(insert="type")

    def test_case_insensitive_matching(self):
        rules = {"slack": AppRule(style="casual")}
        result = match_app_rule(rules, "COM.TINYSPECK.SLACKMACGAP", "")
        assert result == AppRule(style="casual")

    def test_no_match_returns_none(self):
        rules = {"slack": AppRule(style="casual")}
        assert match_app_rule(rules, "com.apple.mail", "Mail") is None

    def test_empty_rules_returns_none(self):
        assert match_app_rule({}, "anything", "Anything") is None
