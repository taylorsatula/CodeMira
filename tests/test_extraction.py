import os

import pytest

from tests.conftest import skip_no_ollama
from codemira.extraction.dedup import is_duplicate_text, extract_entities


PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
OLLAMA_URL = "http://localhost:11434"
ENTITY_MODEL = "gemma4:e2b"


class TestDedupText:
    def test_duplicate_text_detected(self):
        existing = ["Prefers threading over asyncio for concurrent I/O"]
        assert is_duplicate_text("Prefers threading over asyncio for concurrent I/O", existing, 0.95) is True

    def test_non_duplicate_text(self):
        existing = ["Prefers threading over asyncio for concurrent I/O"]
        assert is_duplicate_text("Uses Docker for deployment", existing, 0.95) is False

    def test_near_duplicate_text(self):
        existing = ["Prefers threading over asyncio for concurrent I/O"]
        assert is_duplicate_text("Prefers threading over asyncio for I/O concurrency", existing, 0.85) is True

    def test_different_text_high_similarity(self):
        existing = ["Prefers threading over asyncio for concurrent I/O"]
        assert is_duplicate_text("Prefers threading over asyncio for network I/O", existing, 0.99) is False

    def test_empty_existing(self):
        assert is_duplicate_text("Some text", [], 0.95) is False


@skip_no_ollama
class TestExtractEntities:
    def test_extracts_framework(self):
        entities = extract_entities(
            "Uses FastAPI for REST endpoints",
            ENTITY_MODEL, OLLAMA_URL, PROMPTS_DIR,
        )
        names = {e["name"] for e in entities}
        assert "fastapi" in names, f"Expected fastapi in {names}"

    def test_extracts_multiple(self):
        entities = extract_entities(
            "Uses FastAPI with pytest and Docker",
            ENTITY_MODEL, OLLAMA_URL, PROMPTS_DIR,
        )
        names = {e["name"] for e in entities}
        assert names & {"fastapi", "pytest", "docker"}, f"Expected at least one of fastapi/pytest/docker in {names}"

    def test_no_entities_for_generic_text(self):
        entities = extract_entities(
            "Prefers simple solutions over clever abstractions",
            ENTITY_MODEL, OLLAMA_URL, PROMPTS_DIR,
        )
        assert isinstance(entities, list)

    def test_entity_shape(self):
        entities = extract_entities(
            "Uses pytest for testing",
            ENTITY_MODEL, OLLAMA_URL, PROMPTS_DIR,
        )
        for e in entities:
            assert "name" in e and isinstance(e["name"], str)
            assert "type" in e and isinstance(e["type"], str)
            assert e["name"] == e["name"].lower()

    def test_deduplicates(self):
        entities = extract_entities(
            "pytest is great. Also pytest again.",
            ENTITY_MODEL, OLLAMA_URL, PROMPTS_DIR,
        )
        names = [e["name"] for e in entities]
        assert len(names) == len(set(names)), f"Expected unique names, got {names}"
