import sqlite3
import struct
import uuid
from datetime import datetime, timezone


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    category TEXT NOT NULL,
    embedding BLOB,
    source_session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    access_count INTEGER DEFAULT 0,
    last_accessed_at TEXT,
    is_archived INTEGER DEFAULT 0,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id TEXT REFERENCES memories(id),
    entity_id TEXT REFERENCES entities(id),
    PRIMARY KEY (memory_id, entity_id)
);

CREATE TABLE IF NOT EXISTS memory_links (
    memory_id TEXT REFERENCES memories(id),
    linked_memory_id TEXT REFERENCES memories(id),
    link_type TEXT NOT NULL,
    reasoning TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, linked_memory_id)
);

CREATE TABLE IF NOT EXISTS extraction_log (
    session_id TEXT PRIMARY KEY,
    extracted_at TEXT NOT NULL,
    memory_count INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    is_complete INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS arc_fragments (
    session_id TEXT,
    fragment_index INTEGER,
    topology TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, fragment_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, content='memories', content_rowid='rowid');

CREATE TRIGGER IF NOT EXISTS memories_fts_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_update AFTER UPDATE OF text ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.rowid, old.text);
    INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_delete AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;
"""


def generate_memory_id() -> str:
    return uuid.uuid4().hex[:8]


def init_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def embedding_to_blob(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def blob_to_embedding(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def insert_memory(
    conn: sqlite3.Connection,
    text: str,
    category: str,
    embedding: list[float],
    source_session_id: str | None = None,
) -> str:
    memory_id = generate_memory_id()
    now = datetime.now(timezone.utc).isoformat()
    blob = embedding_to_blob(embedding)
    conn.execute(
        "INSERT INTO memories (id, text, category, embedding, source_session_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (memory_id, text, category, blob, source_session_id, now, now),
    )
    conn.commit()
    return memory_id


def get_memory(conn: sqlite3.Connection, memory_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    if d["embedding"] is not None:
        d["embedding"] = blob_to_embedding(d["embedding"])
    return d


def get_all_memories(conn: sqlite3.Connection, include_archived: bool = False) -> list[dict]:
    if include_archived:
        rows = conn.execute("SELECT * FROM memories").fetchall()
    else:
        rows = conn.execute("SELECT * FROM memories WHERE is_archived = 0").fetchall()
    results = []
    for row in rows:
        d = dict(row)
        if d["embedding"] is not None:
            d["embedding"] = blob_to_embedding(d["embedding"])
        results.append(d)
    return results


def update_memory(conn: sqlite3.Connection, memory_id: str, **kwargs) -> bool:
    allowed = {"text", "category", "embedding", "access_count", "last_accessed_at", "is_archived", "archived_at"}
    updates = {}
    for key, value in kwargs.items():
        if key not in allowed:
            raise ValueError(f"Cannot update field: {key}")
        if key == "embedding" and value is not None:
            value = embedding_to_blob(value)
        updates[key] = value
    if not updates:
        return False
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [memory_id]
    cursor = conn.execute(f"UPDATE memories SET {set_clause} WHERE id = ?", values)
    conn.commit()
    return cursor.rowcount > 0


def delete_memory(conn: sqlite3.Connection, memory_id: str) -> bool:
    conn.execute("DELETE FROM memory_entities WHERE memory_id = ?", (memory_id,))
    conn.execute("DELETE FROM memory_links WHERE memory_id = ? OR linked_memory_id = ?", (memory_id, memory_id))
    cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()
    return cursor.rowcount > 0


def archive_memory(conn: sqlite3.Connection, memory_id: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    return update_memory(conn, memory_id, is_archived=1, archived_at=now)


def increment_access(conn: sqlite3.Connection, memory_ids: list[str]):
    now = datetime.now(timezone.utc).isoformat()
    for mid in memory_ids:
        conn.execute(
            "UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?",
            (now, mid),
        )
    conn.commit()


def insert_entity(conn: sqlite3.Connection, name: str, entity_type: str) -> str:
    entity_id = generate_memory_id()
    conn.execute("INSERT INTO entities (id, name, type) VALUES (?, ?, ?)", (entity_id, name, entity_type))
    conn.commit()
    return entity_id


def get_or_create_entity(conn: sqlite3.Connection, name: str, entity_type: str) -> str:
    row = conn.execute("SELECT id FROM entities WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    return insert_entity(conn, name, entity_type)


def link_memory_entity(conn: sqlite3.Connection, memory_id: str, entity_id: str):
    conn.execute("INSERT OR IGNORE INTO memory_entities (memory_id, entity_id) VALUES (?, ?)", (memory_id, entity_id))
    conn.commit()


def get_entities_for_memory(conn: sqlite3.Connection, memory_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT e.* FROM entities e JOIN memory_entities me ON e.id = me.entity_id WHERE me.memory_id = ?",
        (memory_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_memories_by_entity(conn: sqlite3.Connection, entity_name: str) -> list[dict]:
    rows = conn.execute(
        "SELECT m.* FROM memories m JOIN memory_entities me ON m.id = me.memory_id JOIN entities e ON e.id = me.entity_id WHERE e.name = ? AND m.is_archived = 0",
        (entity_name,),
    ).fetchall()
    results = []
    for row in rows:
        d = dict(row)
        if d["embedding"] is not None:
            d["embedding"] = blob_to_embedding(d["embedding"])
        results.append(d)
    return results


def insert_memory_link(
    conn: sqlite3.Connection,
    memory_id: str,
    linked_memory_id: str,
    link_type: str,
    reasoning: str | None = None,
):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO memory_links (memory_id, linked_memory_id, link_type, reasoning, created_at) VALUES (?, ?, ?, ?, ?)",
        (memory_id, linked_memory_id, link_type, reasoning, now),
    )
    conn.commit()


def get_linked_memories(conn: sqlite3.Connection, memory_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT m.*, ml.link_type, ml.reasoning FROM memories m JOIN memory_links ml ON m.id = ml.linked_memory_id WHERE ml.memory_id = ? AND m.is_archived = 0",
        (memory_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def log_extraction(conn: sqlite3.Connection, session_id: str, memory_count: int, is_complete: bool = True) -> int:
    now = datetime.now(timezone.utc).isoformat()
    complete_flag = 1 if is_complete else 0
    conn.execute(
        "INSERT INTO extraction_log (session_id, extracted_at, memory_count, attempt_count, is_complete) "
        "VALUES (?, ?, ?, 1, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET "
        "extracted_at = excluded.extracted_at, "
        "memory_count = excluded.memory_count, "
        "attempt_count = attempt_count + 1, "
        "is_complete = excluded.is_complete",
        (session_id, now, memory_count, complete_flag),
    )
    conn.commit()
    row = conn.execute("SELECT attempt_count FROM extraction_log WHERE session_id = ?", (session_id,)).fetchone()
    return row["attempt_count"]


def mark_extraction_complete(conn: sqlite3.Connection, session_id: str):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE extraction_log SET is_complete = 1, extracted_at = ? WHERE session_id = ?",
        (now, session_id),
    )
    conn.commit()


def is_session_extracted(conn: sqlite3.Connection, session_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM extraction_log WHERE session_id = ? AND is_complete = 1", (session_id,)).fetchone()
    return row is not None


def get_existing_memory_texts(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT text FROM memories WHERE is_archived = 0").fetchall()
    return [r["text"] for r in rows]


def upsert_arc_fragment(conn: sqlite3.Connection, session_id: str, fragment_index: int, topology: str, content_hash: str, message_count: int):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO arc_fragments (session_id, fragment_index, topology, content_hash, message_count, generated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(session_id, fragment_index) DO UPDATE SET "
        "topology = excluded.topology, "
        "content_hash = excluded.content_hash, "
        "message_count = excluded.message_count, "
        "generated_at = excluded.generated_at",
        (session_id, fragment_index, topology, content_hash, message_count, now),
    )
    conn.commit()


def get_arc_fragments(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM arc_fragments WHERE session_id = ? ORDER BY fragment_index ASC",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_arc_fragments_from(conn: sqlite3.Connection, session_id: str, from_index: int):
    conn.execute(
        "DELETE FROM arc_fragments WHERE session_id = ? AND fragment_index >= ?",
        (session_id, from_index),
    )
    conn.commit()


def get_arc_summary(conn: sqlite3.Connection, session_id: str) -> dict | None:
    rows = conn.execute(
        "SELECT * FROM arc_fragments WHERE session_id = ? ORDER BY fragment_index ASC",
        (session_id,),
    ).fetchall()
    if not rows:
        return None
    full_topology = "\n".join(r["topology"] for r in rows)
    last_row = rows[-1]
    return {
        "topology": full_topology,
        "message_count": last_row["message_count"],
        "generated_at": last_row["generated_at"]
    }
