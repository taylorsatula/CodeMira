import os
import tempfile

import pytest

from tests.conftest import _make_embedding, _make_embeddings


@pytest.fixture
def empty_index():
    from codemira.store.index import MemoryIndex
    with tempfile.TemporaryDirectory() as tmpdir:
        idx = MemoryIndex(os.path.join(tmpdir, "memories.db"), os.path.join(tmpdir, "memories.index"))
        yield idx


@pytest.fixture
def populated_index():
    from codemira.store.db import open_db, insert_memory
    from codemira.store.index import MemoryIndex
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "memories.db")
        conn = open_db(db_path)
        embs = _make_embeddings(3)
        for i, e in enumerate(embs):
            insert_memory(conn, f"memory {i}", "priority", e, f"ses_{i}")
        idx = MemoryIndex(db_path, os.path.join(tmpdir, "memories.index"))
        idx.build_from_db(conn)
        yield idx, embs
        conn.close()


class TestIsDuplicateVectorContract:
    def test_unchecked_on_empty_index(self, empty_index):
        from codemira.extraction.dedup import is_duplicate_vector
        emb = _make_embedding(seed=99)
        assert is_duplicate_vector(emb, empty_index, 0.92) == "unchecked"

    def test_duplicate_on_near_match(self, populated_index):
        from codemira.extraction.dedup import is_duplicate_vector
        idx, embs = populated_index
        assert is_duplicate_vector(embs[0], idx, 0.92) == "duplicate"

    def test_unique_on_distant_vector(self, populated_index):
        from codemira.extraction.dedup import is_duplicate_vector
        idx, _ = populated_index
        far = _make_embedding(seed=12345)
        assert is_duplicate_vector(far, idx, 0.99) == "unique"


class TestPackTurnsIntoChunks:
    def test_short_returns_single(self):
        from codemira.extraction.chunker import pack_turns_into_chunks
        assert pack_turns_into_chunks("User: hi\nAssistant: hello", 1024) == ["User: hi\nAssistant: hello"]

    def test_splits_at_user_boundaries(self):
        from codemira.extraction.chunker import pack_turns_into_chunks
        turn = "User: " + "x" * 4000 + "\nAssistant: reply"
        transcript = "\n\n".join([turn] * 10)
        chunks = pack_turns_into_chunks(transcript, 2000)
        assert len(chunks) > 1
        for c in chunks:
            assert c.startswith("User:")
