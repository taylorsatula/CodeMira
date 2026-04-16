import pytest
import sqlite3
import tempfile
import os
import numpy as np
from datetime import datetime, timezone

from codemira.store.db import (
    init_schema,
    open_db,
    insert_memory,
    get_memory,
    get_all_memories,
    update_memory,
    delete_memory,
    archive_memory,
    increment_access,
    insert_entity,
    get_or_create_entity,
    link_memory_entity,
    get_entities_for_memory,
    get_memories_by_entity,
    insert_memory_link,
    get_linked_memories,
    log_extraction,
    is_session_extracted,
    get_existing_memory_texts,
    embedding_to_blob,
    blob_to_embedding,
    generate_memory_id,
    upsert_arc_summary,
    get_arc_summary,
)


@pytest.fixture
def memory_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "memories.db")
        conn = open_db(db_path)
        yield conn
        conn.close()


def _make_embedding(dim=768):
    rng = np.random.default_rng(42)
    return rng.standard_normal(dim).astype(np.float32).tolist()


class TestSchemaInit:
    def test_init_creates_tables(self, memory_db):
        tables = memory_db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {r["name"] for r in tables}
        assert "memories" in table_names
        assert "entities" in table_names
        assert "memory_entities" in table_names
        assert "memory_links" in table_names
        assert "extraction_log" in table_names

    def test_init_creates_fts(self, memory_db):
        tables = memory_db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'").fetchall()
        assert len(tables) == 1

    def test_init_creates_triggers(self, memory_db):
        triggers = memory_db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        trigger_names = {r["name"] for r in triggers}
        assert "memories_fts_insert" in trigger_names
        assert "memories_fts_update" in trigger_names
        assert "memories_fts_delete" in trigger_names

    def test_wal_mode(self, memory_db):
        mode = memory_db.execute("PRAGMA journal_mode").fetchone()["journal_mode"]
        assert mode == "wal"

    def test_foreign_keys_on(self, memory_db):
        fk = memory_db.execute("PRAGMA foreign_keys").fetchone()["foreign_keys"]
        assert fk == 1


class TestMemoryCRUD:
    def test_insert_and_get(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Prefers threading over asyncio", 0.9, "rejected_alternative", emb, "ses_123")
        mem = get_memory(memory_db, mid)
        assert mem is not None
        assert mem["text"] == "Prefers threading over asyncio"
        assert mem["importance"] == 0.9
        assert mem["category"] == "rejected_alternative"
        assert mem["source_session_id"] == "ses_123"
        assert len(mem["embedding"]) == 768
        assert mem["is_archived"] == 0
        assert mem["access_count"] == 0

    def test_insert_without_session(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Some memory", 0.5, "priority", emb)
        mem = get_memory(memory_db, mid)
        assert mem["source_session_id"] is None

    def test_get_nonexistent(self, memory_db):
        assert get_memory(memory_db, "nonexistent") is None

    def test_get_all_memories(self, memory_db):
        emb = _make_embedding()
        insert_memory(memory_db, "Memory 1", 0.5, "priority", emb)
        insert_memory(memory_db, "Memory 2", 0.6, "decision_rationale", emb)
        all_mems = get_all_memories(memory_db)
        assert len(all_mems) == 2

    def test_get_all_excludes_archived(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Memory 1", 0.5, "priority", emb)
        insert_memory(memory_db, "Memory 2", 0.6, "decision_rationale", emb)
        archive_memory(memory_db, mid)
        active = get_all_memories(memory_db)
        assert len(active) == 1
        assert active[0]["text"] == "Memory 2"

    def test_get_all_includes_archived(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Memory 1", 0.5, "priority", emb)
        archive_memory(memory_db, mid)
        all_mems = get_all_memories(memory_db, include_archived=True)
        assert len(all_mems) == 1

    def test_update_memory_text(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Original", 0.5, "priority", emb)
        update_memory(memory_db, mid, text="Updated")
        mem = get_memory(memory_db, mid)
        assert mem["text"] == "Updated"

    def test_update_memory_importance(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Memory", 0.5, "priority", emb)
        update_memory(memory_db, mid, importance=0.8)
        mem = get_memory(memory_db, mid)
        assert mem["importance"] == 0.8

    def test_update_memory_disallowed_field(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Memory", 0.5, "priority", emb)
        with pytest.raises(ValueError, match="Cannot update field"):
            update_memory(memory_db, mid, id="new_id")

    def test_update_nonexistent_memory(self, memory_db):
        emb = _make_embedding()
        insert_memory(memory_db, "Real memory", 0.5, "priority", emb)
        assert update_memory(memory_db, "nonexistent", text="nope") is False

    def test_delete_memory(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Memory", 0.5, "priority", emb)
        assert delete_memory(memory_db, mid) is True
        assert get_memory(memory_db, mid) is None

    def test_delete_nonexistent_memory(self, memory_db):
        emb = _make_embedding()
        insert_memory(memory_db, "Real memory", 0.5, "priority", emb)
        assert delete_memory(memory_db, "nonexistent") is False

    def test_delete_memory_cascades_entities(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Memory", 0.5, "priority", emb)
        eid = get_or_create_entity(memory_db, "pytest", "tool")
        link_memory_entity(memory_db, mid, eid)
        delete_memory(memory_db, mid)
        entities = get_entities_for_memory(memory_db, mid)
        assert len(entities) == 0

    def test_archive_memory(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Memory", 0.5, "priority", emb)
        archive_memory(memory_db, mid)
        mem = get_memory(memory_db, mid)
        assert mem["is_archived"] == 1
        assert mem["archived_at"] is not None

    def test_increment_access(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Memory", 0.5, "priority", emb)
        increment_access(memory_db, [mid])
        mem = get_memory(memory_db, mid)
        assert mem["access_count"] == 1
        assert mem["last_accessed_at"] is not None

    def test_increment_access_multiple(self, memory_db):
        emb = _make_embedding()
        mid1 = insert_memory(memory_db, "Memory 1", 0.5, "priority", emb)
        mid2 = insert_memory(memory_db, "Memory 2", 0.5, "priority", emb)
        increment_access(memory_db, [mid1, mid2])
        m1 = get_memory(memory_db, mid1)
        m2 = get_memory(memory_db, mid2)
        assert m1["access_count"] == 1
        assert m2["access_count"] == 1


class TestEntities:
    def test_insert_entity(self, memory_db):
        eid = insert_entity(memory_db, "pytest", "tool")
        row = memory_db.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
        assert row["name"] == "pytest"
        assert row["type"] == "tool"

    def test_get_or_create_new(self, memory_db):
        eid = get_or_create_entity(memory_db, "fastapi", "framework")
        assert eid is not None
        row = memory_db.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
        assert row["name"] == "fastapi"
        assert row["type"] == "framework"
        row = memory_db.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
        assert row["name"] == "fastapi"

    def test_get_or_create_existing(self, memory_db):
        eid1 = get_or_create_entity(memory_db, "pytest", "tool")
        eid2 = get_or_create_entity(memory_db, "pytest", "tool")
        assert eid1 == eid2

    def test_link_memory_entity(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Memory", 0.5, "priority", emb)
        eid = get_or_create_entity(memory_db, "docker", "tool")
        link_memory_entity(memory_db, mid, eid)
        entities = get_entities_for_memory(memory_db, mid)
        assert len(entities) == 1
        assert entities[0]["name"] == "docker"

    def test_get_memories_by_entity(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Uses Docker for deployment", 0.7, "decision_rationale", emb)
        eid = get_or_create_entity(memory_db, "docker", "tool")
        link_memory_entity(memory_db, mid, eid)
        mems = get_memories_by_entity(memory_db, "docker")
        assert len(mems) == 1
        assert mems[0]["text"] == "Uses Docker for deployment"


class TestMemoryLinks:
    def test_insert_memory_link(self, memory_db):
        emb = _make_embedding()
        mid1 = insert_memory(memory_db, "Memory 1", 0.5, "priority", emb)
        mid2 = insert_memory(memory_db, "Memory 2", 0.6, "priority", emb)
        insert_memory_link(memory_db, mid1, mid2, "corroborates", "Same preference")
        linked = get_linked_memories(memory_db, mid1)
        assert len(linked) == 1
        assert linked[0]["link_type"] == "corroborates"

    def test_insert_memory_link_idempotent(self, memory_db):
        emb = _make_embedding()
        mid1 = insert_memory(memory_db, "Memory 1", 0.5, "priority", emb)
        mid2 = insert_memory(memory_db, "Memory 2", 0.6, "priority", emb)
        insert_memory_link(memory_db, mid1, mid2, "corroborates")
        insert_memory_link(memory_db, mid1, mid2, "corroborates")
        linked = get_linked_memories(memory_db, mid1)
        assert len(linked) == 1


class TestExtractionLog:
    def test_log_extraction(self, memory_db):
        log_extraction(memory_db, "ses_abc123", 5)
        assert is_session_extracted(memory_db, "ses_abc123") is True

    def test_session_not_extracted(self, memory_db):
        assert is_session_extracted(memory_db, "ses_nonexistent") is False

    def test_log_extraction_upsert(self, memory_db):
        log_extraction(memory_db, "ses_abc123", 3)
        log_extraction(memory_db, "ses_abc123", 5)
        row = memory_db.execute("SELECT * FROM extraction_log WHERE session_id = ?", ("ses_abc123",)).fetchone()
        assert row["memory_count"] == 5


class TestFTS5Sync:
    def test_fts_insert_syncs(self, memory_db):
        emb = _make_embedding()
        insert_memory(memory_db, "Prefers threading over asyncio", 0.9, "rejected_alternative", emb)
        results = memory_db.execute("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'threading'").fetchall()
        assert len(results) == 1

    def test_fts_update_syncs(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Original text about dogs", 0.5, "priority", emb)
        update_memory(memory_db, mid, text="Updated text about cats")
        results = memory_db.execute("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'cats'").fetchall()
        assert len(results) == 1
        results_dogs = memory_db.execute("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'dogs'").fetchall()
        assert len(results_dogs) == 0

    def test_fts_delete_syncs(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Memory about pytest", 0.5, "priority", emb)
        delete_memory(memory_db, mid)
        results = memory_db.execute("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'pytest'").fetchall()
        assert len(results) == 0


class TestEmbeddingConversion:
    def test_embedding_to_blob_roundtrip(self):
        emb = [0.1, 0.2, 0.3, 0.4]
        blob = embedding_to_blob(emb)
        result = blob_to_embedding(blob)
        assert len(result) == 4
        for a, b in zip(emb, result):
            assert abs(a - b) < 1e-6

    def test_generate_memory_id(self):
        id1 = generate_memory_id()
        id2 = generate_memory_id()
        assert len(id1) == 8
        assert id1 != id2


class TestGetExistingMemoryTexts:
    def test_get_existing_texts(self, memory_db):
        emb = _make_embedding()
        insert_memory(memory_db, "Memory A", 0.5, "priority", emb)
        insert_memory(memory_db, "Memory B", 0.5, "priority", emb)
        texts = get_existing_memory_texts(memory_db)
        assert "Memory A" in texts
        assert "Memory B" in texts

    def test_get_existing_excludes_archived(self, memory_db):
        emb = _make_embedding()
        mid = insert_memory(memory_db, "Archived memory", 0.5, "priority", emb)
        insert_memory(memory_db, "Active memory", 0.5, "priority", emb)
        archive_memory(memory_db, mid)
        texts = get_existing_memory_texts(memory_db)
        assert "Archived memory" not in texts
        assert "Active memory" in texts


class TestArcSummaries:
    def test_upsert_and_get(self, memory_db):
        upsert_arc_summary(memory_db, "ses_abc", "[START] Goal: Fix bug\n └─ [CURRENT] Editing file", 10)
        arc = get_arc_summary(memory_db, "ses_abc")
        assert arc is not None
        assert arc["session_id"] == "ses_abc"
        assert arc["topology"] == "[START] Goal: Fix bug\n └─ [CURRENT] Editing file"
        assert arc["message_count"] == 10
        assert arc["generated_at"] is not None

    def test_get_nonexistent(self, memory_db):
        assert get_arc_summary(memory_db, "ses_nonexistent") is None

    def test_upsert_overwrites(self, memory_db):
        upsert_arc_summary(memory_db, "ses_abc", "[START] Old topology", 5)
        upsert_arc_summary(memory_db, "ses_abc", "[START] New topology", 12)
        arc = get_arc_summary(memory_db, "ses_abc")
        assert arc["topology"] == "[START] New topology"
        assert arc["message_count"] == 12
