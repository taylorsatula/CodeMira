# OpenCode Plugin Touchpoints: Complete Investigation Report

## 1. Plugin Hook System

### Hook Type Definitions

**File:** `packages/plugin/src/index.ts`

The `Hooks` interface is defined at **lines 222-333**. The two hooks you care about:

```typescript
// Lines 281-289
"experimental.chat.messages.transform"?: (
  input: {},
  output: {
    messages: {
      info: Message
      parts: Part[]
    }[]
  },
) => Promise<void>

// Lines 290-295
"experimental.chat.system.transform"?: (
  input: { sessionID?: string; model: Model },
  output: {
    system: string[]
  },
) => Promise<void>
```

**Key design:** All hooks follow the **(input, output) => Promise<void>** pattern. The plugin mutates `output` in place. The hook does NOT return a value — it modifies the `output` object passed by reference.

### Hook Trigger/Dispatch Mechanism

**File:** `packages/opencode/src/plugin/index.ts`

**Lines 263-276** — the `trigger` function:
```typescript
const trigger = Effect.fn("Plugin.trigger")(function* <
  Name extends TriggerName,
  Input = Parameters<Required<Hooks>[Name]>[0],
  Output = Parameters<Required<Hooks>[Name]>[1],
>(name: Name, input: Input, output: Output) {
  if (!name) return output
  const s = yield* InstanceState.get(state)
  for (const hook of s.hooks) {
    const fn = hook[name] as any
    if (!fn) continue
    yield* Effect.promise(async () => fn(input, output))
  }
  return output
})
```

**Critical findings:**
- Hooks fire **sequentially** in registration order (line 270: `for (const hook of s.hooks)`)
- The same `output` object is passed to every plugin — mutations accumulate
- After all hooks run, the mutated `output` is returned
- There is no error isolation between hooks — if one throws, the Effect fails

**Line 37-39** defines which hooks use the trigger pattern:
```typescript
type TriggerName = {
  [K in keyof Hooks]-?: NonNullable<Hooks[K]> extends (input: any, output: any) => Promise<void> ? K : never
}[keyof Hooks]
```

### PluginInput Type

**File:** `packages/plugin/src/index.ts`, **lines 57-67**

```typescript
export type PluginInput = {
  client: ReturnType<typeof createOpencodeClient>
  project: Project
  directory: string
  worktree: string
  experimental_workspace: {
    register(type: string, adaptor: WorkspaceAdaptor): void
  }
  serverUrl: URL
  $: BunShell
}
```

The `PluginInput` is constructed in `packages/opencode/src/plugin/index.ts` at **lines 137-152**. The `client` is a real opencode SDK client pointing at the local server. The `$` is `Bun.$` (Bun shell).

### Plugin Function Signature

**File:** `packages/plugin/src/index.ts`, **line 75**

```typescript
export type Plugin = (input: PluginInput, options?: PluginOptions) => Promise<Hooks>
```

A plugin is an async function that receives `PluginInput` + optional `PluginOptions` and returns `Hooks`.

### PluginModule Type (V1 format)

**Lines 77-81:**
```typescript
export type PluginModule = {
  id?: string
  server: Plugin
  tui?: never
}
```

V1 plugins export a default object with `id` and `server`. V0 (legacy) plugins are just functions.

---

## 2. Messages Array Structure

### Message/Part Type Definitions

**File:** `packages/opencode/src/session/message-v2.ts`

**`MessageV2.WithParts`** — the shape passed to hooks — is defined at **lines 511-515**:
```typescript
export const WithParts = z.object({
  info: Info,
  parts: z.array(Part),
})
export type WithParts = z.infer<typeof WithParts>
```

**`Info`** (Message) is a discriminated union at **line 455**:
```typescript
export const Info = z.discriminatedUnion("role", [User, Assistant])
```

**`User` message** (lines 360-384):
```typescript
export const User = Base.extend({
  role: z.literal("user"),
  time: z.object({ created: z.number() }),
  format: Format.optional(),
  summary: z.object({ title: z.string().optional(), body: z.string().optional(), diffs: Snapshot.FileDiff.array() }).optional(),
  agent: z.string(),
  model: z.object({ providerID: ProviderID.zod, modelID: ModelID.zod, variant: z.string().optional() }),
  system: z.string().optional(),
  tools: z.record(z.string(), z.boolean()).optional(),
})
```

**`Assistant` message** (lines 406-453):
```typescript
export const Assistant = Base.extend({
  role: z.literal("assistant"),
  time: z.object({ created: z.number(), completed: z.number().optional() }),
  error: z.discriminatedUnion("name", [...]).optional(),
  parentID: MessageID.zod,
  modelID: ModelID.zod,
  providerID: ProviderID.zod,
  mode: z.string(),    // @deprecated
  agent: z.string(),
  path: z.object({ cwd: z.string(), root: z.string() }),
  summary: z.boolean().optional(),
  cost: z.number(),
  tokens: z.object({ total: z.number().optional(), input: z.number(), output: z.number(), reasoning: z.number(), cache: z.object({ read: z.number(), write: z.number() }) }),
  structured: z.any().optional(),
  variant: z.string().optional(),
  finish: z.string().optional(),
})
```

### Part Types

**`Part`** is a discriminated union at **lines 386-404**:
```typescript
export const Part = z.discriminatedUnion("type", [
  TextPart, SubtaskPart, ReasoningPart, FilePart,
  ToolPart, StepStartPart, StepFinishPart,
  SnapshotPart, PatchPart, AgentPart, RetryPart, CompactionPart,
])
```

**ToolPart** (lines 344-353) — the critical one for tool call detection:
```typescript
export const ToolPart = PartBase.extend({
  type: z.literal("tool"),
  callID: z.string(),
  tool: z.string(),        // tool name, e.g. "bash", "read", "write"
  state: ToolState,         // pending | running | completed | error
  metadata: z.record(z.string(), z.any()).optional(),
})
```

**ToolState** is a discriminated union on `status` (lines 338-342):
- **`ToolStatePending`** (lines 276-286): `{ status: "pending", input: Record<string,any>, raw: string }`
- **`ToolStateRunning`** (lines 288-301): `{ status: "running", input: Record<string,any>, title?: string, metadata?: Record<string,any>, time: { start: number } }`
- **`ToolStateCompleted`** (lines 303-320): `{ status: "completed", input: Record<string,any>, output: string, title: string, metadata: Record<string,any>, time: { start, end, compacted? }, attachments?: FilePart[] }`
- **`ToolStateError`** (lines 322-336): `{ status: "error", input: Record<string,any>, error: string, metadata?: Record<string,any>, time: { start, end } }`

**TextPart** (lines 113-128):
```typescript
export const TextPart = PartBase.extend({
  type: z.literal("text"),
  text: z.string(),
  synthetic: z.boolean().optional(),
  ignored: z.boolean().optional(),
  time: z.object({ start: z.number(), end: z.number().optional() }).optional(),
  metadata: z.record(z.string(), z.any()).optional(),
})
```

### How Tool Calls Appear in the Messages Array

Tool calls are parts on **assistant messages**. When iterating `msgs`, you detect tool calls by:
1. Check `msg.info.role === "assistant"`
2. Check `msg.parts` for entries where `part.type === "tool"`
3. The `part.tool` field gives the tool name (e.g., `"bash"`, `"read"`, `"write"`)
4. The `part.callID` is the unique call identifier
5. The `part.state.status` tells you the state:
   - `"completed"` — has `output`, `title`, `metadata`, `time`
   - `"error"` — has `error` string
   - `"running"` or `"pending"` — in-flight

**To identify a tool call vs text:** `part.type === "tool"` vs `part.type === "text"`.

### Where `experimental.chat.messages.transform` Fires

**File:** `packages/opencode/src/session/prompt.ts`

**Line 1483** — inside the main `runLoop`:
```typescript
yield* plugin.trigger("experimental.chat.messages.transform", {}, { messages: msgs })
```

This fires **after**:
- Messages are fetched from DB via `MessageV2.filterCompactedEffect(sessionID)` (line 1327)
- Reminders are inserted via `insertReminders` (line 1413)
- System-reminder wrapping of interleaved user messages (lines 1465-1481)

This fires **before**:
- System prompt composition (`sys.skills`, `sys.environment`, `instruction.system`) (lines 1485-1488)
- Conversion to AI SDK `ModelMessage[]` via `MessageV2.toModelMessagesEffect(msgs, model)` (line 1489)
- The actual LLM call via `handle.process(...)` (line 1494)

**The messages array at this point contains `MessageV2.WithParts[]`** — the full internal representation, NOT yet converted to AI SDK format. Each entry has `{ info: User | Assistant, parts: Part[] }`.

**Also fires during compaction** — `packages/opencode/src/session/compaction.ts`, **line 219**:
```typescript
const msgs = structuredClone(messages)
yield* plugin.trigger("experimental.chat.messages.transform", {}, { messages: msgs })
```
Note: compaction **clones** the messages first with `structuredClone`, so mutations there do NOT affect the original messages.

---

## 3. Session/Message/Part SQLite Schema

**File:** `packages/opencode/src/session/session.sql.ts`

### Session Table (lines 15-45)
```typescript
export const SessionTable = sqliteTable("session", {
  id: text().$type<SessionID>().primaryKey(),
  project_id: text().$type<ProjectID>().notNull().references(() => ProjectTable.id, { onDelete: "cascade" }),
  workspace_id: text().$type<WorkspaceID>(),
  parent_id: text().$type<SessionID>(),
  slug: text().notNull(),
  directory: text().notNull(),
  title: text().notNull(),
  version: text().notNull(),
  share_url: text(),
  summary_additions: integer(),
  summary_deletions: integer(),
  summary_files: integer(),
  summary_diffs: text({ mode: "json" }).$type<Snapshot.FileDiff[]>(),
  revert: text({ mode: "json" }).$type<{ messageID: MessageID; partID?: PartID; snapshot?: string; diff?: string }>(),
  permission: text({ mode: "json" }).$type<Permission.Ruleset>(),
  ...Timestamps,
  time_compacting: integer(),
  time_archived: integer(),
})
```

### Message Table (lines 47-59)
```typescript
export const MessageTable = sqliteTable("message", {
  id: text().$type<MessageID>().primaryKey(),
  session_id: text().$type<SessionID>().notNull().references(() => SessionTable.id, { onDelete: "cascade" }),
  ...Timestamps,
  data: text({ mode: "json" }).notNull().$type<InfoData>(),
})
```

**`InfoData`** is defined at **line 13**:
```typescript
type InfoData = Omit<MessageV2.Info, "id" | "sessionID">
```
So the `data` JSON column contains everything EXCEPT `id` and `sessionID` (those are separate columns). For a `User` message, `data` would contain: `{ role, time, format?, summary?, agent, model, system?, tools? }`. For an `Assistant` message: `{ role, time, error?, parentID, modelID, providerID, mode, agent, path, summary?, cost, tokens, structured?, variant?, finish? }`.

### Part Table (lines 61-77)
```typescript
export const PartTable = sqliteTable("part", {
  id: text().$type<PartID>().primaryKey(),
  message_id: text().$type<MessageID>().notNull().references(() => MessageTable.id, { onDelete: "cascade" }),
  session_id: text().$type<SessionID>().notNull(),
  ...Timestamps,
  data: text({ mode: "json" }).notNull().$type<PartData>(),
})
```

**`PartData`** is defined at **line 12**:
```typescript
type PartData = Omit<MessageV2.Part, "id" | "sessionID" | "messageID">
```
So `data` contains everything EXCEPT `id`, `sessionID`, `messageID`. For a `ToolPart`, `data` would be: `{ type: "tool", callID, tool, state: { status, input, output?, ... }, metadata? }`.

### How Tool Calls are Serialized in Part Data

A completed tool call part's `data` JSON would look like:
```json
{
  "type": "tool",
  "callID": "01JC...",
  "tool": "bash",
  "state": {
    "status": "completed",
    "input": { "command": "ls -la" },
    "output": "total 48\ndrwxr-xr-x...",
    "title": "Ran bash command",
    "metadata": { "output": "total 48...", "description": "" },
    "time": { "start": 1713148800000, "end": 1713148801000 }
  }
}
```

---

## 4. Message Mutation Patterns

### Is the messages array mutable?

**YES.** The messages array passed to `experimental.chat.messages.transform` is the **live `msgs` array** — NOT a clone. This is visible in `prompt.ts` line 1483:
```typescript
yield* plugin.trigger("experimental.chat.messages.transform", {}, { messages: msgs })
```
And immediately after (line 1489), `msgs` is used directly:
```typescript
MessageV2.toModelMessagesEffect(msgs, model)
```

**Compare with compaction** (`compaction.ts` line 218): it clones first:
```typescript
const msgs = structuredClone(messages)
yield* plugin.trigger("experimental.chat.messages.transform", {}, { messages: msgs })
```

So in the prompt loop, you CAN push/remove/replace entries in the array and those changes will flow to the LLM.

### Existing patterns that mutate messages

**File:** `packages/opencode/src/session/prompt.ts`

The `insertReminders` function (lines 224-358) directly mutates parts on user messages:
```typescript
userMessage.parts.push({
  id: PartID.ascending(),
  messageID: userMessage.info.id,
  sessionID: userMessage.info.sessionID,
  type: "text",
  text: PROMPT_PLAN,
  synthetic: true,
})
```

The `toModelMessagesEffect` function in `message-v2.ts` (lines 585-838) also injects synthetic user messages for media attachments (lines 807-822).

### No existing external plugins mutate messages

The search found **zero** external plugins that use `experimental.chat.messages.transform`. The only call sites are internal (prompt.ts and compaction.ts).

### How the hook output gets consumed downstream

After `plugin.trigger("experimental.chat.messages.transform", ...)` returns on **line 1483**, the flow is:

1. **Line 1485-1489**: System prompt and model messages are built from the (possibly mutated) `msgs`:
   ```typescript
   const [skills, env, instructions, modelMsgs] = yield* Effect.all([
     sys.skills(agent),
     Effect.sync(() => sys.environment(model)),
     instruction.system().pipe(Effect.orDie),
     MessageV2.toModelMessagesEffect(msgs, model),
   ])
   ```

2. **Line 1494**: `handle.process(...)` sends the converted `ModelMessage[]` to the LLM via `streamText(...)`.

3. Inside `toModelMessagesEffect` (message-v2.ts lines 647-838), each `WithParts` entry is converted to a `UIMessage`, then `convertToModelMessages()` from the AI SDK produces the final `ModelMessage[]`.

**So your injected HUD message WILL be included in the LLM call** as long as it survives the `toModelMessagesEffect` conversion. A user message with text parts will be converted normally.

---

## 5. Plugin Loading & Lifecycle

### Plugin Configuration in opencode.json

**File:** `packages/opencode/src/config/config.ts`

**Line 886**: The config schema:
```typescript
plugin: PluginSpec.array().optional(),
```

**Lines 45-48**: `PluginSpec` and `PluginOptions`:
```typescript
const PluginOptions = z.record(z.string(), z.unknown())
export const PluginSpec = z.union([z.string(), z.tuple([z.string(), PluginOptions])])
```

So in `opencode.json`:
```json
{
  "plugin": [
    "my-plugin-package@1.0.0",
    ["file:///path/to/plugin.ts", { "option1": "value1" }]
  ]
}
```

Or a file path:
```json
{
  "plugin": ["./plugins/my-plugin.ts"]
}
```

### Plugin Loading Flow

**File:** `packages/opencode/src/plugin/loader.ts`

1. `PluginLoader.loadExternal()` resolves each plugin spec (install npm package or resolve file path)
2. The module is `import()`ed (line 102: `mod = await import(row.entry)`)
3. The module is checked for V1 format (`{id, server}`) or V0 (bare function export)

**File:** `packages/opencode/src/plugin/shared.ts`, **lines 272-304** (`readV1Plugin`):
```typescript
export function readV1Plugin(mod: Record<string, unknown>, spec: string, kind: PluginKind, mode: PluginMode = "strict") {
  const value = mod.default
  if (!isRecord(value)) { ... }
  if (mode === "detect" && !("id" in value) && !("server" in value) && !("tui" in value)) return
  const server = "server" in value ? value.server : undefined
  // ...
  return value
}
```

### Plugin Registration (applyPlugin)

**File:** `packages/opencode/src/plugin/index.ts`, **lines 101-112**:
```typescript
async function applyPlugin(load: PluginLoader.Loaded, input: PluginInput, hooks: Hooks[]) {
  const plugin = readV1Plugin(load.mod, load.spec, "server", "detect")
  if (plugin) {
    await resolvePluginId(load.source, load.spec, load.target, readPluginId(plugin.id, load.spec), load.pkg)
    hooks.push(await (plugin as PluginModule).server(input, load.options))
    return
  }
  for (const server of getLegacyPlugins(load.mod)) {
    hooks.push(await server(input, load.options))
  }
}
```

### Plugin Options

Options are the second element of the tuple config: `["plugin-spec", { key: "value" }]`. They are passed as the second argument to the plugin function.

### Can a Plugin Maintain State Across Hook Invocations?

**YES.** The plugin function runs once at startup and returns a `Hooks` object. The returned hooks are closures that can capture state. Since the plugin function is `async`, you can initialize state before returning:

```typescript
const myPlugin: Plugin = async (input, options) => {
  let lastHudMessageId = null  // closure state persists across hook invocations
  
  return {
    "experimental.chat.messages.transform": async (_input, output) => {
      // This closure can read/write lastHudMessageId
      // State persists for the lifetime of the plugin (until process restart)
    }
  }
}
```

The hooks array is stored in `InstanceState` (`plugin/index.ts` line 120-261) and persists for the lifetime of the opencode instance.

---

## 6. Local Model Integration

### The `$` (BunShell) Capability

**File:** `packages/plugin/src/shell.ts`

`$` is `Bun.$` — Bun's built-in shell. It supports:
- Template literal shell execution: `` await $`echo hello` ``
- `.cwd(dir)` to change working directory
- `.env({...})` to set environment variables
- `.quiet()` to suppress stdout
- `.nothrow()` to ignore non-zero exit codes
- `.text()`, `.json()`, `.lines()` for output parsing
- `stdin: WritableStream` for piping input

From `plugin/index.ts` line 151:
```typescript
$: typeof Bun === "undefined" ? undefined : Bun.$,
```

### The `client` Capability

The `client` is a full opencode SDK client (`@opencode-ai/sdk`) pointing at the local server. It provides access to all opencode APIs: session CRUD, message listing, part management, etc.

From `plugin/index.ts` lines 126-135:
```typescript
const client = createOpencodeClient({
  baseUrl: "http://localhost:4096",
  directory: ctx.directory,
  headers: Flag.OPENCODE_SERVER_PASSWORD ? { Authorization: `Basic ...` } : undefined,
  fetch: async (...args) => (await Server.Default()).app.fetch(...args),
})
```

### Can a Plugin Start a Background Process?

**YES.** The Bun shell `$` can start processes. You can also use Node.js `child_process` APIs. Since the plugin runs in the Bun runtime, you have full access to `Bun.spawn()`, `process`, etc. For a local model server:

```typescript
const myPlugin: Plugin = async ({ $, client, directory }) => {
  // Start llama.cpp server as a background process
  const proc = Bun.spawn(["./llama-server", "--model", "model.gguf", "--port", "8080"], {
    cwd: directory,
    stdout: "pipe",
    stderr: "pipe",
  })
  
  return {
    "experimental.chat.messages.transform": async (_input, output) => {
      // Can use proc or make HTTP calls to the server
    }
  }
}
```

### The `experimental_workspace` Field

**File:** `packages/plugin/src/index.ts`, **lines 62-64**:
```typescript
experimental_workspace: {
  register(type: string, adaptor: WorkspaceAdaptor): void
}
```

This allows plugins to register workspace adaptors (e.g., custom remote workspace types). The `WorkspaceAdaptor` type (lines 48-55):
```typescript
export type WorkspaceAdaptor = {
  name: string
  description: string
  configure(config: WorkspaceInfo): WorkspaceInfo | Promise<WorkspaceInfo>
  create(config: WorkspaceInfo, from?: WorkspaceInfo): Promise<void>
  remove(config: WorkspaceInfo): Promise<void>
  target(config: WorkspaceInfo): WorkspaceTarget | Promise<WorkspaceTarget>
}
```

Example: `packages/plugin/src/example-workspace.ts` shows a workspace adaptor that creates a local folder.

---

## 7. The Event Bus

### Event Architecture

**File:** `packages/opencode/src/bus/index.ts`

The bus uses a PubSub pattern. Events are published and subscribed via typed definitions.

### How Plugins Receive Events

**File:** `packages/opencode/src/plugin/index.ts`, **lines 248-257**:
```typescript
yield* bus.subscribeAll().pipe(
  Stream.runForEach((input) =>
    Effect.sync(() => {
      for (const hook of hooks) {
        hook["event"]?.({ event: input as any })
      }
    }),
  ),
  Effect.forkScoped,
)
```

The plugin system subscribes to ALL bus events and forwards them to every plugin's `event` hook. The event payload has the shape:
```typescript
{ type: string, properties: { ... } }
```

### Key Session Events

**File:** `packages/opencode/src/session/index.ts`, **lines 204-253**:
```typescript
export const Event = {
  Created: SyncEvent.define({
    type: "session.created",
    version: 1,
    aggregate: "sessionID",
    schema: z.object({ sessionID: SessionID.zod, info: Info }),
  }),
  Updated: SyncEvent.define({
    type: "session.updated",
    version: 1,
    aggregate: "sessionID",
    schema: z.object({ sessionID: SessionID.zod, info: updateSchema(Info).extend({ ... }) }),
  }),
  Deleted: SyncEvent.define({
    type: "session.deleted",
    version: 1,
    aggregate: "sessionID",
    schema: z.object({ sessionID: SessionID.zod, info: Info }),
  }),
  Diff: BusEvent.define("session.diff", z.object({ sessionID: SessionID.zod, diff: Snapshot.FileDiff.array() })),
  Error: BusEvent.define("session.error", z.object({ sessionID: SessionID.zod.optional(), error: MessageV2.Assistant.shape.error })),
}
```

**File:** `packages/opencode/src/session/status.ts`, **lines 29-44**:
```typescript
export const Event = {
  Status: BusEvent.define("session.status", z.object({ sessionID: SessionID.zod, status: Info })),
  Idle: BusEvent.define("session.idle", z.object({ sessionID: SessionID.zod })),
}
```

The `SessionStatus.Info` type (lines 9-27):
```typescript
z.union([
  z.object({ type: z.literal("idle") }),
  z.object({ type: z.literal("retry"), attempt: z.number(), message: z.string(), next: z.number() }),
  z.object({ type: z.literal("busy") }),
])
```

### Detecting Session Idle State via `event` Hook

**YES**, you can detect idle state. When a session finishes processing, `SessionStatus.set` is called with `{ type: "idle" }` (status.ts lines 75-78), which publishes both:
- `"session.status"` with `{ sessionID, status: { type: "idle" } }`
- `"session.idle"` with `{ sessionID }`

Your plugin can listen:
```typescript
const myPlugin: Plugin = async (input) => {
  return {
    event: async ({ event }) => {
      if (event.type === "session.idle") {
        // Session is idle, safe to do cleanup or post-processing
      }
      if (event.type === "session.status" && event.properties.status.type === "busy") {
        // Session started processing
      }
    }
  }
}
```

Other useful bus events from message-v2.ts (lines 460-509):
- `"message.updated"` — fires when any message is updated
- `"message.removed"` — fires when a message is deleted
- `"message.part.updated"` — fires when a part is updated (streaming text deltas, tool state changes)
- `"message.part.delta"` — fires for streaming text deltas
- `"message.part.removed"` — fires when a part is removed

And from compaction:
- `"session.compacted"` — fires after compaction completes

---

## Summary: Implementation Blueprint

1. **Use `experimental.chat.messages.transform`** (fires at prompt.ts:1483) — the `output.messages` array is the LIVE `msgs` array that flows directly to `toModelMessagesEffect` and then the LLM. You can push, remove, and replace entries.

2. **To inject a HUD message**: Push a `WithParts` entry with `info.role === "user"` and parts containing a `TextPart`. Use `synthetic: true` to mark it as non-user content.

3. **To track and replace**: Use closure state to remember the ID of your last injected HUD message. On each invocation, find and remove the previous entry from `output.messages`, then insert the fresh one. No breadcrumb trail.

4. **To read tool calls**: Iterate `output.messages`, filter for `info.role === "assistant"`, then check `parts` for `type === "tool"`. The `part.tool` field gives the tool name, `part.state` gives status/input/output.

5. **Use `experimental.chat.system.transform`** as a secondary hook if you want to append to the system prompt instead of injecting a message. It fires in `llm.ts:111-115` (during LLM call construction) and `agent.ts:344` (during agent generation). It receives `{ sessionID?, model }` as input and `{ system: string[] }` as output.

6. **Maintain state**: Use closure variables in the plugin function — they persist for the process lifetime.

7. **Detect idle**: Use the `event` hook, watch for `session.idle` or `session.status` with `status.type === "idle"`.

8. **Local model**: Use `Bun.spawn()` or the `$` shell to start a background process. The `client` SDK can query sessions/messages.
