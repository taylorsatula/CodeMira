import json
import logging
import threading
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

from codemira.config import DaemonConfig
from codemira.store.manager import StoreManager

log = logging.getLogger(__name__)


class RetrieveHandler(BaseHTTPRequestHandler):
    manager: StoreManager
    config: DaemonConfig

    def do_GET(self):
        if self.path == "/health":
            response = json.dumps({
                "status": "ok",
                "ollama": self._check_ollama(),
                "embedding_model": self._check_embedding_model(),
                "version": "0.1.0",
            })
            self._send_json(200, response)
        elif self.path.startswith("/arc"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            session_id = params.get("session_id", [""])[0]
            project_dir = params.get("project_dir", [""])[0]
            if not session_id or not project_dir:
                self._send_json(400, json.dumps({"error": "session_id and project_dir required"}))
                return
            try:
                conn, _ = self.manager.get(project_dir)
                from codemira.store.db import get_arc_summary
                arc = get_arc_summary(conn, session_id)
                topology = arc["topology"] if arc else None
                self._send_json(200, json.dumps({"topology": topology, "session_id": session_id}))
            except Exception as e:
                self._send_json(500, json.dumps({"error": str(e)}))
        else:
            self._send_json(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path == "/arc/generate":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, json.dumps({"error": "invalid json"}))
                return
            session_id = data.get("session_id", "")
            project_dir = data.get("project_dir", "")
            if not session_id or not project_dir:
                self._send_json(400, json.dumps({"error": "session_id and project_dir required"}))
                return
            self._send_json(202, json.dumps({"status": "generating"}))
            threading.Thread(
                target=self._generate_arc,
                args=(session_id, project_dir),
                daemon=True,
            ).start()
        elif self.path == "/retrieve":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, json.dumps({"error": "invalid json"}))
                return
            project_dir = data.get("project_dir", "")
            if not project_dir:
                self._send_json(400, json.dumps({"error": "project_dir required"}))
                return
            try:
                if self.config.loud:
                    log.info("── Subcortical → /retrieve ──\n  query: %s\n  entities: %s",
                             data.get("query_expansion", ""), data.get("entities", []))
                conn, index = self.manager.get(project_dir)
                from codemira.retrieval.proactive import retrieve
                memories = retrieve(
                    query_expansion=data["query_expansion"],
                    entities=data.get("entities", []),
                    pinned_memory_ids=data.get("pinned_memory_ids", []),
                    project_dir=project_dir,
                    conn=conn,
                    index=index,
                    config=self.config,
                    query_embedding=data.get("query_embedding"),
                )
                result_memories = [
                    {"id": m["id"], "text": m["text"], "category": m["category"]}
                    for m in memories
                ]
                if self.config.loud:
                    log.info("  → returning %d memories: %s", len(result_memories),
                             [m["id"] for m in result_memories])
                self._send_json(200, json.dumps({"memories": result_memories, "degraded": False}))
            except Exception as e:
                self._send_json(500, json.dumps({"error": str(e)}))
        else:
            self._send_json(404, json.dumps({"error": "not found"}))

    def _generate_arc(self, session_id: str, project_dir: str):
        try:
            conn, _ = self.manager.get(project_dir)
            from codemira.opencode_db import discover_opencode_db, open_opencode_db
            opencode_db_path = discover_opencode_db(self.config.opencode_db_path)
            opencode_conn = open_opencode_db(opencode_db_path)
            try:
                from codemira.summarization.handler import generate_arc_summary
                import os
                prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "prompts")
                topology = generate_arc_summary(
                    session_id=session_id,
                    opencode_conn=opencode_conn,
                    memory_conn=conn,
                    model=self.config.arc_summary_model,
                    ollama_url=self.config.ollama_url,
                    prompts_dir=prompts_dir,
                    context_length=self.config.arc_summary_model_context_length,
                    chunk_target_tokens=self.config.arc_chunk_target_tokens,
                )
                if self.config.loud and topology:
                    log.info("── Arc topology for session %s ──\n%s", session_id, topology)
            finally:
                opencode_conn.close()
        except Exception as e:
            log.error("Background arc generation failed for session %s: %s", session_id, e)

    def _send_json(self, code: int, body: str):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def _check_ollama(self) -> bool:
        try:
            urllib.request.urlopen(f"{self.config.ollama_url}/api/tags", timeout=2)
            return True
        except Exception:
            return False

    def _check_embedding_model(self) -> bool:
        try:
            from codemira.embeddings import EmbeddingsProvider
            EmbeddingsProvider.get()
            return True
        except Exception:
            return False

    def log_message(self, format, *args):
        pass


def create_server(manager: StoreManager, config: DaemonConfig, port: int | None = None):
    port = port if port is not None else config.http_port
    RetrieveHandler.manager = manager
    RetrieveHandler.config = config
    server = HTTPServer(("127.0.0.1", port), RetrieveHandler)
    return server
