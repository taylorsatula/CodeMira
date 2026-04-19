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

export interface SessionContext {
  sessionId: string
  projectRoot: string
}

export type DaemonError = "timeout" | "down" | "bad_response" | "not_found"
export type DaemonResult<T> = { ok: T } | { error: DaemonError }

const DEFAULT_TIMEOUT_MS = 2000
const USER_MESSAGE_CHARS = 500
const TOOL_RESULT_CHARS = 80

export async function daemonCall<T = undefined>(
  daemonUrl: string,
  method: "GET" | "POST",
  path: string,
  body: object | null,
  options: { expect: "result" | "ack" | "fire-forget"; timeout?: number },
): Promise<DaemonResult<T>> {
  const url = `${daemonUrl}${path}`
  const headers: Record<string, string> = body ? { "Content-Type": "application/json" } : {}
  const init: RequestInit = {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  }

  if (options.expect === "fire-forget") {
    fetch(url, init).catch(() => {})
    return { ok: undefined as T }
  }

  init.signal = AbortSignal.timeout(options.timeout ?? DEFAULT_TIMEOUT_MS)

  try {
    const resp = await fetch(url, init)
    if (resp.status === 404) return { error: "not_found" }
    if (!resp.ok) return { error: "bad_response" }
    if (options.expect === "ack") return { ok: undefined as T }
    const data = (await resp.json()) as T
    return { ok: data }
  } catch (e: any) {
    if (e?.name === "TimeoutError" || e?.name === "AbortError") return { error: "timeout" }
    if (e?.cause?.code === "ECONNREFUSED" || /fetch failed/.test(e?.message ?? "")) {
      return { error: "down" }
    }
    return { error: "bad_response" }
  }
}

export function pickRecentTurnContext(
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
          const result = title || (part.state.output || "").slice(0, TOOL_RESULT_CHARS)
          recentActions.unshift({ tool, target, result })
        }
      }
    } else if (msg.info.role === "user") {
      let foundRealUserMessage = false
      for (const part of msg.parts) {
        if (part.type === "text" && !part.synthetic) {
          userMessage = part.text.slice(0, USER_MESSAGE_CHARS)
          foundRealUserMessage = true
          break
        }
      }
      if (foundRealUserMessage) break
    }
  }

  return { userMessage, recentActions }
}

function formatTruncated(text: string, words: number): string {
  const all = text.split(" ")
  const taken = all.slice(0, words).join(" ")
  return all.length > words ? `${taken}...` : taken
}

function formatMemoryLine(m: Memory, truncWords: number): string {
  return `mem_${m.id} - ${formatTruncated(m.text, truncWords)}`
}

export function formatPinnedMemories(memories: Memory[], truncWords: number): string {
  if (memories.length === 0) return "None"
  return memories.map((m) => formatMemoryLine(m, truncWords)).join("\n")
}

export function renderPrompt(template: string, slots: Record<string, string>): string {
  const expected = new Set<string>()
  for (const m of template.matchAll(/\{(\w+)\}/g)) {
    expected.add(m[1])
  }
  const missing = [...expected].filter((s) => !(s in slots))
  if (missing.length > 0) throw new Error(`Missing prompt slots: ${missing.sort().join(",")}`)
  const extra = Object.keys(slots).filter((k) => !expected.has(k))
  if (extra.length > 0) throw new Error(`Unknown prompt slots: ${extra.sort().join(",")}`)
  let out = template
  for (const [k, v] of Object.entries(slots)) {
    out = out.split(`{${k}}`).join(v)
  }
  return out
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

function formatXmlAttr(v: string): string {
  return v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;")
}

function formatXmlText(v: string): string {
  return v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
}

export function renderHudItem(item: HudItem): string {
  const attrs = Object.entries(item.attrs ?? {})
    .map(([k, v]) => `${k}="${formatXmlAttr(v)}"`)
    .join(" ")
  const head = attrs ? `<${item.tag} ${attrs}` : `<${item.tag}`
  if (item.content !== undefined) {
    return `${head}>${formatXmlText(item.content)}</${item.tag}>`
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
      content: formatTruncated(m.text, truncWords),
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
