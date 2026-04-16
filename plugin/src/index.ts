import { readFileSync } from "fs"
import { join } from "path"
import { randomBytes } from "crypto"
import {
  extractCurrentTurnContext,
  formatPinnedMemories,
  parseSubcorticalXml,
  formatHud,
  triggerArcGeneration,
  triggerExtraction,
  fetchArcSummary,
  type Memory,
} from "./pure.ts"

function generateOpencodeId(prefix: "msg" | "prt"): string {
  return `${prefix}_${randomBytes(13).toString("hex")}`
}

interface PluginOptions {
  daemonUrl?: string
  ollamaUrl?: string
  subcorticalModel?: string
  toolTraceWindow?: number
  memoryTruncationWords?: number
}

interface PluginInput {
  client: any
  project: any
  directory: string
  worktree: string
  experimental_workspace: { register(type: string, adaptor: any): void }
  serverUrl: URL
  $: any
}

interface Hooks {
  "experimental.chat.messages.transform"?: (
    input: {},
    output: { messages: { info: any; parts: any[] }[] },
  ) => Promise<void>
  event?: (input: { event: { type: string; properties: any } }) => Promise<void>
}

const DEFAULT_OPTIONS: Required<PluginOptions> = {
  daemonUrl: "http://localhost:9473",
  ollamaUrl: "http://localhost:11434",
  // Development: gemma4:e2b (local, fast). Upgrade to gemma4:26b-a4b for production.
  subcorticalModel: "gemma4:e2b",
  toolTraceWindow: 5,
  memoryTruncationWords: 20,
}

async function callOllama(
  systemPrompt: string,
  userPrompt: string,
  ollamaUrl: string,
  model: string,
): Promise<string> {
  const response = await fetch(`${ollamaUrl}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: userPrompt },
      ],
      stream: false,
    }),
  })
  const data = (await response.json()) as { message?: { content?: string } }
  return data.message?.content || ""
}

async function callDaemonRetrieve(
  daemonUrl: string,
  queryExpansion: string,
  entities: string[],
  pinnedMemoryIds: string[],
  projectDir: string,
): Promise<{ memories: Memory[]; degraded: boolean }> {
  const response = await fetch(`${daemonUrl}/retrieve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query_expansion: queryExpansion,
      entities,
      pinned_memory_ids: pinnedMemoryIds,
      project_dir: projectDir,
    }),
  })
  return (await response.json()) as { memories: Memory[]; degraded: boolean }
}

async function checkDaemonHealth(daemonUrl: string): Promise<boolean> {
  try {
    const response = await fetch(`${daemonUrl}/health`, { signal: AbortSignal.timeout(2000) })
    return response.ok
  } catch {
    return false
  }
}

const plugin: (input: PluginInput, options?: PluginOptions) => Promise<Hooks> = async (
  input,
  options,
) => {
  const config = { ...DEFAULT_OPTIONS, ...options }
  let pinnedMemories: Memory[] = []
  let daemonUnavailable = false
  let healthCheckCounter = 0
  let lastUserMessage = ""
  let lastSessionId = ""
  let cachedArc = ""

  const promptsDir = join(import.meta.dir, "..", "..", "prompts")
  const subcorticalSystemPrompt = readFileSync(join(promptsDir, "subcortical_system.txt"), "utf-8")
  const subcorticalUserTemplate = readFileSync(join(promptsDir, "subcortical_user.txt"), "utf-8")

  return {
    "experimental.chat.messages.transform": async (_input, output) => {
      if (daemonUnavailable) {
        healthCheckCounter++
        if (healthCheckCounter >= 10) {
          healthCheckCounter = 0
          daemonUnavailable = !(await checkDaemonHealth(config.daemonUrl))
        }
        if (daemonUnavailable) return
      }

      try {
        const { userMessage, toolTrace } = extractCurrentTurnContext(output.messages, 20)
        
        if (toolTrace.length === 0 && pinnedMemories.length === 0 && !userMessage) return

        const lastMsg = output.messages[output.messages.length - 1]
        const sessionID = lastMsg?.info?.sessionID || ""

        if (sessionID !== lastSessionId) {
          lastSessionId = sessionID
          cachedArc = ""
        }

        if (userMessage && userMessage !== lastUserMessage) {
          lastUserMessage = userMessage
          triggerArcGeneration(config.daemonUrl, sessionID, input.worktree)
        }

        const arcTopology = await fetchArcSummary(config.daemonUrl, sessionID, input.worktree)
        if (arcTopology) {
          cachedArc = arcTopology
        }

        const recentActions = toolTrace.join("\n")
        const pinnedMemoriesStr = formatPinnedMemories(pinnedMemories, config.memoryTruncationWords)

        const userPrompt = subcorticalUserTemplate
          .replace("{user_message}", userMessage)
          .replace("{recent_actions}", recentActions)
          .replace("{pinned_memories}", pinnedMemoriesStr)
          .replace("{conversation_arc}", cachedArc || "None")

        const subcorticalResult = await callOllama(
          subcorticalSystemPrompt,
          userPrompt,
          config.ollamaUrl,
          config.subcorticalModel,
        )

        const { query_expansion, entities, keep } = parseSubcorticalXml(subcorticalResult)

        const pinnedIds = keep.length > 0 ? keep : pinnedMemories.map((m) => m.id)

        const { memories } = await callDaemonRetrieve(
          config.daemonUrl,
          query_expansion,
          entities,
          pinnedIds,
          input.worktree,
        )

        if (memories.length === 0) {
          pinnedMemories = []
          return
        }

        const hudText = formatHud(memories, config.memoryTruncationWords)
        if (!hudText) return

        const agent = lastMsg?.info?.agent || "code"
        const model = lastMsg?.info?.model || { providerID: "anthropic", modelID: "claude-sonnet-4-20250514" }

        const hudMsgId = generateOpencodeId("msg")
        const hudPartId = generateOpencodeId("prt")

        output.messages.push({
          info: {
            id: hudMsgId,
            sessionID,
            role: "user",
            time: { created: Date.now() },
            agent,
            model,
          },
          parts: [
            {
              id: hudPartId,
              messageID: hudMsgId,
              sessionID,
              type: "text",
              text: hudText,
              synthetic: true,
            },
          ],
        })

        pinnedMemories = memories
      } catch (e: any) {
        if (e?.cause?.code === "ECONNREFUSED" || e?.message?.includes("fetch failed")) {
          daemonUnavailable = true
        }
      }
    },
    event: async ({ event }) => {
      if (daemonUnavailable) return
      if (event?.type !== "session.compacted") return
      const sessionID = event.properties?.sessionID
      if (!sessionID) return
      triggerExtraction(config.daemonUrl, sessionID, input.worktree)
    },
  }
}

export default { id: "codemira", server: plugin }
