export interface Memory {
  id: string
  text: string
  category: string
}

export interface RecentAction {
  tool: string
  target: string
  result: string
}

export function extractCurrentTurnContext(
  messages: { info: any; parts: any[] }[],
  toolWindow: number,
): { userMessage: string; recentActions: RecentAction[] } {
  let userMessage = ""
  const recentActions: RecentAction[] = []

  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]

    if (msg.info.role === "assistant") {
      for (const part of [...msg.parts].reverse()) {
        if (recentActions.length >= toolWindow) break
        if (part.type === "tool" && part.state?.status === "completed") {
          const tool = part.tool || "unknown"
          const input = part.state.input || {}
          const title = part.state.title || ""
          const target = input.path || input.command || input.pattern || ""
          const result = title || (part.state.output || "").slice(0, 80)
          recentActions.unshift({ tool, target, result })
        }
      }
    } else if (msg.info.role === "user") {
      let foundRealUserMessage = false
      for (const part of msg.parts) {
        if (part.type === "text" && !part.synthetic) {
          userMessage = part.text.slice(0, 500)
          foundRealUserMessage = true
          break
        }
      }
      if (foundRealUserMessage) break
    }
  }

  return { userMessage, recentActions }
}

function truncate(text: string, words: number): string {
  const all = text.split(" ")
  const taken = all.slice(0, words).join(" ")
  return all.length > words ? `${taken}...` : taken
}

function formatMemoryLine(m: Memory, truncWords: number): string {
  return `mem_${m.id} - ${truncate(m.text, truncWords)}`
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

export interface HudItem {
  tag: string
  attrs?: Record<string, string>
  content?: string
}

export interface HudSection {
  tag: string
  priority: number
  items: HudItem[]
}

function escapeXmlAttr(v: string): string {
  return v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;")
}

function escapeXmlText(v: string): string {
  return v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
}

export function renderHudItem(item: HudItem): string {
  const attrs = Object.entries(item.attrs ?? {})
    .map(([k, v]) => `${k}="${escapeXmlAttr(v)}"`)
    .join(" ")
  const head = attrs ? `<${item.tag} ${attrs}` : `<${item.tag}`
  if (item.content !== undefined) {
    return `${head}>${escapeXmlText(item.content)}</${item.tag}>`
  }
  return `${head} />`
}

export function recentActionsSection(actions: RecentAction[]): HudSection {
  return {
    tag: "recent_actions",
    priority: 10,
    items: actions.map((a) => ({
      tag: "action",
      attrs: { tool: a.tool, target: a.target, result: a.result },
    })),
  }
}

export function memoriesSection(memories: Memory[], truncWords: number): HudSection {
  return {
    tag: "memories",
    priority: 20,
    items: memories.map((m) => ({
      tag: "memory",
      attrs: { id: `mem_${m.id}`, category: m.category },
      content: truncate(m.text, truncWords),
    })),
  }
}

export function formatHud(
  sections: HudSection[],
  options?: { loud?: boolean },
): string {
  const nonEmpty = sections
    .filter((s) => s.items.length > 0)
    .sort((a, b) => a.priority - b.priority)
  if (nonEmpty.length === 0) return ""
  const rendered = nonEmpty.map((s) => {
    const body = s.items.map(renderHudItem).join("\n")
    return `<${s.tag}>\n${body}\n</${s.tag}>`
  })
  const hud = `<developer_context>\n${rendered.join("\n")}\n</developer_context>`
  if (options?.loud) {
    console.log("[codemira:loud] HUD:\n" + hud)
  }
  return hud
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

export function triggerExtraction(
  daemonUrl: string,
  sessionId: string,
  projectDir: string,
): void {
  fetch(`${daemonUrl}/extract`, {
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