"""Polish prompt construction: system prompt content and message shape."""

from local_flow.polish.prompting import POLISH_SYSTEM_PROMPT, build_polish_messages


class TestPolishSystemPrompt:
    def test_preserves_dictation_command_phrases(self):
        assert "press enter" in POLISH_SYSTEM_PROMPT
        assert "new line" in POLISH_SYSTEM_PROMPT

    def test_preserves_spoken_dictionary_add_phrase(self):
        # Guards against the LLM rewording "add X to the dictionary" (e.g.
        # rephrasing or dropping words) before it can be extracted downstream.
        assert "add" in POLISH_SYSTEM_PROMPT.lower()
        assert "to the dictionary" in POLISH_SYSTEM_PROMPT.lower()

    def test_demands_bare_output(self):
        assert "ONLY the cleaned text" in POLISH_SYSTEM_PROMPT


class TestBuildPolishMessages:
    def test_structure_and_content(self):
        messages = build_polish_messages("hey fix this pls")
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[0]["content"].startswith(POLISH_SYSTEM_PROMPT)
        assert messages[1]["content"] == "hey fix this pls"

    def test_dictionary_terms_included(self):
        messages = build_polish_messages("x", dictionary_terms=["JiSpr Flow"])
        assert "JiSpr Flow" in messages[0]["content"]

    def test_style_rules_included(self):
        messages = build_polish_messages("x", style_name="casual", style_rules="Relaxed tone.")
        assert "Relaxed tone." in messages[0]["content"]
        assert "casual" in messages[0]["content"]

    def test_additional_system_prompt_keeps_core_protections(self):
        messages = build_polish_messages(
            "x", additional_system_prompt="Prefer short, direct sentences."
        )
        system = messages[0]["content"]
        assert "Prefer short, direct sentences." in system
        assert "press enter" in system
        assert "ONLY the cleaned text" in system
