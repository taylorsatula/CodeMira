# CodeMira: Implementation Plan (v0.2)

## Architecture Overview

Two processes, one store per project, one HTTP bridge:

```
┌─────────────────────────────────┐     ┌──────────────────────────────────┐
│         OPENCODE PLUGIN          │     │      BACKGROUND DAEMON          │
│  (TypeScript, runs in OpenCode) │     │  (Python, persistent, independent)│
│                                 │     │                                  │
│  1. Read recent tool trace      │     │  HTTP API (bridge for plugin):    │
│  2. Run subcortical (Ollama)    │     │    POST /retrieve                 │
│  3. Call daemon /retrieve       │◄────│    GET  /health                   │
│  4. Inject HUD via msg.transform│     │                                  │
│                                 │     │  Background loop:                 │
│                                 │     │    1. Poll for idle sessions       │
│                                 │     │    2. Read conversation from SQLite │
│                                 │     │    3. Compress tool I/O (local 2B) │
│                                 │     │    4. Extract memories (API model)│
│                                 │     │    5. Embed + store memories       │
│                                 │     │    6. Consolidate (periodic)       │
└──────────────┬──────────────────┘     └──────────────┬───────────────────┘
               │                                         │
               └─────────────┬───────────────────────────┘
                             ▼
                    ┌─────────────────────┐
                    │  MEMORY STORE       │
                    │  .codememory/        │
                    │  memories.db +       │
                    │  hnswlib index       │
                    └─────────────────────┘
```

The memory store lives at `.codememory/memories.db` in the project root. One store per project. No global store — there is no reliable way to automatically differentiate project-scoped from developer-scoped memories, and requiring a human to classify every extraction defeats the purpose of an autonomous system. Instead, memories are simply extracted and stored per-project. If a developer's preference ("prefers threading over asyncio") applies across projects, it gets re-extracted in each project's conversations and stored in each project's store. This is a feature, not duplication — it reinforces the knowledge and means each project's store is self-contained.

The `.codememory/` directory is committable to git. Open source projects can ship institutional knowledge so new contributors' coding agents are immediately effective on first clone. Projects that don't want to commit memories add `.codememory/` to `.gitignore`.

Both processes read and write to the same store. The plugin reads on every LLM call (via the daemon's HTTP bridge). The daemon writes after idle timeout and runs consolidation on its own schedule.

The daemon is a persistent background process on the user's machine, independent from OpenCode. It starts on login (via launchd on macOS) and runs continuously. This makes idle detection trivial — the daemon owns its own polling loop and lifecycle. No dependency on OpenCode's process staying alive, no need for plugin-based event triggers, no race conditions between OpenCode restarting and extraction state being lost.

---

## Python-Plugin Bridge (HTTP API)

The plugin is TypeScript running inside OpenCode's Bun process. The embedding model (`mdbr-leaf-ir-asym`) and memory store (SQLite + hnswlib + FTS5) are Python. The daemon IS the bridge — it exposes a small HTTP API that the plugin calls for retrieval. No separate bridge process needed.

**Why not put Python in the plugin?** The plugin runs in Bun's JavaScript runtime. There is no Python runtime inside Bun. Calling Python from the plugin would require spawning a subprocess per request, managing its lifecycle, and handling IPC — effectively building a worse version of an HTTP bridge. The daemon already runs as a persistent Python process. It owns the memory store. Exposing a few HTTP endpoints is the simplest interface with the cleanest separation.

### Daemon HTTP Endpoints

**`GET /health`** — Health check. Returns daemon status and dependency availability.

```json
{
  "status": "ok",
  "ollama": true,
  "embedding_model": true,
  "version": "0.1.0"
}
```

**`POST /retrieve`** — Memory retrieval for the plugin. Takes subcortical output, returns surfaced memories.

Request:
```json
{
  "query_expansion": "implementing OAuth refresh token rotation; token expiry and renewal patterns",
  "entities": ["OAuth", "refresh_token"],
  "pinned_memory_ids": ["mem_a1b2c3d4", "mem_e5f6g7h8"],
  "project_root": "/Users/dev/myproject"
}
```

Response:
```json
{
  "memories": [
    {
      "id": "mem_a1b2c3d4",
      "text": "Prefers threading over asyncio for concurrent I/O",
      "importance": 0.9,
      "category": "rejected_alternative"
    }
  ],
  "degraded": false
}
```

The `degraded` flag is set when retrieval ran with partial failures (Ollama down, index corrupt, etc.) but still returned some results. When the daemon is completely unavailable, the plugin gets a connection error — that's the graceful degradation path.

### Graceful Degradation

Both processes follow the same rule: **throw a flag, continue without.** No crashing, no silent empty results that mask failures.

**Plugin degradation**: When the daemon is unreachable or `/retrieve` returns an error, the plugin sets an in-memory `daemonUnavailable` flag. On subsequent LLM calls, it skips the daemon call and does NOT inject a HUD. No empty `<developer_context>` blocks that train the LLM to ignore the HUD. The flag resets when the daemon becomes reachable again (checked via `/health`).

**Daemon degradation**: When Ollama is down, the daemon logs the failure and skips extraction/compression for that cycle. When the embedding model fails, individual memories that can't be embedded are stored without embeddings (embedding column is NULL) and are excluded from ANN search but still findable via BM25. When SQLite operations fail, the error propagates — the memory store is required infrastructure, not optional.

### Plugin Retrieval Flow (Updated)

The original plan had the plugin calling Ollama for the subcortical, then calling the daemon for retrieval. The actual flow:

1. **Extract tool trace** (TypeScript, from `output.messages`)
2. **Call Ollama** for subcortical (HTTP POST to `localhost:11434/api/chat`)
3. **Parse subcortical XML** → extract `query_expansion`, `entities`, `keep` IDs
4. **Call daemon `/retrieve`** (HTTP POST to `localhost:9473/retrieve`) with subcortical output
5. **Format HUD** from returned memories (TypeScript)
6. **Inject HUD** into `output.messages` (TypeScript)

Steps 1, 2, 5, 6 are TypeScript. Steps 3-4 cross the bridge. The daemon handles embedding, hybrid search, hub discovery, retention filtering, and merge+cap — all Python.

---

## Component 1: OpenCode Plugin (Retrieval Path)

Runs on every LLM call via `experimental.chat.messages.transform`.

### OpenCode Touchpoints

**Hook registration**: Plugin function returns a `Hooks` object. Hook closures capture state that persists across invocations (process lifetime).

**Hook fires at**: `packages/opencode/src/session/prompt.ts:1483` — after messages are fetched from DB via `MessageV2.filterCompactedEffect(sessionID)` (line 1327), reminders inserted via `insertReminders` (line 1413), and system-reminder wrapping of interleaved user messages (lines 1465-1481). Before system prompt composition (`sys.skills`, `sys.environment`, `instruction.system`) (lines 1485-1488), conversion to AI SDK `ModelMessage[]` via `MessageV2.toModelMessagesEffect(msgs, model)` (line 1489), and the actual LLM call via `handle.process(...)` (line 1494). The `output.messages` array is the LIVE `msgs` array (not a clone). Mutations flow directly to the LLM.

**Also fires during compaction**: `packages/opencode/src/session/compaction.ts:219` — but compaction clones first with `structuredClone(messages)`, so mutations there do NOT affect the original messages. Our plugin should be aware it may be called in both contexts.

**Mutation precedent**: `insertReminders` (prompt.ts lines 224-358) directly mutates parts on user messages using `userMessage.parts.push({...synthetic: true})`. This is the same pattern we use for HUD injection — it's supported and precedented internally.

**Downstream consumption**: After the hook returns, the mutated `msgs` flows through `toModelMessagesEffect` (message-v2.ts lines 647-838), which converts each `WithParts` entry to a `UIMessage`, then `convertToModelMessages()` produces the final `ModelMessage[]` sent to the LLM. A user message with text parts converts normally — our HUD will be included.

**Messages array type**: `MessageV2.WithParts[]` — each entry is `{ info: User | Assistant, parts: Part[] }`. Defined in `packages/opencode/src/session/message-v2.ts:511-515`.

**Tool call detection**: Tool calls are `Part` entries where `part.type === "tool"` on assistant messages. Key fields:
- `part.tool` — tool name (e.g., `"bash"`, `"read"`, `"write"`)
- `part.callID` — unique call identifier
- `part.state.status` — `"pending"`, `"running"`, `"completed"`, or `"error"`
- `part.state.input` — tool arguments (Record<string, any>)
- `part.state.output` — tool result (string, on completed)
- `part.state.error` — error message (string, on error)

**Text detection**: `part.type === "text"` with `part.text` for content. `part.synthetic` marks non-user content.

**HUD injection**: Push a `WithParts` entry with `info.role === "user"` and a `TextPart` with `synthetic: true`. No need to track/remove previous HUD — messages are fetched fresh from DB each iteration, so the in-memory HUD is never persisted. Just inject fresh each time.

**Plugin init**: `PluginInput` provides `client` (SDK client), `$` (Bun shell), `directory`, `serverUrl`. Plugin can use `Bun.spawn()` to start background processes. Plugin options passed as second arg from `opencode.json` config.

**Event bus**: The `event` hook receives all bus events. `session.idle` fires when a session goes idle. Not needed for daemon-based extraction but available for plugin-level coordination.

### Step 1: Extract Recent Tool Trace from Messages

Walk `output.messages` backward to find the last N tool calls. Filter for `info.role === "assistant"`, then scan `parts` for `part.type === "tool"` with `part.state.status === "completed"`. Extract:
- `part.tool` — tool name
- `part.state.input` — arguments (file path, search pattern, command, etc.)
- `part.state.output` — result (truncated for the compressed trace)
- `part.state.title` — human-readable summary if available

Format as compressed `<action>` XML elements. The most recent user message is extracted from `info.role === "user"` entries (the original goal).

N = configurable, default 5 tool calls. This is the sliding window of "what the agent is currently doing."

### Step 2: Run Subcortical (via Ollama)

Call the local Gemma 4 E2B model via Ollama. Ollama is the standard choice — simple to set up, many developers already have it running, and it provides a local HTTP API at `localhost:11434` with OpenAI-compatible endpoints. Input: the system prompt + user prompt template with `{user_message}`, `{recent_actions}`, `{pinned_memories}` substituted.

The subcortical produces:
- `query_expansion`: 2-4 semicolon-separated phrases
- `entities`: Technical concepts for hub discovery
- `keep`: Comma-separated memory IDs to retain from previous iteration

Runtime target: <100ms on Q4 quantization.

### Step 3: Call Daemon /retrieve

Send `{query_expansion, entities, pinned_memory_ids, project_root}` to `POST /retrieve` on the daemon's HTTP server. The daemon handles embedding, hybrid search, hub discovery, retention filtering, and capping internally. Returns the final surfaced memories ready for HUD injection.

If the daemon is unreachable, set the `daemonUnavailable` flag and skip HUD injection for this and subsequent calls. Periodically retry via `/health`.

### Step 4: Inject HUD via messages.transform

The surfaced memories are injected into the messages array as a HUD block at the end of the conversation history, via `experimental.chat.messages.transform`. This is the same pattern MIRA uses — memories ride with the conversation tail, not in the system prompt. The system prompt is for broad behavioral instructions. Memories are turn-specific context that shifts with the task. Putting them in the system prompt dilutes both: behavioral instructions compete with shifting context, and memories lose salience by being buried in a static block.

**Injection mechanism**: Push a new `WithParts` entry to `output.messages`:
```typescript
{
  info: { id: "code-memory-hud", sessionID, role: "user", time: { created: Date.now() }, agent, model },
  parts: [{
    id: "code-memory-hud-part",
    messageID: "code-memory-hud",
    sessionID,
    type: "text",
    text: "<developer_context>\n...\n</developer_context>",
    synthetic: true,
  }]
}
```

**No cleanup needed**: The `experimental.chat.messages.transform` hook receives messages fetched fresh from DB each iteration (prompt.ts:1327). Our injected HUD is in-memory only — it's never persisted to OpenCode's SQLite. On the next iteration, the DB fetch produces a clean array without the old HUD. We simply inject the fresh one. No tracking, no removal, no breadcrumb trail.

**Prompt caching is preserved**: The DB-sourced conversation prefix is identical across iterations. The HUD is always appended at the tip as a new message. Since prompt caching (both explicit prefix caching and implicit KV cache reuse) is prefix-based across all major providers, the stable conversation prefix (everything from DB) hits the cache. The HUD is in the uncached suffix, which is correct — it changes every iteration anyway. Cache savings are identical to what OpenCode would achieve without the plugin.

### Step 5: Cache State for Next Iteration

Store the surfaced memory IDs and their texts in plugin state (in-memory closure). On the next `experimental.chat.messages.transform` call, these become `pinned_memories` that the subcortical evaluates for retention.

Plugin state persistence: in-memory during a session. When the plugin is loaded, state is empty. It accumulates across loop iterations within a session and resets between sessions. No need for cross-session state — the memory store handles that.

---

## Component 2: Background Daemon (Storage Path)

Runs as a persistent Python process. Serves the HTTP bridge for the plugin AND monitors OpenCode's SQLite database for idle sessions to extract memories.

### OpenCode Database Discovery

The daemon must find OpenCode's SQLite database (`opencode.db`) to poll for idle sessions. OpenCode stores its database using platform-specific XDG base directories:

| Platform | Default Path |
|---|---|
| macOS | `~/Library/Application Support/opencode/opencode.db` |
| Linux | `~/.local/share/opencode/opencode.db` |

The `OPENCODE_DB` environment variable overrides the default path. Channel variants use `opencode-{channel}.db`.

The daemon discovers the database path by:
1. Check `OPENCODE_DB` env var — if set, use it
2. Check platform-specific XDG data directory for `opencode/opencode.db`
3. Fall back to common locations: `~/.local/share/opencode/opencode.db`, `~/Library/Application Support/opencode/opencode.db`

The daemon reads OpenCode's database in read-only mode (SELECT queries only). It never writes to OpenCode's database. All writes go to `.codememory/memories.db`.

### Step 1: Detect Idle Session

The daemon polls OpenCode's single global SQLite database (`opencode.db`) for sessions where:
- `time_updated` is older than `IDLE_THRESHOLD` (configurable, default 60 minutes)
- No extraction has been run for this session (tracked in each project store's `extraction_log` table)
- The session has at least `MIN_MESSAGES` messages (default 4 — skip trivial sessions)

The query joins `session` against `project` and returns `project.worktree` for each idle session. That worktree path is the project root used to open the correct per-project memory store at `<worktree>/.codememory/memories.db`. OpenCode's `session.directory` is the cwd where the session was launched and may be a subdirectory — it is NOT used for routing; only `project.worktree` is.

Polling interval: configurable, default 15 minutes. This is a lightweight timestamp check — no heavy queries.

Because the daemon is persistent and independent, idle detection is straightforward. The daemon owns its own event loop and polling schedule. No dependency on OpenCode's process lifecycle, no need for plugin-based event triggers, no state lost on OpenCode restart. The daemon watches the filesystem (OpenCode's SQLite file) and reacts on its own terms.

### Step 2: Read Conversation from SQLite

Query OpenCode's `message` and `part` tables for the session:

```sql
SELECT m.id, m.data, p.data
FROM message m
JOIN part p ON p.message_id = m.id
WHERE m.session_id = ?
ORDER BY m.time_created, p.id
```

Parse the JSON `data` fields to extract:
- User text messages
- Assistant text messages
- Tool call parts (name, arguments, result)

OpenCode's schema stores `data` as JSON text. The `message.data` column contains the `MessageV2.Info` object minus `id` and `sessionID` (those are separate columns). The `part.data` column contains the part payload minus `id`, `sessionID`, `messageID`. When hydrating, these fields are spread back in:
- Message: `{ ...data, id: row.id, sessionID: row.session_id }`
- Part: `{ ...data, id: row.id, sessionID: row.session_id, messageID: row.message_id }`

Session IDs use format `ses_<12 hex chars><14 base62 chars>`. Message IDs use `msg_` prefix, part IDs use `prt_` prefix.

### Step 3: Compress Tool I/O (Local 2B Model)

For each tool call in the conversation, run the local Gemma 4 E2B model via Ollama with a compression prompt:

```
Describe what this tool call did in 1-2 sentences. Focus on what was attempted and the outcome, not the full output.

Tool: read_file
Arguments: {"path": "src/auth/oauth.py"}
Result: [200 lines of Python code]

Description: Read oauth.py to review the existing refresh token implementation.
```

This runs locally. For a conversation with 20 tool calls, this is 20 sequential calls to the 2B model. At ~100ms per call, total compression time is ~2 seconds. Acceptable for a background process.

The output replaces the raw tool I/O in the transcript. User turns and assistant text turns are preserved verbatim.

### Step 4: Extract Memories (API Model)

Feed the compressed transcript to GLM-5.1 via API. This is the heavy lift — the model needs to read the full conversation and identify durable working-style knowledge.

Input: The compressed transcript with:
- User turns (verbatim)
- Assistant turns (verbatim text portions)
- Compressed tool call descriptions
- Any previously extracted memories for this project (as context to avoid duplicates)

Output: JSON array of `{text, importance, category}` objects.

### Step 5: Embed and Store Memories

For each extracted memory:

1. **Deduplicate**: Check the memory store for existing memories with high text similarity (fuzzy match >= 0.95 or cosine >= 0.92). If a near-duplicate exists, skip or merge.

2. **Embed**: Compute a 768-dim embedding of the memory text using `mdbr-leaf-ir-asym`'s document encoder (`encode_deep()`). This is the asymmetric counterpart to the query encoder used at retrieval time — document embeddings and query embeddings live in the same 768-dim space but are produced by different internal models optimized for their respective roles. Store the embedding alongside the text.

3. **Index**: Add the embedding to the hnswlib ANN index. The index is rebuilt from SQLite blob data on each write (these are small local files — no need to optimize for write patterns that haven't happened yet).

4. **Extract entities**: Parse entity names from the memory text (libraries, frameworks, tools, patterns mentioned). Store in an entity index.

5. **Store**: Write to `.codememory/memories.db`:
    - `memories` table: id, text, category, created_at, access_count, source_session_id
   - `entities` table: id, name, type
   - `memory_entities` table: memory_id, entity_id
   - `memory_links` table: memory_id, linked_memory_id, link_type, reasoning

6. **Link**: Find similar existing memories (cosine >= 0.75 via hnswlib) and create bidirectional links. Link types: `corroborates`, `conflicts`, `supersedes`, `refines`, `contextualizes`.

### Step 6: Periodic Consolidation

On a configurable schedule (default: daily), run consolidation:

1. **Cluster**: Find groups of memories with cosine similarity >= 0.85 using connected-components analysis on the similarity graph.

2. **Branch LLM call**: For each cluster, call Gemma 4 26B A4B via Ollama to determine whether memories should be squashed into one or kept separate. The model receives the cluster of memory texts and returns a merge decision with a consolidated text if squashing. No rejection tracking, no retry counters — just a binary squash-or-separate decision per cluster.

3. **Execute**: If squash, create a single consolidated memory (merged entities), archive the originals, rebuild hnswlib index entries. If separate, leave them alone. No state carried forward about past merge attempts.

4. **Entity GC**: Periodically merge duplicate entities (same concept, different names — e.g., "pytest" and "py.test").

Consolidation prompt: see `consolidation_prompt_v0.md`.

---

## Memory Store

**Location**: `.codememory/memories.db` in the project root. One store per project. Self-contained — no external memory dependencies.

**Vector index**: `.codememory/memories.index` — hnswlib ANN index file. Loaded into memory by the daemon. Rebuilt from the SQLite blob data on startup if missing, and rebuilt on each write. The index is never a source of truth — just a fast lookup cache rebuildable from the DB.

**Git integration**: The `.codememory/` directory can be committed to git. Open source projects ship institutional knowledge. Projects that don't want committed memories add `.codememory/` to `.gitignore`. The directory contains:
- `memories.db` — the SQLite store
- `memories.index` — the hnswlib index (binary, rebuildable from DB)
- `config.json` — project-level config overrides (optional)

### Concurrency (WAL Mode)

Both processes read and write to the same `.codememory/memories.db`. SQLite WAL (Write-Ahead Logging) mode enables concurrent readers with a single writer without database locking contention. The daemon opens the database with:

```python
conn = sqlite3.connect(db_path)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA foreign_keys=ON")
```

WAL mode allows the plugin (via daemon HTTP) to read while the daemon writes. The daemon is the sole writer — all writes go through the daemon's process. The HTTP bridge serializes write requests naturally since they all funnel through the single daemon process.

### SQLite Schema

```sql
CREATE TABLE memories (
    id TEXT PRIMARY KEY,           -- UUID, 8-char hex for display
    text TEXT NOT NULL,
    category TEXT NOT NULL,
    embedding BLOB NOT NULL,        -- 768-dim float32 vector, stored as blob
    source_session_id TEXT,         -- OpenCode session that generated this memory
    created_at TEXT NOT NULL,       -- ISO 8601
    updated_at TEXT NOT NULL,
    access_count INTEGER DEFAULT 0,
    last_accessed_at TEXT,
    is_archived INTEGER DEFAULT 0,
    archived_at TEXT
);

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL              -- library, framework, tool, pattern, protocol, error, other
);

CREATE TABLE memory_entities (
    memory_id TEXT REFERENCES memories(id),
    entity_id TEXT REFERENCES entities(id),
    PRIMARY KEY (memory_id, entity_id)
);

CREATE TABLE memory_links (
    memory_id TEXT REFERENCES memories(id),
    linked_memory_id TEXT REFERENCES memories(id),
    link_type TEXT NOT NULL,
    reasoning TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, linked_memory_id)
);

CREATE TABLE extraction_log (
    session_id TEXT PRIMARY KEY,
    extracted_at TEXT NOT NULL,
    memory_count INTEGER NOT NULL
);

-- FTS5 for BM25 search (external content table)
CREATE VIRTUAL TABLE memories_fts USING fts5(text, content='memories', content_rowid='rowid');

-- FTS5 sync triggers: keep the FTS index in sync with the memories table.
-- With content='memories', FTS5 does NOT auto-update — triggers are required.
CREATE TRIGGER memories_fts_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER memories_fts_update AFTER UPDATE OF text ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.rowid, old.text);
    INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER memories_fts_delete AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;
```

**Why FTS5 triggers?** When FTS5 uses `content='memories'`, it references the `memories` table as an external content source but does NOT automatically synchronize its search index when the content table changes. Without triggers, `INSERT`/`UPDATE`/`DELETE` on `memories` would leave `memories_fts` stale — BM25 search would return wrong or missing results. The three triggers keep the FTS index in sync: insert adds entries, update deletes the old entry and inserts the new one, delete removes entries.

---

## Directory Structure

```
CodeMira/
├── plugin/                             # TypeScript OpenCode plugin
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       └── index.ts                    # Plugin entry point
│
├── daemon/                             # Python background daemon + HTTP bridge
│   ├── pyproject.toml
│   └── codemira/
│       ├── __init__.py
│       ├── daemon.py                    # Main daemon loop (polling, orchestration)
│       ├── server.py                    # HTTP API server (bridge for plugin)
│       ├── config.py                    # Configuration (Pydantic BaseSettings)
│       ├── store/                       # Memory store layer
│       │   ├── __init__.py
│       │   ├── db.py                    # SQLite CRUD (schema init, WAL mode, all queries)
│       │   ├── index.py                 # hnswlib ANN index management
│       │   └── search.py                # Hybrid search (BM25 + ANN + RRF)
│       ├── retrieval/                   # Retrieval pipeline
│       │   ├── __init__.py
│       │   ├── hub_discovery.py         # Entity-driven memory discovery
│       │   └── proactive.py             # Full retrieval pipeline (merge + cap)
│       ├── extraction/                  # Extraction pipeline
│       │   ├── __init__.py
│       │   ├── compressor.py            # Tool I/O compression (local 2B model via Ollama)
│       │   ├── extractor.py             # Memory extraction (API model)
│       │   └── dedup.py                 # Deduplication (fuzzy text + cosine)
│       ├── consolidation/              # Consolidation pipeline
│       │   ├── __init__.py
│       │   ├── cluster.py              # Connected-components clustering
│       │   └── handler.py              # Consolidation execution (merge, archive)
│       ├── embeddings.py               # mdbr-leaf-ir-asym wrapper (encode_realtime, encode_deep)
│       ├── opencode_db.py              # OpenCode DB reader (discovery, idle detection, session reading)
│       └── launchd.py                  # macOS launchd plist generation + install
│
├── prompts/                             # LLM prompt templates (loaded from disk)
│   ├── subcortical_system.txt
│   ├── subcortical_user.txt
│   ├── extraction_system.txt
│   ├── extraction_user.txt
│   ├── compression_system.txt
│   └── consolidation_system.txt
│
├── tests/                              # Red/green tests, no mocking
│   ├── test_store_db.py                # Memory store CRUD
│   ├── test_store_index.py            # hnswlib index management
│   ├── test_search.py                  # Hybrid search (BM25 + ANN + RRF)
│   ├── test_retrieval.py               # Full retrieval pipeline
│   ├── test_extraction.py              # Compression + extraction
│   ├── test_consolidation.py           # Clustering + merge
│   ├── test_embeddings.py             # Embedding encode/decode
│   ├── test_server.py                  # HTTP API tests
│   └── conftest.py                      # Shared fixtures (real DB, real hnswlib)
│
├── ImplementationPlan.md
├── CLAUDE.md
├── extraction_prompt_v0.md
├── subcortical_prompt_v0.md
├── consolidation_prompt_v0.md
└── opencode_touchpoints.md
```

**Prompt files on disk**: All LLM prompts are stored as `.txt` files in `prompts/`, loaded at daemon initialization. Missing prompt files cause a `FileNotFoundError` — fail fast, don't guess. This follows MIRA's pattern where prompts are first-class artifacts, not embedded strings.

---

## Startup Sequence

1. **Daemon starts** → launched by launchd on login, begins HTTP server + polling loop
2. **Daemon initializes** → discovers OpenCode DB, loads embedding model, opens `.codememory/memories.db` (creates schema if not exists), loads/rebuilds hnswlib index, starts HTTP server on port 9473
3. **Plugin loads** → OpenCode loads plugin, registers `experimental.chat.messages.transform` hook
4. **Plugin initializes** → checks daemon `/health`, starts in-memory state (empty pinned memories)
5. **First LLM call** → plugin extracts tool trace, calls Ollama for subcortical, calls daemon `/retrieve`, injects HUD
6. **Subsequent LLM calls** → plugin runs subcortical with pinned memories from previous iteration, calls `/retrieve`, injects
7. **Daemon detects idle session** → reads conversation from OpenCode SQLite, compresses tool I/O, extracts memories, embeds and stores

## Session Lifecycle

```
Developer starts coding session
    │
    ▼
Plugin loads, state is empty
    │
    ▼
[Loop: User message → Agent tool calls → LLM calls]
    │
    ├─ Each LLM call: tool trace → subcortical (Ollama) → /retrieve (daemon) → HUD inject
    │
    ▼
Developer stops coding (idle threshold crossed)
    │
    ▼
Daemon detects idle session
    │
    ▼
Read conversation → compress tool I/O → extract memories → embed → store → rebuild index
    │
    ▼
Memories available for next session's /retrieve call
```

---

## macOS launchd Service

The daemon registers as a launchd user agent on macOS. This starts the daemon on login and keeps it running.

**Plist location**: `~/Library/LaunchAgents/com.codemira.daemon.plist`

**Plist content**:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.codemira.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>codemira.daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir}/codemira-daemon.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/codemira-daemon-error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>CODEMIRA_CONFIG</key>
        <string>{config_path}</string>
    </dict>
</dict>
</plist>
```

The `launchd.py` module provides `install()` and `uninstall()` commands that write/remove the plist and load/unload it via `launchctl`. Paths are resolved at install time.

---

## Configuration

### Daemon Configuration (Pydantic BaseSettings)

The daemon reads config from environment variables with `.env` file support, plus project-level overrides from `.codememory/config.json`.

```python
class DaemonConfig(BaseSettings):
    http_port: int = 9473
    idle_threshold_minutes: int = 60
    poll_interval_minutes: int = 15
    max_surfaced_memories: int = 8
    max_fresh_memories: int = 5
    tool_trace_window: int = 5
    memory_truncation_words: int = 20
    embedding_dimension: int = 768
    subcortical_model: str = "gemma-4-e2b-q4"
    extraction_model: str = "glm-5.1"
    consolidation_interval_hours: int = 24
    consolidation_model: str = "gemma-4-26b-a4b"
    consolidation_similarity_threshold: float = 0.85
    link_similarity_threshold: float = 0.75
    deduplicate_text_threshold: float = 0.95
    deduplicate_cosine_threshold: float = 0.92
    hnsw_ef_construction: int = 200
    hnsw_m: int = 16
    hnsw_ef_search: int = 50
    ollama_url: str = "http://localhost:11434"
    opencode_db_path: str | None = None  # auto-discover if None

    model_config = SettingsConfigDict(env_prefix="CODEMIRA_")
```

### Plugin Configuration (opencode.json)

```json
{
  "plugin": [
    ["./path/to/codemira-plugin", {
      "daemonUrl": "http://localhost:9473",
      "ollamaUrl": "http://localhost:11434",
      "subcorticalModel": "gemma-4-e2b-q4"
    }]
  ]
}
```

---

## Test Strategy

**Red/green testing with no mocking.** Every test operates against real infrastructure:
- Real SQLite databases (in-memory or temp files with WAL mode)
- Real hnswlib indexes (built from actual embeddings)
- Real FTS5 indexes (with triggers)
- Real Ollama calls (tests marked `@pytest.mark.ollama` — skipped if Ollama unavailable)
- Real embedding model (tests marked `@pytest.mark.embeddings` — skipped if model not downloaded)

**No mocks, no patches, no fake SQLite.** If the code talks to SQLite, the test creates a real SQLite database. If the code calls hnswlib, the test builds a real index. This catches real integration bugs that mocks paper over.

**Conftest fixtures**:
- `memory_db` — Creates a real SQLite database in a temp directory with the full schema, WAL mode enabled, and FTS5 triggers. Yields the connection, cleans up on teardown.
- `memory_index` — Creates a real hnswlib index with test embeddings, saves to temp file, yields the index path.
- `ollama_available` — Checks if Ollama is running at `localhost:11434`, skips test if not.
- `embedding_model_available` — Checks if `mdbr-leaf-ir-asym` is cached, skips test if not.

**Test categories**:
- `test_store_db.py` — Schema creation, WAL mode, CRUD operations, FTS5 trigger sync
- `test_store_index.py` — Index build from DB, index rebuild, add/delete vectors, search
- `test_search.py` — BM25 search, ANN search, hybrid RRF merge, edge cases (empty store, single memory)
- `test_retrieval.py` — Full pipeline: embed query → hybrid search → hub discovery → retention → cap
- `test_extraction.py` — Compression (requires Ollama), extraction prompt formatting, dedup
- `test_consolidation.py` — Clustering (real vectors), merge execution, archive, index rebuild
- `test_embeddings.py` — `encode_realtime()`, `encode_deep()`, dimension check, asymmetric space
- `test_server.py` — HTTP API: `/health`, `/retrieve` with real DB + index

---

## Out of Scope for v1

**Session resumption and re-extraction prevention**: When a developer resumes a session after a brief idle, the daemon may have already extracted memories from a previous idle period. On the next idle, the daemon should not re-extract the same conversation. The `extraction_log` table tracks which sessions have been processed, but handling partial extraction (session was idle, then resumed, then idle again with new messages) requires tracking extraction boundaries within a session. This is deferred to a future iteration. For v1, the daemon extracts once per session after the first idle threshold and does not re-process on subsequent idle periods.

**Linux systemd / Windows startup**: v1 supports macOS launchd only. Systemd and Windows startup entry support are deferred.

**Embedding cache**: MIRA uses a Valkey-backed embedding cache with 15-minute TTL. For v1, embeddings are computed fresh each time. The overhead is acceptable for small project stores. Caching can be added later if profiling shows it matters.

---

## Resolved Design Decisions

1. **No global memory store**: There is no reliable way to automatically classify a memory as "project-scoped" vs "developer-scoped" without human intervention. A global store would either require a human to classify every extraction (unacceptable) or would dump everything into one undifferentiated pool (unusable for project shipping). One store per project eliminates the problem. Widely applicable preferences get re-extracted per project, which is reinforcement, not duplication.

2. **hnswlib for ANN search**: Brute-force cosine similarity does not scale. Even at 1000 memories, brute-force means loading all embeddings into memory and computing N dot products on every retrieval. hnswlib provides sub-millisecond ANN search with a small memory footprint. The index file (`.codememory/memories.index`) is rebuildable from the SQLite blob data, so it's never a source of truth — just a cache. Rebuild on each write — these are small local files.

3. **Ollama for local inference**: Standard, simple setup, many developers already have it. Both the plugin (subcortical) and daemon (compression, consolidation) call Ollama's HTTP API at `localhost:11434`. If Ollama isn't running, both paths degrade gracefully — plugin skips memory injection, daemon skips extraction.

4. **HUD injection via messages.transform (not system prompt)**: Memories are turn-specific context that shifts with the task. The system prompt is for broad behavioral instructions. Mixing them dilutes both. The HUD rides with the conversation tail where it's most salient.

5. **CPU-only PyTorch for embeddings**: `mdbr-leaf-ir-asym` runs fine on CPU-only PyTorch. The query encoder is 23M params — sub-10ms encoding. No GPU dependency.

6. **Daemon as HTTP bridge**: The daemon is already a persistent Python process that owns the memory store. Exposing a small HTTP API is simpler than building a separate bridge process or spawning Python from the TypeScript plugin. The daemon serves the plugin's retrieval needs and handles all storage operations. Clean interface: plugin sends text, gets memories back.

7. **WAL mode for concurrency**: SQLite WAL mode allows concurrent readers (plugin retrieval via daemon) and a single writer (daemon storage) without lock contention. The daemon is the sole writer — all writes funnel through its process. WAL is the standard SQLite concurrency solution and requires no external coordination.

8. **FTS5 with content table + triggers**: Using `content='memories'` avoids duplicating text in the FTS table while triggers keep the search index synchronized. Without triggers, the FTS index would be stale after any write — BM25 would return wrong results. This is the standard SQLite FTS5 pattern for external content tables.

9. **macOS launchd only for v1**: launchd is the standard macOS service manager. Systemd (Linux) and Windows startup entries are deferred — the daemon code itself is cross-platform, only the service registration is platform-specific.

10. **Prompt files on disk**: All LLM prompts are `.txt` files loaded at init, not embedded strings. Missing files cause `FileNotFoundError`. Prompts are first-class artifacts that should be versioned, reviewed, and iterated independently of code.
