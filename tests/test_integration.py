import json
import os
import sqlite3
import tempfile
import time
import threading
import urllib.request

import numpy as np
import pytest

from tests.conftest import _make_embedding, _make_embeddings, skip_no_ollama


OLLAMA_URL = "http://localhost:11434"
ENTITY_MODEL = "gemma4:e2b"


OPENCODE_SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
    id TEXT PRIMARY KEY,
    worktree TEXT NOT NULL,
    vcs TEXT,
    name TEXT,
    sandboxes TEXT NOT NULL DEFAULT '[]',
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES project(id),
    workspace_id TEXT,
    parent_id TEXT,
    slug TEXT NOT NULL,
    directory TEXT NOT NULL,
    title TEXT NOT NULL,
    version TEXT NOT NULL,
    share_url TEXT,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    time_compacting INTEGER,
    time_archived INTEGER
);

CREATE TABLE IF NOT EXISTS message (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES session(id) ON DELETE CASCADE,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS part (
    id TEXT PRIMARY KEY,
    message_id TEXT REFERENCES message(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
"""


def _create_opencode_db(tmpdir: str, project_worktree: str = "/tmp/test") -> tuple[sqlite3.Connection, str]:
    os.makedirs(project_worktree, exist_ok=True)
    db_path = os.path.join(tmpdir, "opencode.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(OPENCODE_SCHEMA)
    now = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO project (id, worktree, name, sandboxes, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?)",
        ("proj_1", project_worktree, "test-project", "[]", now, now),
    )
    conn.commit()
    return conn, db_path


def _insert_session(conn: sqlite3.Connection, session_id: str, time_updated_ms: int):
    conn.execute(
        "INSERT INTO session (id, project_id, slug, directory, title, version, time_created, time_updated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, "proj_1", "test-slug", "/tmp/test", "Test Session", "1", time_updated_ms - 1000, time_updated_ms),
    )
    conn.commit()


def _insert_user_message(conn: sqlite3.Connection, msg_id: str, session_id: str, text: str, created_ms: int):
    data = json.dumps({
        "role": "user",
        "time": {"created": created_ms},
        "agent": "code",
        "model": {"providerID": "anthropic", "modelID": "claude-sonnet-4-20250514"},
    })
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        (msg_id, session_id, created_ms, created_ms, data),
    )
    part_data = json.dumps({"type": "text", "text": text, "synthetic": False})
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id + "_p1", msg_id, session_id, created_ms, created_ms, part_data),
    )
    conn.commit()


def _insert_assistant_message_with_tool(
    conn: sqlite3.Connection, msg_id: str, session_id: str, text: str,
    tool_name: str, tool_input: dict, tool_output: str, created_ms: int,
):
    data = json.dumps({
        "role": "assistant",
        "time": {"created": created_ms, "completed": created_ms + 1000},
        "parentID": msg_id.replace("asst", "user"),
        "modelID": "claude-sonnet-4-20250514",
        "providerID": "anthropic",
        "mode": "code",
        "agent": "code",
        "path": {"cwd": "/tmp/test", "root": "/tmp/test"},
        "cost": 0.001,
        "tokens": {"input": 100, "output": 50, "reasoning": 0, "cache": {"read": 0, "write": 0}},
    })
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        (msg_id, session_id, created_ms, created_ms, data),
    )
    text_part_data = json.dumps({"type": "text", "text": text})
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id + "_p1", msg_id, session_id, created_ms, created_ms, text_part_data),
    )
    tool_part_data = json.dumps({
        "type": "tool",
        "callID": msg_id + "_call",
        "tool": tool_name,
        "state": {
            "status": "completed",
            "input": tool_input,
            "output": tool_output,
            "title": f"Ran {tool_name}",
            "metadata": {},
            "time": {"start": created_ms, "end": created_ms + 500},
        },
    })
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id + "_p2", msg_id, session_id, created_ms, created_ms, tool_part_data),
    )
    conn.commit()


@pytest.fixture
def opencode_setup():
    with tempfile.TemporaryDirectory() as tmpdir:
        opencode_conn, opencode_db_path = _create_opencode_db(tmpdir)
        memory_db_path = os.path.join(tmpdir, "memories.db")
        from codemira.store.db import open_db
        memory_conn = open_db(memory_db_path)
        yield opencode_conn, memory_conn, tmpdir, opencode_db_path
        opencode_conn.close()
        memory_conn.close()


class TestFindIdleSessions:
    def test_finds_idle_unextracted_session(self, opencode_setup):
        from codemira.opencode_db import find_idle_sessions
        opencode_conn, memory_conn, _, _ = opencode_setup
        old_ts = int((time.time() - 120) * 1000)
        _insert_session(opencode_conn, "ses_idle_1", old_ts)
        _insert_user_message(opencode_conn, "msg_1", "ses_idle_1", "Hello", old_ts)
        _insert_user_message(opencode_conn, "msg_2", "ses_idle_1", "Help me code", old_ts + 100)
        _insert_assistant_message_with_tool(
            opencode_conn, "asst_1", "ses_idle_1", "Sure!",
            "bash", {"command": "ls"}, "file1.txt\nfile2.txt", old_ts + 200,
        )
        _insert_assistant_message_with_tool(
            opencode_conn, "asst_2", "ses_idle_1", "Done!",
            "read", {"path": "file1.txt"}, "contents of file1", old_ts + 300,
        )
        sessions = find_idle_sessions(opencode_conn, set(), idle_threshold_minutes=1, min_messages=4)
        assert len(sessions) == 1
        assert sessions[0]["id"] == "ses_idle_1"

    def test_extracts_already_extracted_sessions(self, opencode_setup):
        from codemira.opencode_db import find_idle_sessions
        opencode_conn, memory_conn, _, _ = opencode_setup
        old_ts = int((time.time() - 120) * 1000)
        _insert_session(opencode_conn, "ses_extracted", old_ts)
        for i in range(4):
            _insert_user_message(opencode_conn, f"msg_ext_{i}", "ses_extracted", f"Message {i}", old_ts + i * 100)
        sessions = find_idle_sessions(opencode_conn, {"ses_extracted"}, idle_threshold_minutes=1, min_messages=4)
        assert len(sessions) == 0

    def test_excludes_recent_sessions(self, opencode_setup):
        from codemira.opencode_db import find_idle_sessions
        opencode_conn, memory_conn, _, _ = opencode_setup
        recent_ts = int(time.time() * 1000)
        _insert_session(opencode_conn, "ses_recent", recent_ts)
        for i in range(4):
            _insert_user_message(opencode_conn, f"msg_rec_{i}", "ses_recent", f"Message {i}", recent_ts + i * 100)
        sessions = find_idle_sessions(opencode_conn, set(), idle_threshold_minutes=60, min_messages=4)
        assert len(sessions) == 0

    def test_excludes_sessions_below_min_messages(self, opencode_setup):
        from codemira.opencode_db import find_idle_sessions
        opencode_conn, memory_conn, _, _ = opencode_setup
        old_ts = int((time.time() - 120) * 1000)
        _insert_session(opencode_conn, "ses_sparse", old_ts)
        _insert_user_message(opencode_conn, "msg_sparse_1", "ses_sparse", "Hello", old_ts)
        sessions = find_idle_sessions(opencode_conn, set(), idle_threshold_minutes=1, min_messages=4)
        assert len(sessions) == 0


class TestReadSessionConversation:
    def test_reads_user_and_assistant_messages(self, opencode_setup):
        from codemira.opencode_db import read_session_conversation
        opencode_conn, _, _, _ = opencode_setup
        old_ts = int((time.time() - 120) * 1000)
        _insert_session(opencode_conn, "ses_conv_1", old_ts)
        _insert_user_message(opencode_conn, "msg_u1", "ses_conv_1", "Hello world", old_ts)
        _insert_assistant_message_with_tool(
            opencode_conn, "asst_a1", "ses_conv_1", "I can help!",
            "bash", {"command": "echo hi"}, "hi", old_ts + 1000,
        )
        messages = read_session_conversation(opencode_conn, "ses_conv_1")
        assert len(messages) == 2
        roles = [m["role"] for m in messages]
        assert roles == ["user", "assistant"], f"Expected ordered roles, got {roles}"

    def test_user_message_has_text_parts(self, opencode_setup):
        from codemira.opencode_db import read_session_conversation
        opencode_conn, _, _, _ = opencode_setup
        old_ts = int((time.time() - 120) * 1000)
        _insert_session(opencode_conn, "ses_conv_2", old_ts)
        _insert_user_message(opencode_conn, "msg_u2", "ses_conv_2", "Build a REST API", old_ts)
        messages = read_session_conversation(opencode_conn, "ses_conv_2")
        user_msg = [m for m in messages if m["role"] == "user"][0]
        text_parts = [p for p in user_msg["parts"] if p["type"] == "text"]
        assert len(text_parts) >= 1
        assert text_parts[0]["text"] == "Build a REST API"

    def test_assistant_message_has_tool_parts(self, opencode_setup):
        from codemira.opencode_db import read_session_conversation
        opencode_conn, _, _, _ = opencode_setup
        old_ts = int((time.time() - 120) * 1000)
        _insert_session(opencode_conn, "ses_conv_3", old_ts)
        _insert_assistant_message_with_tool(
            opencode_conn, "asst_a3", "ses_conv_3", "Running tests",
            "bash", {"command": "pytest"}, "3 passed", old_ts,
        )
        messages = read_session_conversation(opencode_conn, "ses_conv_3")
        asst_msg = [m for m in messages if m["role"] == "assistant"][0]
        tool_parts = [p for p in asst_msg["parts"] if p["type"] == "tool"]
        assert len(tool_parts) >= 1
        assert tool_parts[0]["tool"] == "bash"
        assert tool_parts[0]["state"]["status"] == "completed"

    def test_empty_session(self, opencode_setup):
        from codemira.opencode_db import read_session_conversation
        opencode_conn, _, _, _ = opencode_setup
        old_ts = int((time.time() - 120) * 1000)
        _insert_session(opencode_conn, "ses_empty", old_ts)
        messages = read_session_conversation(opencode_conn, "ses_empty")
        assert len(messages) == 0


class TestDiscoverOpencodeDb:
    def test_uses_override(self, opencode_setup):
        from codemira.opencode_db import discover_opencode_db
        _, _, tmpdir, opencode_db_path = opencode_setup
        assert discover_opencode_db(opencode_db_path) == opencode_db_path

    def test_uses_env_var(self, opencode_setup):
        from codemira.opencode_db import discover_opencode_db
        _, _, tmpdir, _ = opencode_setup
        fake_path = os.path.join(tmpdir, "env.db")
        with open(fake_path, "w") as f:
            f.write("")
        old_env = os.environ.get("OPENCODE_DB")
        try:
            os.environ["OPENCODE_DB"] = fake_path
            assert discover_opencode_db(None) == fake_path
        finally:
            if old_env is not None:
                os.environ["OPENCODE_DB"] = old_env
            else:
                os.environ.pop("OPENCODE_DB", None)

    def test_override_takes_precedence_over_env(self, opencode_setup):
        from codemira.opencode_db import discover_opencode_db
        _, _, tmpdir, opencode_db_path = opencode_setup
        old_env = os.environ.get("OPENCODE_DB")
        try:
            os.environ["OPENCODE_DB"] = "/nonexistent"
            assert discover_opencode_db(opencode_db_path) == opencode_db_path
        finally:
            if old_env is not None:
                os.environ["OPENCODE_DB"] = old_env
            else:
                os.environ.pop("OPENCODE_DB", None)

    def test_raises_when_no_real_db_and_no_env(self, monkeypatch):
        from codemira.opencode_db import discover_opencode_db, MACOS_DB_PATH, LINUX_DB_PATH
        monkeypatch.delenv("OPENCODE_DB", raising=False)
        if not os.path.exists(MACOS_DB_PATH) and not os.path.exists(LINUX_DB_PATH):
            with pytest.raises(FileNotFoundError, match="Cannot find OpenCode database"):
                discover_opencode_db("/nonexistent/path/opencode.db")
        else:
            pytest.skip("Real OpenCode DB exists on this machine")


class TestDaemonStartup:
    def test_creates_project_store_on_first_get(self, opencode_setup):
        _, _, tmpdir, _ = opencode_setup
        project_dir = os.path.join(tmpdir, "some_project")
        os.makedirs(project_dir, exist_ok=True)
        from codemira.config import DaemonConfig
        from codemira.store.manager import StoreManager, project_store_paths
        manager = StoreManager(DaemonConfig())
        conn, idx = manager.get(project_dir)
        _, db_path, _ = project_store_paths(project_dir)
        assert os.path.exists(db_path)
        assert os.path.getsize(db_path) > 0, "DB file should not be empty"
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert "memories" in {r["name"] for r in tables}
        manager.close_all()

    def test_store_manager_scopes_by_project(self, opencode_setup):
        _, _, tmpdir, _ = opencode_setup
        from codemira.config import DaemonConfig
        from codemira.store.manager import StoreManager
        from codemira.store.db import insert_memory, get_all_memories
        proj_a = os.path.join(tmpdir, "proj_a")
        proj_b = os.path.join(tmpdir, "proj_b")
        os.makedirs(proj_a, exist_ok=True)
        os.makedirs(proj_b, exist_ok=True)
        manager = StoreManager(DaemonConfig())
        conn_a, _ = manager.get(proj_a)
        conn_b, _ = manager.get(proj_b)
        insert_memory(conn_a, "proj A memory", 0.5, "priority", _make_embedding(seed=1))
        insert_memory(conn_b, "proj B memory", 0.5, "priority", _make_embedding(seed=2))
        a_mems = [m["text"] for m in get_all_memories(conn_a)]
        b_mems = [m["text"] for m in get_all_memories(conn_b)]
        assert a_mems == ["proj A memory"]
        assert b_mems == ["proj B memory"]
        manager.close_all()

    def test_server_starts_and_serves_health(self, opencode_setup):
        _, _, tmpdir, _ = opencode_setup
        from codemira.config import DaemonConfig
        from codemira.server import create_server
        from codemira.store.manager import StoreManager
        manager = StoreManager(DaemonConfig())
        config = DaemonConfig(http_port=0)
        server = create_server(manager, config, port=0)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        time.sleep(0.1)
        try:
            resp = urllib.request.urlopen(f"http://localhost:{port}/health")
            data = json.loads(resp.read())
            assert data["status"] == "ok"
            assert "version" in data
        finally:
            server.shutdown()
            manager.close_all()


class TestExtractionToRetrievalPipeline:
    @skip_no_ollama
    def test_store_extracted_memory_then_retrieve(self, opencode_setup):
        _, memory_conn, tmpdir, _ = opencode_setup
        from codemira.store.db import insert_memory, get_or_create_entity, link_memory_entity
        from codemira.store.index import MemoryIndex
        from codemira.retrieval.proactive import retrieve
        from codemira.config import DaemonConfig
        from codemira.extraction.dedup import extract_entities
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
        emb = _make_embedding(seed=42)
        mid = insert_memory(memory_conn, "Uses FastAPI for REST endpoints", 0.8, "decision_rationale", emb, "ses_test")
        entities = extract_entities(
            "Uses FastAPI for REST endpoints", ENTITY_MODEL, OLLAMA_URL, prompts_dir,
        )
        for entity in entities:
            eid = get_or_create_entity(memory_conn, entity["name"], entity["type"])
            link_memory_entity(memory_conn, mid, eid)
        index_path = os.path.join(tmpdir, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir, "memories.db"), index_path)
        mi.build_from_db(memory_conn)
        config = DaemonConfig()
        results = retrieve(
            query_expansion="FastAPI REST API",
            entities=["fastapi"],
            pinned_memory_ids=[],
            project_dir="/tmp",
            conn=memory_conn,
            index=mi,
            config=config,
            query_embedding=emb,
        )
        assert len(results) >= 1
        result_ids = [r["id"] for r in results]
        assert mid in result_ids

    def test_store_multiple_and_retrieve_with_hub(self, opencode_setup):
        _, memory_conn, tmpdir, _ = opencode_setup
        from codemira.store.db import insert_memory, get_or_create_entity, link_memory_entity, insert_memory_link
        from codemira.store.index import MemoryIndex
        from codemira.retrieval.proactive import retrieve
        from codemira.config import DaemonConfig
        embs = _make_embeddings(3)
        mid1 = insert_memory(memory_conn, "Uses FastAPI for backend", 0.7, "decision_rationale", embs[0], "ses_a")
        mid2 = insert_memory(memory_conn, "Prefers FastAPI over Flask", 0.6, "rejected_alternative", embs[1], "ses_a")
        mid3 = insert_memory(memory_conn, "Deploy with Docker on AWS", 0.5, "priority", embs[2], "ses_a")
        eid = get_or_create_entity(memory_conn, "fastapi", "framework")
        link_memory_entity(memory_conn, mid1, eid)
        link_memory_entity(memory_conn, mid2, eid)
        insert_memory_link(memory_conn, mid1, mid2, "corroborates", "Same framework choice")
        index_path = os.path.join(tmpdir, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir, "memories.db"), index_path)
        mi.build_from_db(memory_conn)
        config = DaemonConfig()
        config.max_surfaced_memories = 8
        results = retrieve(
            query_expansion="FastAPI framework",
            entities=["fastapi"],
            pinned_memory_ids=[mid1],
            project_dir="/tmp",
            conn=memory_conn,
            index=mi,
            config=config,
            query_embedding=embs[0],
        )
        result_ids = [r["id"] for r in results]
        assert mid1 in result_ids
        assert mid2 in result_ids

    def test_retrieve_through_http_after_store(self, opencode_setup):
        _, _, tmpdir, _ = opencode_setup
        from codemira.store.db import insert_memory
        from codemira.store.manager import StoreManager
        from codemira.config import DaemonConfig
        from codemira.server import create_server
        project_dir = os.path.join(tmpdir, "proj_http")
        os.makedirs(project_dir, exist_ok=True)
        manager = StoreManager(DaemonConfig())
        conn, _ = manager.get(project_dir)
        emb = _make_embedding(seed=42)
        mid = insert_memory(conn, "Prefers threading over asyncio", 0.8, "priority", emb, "ses_int")
        conn2, idx = manager.get(project_dir)
        idx.rebuild_after_write(conn2)
        config = DaemonConfig(http_port=0)
        server = create_server(manager, config, port=0)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        time.sleep(0.1)
        try:
            payload = json.dumps({
                "query_expansion": "threading asyncio concurrency",
                "entities": [],
                "pinned_memory_ids": [],
                "project_dir": project_dir,
                "query_embedding": emb,
            }).encode()
            req = urllib.request.Request(
                f"http://localhost:{port}/retrieve",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req)
            data = json.loads(resp.read())
            assert len(data["memories"]) >= 1
            assert data["degraded"] is False
            returned_ids = [m["id"] for m in data["memories"]]
            assert mid in returned_ids
        finally:
            server.shutdown()
            manager.close_all()


class TestDedupDuringStore:
    def test_vector_dedup_prevents_duplicate_insert(self, opencode_setup):
        _, memory_conn, tmpdir, _ = opencode_setup
        from codemira.store.db import insert_memory
        from codemira.store.index import MemoryIndex
        from codemira.extraction.dedup import is_duplicate_vector
        emb = _make_embedding(seed=42)
        mid = insert_memory(memory_conn, "Prefers threading over asyncio", 0.8, "priority", emb, "ses_dedup")
        index_path = os.path.join(tmpdir, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir, "memories.db"), index_path)
        mi.build_from_db(memory_conn)
        assert is_duplicate_vector(emb, mi, 0.92) is True
        different_emb = _make_embedding(seed=99)
        assert is_duplicate_vector(different_emb, mi, 0.92) is False

    def test_text_dedup_catches_near_duplicates(self):
        from codemira.extraction.dedup import is_duplicate_text
        existing = ["Prefers threading over asyncio for concurrent I/O"]
        assert is_duplicate_text("Prefers threading over asyncio for concurrent I/O", existing, 0.95) is True
        assert is_duplicate_text("Uses Docker for deployment and containerization", existing, 0.95) is False


@skip_no_ollama
class TestEntityExtractionAndLinking:
    def test_extract_and_link_entities_roundtrip(self, opencode_setup):
        _, memory_conn, tmpdir, _ = opencode_setup
        from codemira.store.db import insert_memory, get_or_create_entity, link_memory_entity, get_entities_for_memory, get_memories_by_entity
        from codemira.extraction.dedup import extract_entities
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
        text = "Uses FastAPI with Docker for deployment and pytest for testing"
        emb = _make_embedding()
        mid = insert_memory(memory_conn, text, 0.7, "priority", emb, "ses_entity")
        entities = extract_entities(text, ENTITY_MODEL, OLLAMA_URL, prompts_dir)
        entity_names = {e["name"] for e in entities}
        assert entity_names & {"fastapi", "docker", "pytest"}, f"Expected at least one of fastapi/docker/pytest, got {entity_names}"
        for entity in entities:
            eid = get_or_create_entity(memory_conn, entity["name"], entity["type"])
            link_memory_entity(memory_conn, mid, eid)
        linked = get_entities_for_memory(memory_conn, mid)
        linked_names = {e["name"] for e in linked}
        assert linked_names == entity_names
        if "fastapi" in entity_names:
            fastapi_mems = get_memories_by_entity(memory_conn, "fastapi")
            assert len(fastapi_mems) == 1
            assert fastapi_mems[0]["id"] == mid

    def test_entity_classification_frameworks(self):
        from codemira.extraction.dedup import extract_entities
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
        entities = extract_entities(
            "Uses FastAPI and Flask and Django", ENTITY_MODEL, OLLAMA_URL, prompts_dir,
        )
        names = {e["name"] for e in entities}
        assert names & {"fastapi", "flask", "django"}, f"Expected at least one of fastapi/flask/django, got {names}"


class TestOllamaCompression:
    def test_call_ollama_returns_response(self):
        from codemira.extraction.compressor import call_ollama
        result = call_ollama(
            "gemma4:e2b",
            "You are a helpful assistant. Reply with exactly: HELLO",
            "Say hello",
        )
        assert len(result.strip()) > 0
        assert "HELLO" in result.upper(), f"Expected HELLO in response, got: {result!r}"

    def test_call_ollama_compression_prompt(self):
        from codemira.extraction.compressor import call_ollama
        result = call_ollama(
            "gemma4:e2b",
            "Summarize the following tool call in one short sentence.",
            "Tool: bash\nArguments: {'command': 'pip install fastapi'}\nResult: Successfully installed fastapi-0.104.1",
        )
        assert len(result.strip()) > 0
        assert len(result) < 500
        assert "fastapi" in result.lower() or "install" in result.lower(), f"Compression should mention fastapi or install, got: {result!r}"

    def test_call_ollama_wrong_model_raises(self):
        from codemira.extraction.compressor import call_ollama
        with pytest.raises(Exception):
            call_ollama("nonexistent-model-xyz", "system", "user")

    def test_compress_tool_calls_produces_transcript(self, opencode_setup):
        from codemira.daemon import compress_tool_calls
        opencode_conn, _, _, _ = opencode_setup
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
        old_ts = int((time.time() - 120) * 1000)
        _insert_session(opencode_conn, "ses_comp_1", old_ts)
        _insert_user_message(opencode_conn, "msg_c1", "ses_comp_1", "Set up a FastAPI project", old_ts)
        _insert_assistant_message_with_tool(
            opencode_conn, "asst_c1", "ses_comp_1", "Setting up FastAPI",
            "bash", {"command": "pip install fastapi"}, "Successfully installed fastapi", old_ts + 1000,
        )
        from codemira.opencode_db import read_session_conversation
        conversation = read_session_conversation(opencode_conn, "ses_comp_1")
        result = compress_tool_calls(conversation, "gemma4:e2b", "http://localhost:11434", prompts_dir)
        assert len(result.strip()) > 0
        assert "fastapi" in result.lower(), f"Compressed transcript should mention fastapi, got: {result!r}"


class TestOllamaSubcortical:
    def test_subcortical_returns_xml(self):
        from codemira.extraction.compressor import call_ollama
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
        with open(os.path.join(prompts_dir, "subcortical_system.txt")) as f:
            system_prompt = f.read()
        user_prompt = (
            "User goal: Set up a REST API with FastAPI\n"
            "Recent actions:\n<action tool=\"bash\" target=\"pip install fastapi\" result=\"installed\" />\n"
            "Pinned memories: None"
        )
        result = call_ollama("gemma4:e2b", system_prompt, user_prompt)
        assert len(result.strip()) > 0
        assert "<query_expansion>" in result, f"Subcortical must emit <query_expansion>, got: {result!r}"
        assert "<entities>" in result, f"Subcortical must emit <entities>, got: {result!r}"
        assert "<keep>" in result, f"Subcortical must emit <keep>, got: {result!r}"


class TestOpenRouterExtraction:
    @pytest.fixture
    def api_key(self):
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            pytest.skip("OPENROUTER_API_KEY not set")
        return key

    def test_call_api_model_returns_response(self, api_key):
        from codemira.extraction.extractor import call_api_model
        result = call_api_model(
            "z-ai/GLM-5.1",
            "You are a helpful assistant. Reply with exactly: HELLO",
            "Say hello",
            api_key,
        )
        assert "HELLO" in result.upper(), f"Expected HELLO in response, got: {result!r}"

    def test_extract_memories_from_transcript(self, api_key, opencode_setup):
        from codemira.extraction.extractor import extract_memories
        _, memory_conn, tmpdir, _ = opencode_setup
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
        compressed = (
            "User: Set up a FastAPI project for a REST API\n"
            "Assistant: I'll help you set up FastAPI.\n"
            "Tool: bash — Installed FastAPI and uvicorn\n"
            "Assistant: FastAPI is now installed. Let me create the main app file.\n"
            "Tool: write — Created main.py with FastAPI app\n"
            "Assistant: Your FastAPI app is ready.\n"
            "User: Also add pytest for testing\n"
            "Assistant: Installing pytest.\n"
            "Tool: bash — Installed pytest\n"
            "Assistant: pytest is installed."
        )
        memories = extract_memories(
            compressed, memory_conn,
            "z-ai/GLM-5.1", api_key,
            deduplicate_text_threshold=0.95,
            prompts_dir=prompts_dir,
        )
        assert isinstance(memories, list)
        assert len(memories) > 0, "Should extract at least one memory from a non-trivial transcript"
        for m in memories:
            assert isinstance(m["text"], str) and len(m["text"].strip()) > 0, f"Memory text must be non-empty string, got: {m!r}"

    def test_extract_memories_deduplicates(self, api_key, opencode_setup):
        from codemira.extraction.extractor import extract_memories
        from codemira.store.db import insert_memory
        _, memory_conn, tmpdir, _ = opencode_setup
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
        emb = _make_embedding()
        insert_memory(memory_conn, "User prefers FastAPI for REST APIs", 0.8, "priority", emb)
        compressed = "User chose FastAPI for building their REST API project."
        memories = extract_memories(
            compressed, memory_conn,
            "z-ai/GLM-5.1", api_key,
            deduplicate_text_threshold=0.85,
            prompts_dir=prompts_dir,
        )
        for m in memories:
            existing = ["User prefers FastAPI for REST APIs"]
            from codemira.extraction.dedup import is_duplicate_text
            assert not is_duplicate_text(m["text"], existing, 0.85)


class TestFullOllamaPipeline:
    def test_compress_then_extract_stores_memories(self, opencode_setup):
        _, memory_conn, tmpdir, _ = opencode_setup
        from codemira.daemon import compress_tool_calls
        from codemira.opencode_db import read_session_conversation
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
        old_ts = int((time.time() - 120) * 1000)
        opencode_conn = opencode_setup[0]
        _insert_session(opencode_conn, "ses_full_1", old_ts)
        _insert_user_message(opencode_conn, "msg_f1", "ses_full_1", "Create a FastAPI app with Docker", old_ts)
        _insert_assistant_message_with_tool(
            opencode_conn, "asst_f1", "ses_full_1", "Setting up FastAPI with Docker",
            "bash", {"command": "pip install fastapi uvicorn"}, "Successfully installed fastapi uvicorn", old_ts + 1000,
        )
        _insert_assistant_message_with_tool(
            opencode_conn, "asst_f2", "ses_full_1", "Creating Dockerfile",
            "write", {"path": "Dockerfile"}, "Dockerfile created", old_ts + 2000,
        )
        _insert_user_message(opencode_conn, "msg_f2", "ses_full_1", "Also add pytest", old_ts + 3000)
        _insert_assistant_message_with_tool(
            opencode_conn, "asst_f3", "ses_full_1", "Installing pytest",
            "bash", {"command": "pip install pytest"}, "Successfully installed pytest", old_ts + 4000,
        )
        conversation = read_session_conversation(opencode_conn, "ses_full_1")
        assert len(conversation) == 5, f"Expected 5 messages (2 user + 3 assistant), got {len(conversation)}"
        assert [m["role"] for m in conversation] == ["user", "assistant", "assistant", "user", "assistant"]
        compressed = compress_tool_calls(conversation, "gemma4:e2b", "http://localhost:11434", prompts_dir)
        assert len(compressed.strip()) > 0
        domain_terms = {"fastapi", "docker", "pytest"}
        found = {t for t in domain_terms if t in compressed.lower()}
        assert found, f"Compressed transcript should mention at least one domain term, got: {compressed!r}"
