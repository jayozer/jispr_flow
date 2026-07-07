"""Snippet expansion, dictionary preservation, and the JSON store."""

import json

import pytest

from local_flow.errors import ConfigError
from local_flow.personalization.store import (
    DEFAULT_STYLES,
    AppRule,
    PersonalizationStore,
    _fold_term,
    fold_term,
    match_app_rule,
)
from local_flow.polish.rules import enforce_dictionary, expand_snippets


class TestFoldTerm:
    def test_public_name_folds_case_and_possessive(self):
        assert fold_term("Iva's") == "iva"

    def test_private_alias_matches_public_function(self):
        assert _fold_term is fold_term


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

    def test_atomic_write_uses_a_unique_tmp_name_and_leaves_none_behind(self, tmp_path):
        path = tmp_path / "x.json"
        PersonalizationStore._atomic_write(path, {"a": 1})
        assert json.loads(path.read_text()) == {"a": 1}
        # No leftover ".tmp" file, and the final file is the only thing there.
        assert list(tmp_path.iterdir()) == [path]

    def test_atomic_write_tmp_name_is_unique_across_calls(self, tmp_path, monkeypatch):
        import tempfile

        seen_names: list[str] = []
        real_named_tempfile = tempfile.NamedTemporaryFile

        def spy(*args, **kwargs):
            tmp = real_named_tempfile(*args, **kwargs)
            seen_names.append(tmp.name)
            return tmp

        monkeypatch.setattr(tempfile, "NamedTemporaryFile", spy)
        path = tmp_path / "y.json"
        PersonalizationStore._atomic_write(path, {"a": 1})
        PersonalizationStore._atomic_write(path, {"a": 2})
        assert len(seen_names) == 2
        assert seen_names[0] != seen_names[1]


class TestDictionaryRichEntries:
    """dictionary.json mixes legacy strings with rich {starred, uses} entries."""

    def test_mixed_entries_order_starred_then_uses_then_insertion(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "dictionary.json").write_text(
            json.dumps(
                {
                    "terms": [
                        "Alpha",
                        {"term": "Bravo", "uses": 5},
                        {"term": "Charlie", "starred": True},
                        {"term": "Delta", "uses": 9},
                        {"term": "Echo", "starred": True, "uses": 2},
                    ]
                }
            )
        )
        assert store.dictionary_terms() == ["Echo", "Charlie", "Delta", "Bravo", "Alpha"]

    def test_add_dictionary_term_returns_true_then_false_on_duplicate(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        assert store.add_dictionary_term("JiSpr Flow") is True
        assert store.add_dictionary_term("JiSpr Flow") is False

    def test_add_apostrophe_variant_of_existing_term_returns_false(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        assert store.add_dictionary_term("Iva") is True
        assert store.add_dictionary_term("Iva's") is False
        assert store.add_dictionary_term("Iva’s") is False  # curly apostrophe too
        assert store.dictionary_terms() == ["Iva"]

    def test_add_base_term_when_apostrophe_variant_already_exists_returns_false(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        assert store.add_dictionary_term("Iva's") is True
        assert store.add_dictionary_term("Iva") is False
        assert store.dictionary_terms() == ["Iva's"]

    def test_unknown_fields_survive_add_dictionary_term(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "dictionary.json").write_text(
            json.dumps({"terms": [{"term": "Foo", "starred": True, "note": "custom-field"}]})
        )
        assert store.add_dictionary_term("Bar") is True
        on_disk = json.loads((tmp_path / "dictionary.json").read_text())
        foo_entry = next(e for e in on_disk["terms"] if isinstance(e, dict) and e["term"] == "Foo")
        assert foo_entry["note"] == "custom-field"
        assert foo_entry["starred"] is True
        assert "Bar" in on_disk["terms"]

    def test_unknown_fields_survive_record_term_uses(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "dictionary.json").write_text(
            json.dumps({"terms": [{"term": "Foo", "note": "custom-field"}]})
        )
        store.record_term_uses({"Foo": 3})
        on_disk = json.loads((tmp_path / "dictionary.json").read_text())
        foo_entry = next(e for e in on_disk["terms"] if e["term"] == "Foo")
        assert foo_entry["note"] == "custom-field"
        assert foo_entry["uses"] == 3

    def test_record_term_uses_upgrades_legacy_string_and_increments(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        store.add_dictionary_term("PostgreSQL")
        store.record_term_uses({"PostgreSQL": 2})
        on_disk = json.loads((tmp_path / "dictionary.json").read_text())
        assert on_disk["terms"] == [{"term": "PostgreSQL", "uses": 2}]

        store.record_term_uses({"PostgreSQL": 3})
        on_disk = json.loads((tmp_path / "dictionary.json").read_text())
        assert on_disk["terms"] == [{"term": "PostgreSQL", "uses": 5}]
        assert store.dictionary_terms() == ["PostgreSQL"]

    def test_record_term_uses_ignores_unknown_terms(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        store.add_dictionary_term("PostgreSQL")
        store.record_term_uses({"Nonexistent": 5})
        assert store.dictionary_terms() == ["PostgreSQL"]
        on_disk = json.loads((tmp_path / "dictionary.json").read_text())
        assert on_disk["terms"] == ["PostgreSQL"]

    def test_record_term_uses_empty_counts_is_noop(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        store.add_dictionary_term("PostgreSQL")
        store.record_term_uses({})
        on_disk = json.loads((tmp_path / "dictionary.json").read_text())
        assert on_disk["terms"] == ["PostgreSQL"]

    def test_record_term_uses_tolerates_hand_edited_null_uses(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "dictionary.json").write_text(
            json.dumps({"terms": [{"term": "Foo", "uses": None}]})
        )
        store.record_term_uses({"Foo": 1})  # must not raise TypeError
        on_disk = json.loads((tmp_path / "dictionary.json").read_text())
        assert on_disk["terms"] == [{"term": "Foo", "uses": 1}]


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

    def test_existing_file_missing_all_defaults_gains_them_without_losing_user_style(
        self, tmp_path
    ):
        (tmp_path / "styles.json").write_text(
            json.dumps({"active": "mine", "styles": {"mine": "just mine"}})
        )
        store = PersonalizationStore(tmp_path)
        styles = store.styles()
        assert styles["mine"] == "just mine"
        for name, rules in DEFAULT_STYLES.items():
            assert styles[name] == rules
        # active selection (a non-built-in style) is left untouched
        assert store.style_rules()[0] == "mine"

    def test_existing_file_missing_only_email_and_chat_gains_exactly_those_two(
        self, tmp_path
    ):
        (tmp_path / "styles.json").write_text(
            json.dumps(
                {
                    "active": "professional",
                    "styles": {
                        "default": "custom default",
                        "professional": "custom professional",
                        "casual": "custom casual",
                    },
                }
            )
        )
        store = PersonalizationStore(tmp_path)
        styles = store.styles()
        assert styles["default"] == "custom default"
        assert styles["professional"] == "custom professional"
        assert styles["casual"] == "custom casual"
        assert styles["email"] == DEFAULT_STYLES["email"]
        assert styles["chat"] == DEFAULT_STYLES["chat"]
        # active selection untouched
        assert store.style_rules()[0] == "professional"

    def test_user_customized_email_style_is_preserved(self, tmp_path):
        (tmp_path / "styles.json").write_text(
            json.dumps(
                {
                    "active": "default",
                    "styles": {
                        "default": DEFAULT_STYLES["default"],
                        "professional": DEFAULT_STYLES["professional"],
                        "casual": DEFAULT_STYLES["casual"],
                        "email": "my custom email style",
                    },
                }
            )
        )
        store = PersonalizationStore(tmp_path)
        styles = store.styles()
        assert styles["email"] == "my custom email style"  # never overwritten
        assert styles["chat"] == DEFAULT_STYLES["chat"]  # missing one still added

    def test_file_with_all_defaults_already_present_is_not_rewritten(self, tmp_path):
        (tmp_path / "styles.json").write_text(
            json.dumps({"active": "default", "styles": dict(DEFAULT_STYLES)})
        )
        before = (tmp_path / "styles.json").read_text()
        PersonalizationStore(tmp_path)
        after = (tmp_path / "styles.json").read_text()
        assert before == after  # nothing missing, so no write-back happened


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

    def test_empty_dict_value_is_harmless(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "app_styles.json").write_text(json.dumps({"claude": {}}))
        assert store.app_rules() == {"claude": AppRule("", "")}

    def test_null_sub_values_do_not_stringify_to_none(self, tmp_path):
        store = PersonalizationStore(tmp_path)
        (tmp_path / "app_styles.json").write_text(
            json.dumps({"claude": {"style": None, "insert": None}})
        )
        assert store.app_rules() == {"claude": AppRule("", "")}


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
