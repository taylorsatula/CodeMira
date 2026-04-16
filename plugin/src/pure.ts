export interface Memory {
  id: string
  text: string
  importance: number
  category: string
}

export function extractCurrentTurnContext(
  messages: { info: any; parts: any[] }[],
  toolWindow: number,
): { userMessage: string; toolTrace: string[] } {
  let userMessage = ""
  const toolTrace: string[] = []

  // Traverse backwards to capture the immediate current turn
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    
    if (msg.info.role === "assistant") {
      for (const part of [...msg.parts].reverse()) { // Reverse to get most recent tools first
        if (toolTrace.length >= toolWindow) break
        if (part.type === "tool" && part.state?.status === "completed") {
          const tool = part.tool || "unknown"
          const input = part.state.input || {}
          const title = part.state.title || ""
          const target = input.path || input.command || input.pattern || ""
          const result = title || (part.state.output || "").slice(0, 80)
          // Prepend so they appear in chronological order in the array
          toolTrace.unshift(`<action tool="${tool}" target="${target}" result="${result}" />`)
        }
      }
    } else if (msg.info.role === "user") {
      // Stop at the first real user message we hit (the start of the current turn)
      let foundRealUserMessage = false
      for (const part of msg.parts) {
        if (part.type === "text" && !part.synthetic) {
          userMessage = part.text.slice(0, 500) // Give it a bit more breathing room
          foundRealUserMessage = true
          break
        }
      }
      if (foundRealUserMessage) {
        break // We found the start of the current turn, stop traversing
      }
    }
  }

  return { userMessage, toolTrace }
}

function formatMemoryLine(m: Memory, truncWords: number): string {
  const dots = "●".repeat(Math.round(m.importance * 5)) + "○".repeat(5 - Math.round(m.importance * 5))
  const words = m.text.split(" ").slice(0, truncWords).join(" ")
  const suffix = m.text.split(" ").length > truncWords ? "..." : ""
  return `mem_${m.id} [${dots}] - ${words}${suffix}`
}

export function formatPinnedMemories(memories: Memory[], truncWords: number): string {
  if (memories.length === 0) return "None"
  return memories.map((m) => formatMemoryLine(m, truncWords)).join("\n")
}

export function parseSubcorticalXml(xml: string): {
  query_expansion: string
  entities: string[]
  keep: string[]
} {
  const entityMatch = xml.match(/<entities>([\s\S]*?)<\/entities>/)
  const entitiesRaw = entityMatch ? entityMatch[1].trim() : "None"
  const entities =
    entitiesRaw === "None"
      ? []
      : entitiesRaw
          .split(",")
          .map((e: string) => e.trim())
          .filter(Boolean)

  const keepMatch = xml.match(/<keep>([\s\S]*?)<\/keep>/)
  const keepRaw = keepMatch ? keepMatch[1].trim() : ""
  const keep = keepRaw
    .split(",")
    .map((k: string) => k.trim().replace(/^mem_/, ""))
    .filter(Boolean)

  const expansionMatch = xml.match(/<query_expansion>([\s\S]*?)<\/query_expansion>/)
  const query_expansion = expansionMatch ? expansionMatch[1].trim() : ""

  return { query_expansion, entities, keep }
}

export function formatHud(memories: Memory[], truncWords: number): string {
  if (memories.length === 0) return ""
  const lines = memories.map((m) => formatMemoryLine(m, truncWords)).join("\n")
  return `<developer_context>\n${lines}\n</developer_context>`
}

export function triggerArcGeneration(
  daemonUrl: string,
  sessionId: string,
  projectDir: string,
): void {
  fetch(`${daemonUrl}/arc/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, project_dir: projectDir }),
  }).catch(() => {})
}

export async function fetchArcSummary(
  daemonUrl: string,
  sessionId: string,
  projectDir: string,
): Promise<string | null> {
  try {
    const url = `${daemonUrl}/arc?session_id=${encodeURIComponent(sessionId)}&project_dir=${encodeURIComponent(projectDir)}`
    const response = await fetch(url, { signal: AbortSignal.timeout(2000) })
    if (!response.ok) return null
    const data = (await response.json()) as { topology: string | null }
    return data.topology
  } catch {
    return null
  }
}