class ExtractionError(Exception):
    """Non-retryable extraction failure — the LLM returned unparseable output
    or an unexpected response structure. Retrying the same conversation will
    likely produce the same result, so the session should be marked as extracted
    to avoid infinite retry loops."""
