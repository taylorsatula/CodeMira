from pydantic_settings import BaseSettings, SettingsConfigDict


class DaemonConfig(BaseSettings):
    http_port: int = 9473
    idle_threshold_minutes: int = 60
    poll_interval_minutes: int = 15
    max_surfaced_memories: int = 8
    max_fresh_memories: int = 5
    tool_trace_window: int = 5
    memory_truncation_words: int = 20
    embedding_dimension: int = 768
    subcortical_model: str = "gemma4:e2b"
    extraction_model: str = "z-ai/glm-5.1"
    consolidation_interval_hours: int = 24
    consolidation_model: str = "gemma4:e4b"
    consolidation_similarity_threshold: float = 0.85
    link_similarity_threshold: float = 0.75
    deduplicate_text_threshold: float = 0.95
    deduplicate_cosine_threshold: float = 0.92
    hnsw_ef_construction: int = 200
    hnsw_m: int = 16
    hnsw_ef_search: int = 50
    ollama_url: str = "http://localhost:11434"
    opencode_db_path: str | None = None

    model_config = SettingsConfigDict(env_prefix="CODEMIRA_")
