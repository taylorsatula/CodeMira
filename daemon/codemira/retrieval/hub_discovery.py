import sqlite3

from codemira.store.db import get_memories_by_entity, get_linked_memories, dedupe_by


def discover_by_entities(conn: sqlite3.Connection, entity_names: list[str]) -> list[dict]:
    all_mems: list[dict] = []
    for name in entity_names:
        all_mems.extend(get_memories_by_entity(conn, name))
    return dedupe_by(all_mems)


def discover_by_links(conn: sqlite3.Connection, memory_ids: list[str]) -> list[dict]:
    seed_set = set(memory_ids)
    all_mems: list[dict] = []
    for mid in memory_ids:
        for mem in get_linked_memories(conn, mid):
            if mem["id"] not in seed_set:
                all_mems.append(mem)
    return dedupe_by(all_mems)


def hub_discovery(conn: sqlite3.Connection, entity_names: list[str], seed_memory_ids: list[str]) -> list[dict]:
    entity_results = discover_by_entities(conn, entity_names)
    link_results = discover_by_links(conn, seed_memory_ids)
    return dedupe_by(entity_results + link_results)
