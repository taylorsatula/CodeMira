export interface Memory {
  id: string
  text: string
  importance: number
  category: string
}

export function extractToolTrace(
  messages: { info: any; parts: any[] }[],
  window: number,
): string[] {
  const actions: string[] = []
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (msg.info.role !== "assistant") continue
    for (const part of msg.parts) {
      if (actions.length >= window) break
      if (part.type === "tool" && part.state?.status === "completed") {
        const tool = part.tool || "unknown"
        const input = part.state.input || {}
        const title = part.state.title || ""
        const target = input.path || input.command || input.pattern || ""
        const result = title || (part.state.output || "").slice(0, 80)
        actions.push(`<action tool="${tool}" target="${target}" result="${result}" />`)
      }
    }
    if (actions.length >= window) break
  }
  return actions
}

export function extractUserGoal(messages: { info: any; parts: any[] }[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (msg.info.role === "user") {
      for (const part of msg.parts) {
        if (part.type === "text" && !part.synthetic) {
          return part.text.slice(0, 200)
        }
      }
    }
  }
  return ""
}

export function formatPinnedMemories(memories: Memory[], truncWords: number): string {
  if (memories.length === 0) return "None"
  return memories
    .map((m) => {
      const dots = "●".repeat(Math.round(m.importance * 5)) + "○".repeat(5 - Math.round(m.importance * 5))
      const words = m.text.split(" ").slice(0, truncWords).join(" ")
      const suffix = m.text.split(" ").length > truncWords ? "..." : ""
      return `mem_${m.id} [${dots}] - ${words}${suffix}`
    })
    .join("\n")
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
  const lines = memories
    .map((m) => {
      const dots = "●".repeat(Math.round(m.importance * 5)) + "○".repeat(5 - Math.round(m.importance * 5))
      const words = m.text.split(" ").slice(0, truncWords).join(" ")
      const suffix = m.text.split(" ").length > truncWords ? "..." : ""
      return `mem_${m.id} [${dots}] - ${words}${suffix}`
    })
    .join("\n")
  return `<developer_context>\n${lines}\n</developer_context>`
}