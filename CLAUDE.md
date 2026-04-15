# CodeMira - Project Guide

**Complex problems require simple and clear solutions.**

CodeMira is a developer memory system for OpenCode coding sessions. Two processes cooperate through a single per-project store:

- **Python daemon** (`daemon/codemira/`): persistent process launched by launchd. Polls OpenCode's SQLite for idle sessions, compresses tool I/O via Ollama, extracts memories via OpenRouter (GLM-5.1), embeds via `MongoDB/mdbr-leaf-ir-asym`, classifies links via Ollama, consolidates clusters. Serves `/health` and `/retrieve` on `127.0.0.1:9473`.
- **TypeScript plugin** (`plugin/src/`): loaded by OpenCode. Hooks `experimental.chat.messages.transform`. Extracts recent tool trace + user goal, calls an Ollama subcortical model for intent analysis, calls the daemon `/retrieve`, injects a `<developer_context>` HUD into the messages array before the LLM call.

Memory stores are per-project at `<project-worktree>/.codememory/memories.db` (+ `memories.index` hnswlib cache). No global store — widely applicable preferences re-extract per project (reinforcement, not duplication).

The User's name is Taylor.

## 🗺️ Nested CLAUDE.md Maintenance (Mandatory)
Subdirectories may contain `CLAUDE.md` files that serve as local orientation maps — file indexes, common patterns, and reusable helpers. **These are loaded automatically when you read files in that subtree.** They eliminate redundant exploration and prevent reinventing existing patterns.

**After every code change**, check whether the relevant directory's `CLAUDE.md` needs updating:
- **New file added?** Add a one-liner describing its purpose.
- **File deleted or renamed?** Remove or update its entry.
- **New pattern established?** Add it to "Patterns to Follow" if other files should replicate it.
- **Pattern changed?** Update the description so future sessions don't follow stale guidance.

If you skip this, the maps rot and become misleading — worse than having no map at all. Treat `CLAUDE.md` updates as part of the changeset, not an afterthought.

## 🚨 Critical Principles (Non-Negotiable)

### Technical Integrity
- **Evidence-Based Position Integrity**: Form assessments based on available evidence and analysis, then maintain those positions consistently regardless of the human's reactions, apparent preferences, or pushback. Don't adjust your conclusions to match what you think the human wants to hear - stick to what the evidence supports. When the human proposes actions that contradict your evidence-based assessment, actively push back and explain why the evidence doesn't support their proposal.
- **Brutal Technical Honesty**: Immediately and bluntly reject technically unsound or infeasible ideas & commands from the human. Do not soften criticism or dance around problems. Call out broken ideas directly as "bad," "harmful," or even "stupid" when warranted. Software engineering requires brutal honesty, not diplomacy or enablement! It's better to possibly offend the human than to waste time or compromise system integrity. They will not take your rejection personally and will appreciate your frankness. After rejection, offer superior alternatives that actually solve the core problem.
- **Direct Technical Communication**: Provide honest, specific technical feedback without hedging. Challenge unsound approaches immediately and offer better alternatives. Communicate naturally as a competent colleague.
- **Concrete Code Communication**: When discussing code changes, use specific line numbers, exact method names, actual code snippets, and precise file locations. Instead of saying "the retrieval logic" say "the `retrieve()` function in `daemon/codemira/retrieval/proactive.py` that calls `HybridSearcher.hybrid_search()`". Reference exact current state and exact proposed changes. Avoid vague terms like "stuff", "things", or "logic" — name specific functions, parameters, and return values.
- **Numeric Precision**: Never conjecture numbers without evidence. Use qualitative language ("a few seconds", "significantly lower recall") unless numbers derive from actual measurements, documented benchmarks, explicit requirements, or calculation.
- **No Tech-Bro Evangelism**: Avoid hyperbolic framing of routine technical work. Describe work accurately — a feature is a feature, a refactor is a refactor, a fix is a fix.

### Security & Reliability
- **Credential Management**: `OPENROUTER_API_KEY` is the only external secret. It must be baked into the launchd plist's `EnvironmentVariables` at install time — launchd agents do not inherit the user's shell env. `daemon/codemira/launchd.py` fails at install if the key isn't exported. If the key is missing at runtime, extraction logs an error and the cycle skips; the daemon does not substitute a fallback.
- **Fail-Fast Infrastructure**: Required infrastructure failures MUST propagate. Do not catch exceptions from SQLite, hnswlib, embeddings, the HTTP bridge, or OpenRouter and return `None`/`[]`/defaults — that masks outages as normal operation. `try/except` is only for: (1) adding context before re-raising, (2) legitimately optional paths (link classification falls back to `corroborates` if Ollama is down; consolidation skips a cluster on model error), (3) graceful degradation at the process boundary (plugin sets `daemonUnavailable` and stops injecting HUDs). An empty list means "no memories found". A raised exception means "infrastructure broke". Keep the distinction.
- **No Optional[X] Hedging**: Return the actual type or raise. Don't use `Optional[X]` to smuggle infrastructure failure as "maybe no data". Reserve `Optional` for genuine domain optionality (a memory may or may not have `source_session_id`).
- **Timezone Consistency**: All timestamps are UTC ISO-8601 strings produced by `datetime.now(timezone.utc).isoformat()`. Never use naive datetimes. OpenCode's `time_created`/`time_updated` columns are unix-millis integers; convert at the boundary, not internally.
- **Backwards Compatibility**: Don't deprecate; ablate. CodeMira is greenfield. Breaking changes are preferred — no compatibility shims, no parallel code paths, no `_old_` prefixes. Let the human know and move on.
- **Know Thy Self**: I (Claude) have a tendency to invent endpoints or reinvent patterns rather than reading what's already there. Always look at existing code (especially `daemon/codemira/server.py`, `plugin/src/index.ts`, and `prompts/`) before proposing new structure.

### Core Engineering Practices
- **Thoughtful Component Design**: Design components that reduce cognitive load and manual work. Handle complexity internally, expose simple APIs. `StoreManager.get(project_dir)` returns `(conn, index)` — callers don't open DBs, load schemas, or rebuild indexes. `HybridSearcher.hybrid_search()` takes a query + embedding + limits and returns ranked results — callers don't merge BM25 and ANN by hand.
- **Integrate Rather Than Invent**: When stdlib or OpenCode's plugin API provides a mechanism, use it. `urllib.request` over a new HTTP dependency. OpenCode's `experimental.chat.messages.transform` mutation pattern over a sidecar queue.
- **Root Cause Diagnosis**: Examine related files and dependencies before changing code. Address problems at their source — never adapt downstream to compensate for upstream bugs. (Example: when the plugin was querying a non-existent store, the fix was using `PluginInput.worktree` — not adding fallback paths to the daemon.)
- **Simple Solutions First**: Consider simpler approaches before adding complexity. Implement exactly what is requested without unrequested fallbacks, retries, or error handling. Unrequested "safety" features often create more problems than they solve.
- **Handle Pushback Constructively**: When the human inquires with "Is this right?" or "Are you sure?", they are asking you to self-reflect, not necessarily to change course. Think deeply and respond from evidence, not social pressure.
- **Challenge Incorrect Assumptions Immediately**: When the human is wrong about how something works, say so directly. Don't soften technical corrections.

### Design Discipline Principles

#### Make Strong Choices (Anti-Hedging)
Standardize on one format/approach unless concrete use cases require alternatives. Every "just in case" feature is technical debt. No hedging with "if available" fallbacks, no `Any` types when you know the structure, no supporting multiple formats "for flexibility" — pick one and enforce it with strong types. Example: the daemon supports a single embedding model (`MongoDB/mdbr-leaf-ir-asym`) at a fixed dimension (768). No model registry, no pluggable backends until a second use case exists.

#### Fail-Fast, Fail-Loud
Silent failures hide bugs during development and create mysterious behavior in production. Don't return `[]`/`{}` when parsing fails — it masks errors as "no data found". Use `warning`/`error` log levels for problems, not `debug`. Validate inputs at function entry. Raise `ValueError` with diagnostics, not generic `Exception`.

#### Types as Documentation and Contracts
Type hints are executable documentation. Avoid `Optional[X]`. Use `TypedDict` or `dataclass` for well-defined structures instead of `Dict[str, Any]`. `SearchResult` is a dataclass; memory payloads returned from the DB are `dict` because they pass through to JSON — the boundary justifies the choice.

**Replace positional tuples with named structures**: When a function returns multiple related values, use a dataclass or TypedDict. Named access like `result.memory_id` is self-documenting; positional `result[0]` requires memorizing order.

#### Naming Discipline = Cognitive Load Reduction
Variable names should match class/concept names — every mismatch adds cognitive overhead. `MemoryIndex` → `memory_index`, not `idx` or `vector_db`. Pick one term per concept (memory vs. note, extraction vs. processing, project vs. workspace). Method names match action — `extract_memories()` actually extracts, `is_duplicate_vector()` actually checks duplicates.

#### Forward-Looking Documentation
Write what code does, not what it replaced. Historical context → commit messages, not docstrings.

#### Standardization Over Premature Flexibility
Every code path is a potential bug. Don't add flexibility until you have concrete use cases. Wait for the second use case before abstracting.

#### Method Granularity Test
If the docstring is longer than the code, inline the method. One-line wrappers add indirection with no benefit.

#### Hardcode Known Constraints
Don't parameterize what won't vary. Constants with comments explaining why ("hnswlib default", "OpenRouter spec requirement").

## 🏗️ Architecture & Design

### Process Topology
- **Daemon loop** (`daemon/codemira/daemon.py:run_daemon`): polls at `poll_interval_minutes` (default 15). Each cycle: discover OpenCode DB → read project worktrees → find idle sessions → process each (compress → extract → embed → store → link). Consolidation runs at `consolidation_interval_hours` (default 24) across all known project stores.
- **HTTP bridge** (`daemon/codemira/server.py`): `HTTPServer` on `127.0.0.1:{http_port}` (default 9473). Single-threaded `BaseHTTPRequestHandler`. `POST /retrieve` accepts subcortical output and returns ranked memories; `GET /health` reports Ollama and embedding-model status.
- **Plugin hook** (`plugin/src/index.ts`): closure over pinned-memory state. Fires on every `experimental.chat.messages.transform`. Pure transforms live in `plugin/src/pure.ts` so they can be unit-tested under Bun without a running OpenCode.

### Project Root Resolution
The daemon keys per-project stores by `project.worktree` from OpenCode's SQLite. **The plugin must pass `PluginInput.worktree` to `/retrieve` — not `PluginInput.directory`.** `directory` is the session cwd and may be a subdirectory of the project root; using it points retrieval at a store the daemon never writes to. Any code path that touches project scoping must use `worktree`.

### Fail-Fast Bootstrap
- Missing `OPENROUTER_API_KEY`: `launchd install` refuses to write the plist.
- Missing prompt files: `FileNotFoundError` at the call site (prompts loaded lazily per call, not at startup).
- Missing Ollama: daemon logs + skips the compression/extraction/link/consolidation step that needed it; plugin sets `daemonUnavailable` and stops injecting HUDs.
- Missing embedding model: first call downloads `MongoDB/mdbr-leaf-ir-asym` via `sentence-transformers`; subsequent calls are cached.

### Storage Model
`memories.db` (SQLite WAL) is the source of truth. `memories.index` (hnswlib) is a rebuildable cache — never authoritative. Every write path calls `MemoryIndex.rebuild_after_write(conn)`. FTS5 stays in sync via three triggers (insert/update/delete) because `content='memories'` doesn't auto-sync.

## 🧭 Codebase Patterns

### Prompts On Disk
All LLM prompts live in `prompts/*.txt` and are loaded via `load_prompt(name, prompts_dir)`. Never embed prompts in code. Missing prompt files raise `FileNotFoundError`. Callers pass `prompts_dir` explicitly to enable testing with alternate prompts.

### Ollama via stdlib HTTP
All Ollama calls go through `call_ollama()` in `daemon/codemira/extraction/compressor.py` using `urllib.request`. No `ollama-python` dependency. The plugin side uses `fetch()` for the same reason (stdlib everywhere).

### Entity Extraction is LLM-Based
`daemon/codemira/extraction/dedup.py:extract_entities` calls `gemma4:e2b` via Ollama with `prompts/entity_extraction_*.txt`. The model returns a JSON array of `{name, type}` entries. `name` is lowercased and deduped; `type` is validated against `VALID_ENTITY_TYPES` and falls back to `"other"`. Callers pass `model`, `ollama_url`, and `prompts_dir` explicitly — there are no defaults.

### Hybrid Retrieval
`HybridSearcher.hybrid_search()` issues BM25 (via FTS5) + ANN (via hnswlib) with `limit * 2` each, merges via Reciprocal Rank Fusion (`RRF_K=60`), and caps at `limit`. Fresh search results are augmented by `hub_discovery()` (entity-indexed + link-graph memories) and pinned memories from the previous iteration. Final cap: `max_surfaced_memories` (default 8).

### HUD Injection
The plugin generates fresh `msg_<26hex>` / `prt_<26hex>` IDs on every call (via `randomBytes(13).toString("hex")`) and pushes a `user` message with a synthetic text part containing the HUD. The IDs never touch OpenCode's DB — they live only in the in-memory `output.messages` array the hook mutates. No cleanup logic; messages are re-fetched from DB every iteration, so stale HUDs can't accumulate.

## ⚡ Performance & Tool Usage
- **Synchronous Python**: Daemon is single-threaded polling + a single-threaded `HTTPServer`. No `async`. Keep it simple until profiling demands otherwise.
- **Plugin is async-native**: OpenCode's Bun runtime is async; the hook uses `fetch()` naturally. Don't spawn subprocesses from the plugin.
- **hnswlib rebuild on write**: Fine for local stores (<10k memories). Optimize only when a store outgrows it.
- **Haiku Agents — Big Fast Idiot Rules**: Haiku is fast and cheap but cannot reason, infer intent, or make judgment calls. Use Haiku only for deterministic file operations (find/replace/grep), mechanical edits with exact specifications, and schema-constrained execution. Never for research, architectural analysis, code review, or any task requiring semantic understanding. Use Sonnet or Opus for anything requiring thought.

## 📝 Implementation Guidelines

### Implementation Approach
When modifying files, write as if the new code was always the plan. Never reference removals ("previously we did X, now we do Y"). Understand surrounding architecture first.

### Plan Mode
🚨 **NEVER autonomously enter plan mode.** Only when the user explicitly activates it (e.g., via `/plan`).

## 🔄 Continuous Improvement
- Convert specific feedback into general principles. Consider multiple approaches before implementing.
- Enthusiasm to fix issues shouldn't override testing discipline.

## 📚 Reference Material

### Commands
- **Inspect a project's memory store**:
  `sqlite3 <worktree>/.codememory/memories.db "SELECT id, category, importance, substr(text,1,80) FROM memories WHERE is_archived=0 ORDER BY importance DESC LIMIT 20;"`
- **Inspect OpenCode sessions** (macOS):
  `sqlite3 "$HOME/Library/Application Support/opencode/opencode.db" "SELECT id, time_updated FROM session ORDER BY time_updated DESC LIMIT 10;"`
- **Daemon logs**: `tail -f ~/Library/Logs/codemira/daemon.log`
- **Health check**: `curl -s http://localhost:9473/health`
- **Install launchd agent**: `python -m codemira.launchd install` (requires `OPENROUTER_API_KEY` exported)
- **Run tests**: `python -m pytest tests/ -v` (from repo root); `bun test` in `plugin/`

### Git Workflow
- **MANDATORY**: Invoke the `git-workflow` skill BEFORE every commit
- **Skill command**: `Skill(skill: "git-workflow")`
- **What it provides**: Complete commit message format, staging rules, semantic prefixes, post-commit summary requirements, and critical anti-patterns to avoid
- **Never skip**: This skill contains mandatory formatting and process requirements for all git operations

### Pydantic Standards
Config objects use `pydantic-settings.BaseSettings`. See `daemon/codemira/config.py`. Use `Field()` with descriptions and defaults when adding new settings. Env prefix is `CODEMIRA_`. Naming: `*Config` for config classes.

---

# Critical Anti-Patterns to Avoid

## ❌ Plugin/Daemon Project Scoping Mismatch
**Wrong**: Sending `input.directory` to `/retrieve`.
**Right**: Sending `input.worktree`.
**Why it matters**: The daemon keys stores by `project.worktree`. `directory` is the session cwd and may be a subdirectory. A mismatch means retrieval queries hit an empty store while the daemon writes to a different one.

## ❌ Consolidation With Stale Embeddings
**Wrong**: Reusing `memories[0].embedding` as the embedding for the consolidated memory.
**Right**: Re-embed `consolidated_text` via `EmbeddingsProvider.get().encode_deep([text])[0]`.
**Why it matters**: The consolidated text is different from any source memory's text. A stale embedding means ANN queries matching the new wording won't surface it.

## ❌ Silent Vector Dedup on Empty Stores
**Wrong**: Assuming `is_duplicate_vector()` catches near-duplicates from the first extraction batch on a fresh store.
**Reality**: `MemoryIndex.index` is `None` until the first rebuild, so `add_vector()` no-ops and within-batch duplicates slip past vector dedup. Text dedup (`rapidfuzz`) still runs.
**Mitigation**: Keep `is_duplicate_text` thresholds conservative enough to catch near-duplicates without a vector signal.

## ❌ Infrastructure Hedging (Faux-Resilience)
**Example**: `try: result = db.query() except: return []` making database outages look like empty data.
**Lesson**: Required infrastructure failures must propagate. Returning `None`/`[]` when SQLite/hnswlib/embeddings fail masks outages as normal operation. Only catch exceptions to add context before re-raising, or for legitimately optional features (link classification fallback, consolidation skip).

## ❌ Over-Engineering Without Need
**Example**: Adding severity levels to errors when binary worked/failed suffices.
**Lesson**: Push back on complexity. If you can't explain why it's needed, it probably isn't.

## ❌ Cross-Process Coupling via Fixed IDs
**Wrong**: Using `"code-memory-hud"` as a fixed message ID.
**Right**: Generating fresh `msg_<hex>` and `prt_<hex>` IDs per hook call via `randomBytes`.
**Why it matters**: Fixed IDs don't match OpenCode's ID format (may trip validators), and they invite phantom cleanup logic for a HUD that is never persisted.

## ❌ Premature Abstraction
**Example**: Creating a `BackendRegistry` for one embedding model, or a `StorageAdapter` interface for a single SQLite implementation.
**Lesson**: Start with the straightforward solution. Abstractions emerge from repeated patterns, not from anticipated future needs. Wait for the second use case.

## ❌ Launchd Plist Without EnvironmentVariables
**Wrong**: Writing a plist that only sets `ProgramArguments` and `KeepAlive`, assuming the shell env will flow through.
**Right**: Collecting `OPENROUTER_API_KEY` (and any `CODEMIRA_*` / `OPENCODE_DB`) at install time and XML-escaping them into the plist's `EnvironmentVariables` dict.
**Why it matters**: launchd agents do not inherit the user's shell env. Without `EnvironmentVariables` in the plist, the daemon starts but extraction fails every cycle.
