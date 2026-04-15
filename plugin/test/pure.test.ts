import { describe, test, expect } from "bun:test"
import {
  extractToolTrace,
  extractUserGoal,
  formatPinnedMemories,
  parseSubcorticalXml,
  formatHud,
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

const mem: Memory = { id: "abc123", text: "Prefers threading over asyncio for concurrent I/O", importance: 0.8, category: "priority" }

describe("extractToolTrace", () => {
  test("extracts completed tool calls from assistant messages", () => {
    const messages = [
      makeUserMsg("Run the tests"),
      makeAssistantMsg([
        makeToolPart("bash", { command: "pytest" }, "3 passed", "Ran pytest"),
      ]),
    ]
    const result = extractToolTrace(messages, 5)
    expect(result.length).toBe(1)
    expect(result[0]).toContain('tool="bash"')
    expect(result[0]).toContain('target="pytest"')
    expect(result[0]).toContain('result="Ran pytest"')
  })

  test("ignores pending and running tool states", () => {
    const messages = [
      makeAssistantMsg([
        { type: "tool", callID: "c1", tool: "bash", state: { status: "pending", input: { command: "ls" } } },
        { type: "tool", callID: "c2", tool: "read", state: { status: "running", input: { path: "file.ts" } } },
      ]),
    ]
    const result = extractToolTrace(messages, 5)
    expect(result.length).toBe(0)
  })

  test("respects window limit across multiple messages", () => {
    const messages = [
      makeAssistantMsg([makeToolPart("bash", { command: "cmd1" }, "out1", "title1")]),
      makeAssistantMsg([makeToolPart("bash", { command: "cmd2" }, "out2", "title2")]),
      makeAssistantMsg([makeToolPart("bash", { command: "cmd3" }, "out3", "title3")]),
      makeAssistantMsg([makeToolPart("bash", { command: "cmd4" }, "out4", "title4")]),
    ]
    const result = extractToolTrace(messages, 2)
    expect(result.length).toBe(2)
    expect(result[0]).toContain("cmd4")
    expect(result[1]).toContain("cmd3")
  })

  test("respects window limit within single message", () => {
    const parts = Array.from({ length: 10 }, (_, i) =>
      makeToolPart("bash", { command: `cmd${i}` }, `out${i}`, `title${i}`)
    )
    const messages = [makeAssistantMsg(parts)]
    const result = extractToolTrace(messages, 3)
    expect(result.length).toBe(3)
  })

  test("skips user messages", () => {
    const messages = [
      makeUserMsg("Hello"),
      makeUserMsg("World"),
    ]
    const result = extractToolTrace(messages, 5)
    expect(result.length).toBe(0)
  })

  test("extracts tool target from path, command, or pattern", () => {
    const pathResult = extractToolTrace([makeAssistantMsg([makeToolPart("read", { path: "src/index.ts" }, "content")])], 5)
    const cmdResult = extractToolTrace([makeAssistantMsg([makeToolPart("bash", { command: "npm test" }, "ok")])], 5)
    const patResult = extractToolTrace([makeAssistantMsg([makeToolPart("grep", { pattern: "TODO" }, "3 matches")])], 5)

    expect(pathResult[0]).toContain('target="src/index.ts"')
    expect(cmdResult[0]).toContain('target="npm test"')
    expect(patResult[0]).toContain('target="TODO"')
  })

  test("uses title over truncated output for result", () => {
    const messages = [
      makeAssistantMsg([
        makeToolPart("bash", { command: "ls" }, "file1.txt\nfile2.txt\nfile3.txt", "Listed files"),
      ]),
    ]
    const result = extractToolTrace(messages, 5)
    expect(result[0]).toContain('result="Listed files"')
    expect(result[0]).not.toContain("file1.txt")
  })

  test("truncates output to 80 chars when no title", () => {
    const longOutput = "x".repeat(200)
    const messages = [
      makeAssistantMsg([
        makeToolPart("bash", { command: "ls" }, longOutput, ""),
      ]),
    ]
    const result = extractToolTrace(messages, 5)
    const resultMatch = result[0].match(/result="([^"]*)"/)
    expect(resultMatch).not.toBeNull()
    expect(resultMatch![1].length).toBeLessThanOrEqual(80)
  })
})

describe("extractUserGoal", () => {
  test("extracts text from last user message", () => {
    const messages = [
      makeUserMsg("First question"),
      makeAssistantMsg([]),
      makeUserMsg("Set up a FastAPI project"),
    ]
    expect(extractUserGoal(messages)).toBe("Set up a FastAPI project")
  })

  test("skips synthetic user messages", () => {
    const messages = [
      makeUserMsg("System reminder", true),
      makeUserMsg("Real user message", false),
    ]
    expect(extractUserGoal(messages)).toBe("Real user message")
  })

  test("truncates to 200 characters", () => {
    const longText = "x".repeat(300)
    const messages = [makeUserMsg(longText)]
    expect(extractUserGoal(messages).length).toBe(200)
  })

  test("returns empty string when no user messages", () => {
    const messages = [makeAssistantMsg([])]
    expect(extractUserGoal(messages)).toBe("")
  })

  test("returns empty string when only synthetic user messages exist", () => {
    const messages = [makeUserMsg("reminder", true)]
    expect(extractUserGoal(messages)).toBe("")
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

  test("formats single memory with correct importance dots", () => {
    const result = formatPinnedMemories([mem], 20)
    expect(result).toContain("mem_abc123")
    expect(result).toContain("●●●●○")
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
    const mem2: Memory = { id: "def456", text: "Uses Docker for deployment", importance: 0.6, category: "decision_rationale" }
    const result = formatPinnedMemories([mem, mem2], 20)
    const lines = result.split("\n")
    expect(lines.length).toBe(2)
    expect(lines[0]).toContain("abc123")
    expect(lines[1]).toContain("def456")
  })

  test("zero importance shows empty dots", () => {
    const lowMem: Memory = { ...mem, importance: 0.0 }
    const result = formatPinnedMemories([lowMem], 20)
    expect(result).toContain("○○○○○")
  })

  test("full importance shows full dots", () => {
    const highMem: Memory = { ...mem, importance: 1.0 }
    const result = formatPinnedMemories([highMem], 20)
    expect(result).toContain("●●●●●")
  })
})

describe("formatHud", () => {
  test("returns empty string for empty list", () => {
    expect(formatHud([], 20)).toBe("")
  })

  test("wraps output in developer_context tags", () => {
    const result = formatHud([mem], 20)
    expect(result).toContain("<developer_context>")
    expect(result).toContain("</developer_context>")
  })

  test("includes memory ID and text", () => {
    const result = formatHud([mem], 20)
    expect(result).toContain("mem_abc123")
    expect(result).toContain("Prefers threading over asyncio for concurrent I/O")
  })

  test("formats multiple memories within tags", () => {
    const mem2: Memory = { id: "def456", text: "Uses Docker for deployment", importance: 0.5, category: "priority" }
    const result = formatHud([mem, mem2], 20)
    expect(result).toContain("abc123")
    expect(result).toContain("def456")
    const inner = result.replace("<developer_context>\n", "").replace("\n</developer_context>", "")
    expect(inner.split("\n").length).toBe(2)
  })

  test("truncates within HUD", () => {
    const longMem: Memory = { ...mem, text: "word ".repeat(30).trim() }
    const result = formatHud([longMem], 10)
    expect(result).toContain("...")
  })
})