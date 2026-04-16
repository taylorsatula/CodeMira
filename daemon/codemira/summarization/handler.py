import logging
import sqlite3

from codemira.extraction.chunker import estimate_token_count, PROMPT_OVERHEAD_TOKENS
from codemira.extraction.compressor import call_ollama
from codemira.extraction.extractor import load_prompt
from codemira.store.db import upsert_arc_summary

log = logging.getLogger(__name__)


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


def _split_into_turns(transcript: str) -> list[str]:
    turns: list[str] = []
    current_lines: list[str] = []
    for line in transcript.split("\n"):
        if line.startswith("User:") and current_lines:
            turns.append("\n".join(current_lines))
            current_lines = []
        current_lines.append(line)
    if current_lines:
        turns.append("\n".join(current_lines))
    return [t for t in turns if t.strip()]


def _chunk_transcript(transcript: str, context_length: int) -> list[str]:
    prior_arc_estimate = 1024
    chunk_budget = max(75_000, int(0.7 * context_length)) - PROMPT_OVERHEAD_TOKENS - prior_arc_estimate
    chunk_budget = max(chunk_budget, 1024)
    if estimate_token_count(transcript) <= chunk_budget:
        return [transcript]
    turns = _split_into_turns(transcript)
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
) -> str | None:
    from codemira.opencode_db import read_session_conversation
    conversation = read_session_conversation(opencode_conn, session_id)
    if len(conversation) < 4:
        return None
    transcript = _format_raw_transcript(conversation)
    chunks = _chunk_transcript(transcript, context_length)
    system_prompt = load_prompt("arc_summarizer_system", prompts_dir)
    arc_user_template = load_prompt("arc_summarizer_user", prompts_dir)
    prior_arc = ""
    arc_parts: list[str] = []
    for chunk in chunks:
        user_prompt = arc_user_template.replace("{transcript}", chunk)
        user_prompt = user_prompt.replace("{prior_arc}", prior_arc)
        try:
            fragment = call_ollama(model, system_prompt, user_prompt, ollama_url)
        except Exception as e:
            log.error("Arc summarizer failed for session %s: %s", session_id, e)
            if arc_parts:
                break
            return None
        arc_parts.append(fragment)
        prior_arc = fragment
    topology = "\n".join(arc_parts)
    upsert_arc_summary(memory_conn, session_id, topology, len(conversation))
    return topology
