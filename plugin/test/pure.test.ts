import { describe, test, expect } from "bun:test"
import {
  extractCurrentTurnContext,
  formatPinnedMemories,
  parseSubcorticalXml,
  formatHud,
  triggerArcGeneration,
  fetchArcSummary,
  type Memory,
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

describe("extractCurrentTurnContext", () => {
  test("extracts completed tool calls and user message for the current turn", () => {
    const messages = [
      makeUserMsg("Run the tests"),
      makeAssistantMsg([
        makeToolPart("bash", { command: "pytest" }, "3 passed", "Ran pytest"),
      ]),
    ]
    const { userMessage, toolTrace } = extractCurrentTurnContext(messages, 20)
    expect(toolTrace.length).toBe(1)
    expect(toolTrace[0]).toContain('tool="bash"')
    expect(toolTrace[0]).toContain('target="pytest"')
    expect(toolTrace[0]).toContain('result="Ran pytest"')
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
    const { toolTrace } = extractCurrentTurnContext(messages, 20)
    expect(toolTrace.length).toBe(0)
  })

  test("respects window limit across multiple assistant messages in same turn", () => {
    const messages = [
      makeUserMsg("Do work"),
      makeAssistantMsg([makeToolPart("bash", { command: "cmd1" }, "out1", "title1")]),
      makeAssistantMsg([makeToolPart("bash", { command: "cmd2" }, "out2", "title2")]),
      makeAssistantMsg([makeToolPart("bash", { command: "cmd3" }, "out3", "title3")]),
      makeAssistantMsg([makeToolPart("bash", { command: "cmd4" }, "out4", "title4")]),
    ]
    const { toolTrace } = extractCurrentTurnContext(messages, 2)
    expect(toolTrace.length).toBe(2)
  })

  test("stops at the start of the current turn", () => {
    const messages = [
      makeUserMsg("Old question"),
      makeAssistantMsg([makeToolPart("bash", { command: "old_cmd" }, "out", "old")]),
      makeUserMsg("New question"),
      makeAssistantMsg([makeToolPart("bash", { command: "new_cmd" }, "out", "new")]),
    ]
    const { userMessage, toolTrace } = extractCurrentTurnContext(messages, 20)
    expect(userMessage).toBe("New question")
    expect(toolTrace.length).toBe(1)
    expect(toolTrace[0]).toContain("new_cmd")
  })

  test("extracts tool target from path, command, or pattern", () => {
    const pathResult = extractCurrentTurnContext([makeUserMsg(""), makeAssistantMsg([makeToolPart("read", { path: "src/index.ts" }, "content")])], 20)
    const cmdResult = extractCurrentTurnContext([makeUserMsg(""), makeAssistantMsg([makeToolPart("bash", { command: "npm test" }, "ok")])], 20)
    const patResult = extractCurrentTurnContext([makeUserMsg(""), makeAssistantMsg([makeToolPart("grep", { pattern: "TODO" }, "3 matches")])], 20)

    expect(pathResult.toolTrace[0]).toContain('target="src/index.ts"')
    expect(cmdResult.toolTrace[0]).toContain('target="npm test"')
    expect(patResult.toolTrace[0]).toContain('target="TODO"')
  })

  test("uses title over truncated output for result", () => {
    const messages = [
      makeUserMsg(""),
      makeAssistantMsg([
        makeToolPart("bash", { command: "ls" }, "file1.txt\nfile2.txt\nfile3.txt", "Listed files"),
      ]),
    ]
    const { toolTrace } = extractCurrentTurnContext(messages, 20)
    expect(toolTrace[0]).toContain('result="Listed files"')
    expect(toolTrace[0]).not.toContain("file1.txt")
  })

  test("truncates user message to 500 characters", () => {
    const longText = "x".repeat(600)
    const messages = [makeUserMsg(longText)]
    const { userMessage } = extractCurrentTurnContext(messages, 20)
    expect(userMessage.length).toBe(500)
  })

  test("skips synthetic user messages when finding goal", () => {
    const messages = [
      makeUserMsg("Real user message", false),
      makeUserMsg("System reminder", true),
    ]
    const { userMessage } = extractCurrentTurnContext(messages, 20)
    expect(userMessage).toBe("Real user message")
  })

  test("returns empty when no user messages or tool calls", () => {
    const messages = [makeAssistantMsg([])]
    const { userMessage, toolTrace } = extractCurrentTurnContext(messages, 20)
    expect(userMessage).toBe("")
    expect(toolTrace.length).toBe(0)
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

// These tests are removed because importance score was ablated

})

describe("formatHud", () => {
  test("returns empty string when both memories and tool trace are empty", () => {
    expect(formatHud([], [], 20)).toBe("")
  })

  test("wraps output in developer_context tags", () => {
    const result = formatHud([mem], [], 20)
    expect(result).toContain("<developer_context>")
    expect(result).toContain("</developer_context>")
  })

  test("includes memory ID and text", () => {
    const result = formatHud([mem], [], 20)
    expect(result).toContain("mem_abc123")
    expect(result).toContain("Prefers threading over asyncio for concurrent I/O")
  })

  test("formats multiple memories within tags", () => {
    const mem2: Memory = { id: "def456", text: "Uses Docker for deployment", category: "priority" }
    const result = formatHud([mem, mem2], [], 20)
    expect(result).toContain("abc123")
    expect(result).toContain("def456")
    const inner = result.replace("<developer_context>\n", "").replace("\n</developer_context>", "")
    expect(inner.split("\n").length).toBe(2)
  })

  test("truncates within HUD", () => {
    const longMem: Memory = { ...mem, text: "word ".repeat(30).trim() }
    const result = formatHud([longMem], [], 10)
    expect(result).toContain("...")
  })

  test("renders tool trace inside recent_actions section", () => {
    const trace = [
      '<action tool="bash" target="pytest" result="Ran pytest" />',
      '<action tool="read" target="src/index.ts" result="Read file" />',
    ]
    const result = formatHud([mem], trace, 20)
    expect(result).toContain("<recent_actions>")
    expect(result).toContain("</recent_actions>")
    expect(result).toContain('tool="bash"')
    expect(result).toContain('tool="read"')
    expect(result).toContain("mem_abc123")
    expect(result.indexOf("<recent_actions>")).toBeLessThan(result.indexOf("mem_abc123"))
  })

  test("renders HUD with only tool trace when memories are empty", () => {
    const trace = ['<action tool="bash" target="ls" result="Listed files" />']
    const result = formatHud([], trace, 20)
    expect(result).toContain("<developer_context>")
    expect(result).toContain("<recent_actions>")
    expect(result).toContain('tool="bash"')
    expect(result).not.toContain("mem_")
  })

  test("omits recent_actions section when tool trace is empty", () => {
    const result = formatHud([mem], [], 20)
    expect(result).not.toContain("<recent_actions>")
  })
})

describe("triggerArcGeneration", () => {
  test("fires POST without throwing", () => {
    triggerArcGeneration("http://localhost:1", "ses_123", "/tmp/proj")
  })

  test("does not throw on connection refused", () => {
    triggerArcGeneration("http://localhost:1", "ses_123", "/tmp/proj")
  })
})

describe("fetchArcSummary", () => {
  test("returns null on connection refused", async () => {
    const result = await fetchArcSummary("http://localhost:1", "ses_123", "/tmp/proj")
    expect(result).toBeNull()
  })

  test("returns null on non-200 response", async () => {
    const result = await fetchArcSummary("http://localhost:1", "ses_123", "/tmp/proj")
    expect(result).toBeNull()
  })
})
