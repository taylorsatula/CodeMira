import pytest
import os
import numpy as np
from tests.conftest import _make_embedding, _make_embeddings


@pytest.fixture
def search_setup(tmpdir_path, memory_db):
    from codemira.store.db import insert_memory, get_or_create_entity, link_memory_entity
    from codemira.store.index import MemoryIndex
    embs = _make_embeddings(5)
    ids = []
    texts = [
        "Prefers threading over asyncio for concurrent I/O",
        "Uses Docker for deployment and containerization",
        "Prefers raw sqlite3 over ORM for simple CRUD operations",
        "Tests with timing dependencies use Event.wait not sleep",
        "Rejects asyncio for the task runner called it a mess",
    ]
    for i, (text, emb) in enumerate(zip(texts, embs)):
        mid = insert_memory(memory_db, text, "priority", emb)
        ids.append(mid)
    index_path = os.path.join(tmpdir_path, "memories.index")
    mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
    mi.build_from_db(memory_db)
    return memory_db, mi, ids, embs


class TestBM25Search:
    def test_bm25_finds_matching_text(self, search_setup):
        from codemira.store.search import HybridSearcher
        conn, mi, ids, embs = search_setup
        searcher = HybridSearcher()
        results = searcher.bm25_search("threading asyncio", 5, conn)
        assert len(results) > 0
        top_result = results[0]
        assert top_result.memory_id in ids
        assert "threading" in top_result.text.lower() or "asyncio" in top_result.text.lower()
        assert top_result.score < 0

    def test_bm25_ranks_relevant_higher(self, search_setup):
        from codemira.store.search import HybridSearcher
        conn, mi, ids, embs = search_setup
        searcher = HybridSearcher()
        results = searcher.bm25_search("threading", 5, conn)
        threading_ids = {ids[0], ids[4]}
        non_threading_ids = set(ids) - threading_ids
        top_ids = {r.memory_id for r in results[:2]}
        assert top_ids & threading_ids, "At least one threading result should be in top results"

    def test_bm25_empty_query_returns_nothing(self, search_setup):
        from codemira.store.search import HybridSearcher
        conn, mi, ids, embs = search_setup
        searcher = HybridSearcher()
        results = searcher.bm25_search("xyznonexistent", 5, conn)
        assert len(results) == 0


class TestANNSearch:
    def test_ann_finds_similar(self, search_setup):
        from codemira.store.search import HybridSearcher
        conn, mi, ids, embs = search_setup
        searcher = HybridSearcher()
        results = searcher.ann_search(embs[0], 3, mi, conn)
        assert len(results) >= 1
        assert results[0].memory_id == ids[0]
        assert results[0].score >= 0.8, f"Query embedding for ids[0] should match itself with high score, got {results[0].score}"

    def test_ann_empty_index(self, memory_db, tmpdir_path):
        from codemira.store.search import HybridSearcher
        from codemira.store.index import MemoryIndex
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        mi.build_from_db(memory_db)
        searcher = HybridSearcher()
        results = searcher.ann_search(_make_embedding(), 3, mi, memory_db)
        assert len(results) == 0


class TestHybridSearch:
    def test_hybrid_merges_results(self, search_setup):
        from codemira.store.search import HybridSearcher
        conn, mi, ids, embs = search_setup
        searcher = HybridSearcher()
        results = searcher.hybrid_search("threading asyncio", embs[0], 5, conn, mi)
        assert len(results) >= 1
        memory_ids = [r.memory_id for r in results]
        assert ids[0] in memory_ids
        top_result = [r for r in results if r.memory_id == ids[0]][0]
        assert "threading" in top_result.text.lower() or "asyncio" in top_result.text.lower()

    def test_hybrid_respects_limit(self, search_setup):
        from codemira.store.search import HybridSearcher
        conn, mi, ids, embs = search_setup
        searcher = HybridSearcher()
        results = searcher.hybrid_search("threading asyncio", embs[0], 2, conn, mi)
        assert 1 <= len(results) <= 2

    def test_hybrid_single_memory(self, memory_db, tmpdir_path):
        from codemira.store.db import insert_memory
        from codemira.store.index import MemoryIndex
        from codemira.store.search import HybridSearcher
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Prefers threading over asyncio", "priority", emb)
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        mi.build_from_db(memory_db)
        searcher = HybridSearcher()
        results = searcher.hybrid_search("threading", emb, 5, memory_db, mi)
        assert len(results) == 1
        assert results[0].memory_id == mid

    def test_hybrid_deduplicates(self, search_setup):
        from codemira.store.search import HybridSearcher
        conn, mi, ids, embs = search_setup
        searcher = HybridSearcher()
        results = searcher.hybrid_search("threading asyncio", embs[0], 10, conn, mi)
        memory_ids = [r.memory_id for r in results]
        assert len(memory_ids) == len(set(memory_ids))
