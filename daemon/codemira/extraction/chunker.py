from codemira.extraction.transcript import USER_PREFIX


def get_token_count(text: str) -> int:
    return len(text) // 4


PROMPT_OVERHEAD_TOKENS = 2048


def parse_turns(transcript: str) -> list[str]:
    turns: list[str] = []
    current_lines: list[str] = []
    for line in transcript.split("\n"):
        if line.startswith(USER_PREFIX) and current_lines:
            turns.append("\n".join(current_lines))
            current_lines = []
        current_lines.append(line)
    if current_lines:
        turns.append("\n".join(current_lines))
    return [t for t in turns if t.strip()]


def build_chunks(transcript: str, budget: int) -> list[str]:
    if get_token_count(transcript) <= budget:
        return [transcript]
    turns = parse_turns(transcript)
    chunks: list[str] = []
    current_turns: list[str] = []
    current_tokens = 0
    for turn in turns:
        turn_tokens = get_token_count(turn)
        if current_turns and current_tokens + turn_tokens > budget:
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


def build_extraction_chunks(transcript: str, context_length: int, existing_memories_token_estimate: int = 0, chunk_target_tokens: int = 75_000) -> list[str]:
    chunk_budget = max(chunk_target_tokens, int(0.7 * context_length)) - PROMPT_OVERHEAD_TOKENS - existing_memories_token_estimate
    chunk_budget = max(chunk_budget, 1024)
    return build_chunks(transcript, chunk_budget)
