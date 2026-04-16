import pytest
import json
import threading
import time
import urllib.request
import urllib.parse
import os
import numpy as np
from tests.conftest import _make_embedding, _make_embeddings


@pytest.fixture
def server_setup(tmpdir_path):
    from codemira.store.db import insert_memory
    from codemira.store.manager import StoreManager
    from codemira.config import DaemonConfig
    from codemira.server import create_server
    project_dir = os.path.join(tmpdir_path, "proj")
    os.makedirs(project_dir, exist_ok=True)
    config = DaemonConfig(http_port=0)
    manager = StoreManager(config)
    conn, mi = manager.get(project_dir)
    embs = _make_embeddings(3)
    ids = []
    texts = [
        "Prefers threading over asyncio",
        "Uses Docker for deployment",
        "Prefers raw sqlite3 over ORM",
    ]
    for i, (text, emb) in enumerate(zip(texts, embs)):
        mid = insert_memory(conn, text, "priority", emb)
        ids.append(mid)
    mi.rebuild_after_write(conn)
    server = create_server(manager, config, port=0)
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)
    yield f"http://localhost:{port}", conn, mi, ids, embs, config, project_dir
    server.shutdown()
    manager.close_all()


class TestHealthEndpoint:
    def test_health_returns_ok(self, server_setup):
        base_url = server_setup[0]
        resp = urllib.request.urlopen(f"{base_url}/health")
        data = json.loads(resp.read())
        assert data["status"] == "ok"
        assert isinstance(data["version"], str) and len(data["version"]) > 0


class TestRetrieveEndpoint:
    def test_retrieve_returns_memories(self, server_setup):
        base_url, conn, mi, ids, embs, config, project_dir = server_setup
        payload = json.dumps({
            "query_expansion": "threading asyncio",
            "entities": [],
            "pinned_memory_ids": [],
            "project_dir": project_dir,
            "query_embedding": embs[0],
        }).encode()
        req = urllib.request.Request(f"{base_url}/retrieve", data=payload,
                                      headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        assert len(data["memories"]) >= 1
        assert data["degraded"] is False
        returned_ids = [m["id"] for m in data["memories"]]
        assert ids[0] in returned_ids, f"Expected threading memory {ids[0]} in results, got {returned_ids}"

    def test_retrieve_with_pinned(self, server_setup):
        base_url, conn, mi, ids, embs, config, project_dir = server_setup
        payload = json.dumps({
            "query_expansion": "sqlite3 ORM",
            "entities": [],
            "pinned_memory_ids": [ids[0]],
            "project_dir": project_dir,
            "query_embedding": embs[2],
        }).encode()
        req = urllib.request.Request(f"{base_url}/retrieve", data=payload,
                                      headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        returned_ids = [m["id"] for m in data["memories"]]
        assert ids[0] in returned_ids

    def test_retrieve_invalid_json(self, server_setup):
        base_url = server_setup[0]
        req = urllib.request.Request(f"{base_url}/retrieve", data=b"not json",
                                      headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
            assert False, "Should have raised"
        except urllib.error.HTTPError as e:
            assert e.code == 400

    def test_retrieve_memory_has_required_fields(self, server_setup):
        base_url, conn, mi, ids, embs, config, project_dir = server_setup
        payload = json.dumps({
            "query_expansion": "docker deployment",
            "entities": ["docker"],
            "pinned_memory_ids": [],
            "project_dir": project_dir,
            "query_embedding": embs[1],
        }).encode()
        req = urllib.request.Request(f"{base_url}/retrieve", data=payload,
                                      headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        for mem in data["memories"]:
            assert isinstance(mem["id"], str) and len(mem["id"]) > 0
            assert isinstance(mem["text"], str) and len(mem["text"]) > 0
            assert mem["category"] in ("priority", "decision_rationale", "rejected_alternative")


class TestNotFoundEndpoint:
    def test_unknown_path_returns_404(self, server_setup):
        base_url = server_setup[0]
        try:
            urllib.request.urlopen(f"{base_url}/unknown")
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 404


class TestArcGetEndpoint:
    def test_arc_returns_null_when_no_arc(self, server_setup):
        base_url, conn, mi, ids, embs, config, project_dir = server_setup
        resp = urllib.request.urlopen(f"{base_url}/arc?session_id=ses_none&project_dir={urllib.parse.quote(project_dir)}")
        data = json.loads(resp.read())
        assert data["topology"] is None
        assert data["session_id"] == "ses_none"

    def test_arc_returns_stored_topology(self, server_setup):
        base_url, conn, mi, ids, embs, config, project_dir = server_setup
        from codemira.store.db import upsert_arc_fragment
        upsert_arc_fragment(conn, "ses_test", 0, "[START] Goal: Fix bug\n └─ [CURRENT] Done", "hash1", 10)
        resp = urllib.request.urlopen(f"{base_url}/arc?session_id=ses_test&project_dir={urllib.parse.quote(project_dir)}")
        data = json.loads(resp.read())
        assert data["topology"] == "[START] Goal: Fix bug\n └─ [CURRENT] Done"
        assert data["session_id"] == "ses_test"

    def test_arc_missing_params_returns_400(self, server_setup):
        base_url = server_setup[0]
        try:
            urllib.request.urlopen(f"{base_url}/arc?session_id=ses_x")
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 400


class TestArcGenerateEndpoint:
    def test_arc_generate_returns_202(self, server_setup):
        base_url, conn, mi, ids, embs, config, project_dir = server_setup
        payload = json.dumps({
            "session_id": "ses_gen",
            "project_dir": project_dir,
        }).encode()
        req = urllib.request.Request(f"{base_url}/arc/generate", data=payload,
                                      headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        assert resp.code == 202
        data = json.loads(resp.read())
        assert data["status"] == "generating"

    def test_arc_generate_missing_params_returns_400(self, server_setup):
        base_url = server_setup[0]
        payload = json.dumps({"session_id": "ses_x"}).encode()
        req = urllib.request.Request(f"{base_url}/arc/generate", data=payload,
                                      headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 400


class TestExtractEndpoint:
    def test_extract_returns_202(self, server_setup):
        base_url, conn, mi, ids, embs, config, project_dir = server_setup
        payload = json.dumps({
            "session_id": "ses_extract",
            "project_dir": project_dir,
        }).encode()
        req = urllib.request.Request(f"{base_url}/extract", data=payload,
                                      headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        assert resp.code == 202
        data = json.loads(resp.read())
        assert data["status"] == "extracting"

    def test_extract_missing_params_returns_400(self, server_setup):
        base_url = server_setup[0]
        payload = json.dumps({"session_id": "ses_x"}).encode()
        req = urllib.request.Request(f"{base_url}/extract", data=payload,
                                      headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 400

    def test_extract_invalid_json_returns_400(self, server_setup):
        base_url = server_setup[0]
        req = urllib.request.Request(f"{base_url}/extract", data=b"not json",
                                      headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 400

    def test_extract_skips_already_extracted_session(self, server_setup):
        base_url, conn, mi, ids, embs, config, project_dir = server_setup
        from codemira.store.db import log_extraction
        log_extraction(conn, "ses_done", memory_count=3, is_complete=True)
        payload = json.dumps({
            "session_id": "ses_done",
            "project_dir": project_dir,
        }).encode()
        req = urllib.request.Request(f"{base_url}/extract", data=payload,
                                      headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        assert resp.code == 202
        time.sleep(0.2)
        row = conn.execute(
            "SELECT memory_count, attempt_count FROM extraction_log WHERE session_id = ?",
            ("ses_done",),
        ).fetchone()
        assert row["memory_count"] == 3
        assert row["attempt_count"] == 1
