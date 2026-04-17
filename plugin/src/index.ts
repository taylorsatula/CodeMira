import { readFileSync } from "fs"
import { join } from "path"
import { randomBytes } from "crypto"
import {
  pickRecentTurnContext,
  formatPinnedMemories,
  parseSubcorticalXml,
  formatHud,
  memoriesSection,
  recentActionsSection,
  renderHudItem,
  renderPrompt,
  daemonCall,
  type Memory,
  type DaemonResult,
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
  loud?: boolean
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
  loud: false,
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

  function trackHealth<T>(result: DaemonResult<T>): DaemonResult<T> {
    if ("error" in result && (result.error === "down" || result.error === "timeout")) {
      daemonUnavailable = true
    } else if ("ok" in result) {
      daemonUnavailable = false
    }
    return result
  }

  return {
    "experimental.chat.messages.transform": async (_input, output) => {
      if (daemonUnavailable) {
        healthCheckCounter++
        if (healthCheckCounter < 10) return
        healthCheckCounter = 0
      }

      try {
        const { userMessage, recentActions } = pickRecentTurnContext(output.messages, 20)

        if (recentActions.length === 0 && pinnedMemories.length === 0 && !userMessage) return

        const lastMsg = output.messages[output.messages.length - 1]
        const sessionID = lastMsg?.info?.sessionID || ""
        const projectRoot = input.worktree

        if (sessionID !== lastSessionId) {
          lastSessionId = sessionID
          cachedArc = ""
        }

        if (userMessage && userMessage !== lastUserMessage) {
          lastUserMessage = userMessage
          daemonCall(
            config.daemonUrl, "POST", "/arc/generate",
            { session_id: sessionID, project_root: projectRoot },
            { expect: "fire-forget" },
          )
        }

        const arcResult = trackHealth(
          await daemonCall<{ arc: string | null }>(
            config.daemonUrl, "GET",
            `/arc?session_id=${encodeURIComponent(sessionID)}&project_root=${encodeURIComponent(projectRoot)}`,
            null,
            { expect: "result" },
          )
        )
        if ("ok" in arcResult && arcResult.ok.arc) {
          cachedArc = arcResult.ok.arc
        }

        const recentActionsStr = recentActions
          .map((a) =>
            renderHudItem({
              tag: "action",
              attrs: { tool: a.tool, target: a.target, result: a.result },
            }),
          )
          .join("\n")
        const pinnedMemoriesStr = formatPinnedMemories(pinnedMemories, config.memoryTruncationWords)

        const userPrompt = renderPrompt(subcorticalUserTemplate, {
          user_message: userMessage,
          recent_actions: recentActionsStr,
          pinned_memories: pinnedMemoriesStr,
          conversation_arc: cachedArc || "None",
        })

        const subcorticalResult = await callOllama(
          subcorticalSystemPrompt,
          userPrompt,
          config.ollamaUrl,
          config.subcorticalModel,
        )

        const { query_expansion, entities, keep } = parseSubcorticalXml(subcorticalResult)

        const pinnedIds = keep.length > 0 ? keep : pinnedMemories.map((m) => m.id)

        const retrieveResult = trackHealth(
          await daemonCall<{ memories: Memory[]; degraded: boolean }>(
            config.daemonUrl, "POST", "/retrieve",
            {
              query_expansion,
              entities,
              pinned_memory_ids: pinnedIds,
              project_root: projectRoot,
            },
            { expect: "result" },
          )
        )
        if ("error" in retrieveResult) return
        const { memories } = retrieveResult.ok

        const hudText = formatHud(
          [
            recentActionsSection(recentActions),
            memoriesSection(memories, config.memoryTruncationWords),
          ],
          { loud: config.loud },
        )
        if (!hudText) {
          pinnedMemories = []
          return
        }

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
      daemonCall(
        config.daemonUrl, "POST", "/extract",
        { session_id: sessionID, project_root: input.worktree },
        { expect: "fire-forget" },
      )
    },
  }
}

export default { id: "codemira", server: plugin }
