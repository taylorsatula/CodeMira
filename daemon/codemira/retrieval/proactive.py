import sqlite3

from codemira.config import DaemonConfig
from codemira.store.db import increment_access, get_memory
from codemira.store.index import MemoryIndex
from codemira.store.search import HybridSearcher
from codemira.retrieval.hub_discovery import hub_discovery


def retrieve(
    query_expansion: str,
    entities: list[str],
    pinned_memory_ids: list[str],
    project_dir: str,
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
    fresh_memories = []
    fresh_ids = set()
    for r in fresh_results:
        mem = get_memory(conn, r.memory_id)
        if mem is not None:
            fresh_memories.append(mem)
            fresh_ids.add(mem["id"])

    hub_memories = hub_discovery(conn, entities, list(fresh_ids))
    hub_ids = {m["id"] for m in hub_memories}

    retained_pinned = []
    for pid in pinned_memory_ids:
        mem = get_memory(conn, pid)
        if mem is not None and mem["is_archived"] == 0:
            retained_pinned.append(mem)

    seen = set()
    combined = []
    for mem in fresh_memories:
        if mem["id"] not in seen:
            seen.add(mem["id"])
            combined.append(mem)
    for mem in hub_memories:
        if mem["id"] not in seen:
            seen.add(mem["id"])
            combined.append(mem)
    for mem in retained_pinned:
        if mem["id"] not in seen:
            seen.add(mem["id"])
            combined.append(mem)

    combined = combined[:config.max_surfaced_memories]

    surfaced_ids = [m["id"] for m in combined]
    if surfaced_ids:
        increment_access(conn, surfaced_ids)

    return combined
