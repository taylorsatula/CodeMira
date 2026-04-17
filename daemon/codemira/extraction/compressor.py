import json
import urllib.request
from typing import Callable

from codemira.errors import ExtractionError


def call_ollama(model: str, system: str, user: str, url: str = "http://localhost:11434") -> str:
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "stream": False
    }).encode()
    req = urllib.request.Request(f"{url}/api/chat", data=payload,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
    try:
        result = json.loads(body)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"Ollama returned non-JSON response: {e}") from e
    try:
        return result["message"]["content"]
    except (KeyError, TypeError) as e:
        raise ExtractionError(f"Ollama response missing expected structure: {e}") from e


def ollama_tool_compressor(model: str, ollama_url: str, prompts_dir: str) -> Callable[[dict], str | None]:
    from codemira.extraction.extractor import load_prompt
    from codemira.extraction.transcript import TOOL_PREFIX
    system_prompt = load_prompt("compression_system", prompts_dir).render()

    def _compress(part: dict) -> str | None:
        state = part.get("state", {})
        if state.get("status") != "completed":
            return None
        tool_name = part.get("tool", "unknown")
        tool_input = state.get("input", {})
        tool_output = state.get("output", "")
        user_msg = f"{TOOL_PREFIX} {tool_name}\nArguments: {tool_input}\nResult: {tool_output[:500]}"
        compressed = call_ollama(model, system_prompt, user_msg, ollama_url)
        return f"{TOOL_PREFIX} {tool_name} — {compressed}"

    return _compress
