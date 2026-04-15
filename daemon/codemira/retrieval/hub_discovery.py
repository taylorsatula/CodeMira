import sqlite3

from codemira.store.db import get_memories_by_entity, get_linked_memories


def discover_by_entities(conn: sqlite3.Connection, entity_names: list[str]) -> list[dict]:
    seen = set()
    results = []
    for name in entity_names:
        mems = get_memories_by_entity(conn, name)
        for mem in mems:
            if mem["id"] not in seen:
                seen.add(mem["id"])
                results.append(mem)
    return results


def discover_by_links(conn: sqlite3.Connection, memory_ids: list[str]) -> list[dict]:
    seen = set(memory_ids)
    results = []
    for mid in memory_ids:
        linked = get_linked_memories(conn, mid)
        for mem in linked:
            if mem["id"] not in seen:
                seen.add(mem["id"])
                results.append(mem)
    return results


def hub_discovery(conn: sqlite3.Connection, entity_names: list[str], seed_memory_ids: list[str]) -> list[dict]:
    entity_results = discover_by_entities(conn, entity_names)
    link_results = discover_by_links(conn, seed_memory_ids)
    seen = set()
    combined = []
    for mem in entity_results + link_results:
        if mem["id"] not in seen:
            seen.add(mem["id"])
            combined.append(mem)
    return combined
