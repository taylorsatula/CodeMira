import sqlite3

from codemira.config import DaemonConfig
from codemira.store.db import increment_access, get_memory, dedupe_by
from codemira.store.index import MemoryIndex
from codemira.store.search import HybridSearcher
from codemira.retrieval.hub_discovery import hub_discovery


def retrieve(
    query_expansion: str,
    entities: list[str],
    pinned_memory_ids: list[str],
    project_root: str,
    conn: sqlite3.Connection,
    index: MemoryIndex,
    config: DaemonConfig,
    query_embedding: list[float] | None = None,
) -> list[dict]:
    if query_embedding is None:
        from codemira.embeddings import EmbeddingsProvider
        provider = EmbeddingsProvider.get()
        query_embedding = provider.encode_realtime(query_expansion)

    searcher = HybridSearcher()
    fresh_results = searcher.hybrid_search(
        query_expansion, query_embedding, config.max_fresh_memories,
        conn, index,
    )
    fresh_memories: list[dict] = []
    for r in fresh_results:
        mem = get_memory(conn, r.memory_id)
        if mem is not None:
            fresh_memories.append(mem)
    fresh_ids = {m["id"] for m in fresh_memories}

    hub_memories = hub_discovery(conn, entities, list(fresh_ids))

    retained_pinned: list[dict] = []
    for pid in pinned_memory_ids:
        mem = get_memory(conn, pid)
        if mem is not None and mem["is_archived"] == 0:
            retained_pinned.append(mem)

    combined = dedupe_by(fresh_memories + hub_memories + retained_pinned)
    combined = combined[:config.max_surfaced_memories]

    surfaced_ids = [m["id"] for m in combined]
    if surfaced_ids:
        increment_access(conn, surfaced_ids)

    return combined
