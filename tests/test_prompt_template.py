import pytest

from codemira.extraction.extractor import PromptTemplate


class TestPromptTemplate:
    def test_no_slots_renders_unchanged(self):
        t = PromptTemplate("Just instructions.")
        assert t.render() == "Just instructions."

    def test_single_slot(self):
        t = PromptTemplate("hello {name}")
        assert t.render(name="world") == "hello world"

    def test_multiple_slots(self):
        t = PromptTemplate("{a} and {b}")
        assert t.render(a="x", b="y") == "x and y"

    def test_repeated_slot_replaced_everywhere(self):
        t = PromptTemplate("{x}-{x}-{x}")
        assert t.render(x="z") == "z-z-z"

    def test_missing_slot_raises(self):
        t = PromptTemplate("hello {name}")
        with pytest.raises(ValueError, match="Missing prompt slots"):
            t.render()

    def test_unknown_slot_raises(self):
        t = PromptTemplate("hello {name}")
        with pytest.raises(ValueError, match="Unknown prompt slots"):
            t.render(name="x", extra="y")

    def test_json_braces_are_not_slots(self):
        t = PromptTemplate('Example: {"decision": "squash"}')
        assert t.render() == 'Example: {"decision": "squash"}'

    def test_nested_braces_skipped(self):
        t = PromptTemplate('[{"name":"x","type":"y"}]')
        assert t.render() == '[{"name":"x","type":"y"}]'

    def test_load_prompt_returns_template(self, tmp_path):
        from codemira.extraction.extractor import load_prompt
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test_prompt.txt").write_text("hello {name}")
        t = load_prompt("test_prompt", str(prompts_dir))
        assert isinstance(t, PromptTemplate)
        assert t.render(name="world") == "hello world"

    def test_real_prompts_have_expected_slots(self):
        from codemira.extraction.extractor import load_prompt
        import os
        prompts_dir = os.path.join(os.path.dirname(__file__), "..", "prompts")
        # These pairs should render cleanly with their documented slots:
        cases = [
            ("extraction_system", {"conversation_arc": "x"}),
            ("extraction_user", {"compressed_transcript": "x", "existing_memories": "y"}),
            ("consolidation_system", {}),
            ("consolidation_user", {"memory_texts": "x"}),
            ("arc_summarizer_system", {}),
            ("arc_summarizer_user", {"transcript": "x", "prior_arc": "y"}),
            ("link_classification_system", {}),
            ("link_classification_user", {"text_a": "x", "text_b": "y"}),
            ("entity_extraction_system", {}),
            ("entity_extraction_user", {"text": "x"}),
            ("compression_system", {}),
        ]
        for name, slots in cases:
            t = load_prompt(name, prompts_dir)
            t.render(**slots)
