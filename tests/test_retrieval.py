import pytest
import os
import numpy as np
from tests.conftest import _make_embedding, _make_embeddings


@pytest.fixture
def retrieval_setup(tmpdir_path, memory_db):
    from codemira.store.db import insert_memory, upsert_entity, link_memory_entity, insert_memory_link
    from codemira.store.index import MemoryIndex
    from codemira.config import DaemonConfig
    embs = _make_embeddings(5)
    ids = []
    texts = [
        "Prefers threading over asyncio for concurrent I/O",
        "Uses Docker for deployment and containerization",
        "Prefers raw sqlite3 over ORM for simple CRUD",
        "Tests with Event.wait not sleep for timing",
        "Rejected asyncio for the task runner",
    ]
    for i, (text, emb) in enumerate(zip(texts, embs)):
        mid = insert_memory(memory_db, text, "priority", emb)
        ids.append(mid)
    eid = upsert_entity(memory_db, "docker", "tool")
    link_memory_entity(memory_db, ids[1], eid)
    insert_memory_link(memory_db, ids[0], ids[4], "corroborates", "Same topic")
    index_path = os.path.join(tmpdir_path, "memories.index")
    mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
    mi.build_from_db(memory_db)
    config = DaemonConfig()
    return memory_db, mi, ids, embs, config


class TestRetrieve:
    def test_retrieve_returns_memories(self, retrieval_setup):
        from codemira.retrieval.proactive import collect_ranked_memories
        conn, mi, ids, embs, config = retrieval_setup
        results = collect_ranked_memories(
            query_expansion="threading asyncio",
            entities=[],
            pinned_memory_ids=[],
            project_root="/tmp",
            conn=conn,
            index=mi,
            config=config,
            query_embedding=embs[0],
        )
        assert len(results) >= 1
        result_ids = [r["id"] for r in results]
        assert ids[0] in result_ids, f"Expected threading memory {ids[0]} in results, got {result_ids}"

    def test_retrieve_caps_at_max(self, retrieval_setup):
        from codemira.retrieval.proactive import collect_ranked_memories
        conn, mi, ids, embs, config = retrieval_setup
        config.max_surfaced_memories = 2
        results = collect_ranked_memories(
            query_expansion="threading asyncio docker",
            entities=["docker"],
            pinned_memory_ids=[],
            project_root="/tmp",
            conn=conn,
            index=mi,
            config=config,
            query_embedding=embs[0],
        )
        assert 1 <= len(results) <= 2

    def test_retrieve_entity_hub_discovery(self, retrieval_setup):
        from codemira.retrieval.proactive import collect_ranked_memories
        conn, mi, ids, embs, config = retrieval_setup
        results = collect_ranked_memories(
            query_expansion="containerization",
            entities=["docker"],
            pinned_memory_ids=[],
            project_root="/tmp",
            conn=conn,
            index=mi,
            config=config,
            query_embedding=embs[1],
        )
        result_ids = [r["id"] for r in results]
        assert ids[1] in result_ids, f"Expected docker memory {ids[1]} in results via hub discovery, got {result_ids}"
        docker_result = [r for r in results if r["id"] == ids[1]][0]
        assert "docker" in docker_result["text"].lower()

    def test_retrieve_pinned_memories(self, retrieval_setup):
        from codemira.retrieval.proactive import collect_ranked_memories
        conn, mi, ids, embs, config = retrieval_setup
        config.max_surfaced_memories = 8
        results = collect_ranked_memories(
            query_expansion="sqlite3 ORM",
            entities=[],
            pinned_memory_ids=[ids[0]],
            project_root="/tmp",
            conn=conn,
            index=mi,
            config=config,
            query_embedding=embs[2],
        )
        result_ids = [r["id"] for r in results]
        assert ids[0] in result_ids

    def test_retrieve_updates_access_count(self, retrieval_setup):
        from codemira.retrieval.proactive import collect_ranked_memories
        from codemira.store.db import read_memory
        conn, mi, ids, embs, config = retrieval_setup
        results = collect_ranked_memories(
            query_expansion="threading asyncio",
            entities=[],
            pinned_memory_ids=[],
            project_root="/tmp",
            conn=conn,
            index=mi,
            config=config,
            query_embedding=embs[0],
        )
        for r in results:
            mem = read_memory(conn, r["id"])
            assert mem["access_count"] == 1, f"Expected access_count=1 after retrieval, got {mem['access_count']}"
