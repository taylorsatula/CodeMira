import { describe, test, expect, spyOn } from "bun:test"
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
  type RecentAction,
} from "../src/pure.ts"

function makeAssistantMsg(parts: any[]): { info: any; parts: any[] } {
  return {
    info: { role: "assistant", id: "asst_1", sessionID: "ses_1", agent: "code", model: { providerID: "anthropic", modelID: "claude-sonnet-4-20250514" } },
    parts,
  }
}

function makeUserMsg(text: string, synthetic = false): { info: any; parts: any[] } {
  return {
    info: { role: "user", id: "user_1", sessionID: "ses_1", agent: "code", model: { providerID: "anthropic", modelID: "claude-sonnet-4-20250514" } },
    parts: [{ type: "text", text, synthetic }],
  }
}

function makeToolPart(tool: string, input: Record<string, string>, output: string, title = ""): any {
  return {
    type: "tool",
    callID: "call_1",
    tool,
    state: { status: "completed", input, output, title, metadata: {}, time: { start: 1, end: 2 } },
  }
}

const mem: Memory = { id: "abc123", text: "Prefers threading over asyncio for concurrent I/O", category: "priority" }

describe("pickRecentTurnContext", () => {
  test("extracts completed tool calls and user message for the current turn", () => {
    const messages = [
      makeUserMsg("Run the tests"),
      makeAssistantMsg([
        makeToolPart("bash", { command: "pytest" }, "3 passed", "Ran pytest"),
      ]),
    ]
    const { userMessage, recentActions } = pickRecentTurnContext(messages, 20)
    expect(recentActions.length).toBe(1)
    expect(recentActions[0].tool).toBe("bash")
    expect(recentActions[0].target).toBe("pytest")
    expect(recentActions[0].result).toBe("Ran pytest")
    expect(userMessage).toBe("Run the tests")
  })

  test("ignores pending and running tool states", () => {
    const messages = [
      makeUserMsg("Do work"),
      makeAssistantMsg([
        { type: "tool", callID: "c1", tool: "bash", state: { status: "pending", input: { command: "ls" } } },
        { type: "tool", callID: "c2", tool: "read", state: { status: "running", input: { path: "file.ts" } } },
      ]),
    ]
    const { recentActions } = pickRecentTurnContext(messages, 20)
    expect(recentActions.length).toBe(0)
  })

  test("respects window limit across multiple assistant messages in same turn", () => {
    const messages = [
      makeUserMsg("Do work"),
      makeAssistantMsg([makeToolPart("bash", { command: "cmd1" }, "out1", "title1")]),
      makeAssistantMsg([makeToolPart("bash", { command: "cmd2" }, "out2", "title2")]),
      makeAssistantMsg([makeToolPart("bash", { command: "cmd3" }, "out3", "title3")]),
      makeAssistantMsg([makeToolPart("bash", { command: "cmd4" }, "out4", "title4")]),
    ]
    const { recentActions } = pickRecentTurnContext(messages, 2)
    expect(recentActions.length).toBe(2)
  })

  test("stops at the start of the current turn", () => {
    const messages = [
      makeUserMsg("Old question"),
      makeAssistantMsg([makeToolPart("bash", { command: "old_cmd" }, "out", "old")]),
      makeUserMsg("New question"),
      makeAssistantMsg([makeToolPart("bash", { command: "new_cmd" }, "out", "new")]),
    ]
    const { userMessage, recentActions } = pickRecentTurnContext(messages, 20)
    expect(userMessage).toBe("New question")
    expect(recentActions.length).toBe(1)
    expect(recentActions[0].target).toBe("new_cmd")
  })

  test("extracts tool target from path, command, or pattern", () => {
    const pathResult = pickRecentTurnContext([makeUserMsg(""), makeAssistantMsg([makeToolPart("read", { path: "src/index.ts" }, "content")])], 20)
    const cmdResult = pickRecentTurnContext([makeUserMsg(""), makeAssistantMsg([makeToolPart("bash", { command: "npm test" }, "ok")])], 20)
    const patResult = pickRecentTurnContext([makeUserMsg(""), makeAssistantMsg([makeToolPart("grep", { pattern: "TODO" }, "3 matches")])], 20)

    expect(pathResult.recentActions[0].target).toBe("src/index.ts")
    expect(cmdResult.recentActions[0].target).toBe("npm test")
    expect(patResult.recentActions[0].target).toBe("TODO")
  })

  test("uses title over truncated output for result", () => {
    const messages = [
      makeUserMsg(""),
      makeAssistantMsg([
        makeToolPart("bash", { command: "ls" }, "file1.txt\nfile2.txt\nfile3.txt", "Listed files"),
      ]),
    ]
    const { recentActions } = pickRecentTurnContext(messages, 20)
    expect(recentActions[0].result).toBe("Listed files")
    expect(recentActions[0].result).not.toContain("file1.txt")
  })

  test("truncates user message to 500 characters", () => {
    const longText = "x".repeat(600)
    const messages = [makeUserMsg(longText)]
    const { userMessage } = pickRecentTurnContext(messages, 20)
    expect(userMessage.length).toBe(500)
  })

  test("skips synthetic user messages when finding goal", () => {
    const messages = [
      makeUserMsg("Real user message", false),
      makeUserMsg("System reminder", true),
    ]
    const { userMessage } = pickRecentTurnContext(messages, 20)
    expect(userMessage).toBe("Real user message")
  })

  test("returns empty when no user messages or tool calls", () => {
    const messages = [makeAssistantMsg([])]
    const { userMessage, recentActions } = pickRecentTurnContext(messages, 20)
    expect(userMessage).toBe("")
    expect(recentActions.length).toBe(0)
  })
})

describe("parseSubcorticalXml", () => {
  test("parses all three fields", () => {
    const xml = `<query_expansion>threading concurrency patterns</query_expansion>
<entities>threading, asyncio, concurrency</entities>
<keep>mem_abc123, mem_def456</keep>`
    const result = parseSubcorticalXml(xml)
    expect(result.query_expansion).toBe("threading concurrency patterns")
    expect(result.entities).toEqual(["threading", "asyncio", "concurrency"])
    expect(result.keep).toEqual(["abc123", "def456"])
  })

  test("handles None entities", () => {
    const xml = `<query_expansion>docker deployment</query_expansion>
<entities>None</entities>
<keep></keep>`
    const result = parseSubcorticalXml(xml)
    expect(result.query_expansion).toBe("docker deployment")
    expect(result.entities).toEqual([])
    expect(result.keep).toEqual([])
  })

  test("handles missing tags gracefully", () => {
    const result = parseSubcorticalXml("just some random text")
    expect(result.query_expansion).toBe("")
    expect(result.entities).toEqual([])
    expect(result.keep).toEqual([])
  })

  test("strips mem_ prefix from keep IDs", () => {
    const xml = `<query_expansion>test</query_expansion><entities>None</entities><keep>mem_x1, mem_y2</keep>`
    const result = parseSubcorticalXml(xml)
    expect(result.keep).toEqual(["x1", "y2"])
  })

  test("keeps IDs without mem_ prefix unchanged", () => {
    const xml = `<query_expansion>test</query_expansion><entities>None</entities><keep>abc123</keep>`
    const result = parseSubcorticalXml(xml)
    expect(result.keep).toEqual(["abc123"])
  })

  test("handles multiline content in query_expansion", () => {
    const xml = `<query_expansion>
threading patterns for concurrency
</query_expansion><entities>None</entities><keep></keep>`
    const result = parseSubcorticalXml(xml)
    expect(result.query_expansion).toBe("threading patterns for concurrency")
  })
})

describe("formatPinnedMemories", () => {
  test("returns None for empty list", () => {
    expect(formatPinnedMemories([], 20)).toBe("None")
  })

  test("formats single memory", () => {
    const result = formatPinnedMemories([mem], 20)
    expect(result).toContain("mem_abc123")
    expect(result).toContain("Prefers threading over asyncio for concurrent I/O")
  })

  test("truncates long text", () => {
    const longMem: Memory = { ...mem, text: "word ".repeat(30).trim() }
    const result = formatPinnedMemories([longMem], 10)
    expect(result).toContain("...")
  })

  test("does not truncate short text", () => {
    const shortMem: Memory = { ...mem, text: "Short memory" }
    const result = formatPinnedMemories([shortMem], 20)
    expect(result).not.toContain("...")
  })

  test("formats multiple memories on separate lines", () => {
    const mem2: Memory = { id: "def456", text: "Uses Docker for deployment", category: "decision_rationale" }
    const result = formatPinnedMemories([mem, mem2], 20)
    const lines = result.split("\n")
    expect(lines.length).toBe(2)
    expect(lines[0]).toContain("abc123")
    expect(lines[1]).toContain("def456")
  })
})

describe("renderHudItem", () => {
  test("renders self-closing element when content is omitted", () => {
    const out = renderHudItem({ tag: "action", attrs: { tool: "bash", target: "pytest" } })
    expect(out).toBe('<action tool="bash" target="pytest" />')
  })

  test("renders element with content when content is provided", () => {
    const out = renderHudItem({ tag: "memory", attrs: { id: "mem_x" }, content: "hello" })
    expect(out).toBe('<memory id="mem_x">hello</memory>')
  })

  test("renders bare tag when no attrs and no content", () => {
    const out = renderHudItem({ tag: "marker" })
    expect(out).toBe("<marker />")
  })

  test("escapes attribute special characters", () => {
    const out = renderHudItem({ tag: "x", attrs: { v: `a & b < c "quoted"` } })
    expect(out).toContain('v="a &amp; b &lt; c &quot;quoted&quot;"')
  })

  test("escapes text content special characters", () => {
    const out = renderHudItem({ tag: "x", content: "a & b < c > d" })
    expect(out).toContain(">a &amp; b &lt; c &gt; d<")
  })
})

describe("memoriesSection", () => {
  test("builds section with memories priority and tag", () => {
    const section = memoriesSection([mem], 20)
    expect(section.tag).toBe("memories")
    expect(section.priority).toBe(20)
    expect(section.items.length).toBe(1)
  })

  test("each item carries id, category, and truncated content", () => {
    const section = memoriesSection([mem], 20)
    const item = section.items[0]
    expect(item.tag).toBe("memory")
    expect(item.attrs?.id).toBe("mem_abc123")
    expect(item.attrs?.category).toBe("priority")
    expect(item.content).toBe("Prefers threading over asyncio for concurrent I/O")
  })

  test("truncates content to configured word count", () => {
    const longMem: Memory = { ...mem, text: "word ".repeat(30).trim() }
    const section = memoriesSection([longMem], 10)
    expect(section.items[0].content).toContain("...")
  })

  test("returns empty items array for empty input", () => {
    const section = memoriesSection([], 20)
    expect(section.items).toEqual([])
  })
})

describe("recentActionsSection", () => {
  test("builds section with recent_actions priority and tag", () => {
    const actions: RecentAction[] = [{ tool: "bash", target: "pytest", result: "ok" }]
    const section = recentActionsSection(actions)
    expect(section.tag).toBe("recent_actions")
    expect(section.priority).toBe(10)
    expect(section.items.length).toBe(1)
  })

  test("each item carries tool, target, result as attributes and no content", () => {
    const actions: RecentAction[] = [{ tool: "bash", target: "pytest", result: "ok" }]
    const section = recentActionsSection(actions)
    const item = section.items[0]
    expect(item.tag).toBe("action")
    expect(item.attrs?.tool).toBe("bash")
    expect(item.attrs?.target).toBe("pytest")
    expect(item.attrs?.result).toBe("ok")
    expect(item.content).toBeUndefined()
  })

  test("returns empty items array for empty input", () => {
    expect(recentActionsSection([]).items).toEqual([])
  })
})

describe("formatHud", () => {
  test("returns empty string when all sections are empty", () => {
    expect(formatHud([])).toBe("")
    expect(formatHud([memoriesSection([], 20), recentActionsSection([])])).toBe("")
  })

  test("wraps output in developer_context tags", () => {
    const result = formatHud([memoriesSection([mem], 20)])
    expect(result).toContain("<developer_context>")
    expect(result).toContain("</developer_context>")
  })

  test("renders memories as <memory> elements with id and content", () => {
    const result = formatHud([memoriesSection([mem], 20)])
    expect(result).toContain('id="mem_abc123"')
    expect(result).toContain("Prefers threading over asyncio for concurrent I/O")
  })

  test("wraps memory items in <memories> section tag", () => {
    const result = formatHud([memoriesSection([mem], 20)])
    expect(result).toContain("<memories>")
    expect(result).toContain("</memories>")
  })

  test("renders multiple memory items each as its own element", () => {
    const mem2: Memory = { id: "def456", text: "Uses Docker for deployment", category: "priority" }
    const result = formatHud([memoriesSection([mem, mem2], 20)])
    const memoryCount = (result.match(/<memory /g) ?? []).length
    expect(memoryCount).toBe(2)
    expect(result).toContain("abc123")
    expect(result).toContain("def456")
  })

  test("truncates memory content within HUD", () => {
    const longMem: Memory = { ...mem, text: "word ".repeat(30).trim() }
    const result = formatHud([memoriesSection([longMem], 10)])
    expect(result).toContain("...")
  })

  test("renders recent actions section alongside memories", () => {
    const actions: RecentAction[] = [
      { tool: "bash", target: "pytest", result: "Ran pytest" },
      { tool: "read", target: "src/index.ts", result: "Read file" },
    ]
    const result = formatHud([recentActionsSection(actions), memoriesSection([mem], 20)])
    expect(result).toContain("<recent_actions>")
    expect(result).toContain("</recent_actions>")
    expect(result).toContain('tool="bash"')
    expect(result).toContain('tool="read"')
    expect(result).toContain("mem_abc123")
  })

  test("orders sections by priority regardless of caller argument order", () => {
    const actions: RecentAction[] = [{ tool: "bash", target: "ls", result: "ok" }]
    const result = formatHud([memoriesSection([mem], 20), recentActionsSection(actions)])
    expect(result.indexOf("<recent_actions>")).toBeLessThan(result.indexOf("<memories>"))
  })

  test("renders HUD with only tool trace when memories are empty", () => {
    const actions: RecentAction[] = [{ tool: "bash", target: "ls", result: "Listed files" }]
    const result = formatHud([recentActionsSection(actions), memoriesSection([], 20)])
    expect(result).toContain("<developer_context>")
    expect(result).toContain("<recent_actions>")
    expect(result).toContain('tool="bash"')
    expect(result).not.toContain("<memories>")
  })

  test("omits recent_actions section when tool trace is empty", () => {
    const result = formatHud([recentActionsSection([]), memoriesSection([mem], 20)])
    expect(result).not.toContain("<recent_actions>")
  })

  test("escapes special characters in memory content", () => {
    const dangerous: Memory = { id: "x1", text: "uses <script>alert(1)</script> & stuff", category: "priority" }
    const result = formatHud([memoriesSection([dangerous], 20)])
    expect(result).toContain("&lt;script&gt;")
    expect(result).toContain("&amp;")
    expect(result).not.toContain("<script>")
  })

  test("logs rendered HUD when loud option is true", () => {
    const spy = spyOn(console, "log").mockImplementation(() => {})
    try {
      formatHud([memoriesSection([mem], 20)], { loud: true })
      expect(spy).toHaveBeenCalled()
      const call = spy.mock.calls[0][0] as string
      expect(call).toContain("[codemira:loud] HUD:")
      expect(call).toContain("<developer_context>")
    } finally {
      spy.mockRestore()
    }
  })

  test("does not log when loud option is omitted", () => {
    const spy = spyOn(console, "log").mockImplementation(() => {})
    try {
      formatHud([memoriesSection([mem], 20)])
      expect(spy).not.toHaveBeenCalled()
    } finally {
      spy.mockRestore()
    }
  })
})

describe("renderPrompt", () => {
  test("substitutes named slots", () => {
    expect(renderPrompt("hello {name}", { name: "world" })).toBe("hello world")
  })

  test("substitutes multiple slots", () => {
    expect(renderPrompt("{a} and {b}", { a: "x", b: "y" })).toBe("x and y")
  })

  test("throws on missing slot", () => {
    expect(() => renderPrompt("hello {name}", {})).toThrow("Missing prompt slots")
  })

  test("throws on unknown slot", () => {
    expect(() => renderPrompt("hello {name}", { name: "x", extra: "y" })).toThrow("Unknown prompt slots")
  })

  test("ignores JSON-style braces that are not slots", () => {
    const tmpl = 'output {"decision":"squash"} ok'
    expect(renderPrompt(tmpl, {})).toBe(tmpl)
  })

  test("repeated slot replaced everywhere", () => {
    expect(renderPrompt("{x}-{x}", { x: "z" })).toBe("z-z")
  })
})

describe("daemonCall", () => {
  test("fire-forget returns immediately without throwing", async () => {
    const result = await daemonCall("http://localhost:1", "POST", "/x", { a: 1 }, { expect: "fire-forget" })
    expect("ok" in result).toBe(true)
  })

  test("returns error on connection refused", async () => {
    const result = await daemonCall("http://localhost:1", "POST", "/x", { a: 1 }, { expect: "result" })
    expect("error" in result).toBe(true)
    if ("error" in result) {
      expect(["down", "timeout", "bad_response"]).toContain(result.error)
    }
  })

  test("respects timeout option", async () => {
    const result = await daemonCall("http://localhost:1", "GET", "/x", null, { expect: "result", timeout: 100 })
    expect("error" in result).toBe(true)
  })
})
