# CodeMira

A developer memory system that learns from your coding sessions and surfaces relevant context when you need it.

CodeMira watches your OpenCode sessions, extracts patterns and decisions from idle conversations, stores them in a local knowledge base, and injects relevant memories into the LLM context on every call — so your AI assistant remembers what you've already figured out.

## How It Works

Two processes cooperate:

1. **Python daemon** — runs in the background, monitors OpenCode sessions for idle time, compresses tool call transcripts via Ollama, extracts memories via OpenRouter, stores them in SQLite + hnswlib (ANN) + FTS5, and serves retrieval requests over HTTP.

2. **TypeScript plugin** — hooks into OpenCode's `experimental.chat.messages.transform`, calls a local Ollama model (the "subcortical") to analyze the current conversation intent, queries the daemon for relevant memories, and injects a `<developer_context>` HUD block into the message stream.

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
- Ollama running locally (for compression and subcortical inference)
- An OpenRouter API key (for memory extraction)
- SQLite, hnswlib, rapidfuzz (installed via pip)

Set environment variables:
```bash
export OPENROUTER_API_KEY=sk-or-v1-...
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

Copy the `plugin/` directory into your OpenCode project's `.opencode/plugins/` path, or configure OpenCode to load it. Requires Bun or a bundler that handles TypeScript ESM.

Prompt templates are loaded from `prompts/` relative to the plugin source (`plugin/src/../../prompts`). Missing files raise a fatal error at plugin load — the plugin does not silently no-op.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `CODEMIRA_HTTP_PORT` | `9473` | Daemon HTTP port (bound to `127.0.0.1`) |
| `CODEMIRA_IDLE_THRESHOLD_MINUTES` | `60` | Minutes before a session is considered idle |
| `CODEMIRA_EXTRACTION_MODEL` | `z-ai/GLM-5.1` | OpenRouter model for extraction |
| `CODEMIRA_SUBCORTICAL_MODEL` | `gemma-4-e2b-q4` | Ollama model for subcortical / compression / link classification |
| `CODEMIRA_MAX_SURFACED_MEMORIES` | `8` | Max memories per retrieval |
| `OPENROUTER_API_KEY` | — | Required for extraction |
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

> **Note:** Embedding tests require the `MongoDB/mdbr-leaf-ir-asym` model to be downloaded (first run will fetch it). OpenRouter tests require `OPENROUTER_API_KEY` in the environment.

## ⚠️ Initial Implementation

This is an early release under active development. There **will** be bugs that tests didn't catch. The integration between the daemon, plugin, and live OpenCode sessions has not been exhaustively tested. Use with that in mind, and file issues when something breaks.
