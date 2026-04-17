import json
import logging
import sqlite3

from codemira.store.db import archive_memory, insert_memory, get_or_create_entity, link_memory_entity, get_entities_for_memory
from codemira.store.index import MemoryIndex
from codemira.extraction.compressor import call_ollama
from codemira.extraction.extractor import load_prompt

log = logging.getLogger(__name__)


def consolidate_cluster(
    cluster: list[str],
    conn: sqlite3.Connection,
    index: MemoryIndex,
    model: str,
    ollama_url: str = "http://localhost:11434",
    prompts_dir: str | None = None,
) -> str | None:
    from codemira.store.db import get_memory
    memories = []
    for mid in cluster:
        mem = get_memory(conn, mid)
        if mem is not None:
            memories.append(mem)
    if len(memories) < 2:
        return None
    texts = [m["text"] for m in memories]
    memory_texts = "\n".join(f"- {t}" for t in texts)
    system_prompt = load_prompt("consolidation_system", prompts_dir).render()
    user_prompt = load_prompt("consolidation_user", prompts_dir).render(memory_texts=memory_texts)
    try:
        result = call_ollama(model, system_prompt, user_prompt, ollama_url)
    except Exception:
        return None
    try:
        decision = json.loads(result)
    except json.JSONDecodeError:
        start = result.find("{")
        end = result.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                decision = json.loads(result[start:end])
            except json.JSONDecodeError:
                log.warning("Consolidation model returned unparseable JSON: %r", result[:200])
                return None
        else:
            return None
    if decision.get("decision") != "squash" or not decision.get("consolidated"):
        return None
    consolidated_text = decision["consolidated"]
    category = memories[0]["category"]
    all_entities = set()
    for m in memories:
        for e in get_entities_for_memory(conn, m["id"]):
            all_entities.add((e["name"], e["type"]))
    from codemira.embeddings import EmbeddingsProvider
    consolidated_embedding = EmbeddingsProvider.get().encode_deep([consolidated_text])[0]
    new_mid = insert_memory(conn, consolidated_text, category, consolidated_embedding)
    for name, etype in all_entities:
        eid = get_or_create_entity(conn, name, etype)
        link_memory_entity(conn, new_mid, eid)
    for mid in cluster:
        archive_memory(conn, mid)
    return new_mid


def run_consolidation(
    conn: sqlite3.Connection,
    index: MemoryIndex,
    model: str,
    similarity_threshold: float = 0.85,
    ollama_url: str = "http://localhost:11434",
    prompts_dir: str | None = None,
) -> list[str]:
    from codemira.consolidation.cluster import find_clusters
    clusters = find_clusters(conn, index, similarity_threshold)
    new_ids = []
    for cluster in clusters:
        new_mid = consolidate_cluster(cluster, conn, index, model, ollama_url, prompts_dir)
        if new_mid:
            new_ids.append(new_mid)
    if new_ids:
        index.rebuild_after_write(conn)
    return new_ids
