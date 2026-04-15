import json
import urllib.request

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
