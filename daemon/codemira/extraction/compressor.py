import json
import urllib.request


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
        result = json.loads(resp.read())
        return result["message"]["content"]
