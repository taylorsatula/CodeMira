import json
import urllib.request
import os

from codemira.errors import ExtractionError
from codemira.store.db import get_existing_memory_texts
from codemira.extraction.dedup import is_duplicate_text


def call_api_model(model: str, system: str, user: str, api_key: str) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]
    }).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                                  data=payload,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
    try:
        result = json.loads(body)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"OpenRouter returned non-JSON response: {e}") from e
    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, TypeError, IndexError) as e:
        raise ExtractionError(f"OpenRouter response missing expected structure: {e}") from e


def load_prompt(name: str, prompts_dir: str | None = None) -> str:
    if prompts_dir is None:
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "..", "prompts")
    path = os.path.join(prompts_dir, f"{name}.txt")
    with open(path) as f:
        return f.read()


def _build_existing_memories_str(existing_texts: list[str], prior_chunk_texts: list[str] | None) -> tuple[str, list[str]]:
    combined_texts = list(existing_texts)
    if prior_chunk_texts:
        combined_texts.extend(prior_chunk_texts)
    lines = []
    if existing_texts:
        lines.extend(f"- {t}" for t in existing_texts)
    if prior_chunk_texts:
        lines.append("--- Previously extracted from this session ---")
        lines.extend(f"- {t}" for t in prior_chunk_texts)
    existing_str = "\n".join(lines) if lines else "None"
    return existing_str, combined_texts


def extract_memories(
    compressed_transcript: str,
    conn,
    extraction_model: str,
    api_key: str,
    deduplicate_text_threshold: float = 0.95,
    prompts_dir: str | None = None,
    prior_chunk_texts: list[str] | None = None,
) -> list[dict]:
    system_prompt = load_prompt("extraction_system", prompts_dir)
    existing_texts = get_existing_memory_texts(conn)
    existing_str, combined_texts = _build_existing_memories_str(existing_texts, prior_chunk_texts)
    user_prompt = load_prompt("extraction_user", prompts_dir)
    user_prompt = user_prompt.replace("{compressed_transcript}", compressed_transcript)
    user_prompt = user_prompt.replace("{existing_memories}", existing_str)
    response_text = call_api_model(extraction_model, system_prompt, user_prompt, api_key)
    try:
        memories = json.loads(response_text)
    except json.JSONDecodeError:
        start = response_text.find("[")
        end = response_text.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                memories = json.loads(response_text[start:end])
            except json.JSONDecodeError as e:
                raise ExtractionError(f"Extraction model returned completely unparseable output: {e}") from e
        else:
            raise ExtractionError(f"Extraction model returned no array in output: {response_text[:200]}")
    if not isinstance(memories, list):
        raise ExtractionError(f"Extraction model returned {type(memories).__name__} instead of list")
    deduped = []
    for m in memories:
        if not isinstance(m, dict) or "text" not in m:
            continue
        if is_duplicate_text(m["text"], combined_texts, deduplicate_text_threshold):
            continue
        deduped.append(m)
    return deduped
