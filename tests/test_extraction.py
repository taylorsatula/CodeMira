import os

import pytest

from tests.conftest import skip_no_ollama
from codemira.extraction.dedup import is_duplicate_text, extract_entities, VALID_ENTITY_TYPES
from codemira.extraction.extractor import _build_existing_memories_str


PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
OLLAMA_URL = "http://localhost:11434"
ENTITY_MODEL = "gemma4:e2b"


class TestValidEntityTypes:
    def test_project_concept_in_valid_types(self):
        assert "project_concept" in VALID_ENTITY_TYPES

    def test_all_original_types_preserved(self):
        expected = {"library", "framework", "tool", "pattern", "protocol", "error", "project_concept", "other"}
        assert VALID_ENTITY_TYPES == expected


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

    def test_extracts_project_concept(self):
        entities = extract_entities(
            "The PeanutGallery is a two-stage metacognitive observer that watches MIRA's conversations",
            ENTITY_MODEL, OLLAMA_URL, PROMPTS_DIR,
        )
        names = {e["name"] for e in entities}
        types = {e["type"] for e in entities}
        assert "peanutgallery" in names, f"Expected peanutgallery in {names}"
        assert "project_concept" in types, f"Expected project_concept type in {types}"

    def test_project_concept_type_preserved(self):
        entities = extract_entities(
            "The DomainDocs subsystem generates project documentation",
            ENTITY_MODEL, OLLAMA_URL, PROMPTS_DIR,
        )
        domaindocs = next((e for e in entities if e["name"] == "domaindocs"), None)
        if domaindocs is not None:
            assert domaindocs["type"] == "project_concept", f"Expected project_concept, got {domaindocs['type']}"


class TestBuildExistingMemoriesStr:
    def test_no_memories(self):
        existing_str, combined = _build_existing_memories_str([], None)
        assert existing_str == "None"
        assert combined == []

    def test_existing_only(self):
        existing_str, combined = _build_existing_memories_str(["memory one", "memory two"], None)
        assert existing_str == "- memory one\n- memory two"
        assert combined == ["memory one", "memory two"]

    def test_prior_chunks_only(self):
        existing_str, combined = _build_existing_memories_str([], ["chunk memory a"])
        assert "--- Previously extracted from this session ---" in existing_str
        assert "- chunk memory a" in existing_str
        assert combined == ["chunk memory a"]

    def test_existing_and_prior_chunks(self):
        existing_str, combined = _build_existing_memories_str(
            ["db memory"], ["chunk memory a", "chunk memory b"]
        )
        assert "- db memory" in existing_str
        assert "--- Previously extracted from this session ---" in existing_str
        assert "- chunk memory a" in existing_str
        assert "- chunk memory b" in existing_str
        assert combined == ["db memory", "chunk memory a", "chunk memory b"]

    def test_db_memories_before_prior_chunks(self):
        existing_str, combined = _build_existing_memories_str(
            ["db memory"], ["chunk memory"]
        )
        db_idx = existing_str.index("- db memory")
        prior_idx = existing_str.index("--- Previously extracted from this session ---")
        assert db_idx < prior_idx
