import hashlib
import logging
import sqlite3

from codemira.extraction.chunker import estimate_token_count, PROMPT_OVERHEAD_TOKENS, split_into_turns
from codemira.extraction.compressor import call_ollama
from codemira.extraction.extractor import load_prompt
from codemira.store.db import upsert_arc_fragment, get_arc_fragments, delete_arc_fragments_from

log = logging.getLogger(__name__)


def _chunk_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _format_raw_transcript(conversation: list[dict]) -> str:
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
                        title = state.get("title", "")
                        output = state.get("output", "")[:500]
                        display = title if title else output
                        parts.append(f"Tool: {tool_name} — {display}")
    return "\n\n".join(parts)


def _chunk_transcript(transcript: str, context_length: int, chunk_target_tokens: int) -> list[str]:
    prior_arc_estimate = 1024
    chunk_budget = max(chunk_target_tokens, int(0.7 * context_length)) - PROMPT_OVERHEAD_TOKENS - prior_arc_estimate
    chunk_budget = max(chunk_budget, 1024)
    if estimate_token_count(transcript) <= chunk_budget:
        return [transcript]
    turns = split_into_turns(transcript)
    chunks: list[str] = []
    current_turns: list[str] = []
    current_tokens = 0
    for turn in turns:
        turn_tokens = estimate_token_count(turn)
        if current_turns and current_tokens + turn_tokens > chunk_budget:
            chunks.append("\n\n".join(current_turns))
            current_turns = [turn]
            current_tokens = turn_tokens
        else:
            current_turns.append(turn)
            current_tokens += turn_tokens
    if current_turns:
        chunks.append("\n\n".join(current_turns))
    if not chunks:
        return [transcript]
    return chunks


def generate_arc_summary(
    session_id: str,
    opencode_conn: sqlite3.Connection,
    memory_conn: sqlite3.Connection,
    model: str,
    ollama_url: str,
    prompts_dir: str,
    context_length: int,
    chunk_target_tokens: int = 30_000,
) -> str | None:
    from codemira.opencode_db import read_session_conversation
    conversation = read_session_conversation(opencode_conn, session_id)
    if len(conversation) < 4:
        return None
    transcript = _format_raw_transcript(conversation)
    chunks = _chunk_transcript(transcript, context_length, chunk_target_tokens)
    system_prompt = load_prompt("arc_summarizer_system", prompts_dir)
    arc_user_template = load_prompt("arc_summarizer_user", prompts_dir)

    existing_fragments = get_arc_fragments(memory_conn, session_id)
    existing_by_index = {f["fragment_index"]: f for f in existing_fragments}

    # Find the first chunk whose content hash differs from the stored fragment.
    # All fragments from that point onward must be reprocessed (prior_arc cascades).
    first_dirty = len(chunks)
    for i in range(len(chunks)):
        h = _chunk_hash(chunks[i])
        stored = existing_by_index.get(i)
        if stored is None or stored["content_hash"] != h:
            first_dirty = i
            break

    # Purge stale fragments from the first dirty index onward.
    if first_dirty < len(chunks):
        delete_arc_fragments_from(memory_conn, session_id, first_dirty)
    # Also purge any fragments beyond the current chunk count.
    if len(existing_fragments) > len(chunks):
        delete_arc_fragments_from(memory_conn, session_id, len(chunks))

    # Reuse cached fragments before the dirty boundary.
    prior_arc = ""
    arc_parts: list[str] = []
    for i in range(first_dirty):
        arc_parts.append(existing_by_index[i]["topology"])
        prior_arc = existing_by_index[i]["topology"]

    # Process dirty and new chunks.
    for i in range(first_dirty, len(chunks)):
        user_prompt = arc_user_template.replace("{transcript}", chunks[i])
        user_prompt = user_prompt.replace("{prior_arc}", prior_arc)
        try:
            fragment = call_ollama(model, system_prompt, user_prompt, ollama_url)
        except Exception as e:
            log.error("Arc summarizer failed for session %s at chunk %d: %s", session_id, i, e)
            if arc_parts:
                break
            return None
        arc_parts.append(fragment)
        upsert_arc_fragment(memory_conn, session_id, i, fragment, _chunk_hash(chunks[i]), len(conversation))
        prior_arc = fragment

    topology = "\n".join(arc_parts)
    return topology
