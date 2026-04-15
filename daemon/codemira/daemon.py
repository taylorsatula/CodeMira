import logging
import os
import sqlite3
import time

from codemira.config import DaemonConfig
from codemira.errors import ExtractionError
from codemira.store.db import insert_memory, get_or_create_entity, link_memory_entity, insert_memory_link, log_extraction, mark_extraction_complete, get_memory
from codemira.store.index import MemoryIndex
from codemira.store.manager import StoreManager
from codemira.server import create_server
from codemira.extraction.compressor import call_ollama
from codemira.extraction.extractor import extract_memories
from codemira.extraction.dedup import is_duplicate_vector, extract_entities
from codemira.extraction.link_classifier import classify_link

log = logging.getLogger(__name__)


def compress_tool_calls(conversation: list[dict], model: str, ollama_url: str, prompts_dir: str) -> str:
    from codemira.extraction.extractor import load_prompt
    system_prompt = load_prompt("compression_system", prompts_dir)
    parts = []
    for msg in conversation:
        if msg.get("role") == "user":
            for part in msg.get("parts", []):
                if part.get("type") == "text":
                    parts.append(f"User: {part['text']}")
        elif msg.get("role") == "assistant":
            for part in msg.get("parts", []):
                if part.get("type") == "text":
                    parts.append(f"Assistant: {part['text']}")
                elif part.get("type") == "tool":
                    state = part.get("state", {})
                    if state.get("status") == "completed":
                        tool_name = part.get("tool", "unknown")
                        tool_input = state.get("input", {})
                        tool_output = state.get("output", "")
                        user_msg = f"Tool: {tool_name}\nArguments: {tool_input}\nResult: {tool_output[:500]}"
                        compressed = call_ollama(model, system_prompt, user_msg, ollama_url)
                        parts.append(f"Tool: {tool_name} — {compressed}")
    return "\n\n".join(parts)


def process_idle_session(
    session_id: str,
    project_root: str,
    opencode_conn: sqlite3.Connection,
    manager: StoreManager,
    config: DaemonConfig,
    prompts_dir: str,
    api_key: str,
):
    from codemira.opencode_db import read_session_conversation
    memory_conn, memory_index = manager.get(project_root)
    try:
        conversation = read_session_conversation(opencode_conn, session_id)
        if not conversation:
            log_extraction(memory_conn, session_id, 0)
            return
        try:
            compressed = compress_tool_calls(conversation, config.subcortical_model, config.ollama_url, prompts_dir)
            memories = extract_memories(
                compressed, memory_conn, config.extraction_model, api_key,
                config.deduplicate_text_threshold, prompts_dir,
            )
        except ExtractionError as e:
            log_extraction(memory_conn, session_id, 0)
            log.warning("Marking session %s as extracted after non-retryable error: %s", session_id, e)
            return
        if not memories:
            log_extraction(memory_conn, session_id, 0)
            return
        from codemira.embeddings import EmbeddingsProvider
        provider = EmbeddingsProvider.get()
        texts = [m["text"] for m in memories]
        embeddings = provider.encode_deep(texts)
        stored_count = 0
        for mem, emb in zip(memories, embeddings):
            if is_duplicate_vector(emb, memory_index, config.deduplicate_cosine_threshold):
                continue
            mid = insert_memory(memory_conn, mem["text"], mem.get("importance", 0.5),
                                mem.get("category", "priority"), emb, session_id)
            entities = extract_entities(
                mem["text"], config.subcortical_model, config.ollama_url, prompts_dir,
            )
            for entity in entities:
                eid = get_or_create_entity(memory_conn, entity["name"], entity["type"])
                link_memory_entity(memory_conn, mid, eid)
            similar = memory_index.search(emb, k=5)
            for linked_id, sim in similar:
                if sim >= config.link_similarity_threshold:
                    linked_mem = get_memory(memory_conn, linked_id)
                    if linked_mem is None:
                        continue
                    link_type = classify_link(
                        mem["text"], linked_mem["text"],
                        config.subcortical_model, config.ollama_url, prompts_dir,
                    )
                    insert_memory_link(memory_conn, mid, linked_id, link_type)
            memory_index.add_vector(mid, emb)
            stored_count += 1
        memory_index.rebuild_after_write(memory_conn)
        log_extraction(memory_conn, session_id, stored_count)
        log.info("Extracted %d memories from session %s (project=%s)", stored_count, session_id, project_root)
    except Exception:
        attempt_count = log_extraction(memory_conn, session_id, 0, is_complete=False)
        if attempt_count >= config.max_extraction_attempts:
            mark_extraction_complete(memory_conn, session_id)
            log.warning("Session %s marked as unextractable after %d failed attempts", session_id, attempt_count)
            return
        raise


def _collect_extracted_session_ids(manager: StoreManager) -> set[str]:
    extracted: set[str] = set()
    for _, conn, _ in manager.items():
        rows = conn.execute("SELECT session_id FROM extraction_log WHERE is_complete = 1").fetchall()
        extracted.update(r["session_id"] for r in rows)
    return extracted


def run_daemon(config: DaemonConfig | None = None):
    config = config or DaemonConfig()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "prompts")
    manager = StoreManager(config)
    from codemira.embeddings import EmbeddingsProvider
    EmbeddingsProvider.get()
    server = create_server(manager, config)
    log.info("CodeMira daemon starting on port %d", config.http_port)
    import threading
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    last_consolidation = 0
    first_cycle = True
    while True:
        try:
            from codemira.opencode_db import discover_opencode_db, open_opencode_db, find_idle_sessions, list_project_roots
            opencode_db_path = discover_opencode_db(config.opencode_db_path)
            opencode_conn = open_opencode_db(opencode_db_path)
            for project_root in list_project_roots(opencode_conn):
                manager.get(project_root)
            extracted_ids = _collect_extracted_session_ids(manager)
            idle_sessions = find_idle_sessions(opencode_conn, extracted_ids, config.idle_threshold_minutes)
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                log.error("OPENROUTER_API_KEY not set — extraction will fail")
            for session in idle_sessions:
                if not session["project_root"]:
                    log.warning("Skipping session %s: no project_root", session["id"])
                    continue
                try:
                    process_idle_session(session["id"], session["project_root"],
                                         opencode_conn, manager, config, prompts_dir, api_key)
                except Exception as e:
                    log.error("Error processing session %s: %s", session["id"], e)
            opencode_conn.close()
            if first_cycle:
                log.info("Daemon ready — polling every %d min, found %d project(s)", config.poll_interval_minutes, len(manager._stores))
                first_cycle = False
            now = time.time()
            if now - last_consolidation >= config.consolidation_interval_hours * 3600:
                try:
                    from codemira.consolidation.handler import run_consolidation
                    for project_dir, conn, index in manager.items():
                        new_ids = run_consolidation(conn, index, config.consolidation_model,
                                                    config.consolidation_similarity_threshold,
                                                    config.ollama_url, prompts_dir)
                        if new_ids:
                            log.info("Consolidated %d clusters in %s", len(new_ids), project_dir)
                    last_consolidation = now
                except Exception as e:
                    log.error("Consolidation error: %s", e)
        except FileNotFoundError:
            log.debug("OpenCode DB not found, waiting")
        except Exception as e:
            log.error("Daemon loop error: %s", e)
        time.sleep(config.poll_interval_minutes * 60)


def main():
    config = DaemonConfig()
    run_daemon(config)


if __name__ == "__main__":
    main()
