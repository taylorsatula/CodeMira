# CodeMira → Pi.dev Porting Plan

Target harness: [pi-mono coding-agent](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent) (published as `@mariozechner/pi-coding-agent`).

## Scope

- **Ablate OpenCode.** CodeMira is greenfield (`CLAUDE.md`: "Backwards Compatibility: Don't deprecate; ablate"). After this port, OpenCode is not a supported harness. No dual-support shims.
- **Preserve the daemon core.** Extraction, retrieval, consolidation, arc generation, embeddings, SQLite store, HTTP bridge, prompts stay unchanged. Work is confined to the integration seams.
- **Per-project `.codememory/` store semantics unchanged.** Store path, schema, FTS5 triggers, hnswlib cache — all unchanged.

## Integration Contract

### HUD injection — `context` event

The plugin injects the `<developer_context>` HUD by returning a modified message list from Pi's `context` hook. The hook fires at LLM-request assembly time; the returned messages are what the LLM sees for that turn only. Pi's SessionManager is the sole writer to the on-disk JSONL, so no HUD ever enters the persisted session.

```ts
pi.on("context", async (event, ctx) => {
  const hud = await buildHud(...)          // pure.ts formatHud, unchanged
  if (!hud) return { messages: event.messages }
  return {
    messages: [...event.messages, { role: "user", content: hud }],
  }
})
```

**Why `context` and not `before_agent_start` / `sendMessage` / `systemPrompt`:**
- `before_agent_start` with `{ message }` persists the HUD to JSONL — HUDs accumulate in the session file.
- `pi.sendMessage` persists regardless of `deliverAs` value (`steer` / `followUp` / `nextTurn` control timing, not persistence).
- `systemPrompt` return is non-persistent but cache-hostile: Pi auto-injects the Anthropic `cache_control` breakpoint at the end of the system prompt, so any HUD change invalidates the full system-prompt cache entry every turn.
- `context` mutates the outgoing message list without touching disk, and the HUD lands at the tail where Pi's third breakpoint sits — the static prefix stays cached, only the last block is rewritten.

Race surface: zero. The extension never calls `fs.*` on session files, and the returned array is request-scoped state.

### Compaction-triggered extraction

```ts
pi.on("session_compact", async (event, ctx) => {
  await fetch(`${daemonUrl}/extract`, {
    method: "POST",
    body: JSON.stringify({ session_id: ctx.sessionManager.getLeafId(), project_root }),
  })
})
```

Fire-and-forget; daemon's `/extract` handler (`server.py:_extract_session`) already spawns a background thread.

### Arc generation on new user turn

```ts
pi.on("turn_start", async (event, ctx) => {
  // fire-and-forget POST /arc/generate with session_id + project_root
})
```

`context` hook also performs the `GET /arc` call on each invocation and folds the arc into the HUD.

## Plugin Rewrite (`plugin/`)

### Files

- **`plugin/src/index.ts`** — full rewrite. Pi extension factory shape: `export default (pi, ctx) => { ... }`. ~260 lines today; expected similar size after rewrite.
- **`plugin/src/pure.ts`** — **unchanged.** `formatHud`, `pickRecentTurnContext`, `parseSubcorticalXml`, `renderPrompt`, `daemonCall`, section helpers are harness-agnostic by design (per CLAUDE.md's "HUD Section Contract" principle).
- **`plugin/package.json`** — target pi's npm-install surface. `pi install npm:@taylor/codemira-plugin` or similar.

### Message-shape adapter in `pure.ts`

`pickRecentTurnContext` currently expects OpenCode's `{ info, parts[] }` shape. Pi's `ctx.sessionManager.getEntries()` returns `{ type, message: { role, content } }` entries where `content` is a `MessageContent[]` array (text / tool_use / tool_result blocks).

Add one normalization function in `pure.ts` that converts Pi entries to the internal `RecentAction[]` / `userMessage` shape `pickRecentTurnContext` already produces. This keeps the HUD pipeline unchanged downstream. Name the function `parsePiEntries` and co-locate it with `pickRecentTurnContext`.

### Project root resolution

Pi does not expose a "worktree" concept — sessions are cwd-scoped. Resolve the project root at extension init by walking upward from `process.cwd()` to the nearest `.git` directory (or `.pi/` directory, whichever comes first). Cache it in closure state; pass as `project_root` to `/retrieve`, `/arc`, `/extract`.

Name the resolved value `projectRoot` in TS and `project_root` on the wire, consistent with the daemon side. Do not name the closure variable `cwd` or `projectDir` — the cross-surface naming rule in CLAUDE.md applies.

### State the extension holds (closure)

Same as today:
- `pinnedMemories: Memory[]`
- `daemonUnavailable: boolean`
- `healthCheckCounter: number`
- `lastUserMessage: string`
- `lastSessionId: string`
- `cachedArc: string`

No new state required.

## Daemon Changes (`daemon/codemira/`)

### Replace `opencode_db.py` with `pi_sessions.py`

`opencode_db.py` (112 lines) is the only OpenCode-coupled file in the daemon. Replace with `pi_sessions.py` providing the same interface:

- `discover_project_stores() -> list[ProjectInfo]` — walks `~/.pi/agent/sessions/` (respecting `PI_CODING_AGENT_DIR` env override), groups JSONL files by working directory, yields `(project_root, session_ids)`.
- `read_session_conversation(session_id, project_root) -> list[Turn]` — streams the session's JSONL file and returns the normalized turn list. Handles tree structure via `id` / `parentId`: follow the active leaf branch by default.
- `is_session_idle(session_id, project_root, threshold_minutes) -> bool` — checks the JSONL file's last-entry timestamp (or mtime as cheap pre-filter).

Keep function names consistent with the current `opencode_db.py` interface so the rest of the daemon (especially `daemon.py:run_daemon`) needs only import-path changes.

### Tool-call shape adapter

Pi serializes tool calls as `assistant` message content blocks with `type: "tool_use"` plus a separate `toolResult` role message. OpenCode's single `ToolPart` with discriminated `state.status` does not match this shape. Add a normalizer in `pi_sessions.py` that emits the same `(tool_name, input, output, title)` tuples the extraction pipeline already consumes. Confine the divergence to this one function.

### Idle detection

Primary extraction trigger remains the plugin's `session_compact` → `POST /extract` path. The polling loop becomes a safety net for sessions that go quiet without compacting:
- `daemon.py:run_daemon` polls every `poll_interval_minutes` (default 15).
- Per-project: `os.scandir` over the sessions directory, mtime pre-filter (`now - mtime > idle_threshold_minutes`), then parse JSONL to confirm last-entry timestamp.
- Avoids full-parse O(N) per session per poll.

### Files unchanged

- `daemon/codemira/extraction/**`
- `daemon/codemira/retrieval/**`
- `daemon/codemira/consolidation/**`
- `daemon/codemira/summarization/arc.py`
- `daemon/codemira/embeddings.py`
- `daemon/codemira/store/**`
- `daemon/codemira/llm.py`
- `daemon/codemira/server.py`
- `daemon/codemira/errors.py`
- `daemon/codemira/config.py`
- `prompts/**`

### Config additions (`config.py`)

- `pi_sessions_dir: str` — default `~/.pi/agent/sessions` (respect `PI_CODING_AGENT_DIR` if set).

Remove: any `opencode_db_path` config. No compatibility shim.

## Installer

- `daemon/codemira/launchd.py` — unchanged. Daemon is still a long-running Python process keyed off `CODEMIRA_EXTRACTION_API_KEY`.
- `daemon/codemira/systemd.py` — unchanged.
- Plugin distribution — publish as npm package. User installs via `pi install npm:@<scope>/codemira` (or `pi install git:github.com/taylorsatula/codemira-plugin` during development).

## Work Sequencing

Rough effort: ~1 working week.

| # | Task | Effort |
|---|---|---|
| 1 | JSONL fixture capture from real Pi session (for tests) | 0.5d |
| 2 | `pi_sessions.py` + tool-call adapter with unit tests | 1.5d |
| 3 | Wire `pi_sessions` into `daemon.py` / `server.py` import points; delete `opencode_db.py` | 0.5d |
| 4 | Plugin rewrite: extension factory, `context` + `session_compact` + `turn_start` hooks, project-root walk | 1.5d |
| 5 | `pure.ts` additions (Pi entry normalizer); confirm existing functions untouched | 0.5d |
| 6 | End-to-end smoke test: install daemon + extension in a real Pi session, verify HUD renders, compaction-triggered extraction writes memories, arc generation runs | 1.0d |
| 7 | Update `CLAUDE.md`, `ImplementationPlan.md`, `mira_lineage.md`, nested `CLAUDE.md` maps | 0.5d |

## Test Plan

- **Unit tests (Python)** — `tests/test_pi_sessions.py`: JSONL parsing, tree-branch traversal, tool-call normalization, idle detection via mtime. Use fixtures captured in step 1.
- **Unit tests (Bun)** — `plugin/tests/pure.test.ts`: extend existing `pure.ts` tests to cover `parsePiEntries`. Existing HUD-format tests unchanged.
- **Cache verification** — in the smoke test, capture the outgoing provider payload (via `before_provider_request` logging during dev) and confirm the `cache_control` breakpoint lands past the HUD block, not before it.
- **Race check** — run the smoke test with a long session, verify `ls -la ~/.pi/agent/sessions/<project>/*.jsonl` shows no HUD entries written by the extension (SessionManager's writes only).

## Open Questions (resolve at porting time, not now)

1. Exact shape of the `context` event's `event.messages` — need to read `packages/coding-agent/src/core/extensions/types.ts` to pin down the `Message` type and confirm appending a plain `{ role: "user", content: string }` works without a `customType`.
2. Does `ctx.sessionManager.getLeafId()` return a stable session ID usable as `session_id` on the wire, or does it return the branch-leaf entry ID? If the latter, resolve up to the session root at extension init.
3. Whether Pi's JSONL tree model means multiple "active branches" per project. If yes, decide whether the daemon follows only the active leaf or indexes all branches. Default: active leaf only, matching CodeMira's current single-transcript assumption.

## Anti-Patterns to Avoid During Execution

- **Don't add a fallback for `before_provider_request`** if `context` works. Per the principle "code a fallback we'll reasonably never need" — wait for a concrete second use case.
- **Don't mutate `~/.pi/agent/sessions/*.jsonl` from the extension.** SessionManager owns the file. Any direct fs write is a race.
- **Don't keep OpenCode plugin code in parallel.** Delete `plugin/src/index.ts`'s OpenCode shape entirely; the duplication lives at exactly the seams the CLAUDE.md "Standardization Over Premature Flexibility" principle warns about.
- **Don't rename the project-root variable to `cwd` or `directory` anywhere downstream.** The `project_root` name is canonical per CLAUDE.md's "One noun per concept" rule and the known anti-pattern "Plugin/Daemon Project Scoping Mismatch".

## References

- [pi-mono extensions.md](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/extensions.md)
- [pi-mono extension types.ts](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/src/core/extensions/types.ts)
- [pi-mono compaction.md](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/compaction.md)
- [pi-mono CHANGELOG](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/CHANGELOG.md) — cache_control breakpoint placement (0.69.0, 2026-04-22)
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
