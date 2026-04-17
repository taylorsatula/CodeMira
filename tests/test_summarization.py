from codemira.summarization.handler import _format_raw_transcript, split_into_turns, _chunk_transcript


class TestFormatRawTranscript:
    def test_user_message(self):
        conversation = [{"role": "user", "parts": [{"type": "text", "text": "Hello"}]}]
        assert _format_raw_transcript(conversation) == "User: Hello"

    def test_assistant_text(self):
        conversation = [{"role": "assistant", "parts": [{"type": "text", "text": "Hi there"}]}]
        assert _format_raw_transcript(conversation) == "Assistant: Hi there"

    def test_completed_tool_with_title(self):
        conversation = [{"role": "assistant", "parts": [{"type": "tool", "tool": "bash", "state": {"status": "completed", "title": "Ran tests", "output": "long output..."}}]}]
        assert _format_raw_transcript(conversation) == "Tool: bash — Ran tests"

    def test_completed_tool_without_title_truncates_output(self):
        long_output = "x" * 1000
        conversation = [{"role": "assistant", "parts": [{"type": "tool", "tool": "read", "state": {"status": "completed", "output": long_output}}]}]
        result = _format_raw_transcript(conversation)
        assert "Tool: read — " in result
        assert len(result.split("Tool: read — ")[1]) <= 500

    def test_skips_non_completed_tools(self):
        conversation = [{"role": "assistant", "parts": [{"type": "tool", "tool": "bash", "state": {"status": "pending"}}]}]
        assert _format_raw_transcript(conversation) == ""

    def test_full_conversation(self):
        conversation = [
            {"role": "user", "parts": [{"type": "text", "text": "Fix the bug"}]},
            {"role": "assistant", "parts": [
                {"type": "tool", "tool": "grep", "state": {"status": "completed", "title": "Found 3 matches", "output": "file.py:10"}},
                {"type": "text", "text": "I found it"},
            ]},
        ]
        result = _format_raw_transcript(conversation)
        assert "User: Fix the bug" in result
        assert "Tool: grep — Found 3 matches" in result
        assert "Assistant: I found it" in result


class TestSplitIntoTurns:
    def test_single_turn(self):
        assert split_into_turns("User: hello\nAssistant: hi") == ["User: hello\nAssistant: hi"]

    def test_two_turns(self):
        transcript = "User: hello\nAssistant: hi\nUser: fix it\nAssistant: done"
        turns = split_into_turns(transcript)
        assert len(turns) == 2

    def test_empty(self):
        assert split_into_turns("") == []

    def test_whitespace_only(self):
        assert split_into_turns("  \n  ") == []


class TestChunkTranscript:
    def test_short_returns_single(self):
        transcript = "User: hello\nAssistant: hi"
        assert _chunk_transcript(transcript, 128000) == [transcript]

    def test_splits_long_transcript(self):
        turn = "User: " + "x" * 8000 + "\nAssistant: reply"
        transcript = "\n\n".join([turn] * 50)
        chunks = _chunk_transcript(transcript, 32000)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.startswith("User:")
