from codemira.extraction.transcript import (
    USER_PREFIX,
    ASSISTANT_PREFIX,
    TOOL_PREFIX,
    Turn,
    iter_turns,
    render_transcript,
)


class TestIterTurns:
    def test_yields_user_and_assistant(self):
        conversation = [
            {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "hello"}]},
        ]
        turns = list(iter_turns(conversation))
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[1].role == "assistant"

    def test_skips_unknown_roles(self):
        conversation = [
            {"role": "system", "parts": []},
            {"role": "user", "parts": [{"type": "text", "text": "x"}]},
        ]
        turns = list(iter_turns(conversation))
        assert len(turns) == 1
        assert turns[0].role == "user"

    def test_missing_parts_defaults_empty(self):
        conversation = [{"role": "user"}]
        turns = list(iter_turns(conversation))
        assert turns == [Turn(role="user", parts=[])]


class TestRenderTranscript:
    def test_user_text(self):
        turns = [Turn(role="user", parts=[{"type": "text", "text": "fix bug"}])]
        assert render_transcript(turns, lambda p: None) == f"{USER_PREFIX} fix bug"

    def test_assistant_text(self):
        turns = [Turn(role="assistant", parts=[{"type": "text", "text": "ok"}])]
        assert render_transcript(turns, lambda p: None) == f"{ASSISTANT_PREFIX} ok"

    def test_tool_renderer_called(self):
        turns = [Turn(role="assistant", parts=[{"type": "tool", "tool": "grep"}])]
        out = render_transcript(turns, lambda p: f"{TOOL_PREFIX} {p['tool']} ran")
        assert out == f"{TOOL_PREFIX} grep ran"

    def test_tool_renderer_returning_none_skips(self):
        turns = [Turn(role="assistant", parts=[{"type": "tool", "tool": "grep"}])]
        out = render_transcript(turns, lambda p: None)
        assert out == ""

    def test_full_conversation(self):
        conversation = [
            {"role": "user", "parts": [{"type": "text", "text": "Fix"}]},
            {"role": "assistant", "parts": [
                {"type": "tool", "tool": "grep", "state": {"status": "completed", "title": "found"}},
                {"type": "text", "text": "done"},
            ]},
        ]

        def render_tool(part):
            return f"{TOOL_PREFIX} {part['tool']} — {part['state']['title']}"

        out = render_transcript(iter_turns(conversation), render_tool)
        assert "User: Fix" in out
        assert "Tool: grep — found" in out
        assert "Assistant: done" in out


class TestPrefixesMatchChunkerContract:
    def test_user_prefix_used_by_chunker(self):
        from codemira.extraction.chunker import parse_turns
        transcript = f"{USER_PREFIX} a\n{ASSISTANT_PREFIX} b\n{USER_PREFIX} c"
        turns = parse_turns(transcript)
        assert len(turns) == 2
