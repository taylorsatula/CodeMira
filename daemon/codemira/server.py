import json
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

from codemira.config import DaemonConfig
from codemira.store.manager import StoreManager


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
        else:
            self._send_json(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path == "/retrieve":
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
                    {"id": m["id"], "text": m["text"], "importance": m["importance"], "category": m["category"]}
                    for m in memories
                ]
                self._send_json(200, json.dumps({"memories": result_memories, "degraded": False}))
            except Exception as e:
                self._send_json(500, json.dumps({"error": str(e)}))
        else:
            self._send_json(404, json.dumps({"error": "not found"}))

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
