# CodeMira

A developer memory system that learns from your coding sessions and surfaces relevant context when you need it.

CodeMira watches your OpenCode sessions, extracts patterns and decisions from idle conversations, stores them in a local knowledge base, and injects relevant memories into the LLM context on every call — so your AI assistant remembers what you've already figured out.

## How It Works

Two processes cooperate:

1. **Python daemon** — runs in the background, monitors OpenCode sessions for idle time, compresses tool call transcripts and extracts memories via OpenAI-compatible LLM endpoints (Ollama, OpenRouter, vLLM, llama.cpp, OpenAI itself, etc.), stores them in SQLite + hnswlib (ANN) + FTS5, and serves retrieval requests over HTTP.

2. **TypeScript plugin** — hooks into OpenCode's `experimental.chat.messages.transform`, calls an OpenAI-compatible LLM (the "subcortical", Ollama by default) to analyze the current conversation intent, queries the daemon for relevant memories, and injects a `<developer_context>` HUD block into the message stream.

Retrieval uses hybrid search: BM25 (full-text), ANN (cosine similarity via hnswlib), and Reciprocal Rank Fusion. Entity-based hub discovery pulls in memories linked to the same technologies. A dedup layer (fuzzy text + vector similarity) prevents storing near-duplicates.

## Storage Model

**Memories are scoped per-project. There is no global store.**

Each project gets its own memory store at `<project-worktree>/.codememory/memories.db`, alongside `memories.index` (hnswlib ANN cache rebuildable from the DB). Commit `.codememory/` to share institutional knowledge with collaborators, or add it to `.gitignore` to keep memories local.

OpenCode itself uses a single global SQLite database (typically `~/.local/share/opencode/opencode.db`) for all its session history, discriminated by `project_id`. The daemon reads that DB read-only, joins `session.project_id` against `project.worktree` to resolve each session's project root, and routes extracted memories into that project's `.codememory/` directory. There is no shared memory namespace across projects — a preference learned in project A does not bleed into project B.

## Install

### Daemon

```bash
cd daemon
pip install -e .
```

Requires:
- Python 3.12+
- An OpenAI-compatible LLM endpoint for the subcortical / consolidation / arc roles. Default: Ollama at `http://localhost:11434/v1` (Ollama 0.1+ exposes the OpenAI-compat path natively). Any provider that speaks `POST /chat/completions` works — vLLM, llama.cpp's HTTP server, LM Studio, etc.
- An OpenAI-compatible endpoint with API key for the extraction role. Default: OpenRouter (`https://openrouter.ai/api/v1`). Substitute OpenAI, Together, Anthropic-via-proxy, or your own self-hosted endpoint as needed.
- SQLite, hnswlib, rapidfuzz (installed via pip)

Set environment variables:
```bash
export CODEMIRA_EXTRACTION_API_KEY=sk-or-v1-...
```

Run:
```bash
python -m codemira.daemon
```

Or install as a macOS launchd service:
```bash
python -m codemira.launchd install
```

### Plugin

Add the plugin to your global OpenCode config at `~/.config/opencode/opencode.json`:

```json
{
  "plugin": ["file:///path/to/CodeMira/plugin/src/index.ts"]
}
```

This makes it available in every OpenCode session regardless of working directory. OpenCode auto-discovers plugins from this config on startup.

Requires Bun or a bundler that handles TypeScript ESM. The plugin calls an OpenAI-compatible LLM for the subcortical model — by default Ollama at `http://localhost:11434/v1`. Override via `subcorticalBaseUrl` / `subcorticalApiKey` in plugin options to point at any other provider.

Prompt templates are loaded from `prompts/` relative to the plugin source (`plugin/src/../../prompts`). Missing files raise a fatal error at plugin load — the plugin does not silently no-op.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `CODEMIRA_HTTP_PORT` | `9473` | Daemon HTTP port (bound to `127.0.0.1`) |
| `CODEMIRA_POLL_INTERVAL_MINUTES` | `15` | Minutes between daemon poll cycles |
| `CODEMIRA_IDLE_THRESHOLD_MINUTES` | `60` | Minutes before a session is considered idle |
| `CODEMIRA_EXTRACTION_MODEL` | `z-ai/glm-5.1` | Model name for memory extraction |
| `CODEMIRA_EXTRACTION_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible endpoint for extraction |
| `CODEMIRA_EXTRACTION_API_KEY` | — | Required. API key for the extraction endpoint. |
| `CODEMIRA_SUBCORTICAL_MODEL` | `gemma4:e2b` | Model name for subcortical / compression / link classification |
| `CODEMIRA_SUBCORTICAL_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint for the subcortical roles |
| `CODEMIRA_SUBCORTICAL_API_KEY` | `""` | API key for the subcortical endpoint (empty for Ollama) |
| `CODEMIRA_CONSOLIDATION_MODEL` | `gemma4:e4b` | Model name for memory consolidation |
| `CODEMIRA_CONSOLIDATION_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint for consolidation |
| `CODEMIRA_CONSOLIDATION_API_KEY` | `""` | API key for the consolidation endpoint |
| `CODEMIRA_ARC_MODEL` | `gemma4:e2b` | Model name for arc summarization |
| `CODEMIRA_ARC_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint for arc generation |
| `CODEMIRA_ARC_API_KEY` | `""` | API key for the arc endpoint |
| `CODEMIRA_MAX_SURFACED_MEMORIES` | `8` | Max memories per retrieval |
| `OPENCODE_DB` | auto-discovered | Override path to OpenCode's global database |

Memory stores are always located at `<project-worktree>/.codememory/` — there is no configurable data directory and no global fallback.

## Test

```bash
# Python tests
cd daemon
pip install -e .
cd .. && python -m pytest tests/ -v

# TypeScript tests
cd plugin && bun test
```

> **Note:** Embedding tests require the `MongoDB/mdbr-leaf-ir-asym` model to be downloaded (first run will fetch it). Remote-extraction tests require `CODEMIRA_EXTRACTION_API_KEY` in the environment.

## Development

For faster iteration during development, lower the poll and idle thresholds:

```bash
export CODEMIRA_EXTRACTION_API_KEY=sk-or-v1-...
CODEMIRA_POLL_INTERVAL_MINUTES=1 CODEMIRA_IDLE_THRESHOLD_MINUTES=1 python -m codemira.daemon
```

This makes the daemon check for idle sessions every minute and treat sessions as idle after just 1 minute of inactivity (defaults are 15 and 60 respectively).

Verify extraction with:

```bash
sqlite3 <project-worktree>/.codememory/memories.db \
  "SELECT id, category, substr(text,1,80) FROM memories WHERE is_archived=0 LIMIT 20;"
```

Verify retrieval by hitting the daemon directly:

```bash
curl -s -X POST http://localhost:9473/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query_expansion":"your query","entities":[],"pinned_memory_ids":[],"project_root":"/path/to/project"}'
```

Health check:

```bash
curl -s http://localhost:9473/health
```

## ⚠️ Initial Implementation

This is an early release under active development. There **will** be bugs that tests didn't catch. The integration between the daemon, plugin, and live OpenCode sessions has not been exhaustively tested. Use with that in mind, and file issues when something breaks.
