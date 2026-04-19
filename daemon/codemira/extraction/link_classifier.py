import logging

from codemira.llm import call_llm
from codemira.extraction.extractor import load_prompt

log = logging.getLogger(__name__)

VALID_LINK_TYPES = {"corroborates", "conflicts", "supersedes", "refines", "contextualizes"}


def classify_link(
    text_a: str,
    text_b: str,
    model: str,
    base_url: str,
    api_key: str,
    prompts_dir: str,
) -> str:
    system_prompt = load_prompt("link_classification_system", prompts_dir).render()
    user_prompt = load_prompt("link_classification_user", prompts_dir).render(text_a=text_a, text_b=text_b)
    try:
        response = call_llm(model, system_prompt, user_prompt, base_url, api_key)
    except Exception as e:
        log.warning("Link classification call failed (%s); defaulting to corroborates", e)
        return "corroborates"
    token = response.strip().lower().split()[0] if response.strip() else ""
    token = token.rstrip(".,:;!?")
    if token in VALID_LINK_TYPES:
        return token
    log.warning("Link classifier returned unrecognized token %r; defaulting to corroborates", token)
    return "corroborates"
