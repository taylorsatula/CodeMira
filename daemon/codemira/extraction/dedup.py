import json
import logging

from rapidfuzz import fuzz

from codemira.extraction.compressor import call_ollama
from codemira.store.index import MemoryIndex

log = logging.getLogger(__name__)

VALID_ENTITY_TYPES = {"library", "framework", "tool", "pattern", "protocol", "error", "other"}


def extract_entities(
    text: str,
    model: str,
    ollama_url: str,
    prompts_dir: str,
) -> list[dict]:
    from codemira.extraction.extractor import load_prompt
    system_prompt = load_prompt("entity_extraction_system", prompts_dir)
    user_template = load_prompt("entity_extraction_user", prompts_dir)
    user_prompt = user_template.replace("{text}", text)
    try:
        response = call_ollama(model, system_prompt, user_prompt, ollama_url)
    except Exception as e:
        log.warning("Entity extraction call failed (%s); returning no entities", e)
        return []
    response = response.strip()
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        start = response.find("[")
        end = response.rfind("]") + 1
        if start < 0 or end <= start:
            log.warning("Entity extractor returned non-JSON: %r", response[:200])
            return []
        try:
            parsed = json.loads(response[start:end])
        except json.JSONDecodeError:
            log.warning("Entity extractor returned malformed JSON: %r", response[:200])
            return []
    if not isinstance(parsed, list):
        return []
    seen: set[str] = set()
    entities: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        etype = item.get("type")
        if not isinstance(name, str) or not isinstance(etype, str):
            continue
        name = name.strip().lower()
        etype = etype.strip().lower()
        if not name:
            continue
        if etype not in VALID_ENTITY_TYPES:
            etype = "other"
        if name in seen:
            continue
        seen.add(name)
        entities.append({"name": name, "type": etype})
    return entities


def is_duplicate_text(new_text: str, existing_texts: list[str], threshold: float = 0.95) -> bool:
    for existing in existing_texts:
        if fuzz.ratio(new_text, existing) / 100.0 >= threshold:
            return True
    return False


def is_duplicate_vector(new_embedding: list[float], index: MemoryIndex,
                         threshold: float = 0.92) -> bool:
    results = index.search(new_embedding, k=1)
    if results and results[0][1] >= threshold:
        return True
    return False
