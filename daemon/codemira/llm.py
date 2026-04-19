import json
import urllib.request
from urllib.parse import urlparse

from codemira.errors import ExtractionError


def call_llm(model: str, system: str, user: str, base_url: str,
             api_key: str = "", timeout: int = 120) -> str:
    base_url = base_url.rstrip("/")
    parsed = urlparse(base_url)
    if not parsed.path or parsed.path == "/":
        raise ValueError(
            f"base_url must include the API path, e.g. http://localhost:11434/v1 — got {base_url}"
        )
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(f"{base_url}/chat/completions", data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    try:
        result = json.loads(body)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"LLM returned non-JSON response: {e}") from e
    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, TypeError, IndexError) as e:
        raise ExtractionError(f"LLM response missing expected structure: {e}") from e
