import json
import logging
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

from codemira.config import DaemonConfig
from codemira.extraction.context import ExtractionContext
from codemira.store.manager import StoreManager

log = logging.getLogger(__name__)


class _HttpError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message


class RetrieveHandler(BaseHTTPRequestHandler):
    manager: StoreManager
    config: DaemonConfig
    prompts_dir: str

    def do_GET(self):
        try:
            if self.path == "/health":
                self._send_json(200, self._collect_health_status())
            elif self.path.startswith("/arc"):
                self._send_json(200, self._read_arc())
            else:
                self._send_error(404, "not found")
        except _HttpError as e:
            self._send_error(e.code, e.message)
        except Exception as e:
            log.exception("GET %s crashed", self.path)
            self._send_error(500, str(e))

    def do_POST(self):
        try:
            routes = {
                "/arc/generate": (self._start_arc_generation, ["session_id", "project_root"]),
                "/extract": (self._start_extraction, ["session_id", "project_root"]),
                "/retrieve": (self._collect_retrieval_response, ["project_root"]),
            }
            if self.path not in routes:
                self._send_error(404, "not found")
                return
            handler_fn, required = routes[self.path]
            data = self._read_json_body()
            self._require_fields(data, required)
            status, body = handler_fn(data)
            self._send_json(status, body)
        except _HttpError as e:
            self._send_error(e.code, e.message)
        except Exception as e:
            log.exception("POST %s crashed", self.path)
            self._send_error(500, str(e))

    def _collect_health_status(self) -> dict:
        return {
            "status": "ok",
            "llm": self._is_llm_reachable(),
            "embedding_model": self._is_embedding_model_ready(),
            "version": "0.1.0",
        }

    def _read_arc(self) -> dict:
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(self.path).query)
        session_id = params.get("session_id", [""])[0]
        project_root = params.get("project_root", [""])[0]
        if not session_id or not project_root:
            raise _HttpError(400, "session_id and project_root required")
        store = self.manager.get(project_root)
        from codemira.store.db import read_arc
        with store.lock:
            arc_record = read_arc(store.conn, session_id)
        arc = arc_record["arc"] if arc_record else None
        return {"arc": arc, "session_id": session_id}

    def _start_arc_generation(self, data: dict) -> tuple[int, dict]:
        self._spawn_thread(self._generate_arc, data["session_id"], data["project_root"])
        return 202, {"status": "generating"}

    def _start_extraction(self, data: dict) -> tuple[int, dict]:
        self._spawn_thread(self._extract_session, data["session_id"], data["project_root"])
        return 202, {"status": "extracting"}

    def _collect_retrieval_response(self, data: dict) -> tuple[int, dict]:
        if self.config.loud:
            log.info("── Subcortical → /retrieve ──\n  query: %s\n  entities: %s",
                     data.get("query_expansion", ""), data.get("entities", []))
        project_root = data["project_root"]
        store = self.manager.get(project_root)
        from codemira.retrieval.proactive import collect_ranked_memories
        with store.lock:
            memories = collect_ranked_memories(
                query_expansion=data["query_expansion"],
                entities=data.get("entities", []),
                pinned_memory_ids=data.get("pinned_memory_ids", []),
                project_root=project_root,
                conn=store.conn,
                index=store.index,
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
        return 200, {"memories": result_memories, "degraded": False}

    def _extract_session(self, session_id: str, project_root: str):
        try:
            store = self.manager.get(project_root)
            from codemira.store.db import is_session_extracted
            with store.lock:
                if is_session_extracted(store.conn, session_id):
                    return
            from codemira.opencode_db import OpenCodeConnection
            with OpenCodeConnection(self.config.opencode_db_path) as opencode_conn:
                from codemira.daemon import extract_session_memories
                ctx = ExtractionContext(
                    store=store,
                    opencode_conn=opencode_conn,
                    prompts_dir=self.prompts_dir,
                )
                extract_session_memories(session_id, project_root, ctx, self.config)
        except Exception as e:
            log.error("Compaction-triggered extraction failed for session %s: %s", session_id, e)

    def _generate_arc(self, session_id: str, project_root: str):
        try:
            store = self.manager.get(project_root)
            from codemira.opencode_db import OpenCodeConnection
            with OpenCodeConnection(self.config.opencode_db_path) as opencode_conn:
                from codemira.summarization.arc import generate_arc
                with store.lock:
                    arc = generate_arc(
                        session_id=session_id,
                        opencode_conn=opencode_conn,
                        memory_conn=store.conn,
                        model=self.config.arc_model,
                        base_url=self.config.arc_base_url,
                        api_key=self.config.arc_api_key,
                        prompts_dir=self.prompts_dir,
                        context_length=self.config.arc_model_context_length,
                        chunk_target_tokens=self.config.arc_chunk_target_tokens,
                    )
                if self.config.loud and arc:
                    log.info("── Arc for session %s ──\n%s", session_id, arc)
        except Exception as e:
            log.error("Background arc generation failed for session %s: %s", session_id, e)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise _HttpError(400, "invalid json")

    def _require_fields(self, data: dict, fields: list[str]):
        missing = [f for f in fields if not data.get(f)]
        if missing:
            raise _HttpError(400, f"{' and '.join(missing)} required")

    def _send_json(self, code: int, body):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def _send_error(self, code: int, message: str):
        self._send_json(code, {"error": message})

    def _spawn_thread(self, target, *args):
        threading.Thread(target=target, args=args, daemon=True).start()

    def _is_llm_reachable(self) -> bool:
        base_url = self.config.subcortical_base_url.rstrip("/")
        try:
            urllib.request.urlopen(f"{base_url}/models", timeout=2)
            return True
        except urllib.error.HTTPError:
            # Service responded with an HTTP status (e.g. 401, 404) — endpoint is up.
            return True
        except Exception:
            return False

    def _is_embedding_model_ready(self) -> bool:
        try:
            from codemira.embeddings import EmbeddingsProvider
            EmbeddingsProvider.get()
            return True
        except Exception:
            return False

    def log_message(self, format, *args):
        pass


def create_server(manager: StoreManager, config: DaemonConfig, prompts_dir: str, port: int | None = None):
    port = port if port is not None else config.http_port
    RetrieveHandler.manager = manager
    RetrieveHandler.config = config
    RetrieveHandler.prompts_dir = prompts_dir
    server = HTTPServer(("127.0.0.1", port), RetrieveHandler)
    return server
