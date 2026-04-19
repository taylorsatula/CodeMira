from typing import Callable

from codemira.llm import call_llm


def build_tool_compressor(model: str, base_url: str, api_key: str, prompts_dir: str) -> Callable[[dict], str | None]:
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
        compressed = call_llm(model, system_prompt, user_msg, base_url, api_key)
        return f"{TOOL_PREFIX} {tool_name} — {compressed}"

    return _compress
