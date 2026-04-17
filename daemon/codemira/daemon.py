import logging
import os
import time

from codemira.config import DaemonConfig
from codemira.errors import ExtractionError
from codemira.extraction.context import ExtractionContext
from codemira.store.db import insert_memory, get_or_create_entity, link_memory_entity, insert_memory_link, log_extraction, mark_extraction_complete, get_memory
from codemira.store.manager import StoreManager
from codemira.server import create_server
from codemira.extraction.compressor import call_ollama
from codemira.extraction.extractor import extract_memories
from codemira.extraction.chunker import chunk_compressed_transcript, estimate_token_count
from codemira.extraction.dedup import is_duplicate_vector, extract_entities
from codemira.extraction.link_classifier import classify_link

log = logging.getLogger(__name__)


def compress_tool_calls(conversation: list[dict], model: str, ollama_url: str, prompts_dir: str) -> str:
    from codemira.extraction.extractor import load_prompt
    from codemira.extraction.transcript import iter_turns, render_transcript, TOOL_PREFIX
    system_prompt = load_prompt("compression_system", prompts_dir).render()

    def _compress_tool(part: dict) -> str | None:
        state = part.get("state", {})
        if state.get("status") != "completed":
            return None
        tool_name = part.get("tool", "unknown")
        tool_input = state.get("input", {})
        tool_output = state.get("output", "")
        user_msg = f"{TOOL_PREFIX} {tool_name}\nArguments: {tool_input}\nResult: {tool_output[:500]}"
        compressed = call_ollama(model, system_prompt, user_msg, ollama_url)
        return f"{TOOL_PREFIX} {tool_name} — {compressed}"

    return render_transcript(iter_turns(conversation), _compress_tool)


def process_idle_session(
    session_id: str,
    project_root: str,
    ctx: ExtractionContext,
    config: DaemonConfig,
):
    from codemira.opencode_db import read_session_conversation
    store = ctx.store
    with store.lock:
        try:
            conversation = read_session_conversation(ctx.opencode_conn, session_id)
            if not conversation:
                log_extraction(store.conn, session_id, 0)
                return
            try:
                compressed = compress_tool_calls(conversation, config.subcortical_model, config.ollama_url, ctx.prompts_dir)
                from codemira.store.db import get_existing_memory_texts
                existing_memories_token_estimate = estimate_token_count(
                    "\n".join(get_existing_memory_texts(store.conn))
                )
                chunks = chunk_compressed_transcript(
                    compressed, config.extraction_model_context_length,
                    existing_memories_token_estimate,
                    chunk_target_tokens=config.extraction_chunk_target_tokens,
                )
                all_memories: list[dict] = []
                prior_texts: list[str] = []
                for chunk in chunks:
                    chunk_memories = extract_memories(
                        chunk, store.conn, config.extraction_model, ctx.api_key,
                        session_id, config.deduplicate_text_threshold, ctx.prompts_dir,
                        prior_chunk_texts=prior_texts if prior_texts else None,
                    )
                    all_memories.extend(chunk_memories)
                    prior_texts = [m["text"] for m in all_memories]
                memories = all_memories
            except ExtractionError as e:
                log_extraction(store.conn, session_id, 0)
                log.warning("Marking session %s as extracted after non-retryable error: %s", session_id, e)
                return
            if not memories:
                log_extraction(store.conn, session_id, 0)
                return
            from codemira.embeddings import EmbeddingsProvider
            provider = EmbeddingsProvider.get()
            texts = [m["text"] for m in memories]
            embeddings = provider.encode_deep(texts)
            stored_count = 0
            for mem, emb in zip(memories, embeddings):
                if is_duplicate_vector(emb, store.index, config.deduplicate_cosine_threshold) == "duplicate":
                    continue
                mid = insert_memory(store.conn, mem["text"],
                                     mem["category"], emb, session_id)
                entities = extract_entities(
                    mem["text"], config.subcortical_model, config.ollama_url, ctx.prompts_dir,
                )
                for entity in entities:
                    eid = get_or_create_entity(store.conn, entity["name"], entity["type"])
                    link_memory_entity(store.conn, mid, eid)
                similar = store.index.search(emb, k=5)
                for linked_id, sim in similar:
                    if sim >= config.link_similarity_threshold:
                        linked_mem = get_memory(store.conn, linked_id)
                        if linked_mem is None:
                            continue
                        link_type = classify_link(
                            mem["text"], linked_mem["text"],
                            config.subcortical_model, config.ollama_url, ctx.prompts_dir,
                        )
                        insert_memory_link(store.conn, mid, linked_id, link_type)
                store.index.add_vector(mid, emb)
                stored_count += 1
            store.index.rebuild_after_write(store.conn)
            log_extraction(store.conn, session_id, stored_count)
            log.info("Extracted %d memories from session %s (project=%s)", stored_count, session_id, project_root)
            if config.loud and memories:
                log.info("── Extraction results for session %s: %d extracted, %d stored ──", session_id, len(memories), stored_count)
                for m in memories:
                    log.info("  [%s] %s", m.get("category", "?"), m["text"][:200])
        except Exception:
            attempt_count = log_extraction(store.conn, session_id, 0, is_complete=False)
            if attempt_count >= config.max_extraction_attempts:
                mark_extraction_complete(store.conn, session_id)
                log.warning("Session %s marked as unextractable after %d failed attempts", session_id, attempt_count)
                return
            raise


def _collect_extracted_session_ids(manager: StoreManager) -> set[str]:
    extracted: set[str] = set()
    for _, store in manager.items():
        with store.lock:
            rows = store.conn.execute("SELECT session_id FROM extraction_log WHERE is_complete = 1").fetchall()
            extracted.update(r["session_id"] for r in rows)
    return extracted


PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "prompts")


def run_daemon(config: DaemonConfig | None = None):
    config = config or DaemonConfig()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    manager = StoreManager(config)
    from codemira.embeddings import EmbeddingsProvider
    EmbeddingsProvider.get()
    server = create_server(manager, config, PROMPTS_DIR)
    log.info("CodeMira daemon starting on port %d", config.http_port)
    import threading
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    last_consolidation = 0
    first_cycle = True
    while True:
        try:
            from codemira.opencode_db import OpenCodeConnection, find_idle_sessions, list_project_roots
            with OpenCodeConnection(config.opencode_db_path) as opencode_conn:
                for project_root in list_project_roots(opencode_conn):
                    manager.get(project_root)
                extracted_ids = _collect_extracted_session_ids(manager)
                idle_sessions = find_idle_sessions(opencode_conn, extracted_ids, config.idle_threshold_minutes)
                api_key = os.environ.get("OPENROUTER_API_KEY", "")
                if not api_key:
                    if idle_sessions:
                        log.error("OPENROUTER_API_KEY not set — skipping %d idle session(s)", len(idle_sessions))
                else:
                    for session in idle_sessions:
                        if not session["project_root"]:
                            log.warning("Skipping session %s: no project_root", session["id"])
                            continue
                        try:
                            store = manager.get(session["project_root"])
                            ctx = ExtractionContext(
                                store=store,
                                opencode_conn=opencode_conn,
                                prompts_dir=PROMPTS_DIR,
                                api_key=api_key,
                            )
                            process_idle_session(session["id"], session["project_root"], ctx, config)
                        except Exception as e:
                            log.error("Error processing session %s: %s", session["id"], e)
            if first_cycle:
                log.info("Daemon ready — polling every %d min, found %d project(s)", config.poll_interval_minutes, len(manager._stores))
                first_cycle = False
            now = time.time()
            if now - last_consolidation >= config.consolidation_interval_hours * 3600:
                try:
                    from codemira.consolidation.handler import run_consolidation
                    for project_root, store in manager.items():
                        with store.lock:
                            new_ids = run_consolidation(store.conn, store.index, config.consolidation_model,
                                                        config.consolidation_similarity_threshold,
                                                        config.ollama_url, PROMPTS_DIR)
                        if new_ids:
                            log.info("Consolidated %d clusters in %s", len(new_ids), project_root)
                    last_consolidation = now
                except Exception as e:
                    log.error("Consolidation error: %s", e)
        except FileNotFoundError:
            log.debug("OpenCode DB not found, waiting")
        except Exception:
            log.exception("Daemon loop error")
        time.sleep(config.poll_interval_minutes * 60)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CodeMira daemon")
    parser.add_argument("--loud", action="store_true",
                        help="Log extraction results, arc topologies, and subcortical queries")
    args = parser.parse_args()
    overrides = {}
    if args.loud:
        overrides["loud"] = True
    config = DaemonConfig(**overrides)
    run_daemon(config)


if __name__ == "__main__":
    main()
