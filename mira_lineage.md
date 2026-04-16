# Mira → CodeMira Lineage Map

Concept-by-concept mapping between Mira (`../botwithmemory`) and CodeMira. Each entry pins both sides to specific files/functions so you can drill in. Use this when adapting Mira patterns to CodeMira or interpreting requests phrased in Mira's vocabulary (e.g., "entities are modules in the codebase").

---

## 1. The trigger: "this conversation segment is done, time to extract"

- **Mira**: `SegmentTimeoutEvent` (120 min inactivity) → `cns/.../segment_collapse_handler.py:collapse_segment()` — in-process event bus.
- **CodeMira**: two paths, both terminate at `process_idle_session()` (`daemon/codemira/daemon.py:46`):
  - **Idle poll**: launchd-driven `daemon/codemira/daemon.py:run_daemon` → `find_idle_sessions()` in `daemon/codemira/opencode_db.py:42` (idle threshold from `DaemonConfig`).
  - **Compaction-triggered**: plugin `event` hook in `plugin/src/index.ts` watches OpenCode's `session.compacted` bus event → POSTs `/extract` on the daemon → `RetrieveHandler._extract_session` (`daemon/codemira/server.py`) spawns a background thread.
- *Pin*: same idea — "this conversation segment is done, now extract" — but split across two triggers because the daemon doesn't share a runtime with OpenCode and we don't want to lose the original messages to compaction before the polling cycle gets to them.

## 2. Extraction orchestrator

- **Mira**: `lt_memory/processing/orchestrator.py` (chunk → build payload → submit) + `extraction_engine.py` (LLM payload assembly with UUID short-ID mapping).
- **CodeMira**: `daemon/codemira/extraction/extractor.py:extract_memories` (single-shot OpenRouter GLM-5.1, no chunking yet) + the compression pre-step `daemon/codemira/extraction/compressor.py:call_ollama` that doesn't exist in Mira (Mira's transcripts don't have giant tool I/O blobs).
- *Pin*: same role; CodeMira added a pre-extraction Ollama compression pass because tool traces would blow the extractor's context.

## 3. Extraction prompts

- **Mira**: prompts assembled in `extraction_engine.py` payload-building.
- **CodeMira**: `prompts/extraction_system.txt` + `prompts/extraction_user.txt`, loaded by `extraction/extractor.py:load_prompt`.
- *Pin*: this is the file you'd touch to redefine "what counts as a memory" for code.

## 4. Entity extraction (the "entities = modules" question)

- **Mira**: entities are people/places/projects, persisted via `batch_result_handlers.py`, indexed for hub discovery via Postgres `pg_trgm` fuzzy match in `lt_memory/.../hub_discovery.py`.
- **CodeMira**: `daemon/codemira/extraction/dedup.py:extract_entities` (line 14) calls Ollama `gemma4:e2b` with `prompts/entity_extraction_system.txt` + `prompts/entity_extraction_user.txt`. Entities stored via `store/db.py:get_or_create_entity` (line 188) + `link_memory_entity` (line 195). `VALID_ENTITY_TYPES` set in `dedup.py` constrains the taxonomy.
- *Pin*: **this is your "modules in the codebase" string** — to make entities mean modules, you change the type taxonomy in `dedup.py:VALID_ENTITY_TYPES` and rewrite `prompts/entity_extraction_*.txt`. Storage and hub lookup don't care what the strings mean.

## 5. Embeddings

- **Mira**: 768-dim `mdbr-leaf-ir-asym`, generated in `summary_generator.py` and during extraction.
- **CodeMira**: same model, `daemon/codemira/embeddings.py:EmbeddingsProvider` (singleton via `.get()`), 768-dim hardcoded.
- *Pin*: identical. Same dimensionality, same model, same provider pattern.

## 6. Hybrid retrieval (BM25 + ANN + RRF)

- **Mira**: `lt_memory/.../hybrid_search.py` (BM25 + vector, reciprocal rank fusion).
- **CodeMira**: `daemon/codemira/store/search.py:HybridSearcher.hybrid_search` (FTS5 BM25 + hnswlib ANN, RRF with `RRF_K=60`).
- *Pin*: same algorithm, different storage substrate (SQLite FTS5 vs Postgres FTS, hnswlib vs pgvector).

## 7. Hub discovery (entity + link expansion)

- **Mira**: `hub_discovery.py` — entity fuzzy match → memories mentioning entity → expand via links.
- **CodeMira**: `daemon/codemira/retrieval/hub_discovery.py:discover_by_entities` (line 6) + `discover_by_links` (line 18) + composite `hub_discovery()` (line 30).
- *Pin*: structurally identical, just SQL exact-match instead of `pg_trgm` fuzzy. Worth noting: CodeMira's exact-match means entity-name normalization in `extract_entities` matters more (lowercased there).

## 8. Link classification (corroborates / conflicts / supersedes)

- **Mira**: link types weighted in proactive reranking (conflicts=1.0, etc.).
- **CodeMira**: `daemon/codemira/extraction/link_classifier.py:classify_link` (Ollama call, prompts in `prompts/link_classification_*.txt`); falls back to `corroborates` if Ollama is down (per CLAUDE.md). Stored via `store/db.py:insert_memory_link` (line 222).
- *Pin*: same taxonomy, same fallback intent.

## 9. Subcortical preprocessor

- **Mira**: `cns/services/subcortical.py` — runs in-process, returns `SubcorticalResult{query_expansion, pinned_memory_ids, entities, complexity}`, has graduated pinned-memory pressure (warning at MAX-4, critical at MAX=15).
- **CodeMira**: lives **in the plugin** at `plugin/src/index.ts:callOllama` (line 51) using `prompts/subcortical_system.txt` + `prompts/subcortical_user.txt`; output parsed by `plugin/src/pure.ts:parseSubcorticalXml` (line 58); pinned state held in plugin closure across hook calls; result POSTed to daemon `/retrieve` (`callDaemonRetrieve`, line 73).
- *Pin*: **biggest topology shift.** Same conceptual role, but CodeMira moved it across the process boundary into the plugin so the daemon never sees the user's live message — only the subcortical's structured output.

## 10. Proactive surfacing / merge

- **Mira**: `lt_memory/.../proactive.py:ProactiveService.search_with_embedding` + `orchestrator._surface_memories` (merges similarity + hub + pinned, caps at MAX_SURFACED).
- **CodeMira**: `daemon/codemira/retrieval/proactive.py:retrieve` (line 10) called from `server.py:RetrieveHandler` POST `/retrieve`. Cap is `max_surfaced_memories` (default 8 — much tighter than Mira's 20, because it's competing for context with code).
- *Pin*: same merge pattern, smaller budget.

## 11. Pinned memory retention

- **Mira**: subcortical emits `pinned_memory_ids`, retained next turn; regex on `<mira:memory_ref="id">` tags also force-retains.
- **CodeMira**: plugin holds pinned IDs in JS closure between `experimental.chat.messages.transform` calls; subcortical XML output parsed for `<pinned>` IDs in `pure.ts:parseSubcorticalXml`.
- *Pin*: same idea, JS closure replaces in-process state.

## 12. HUD / context injection into the model

- **Mira**: memories formatted with `mem_<8hex> [●●●○○]` and injected into working memory context.
- **CodeMira**: `plugin/src/pure.ts:formatHud` (line 86) builds `<developer_context>` block; `plugin/src/index.ts` mutates `output.messages` in the hook, generating fresh `msg_<26hex>`/`prt_<26hex>` IDs per call via `generateOpencodeId` (line 13).
- *Pin*: same job (give the model a memory cheat-sheet); CodeMira's IDs are ephemeral and never persisted, because OpenCode re-fetches messages from its DB each turn.

## 13. Consolidation / decay

- **Mira**: continuous decay via `scoring_formula.sql` (multi-axis sigmoid: value, hub, mention, newness, recency, temporal). Memories die unless they earn it.
- **CodeMira**: `daemon/codemira/consolidation/cluster.py:find_clusters` + `consolidation/handler.py:consolidate_cluster` (line 20) + `run_consolidation` (line 73), runs every `consolidation_interval_hours` (default 24). Clusters near-duplicates and merges via LLM.
- *Pin*: **conceptually different.** Mira's decay is a continuous SQL-side scoring function; CodeMira's is a periodic batch dedup-and-merge. Same goal (prevent rot), different mechanism. If you ever wanted Mira-style continuous decay in CodeMira, this is the gap to close.

## 14. Storage

- **Mira**: Postgres + pgvector + RLS by user_id.
- **CodeMira**: SQLite WAL + FTS5 triggers + hnswlib cache, scoped by `project.worktree`. `store/manager.py:StoreManager.get(project_dir)` returns `(conn, index)`. Schema in `store/db.py:init_schema` (line 73). Project root resolution via `manager.py:project_store_paths` (line 15).
- *Pin*: user-scoped → project-scoped. The boundary moved from "who" to "where."

## 15. Extraction skip / idempotency

- **Mira**: segment-level state machine (active → collapsing → collapsed) prevents re-extraction.
- **CodeMira**: `extraction_log` table (`store/db.py:log_extraction` line 245, `mark_extraction_complete` line 263, `is_session_extracted` line 272) plus `_collect_extracted_session_ids` in `daemon.py:115`. Tracks `attempt_count` against `max_extraction_attempts` for retry-vs-give-up on infra failure.
- *Pin*: same goal (don't re-extract), simpler implementation because there's no segment lifecycle — just a flat "this session_id is done" log.

---

## Quick translation guide

When Taylor speaks in Mira vocabulary, look here first:

| If Taylor says... | Touch these files |
|---|---|
| "extraction prompt" | `prompts/extraction_system.txt`, `prompts/extraction_user.txt` |
| "entities are X" | `prompts/entity_extraction_*.txt` + `extraction/dedup.py:VALID_ENTITY_TYPES` |
| "subcortical should..." | `plugin/src/index.ts:callOllama` + `prompts/subcortical_*.txt` + `pure.ts:parseSubcorticalXml` |
| "hub discovery / link expansion" | `retrieval/hub_discovery.py` |
| "consolidation / decay" | `consolidation/handler.py` + `consolidation/cluster.py` |
| "segment collapse" | `daemon.py:process_idle_session` (the analog) |
| "RLS / user scoping" | `store/manager.py:project_store_paths` (project, not user) |
| "memory schema" | `store/db.py:init_schema` |
| "HUD / surfacing format" | `plugin/src/pure.ts:formatHud` |
