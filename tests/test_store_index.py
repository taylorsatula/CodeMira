import pytest
import os
import numpy as np
from tests.conftest import _make_embedding, _make_embeddings


@pytest.fixture
def memory_db_with_memories(memory_db):
    embs = _make_embeddings(5)
    from codemira.store.db import insert_memory
    ids = []
    for i, emb in enumerate(embs):
        mid = insert_memory(memory_db, f"Memory {i}", "priority", emb)
        ids.append(mid)
    return memory_db, ids, embs


class TestMemoryIndexBuild:
    def test_build_from_db(self, memory_db_with_memories, tmpdir_path):
        from codemira.store.index import MemoryIndex
        conn, ids, embs = memory_db_with_memories
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        mi.build_from_db(conn)
        assert mi.index is not None
        assert mi.index.get_current_count() == 5, "Index should contain exactly 5 vectors"
        assert len(mi.id_map) == 5
        assert len(mi.label_map) == 5
        for mid in ids:
            assert mid in mi.label_map, f"Inserted memory {mid} missing from label_map"

    def test_build_from_empty_db(self, memory_db, tmpdir_path):
        from codemira.store.index import MemoryIndex
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        mi.build_from_db(memory_db)
        assert mi.index is None
        assert len(mi.id_map) == 0

    def test_build_excludes_archived(self, memory_db, tmpdir_path):
        from codemira.store.db import insert_memory, archive_memory
        from codemira.store.index import MemoryIndex
        emb = _make_embedding()
        mid1 = insert_memory(memory_db, "Active", "priority", emb)
        mid2 = insert_memory(memory_db, "To archive", "priority", emb)
        archive_memory(memory_db, mid2)
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        mi.build_from_db(memory_db)
        assert len(mi.id_map) == 1
        assert mid1 in mi.label_map
        assert mid2 not in mi.label_map


class TestMemoryIndexSearch:
    def test_search_finds_similar(self, memory_db_with_memories, tmpdir_path):
        from codemira.store.index import MemoryIndex
        conn, ids, embs = memory_db_with_memories
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        mi.build_from_db(conn)
        results = mi.search(embs[0], k=3)
        assert len(results) > 0
        assert results[0][0] == ids[0]
        assert results[0][1] > 0.99

    def test_search_empty_index(self, memory_db, tmpdir_path):
        from codemira.store.index import MemoryIndex
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        results = mi.search(_make_embedding(), k=3)
        assert len(results) == 0

    def test_search_returns_cosine_similarity(self, memory_db_with_memories, tmpdir_path):
        from codemira.store.index import MemoryIndex
        conn, ids, embs = memory_db_with_memories
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        mi.build_from_db(conn)
        results = mi.search(embs[0], k=5)
        for _, sim in results:
            assert 0.0 <= sim <= 1.0


class TestMemoryIndexAddVector:
    def test_add_vector(self, memory_db_with_memories, tmpdir_path):
        from codemira.store.index import MemoryIndex
        conn, ids, embs = memory_db_with_memories
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        mi.build_from_db(conn)
        new_emb = _make_embedding(seed=99)
        mi.add_vector("new_id", new_emb)
        assert "new_id" in mi.label_map
        assert mi._next_label == 6
        results = mi.search(new_emb, k=1)
        assert len(results) == 1
        assert results[0][0] == "new_id", f"Added vector should be searchable as 'new_id', got {results[0][0]}"

    def test_add_vector_to_none_index(self, memory_db, tmpdir_path):
        from codemira.store.index import MemoryIndex
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        mi.add_vector("test", _make_embedding())
        assert "test" not in mi.label_map


class TestMemoryIndexRebuild:
    def test_rebuild_after_write(self, memory_db, tmpdir_path):
        from codemira.store.db import insert_memory
        from codemira.store.index import MemoryIndex
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Test", "priority", emb)
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        mi.build_from_db(memory_db)
        assert len(mi.id_map) == 1
        emb2 = _make_embedding(seed=99)
        insert_memory(memory_db, "Another", "priority", emb2)
        mi.rebuild_after_write(memory_db)
        assert len(mi.id_map) == 2
