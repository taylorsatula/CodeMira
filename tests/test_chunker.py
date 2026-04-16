import pytest

from codemira.extraction.chunker import estimate_token_count, chunk_compressed_transcript, _split_into_turns


class TestEstimateTokenCount:
    def test_empty_string(self):
        assert estimate_token_count("") == 0

    def test_basic(self):
        text = "a" * 400
        assert estimate_token_count(text) == 100

    def test_unicode(self):
        text = "hello" * 20
        assert estimate_token_count(text) > 0


class TestSplitIntoTurns:
    def test_single_user_message(self):
        assert _split_into_turns("User: hello") == ["User: hello"]

    def test_user_assistant_pair(self):
        transcript = "User: hello\nAssistant: hi"
        assert _split_into_turns(transcript) == ["User: hello\nAssistant: hi"]

    def test_user_with_tool_chain(self):
        transcript = "User: fix the bug\nAssistant: let me check\nTool: grep — found 3 matches\nAssistant: here's the fix\nTool: edit — updated file\nAssistant: done"
        turns = _split_into_turns(transcript)
        assert len(turns) == 1
        assert "Tool: grep" in turns[0]
        assert "Tool: edit" in turns[0]

    def test_two_turns_split_at_user_boundary(self):
        transcript = "User: hello\nAssistant: hi\nUser: now fix this\nAssistant: done"
        turns = _split_into_turns(transcript)
        assert len(turns) == 2
        assert turns[0] == "User: hello\nAssistant: hi"
        assert turns[1] == "User: now fix this\nAssistant: done"

    def test_tool_chain_stays_with_its_user(self):
        transcript = "User: check tests\nTool: pytest — 3 failures\nAssistant: fix coming\nUser: go ahead\nTool: edit — patched\nAssistant: done"
        turns = _split_into_turns(transcript)
        assert len(turns) == 2
        assert "Tool: pytest" in turns[0]
        assert "Tool: edit" in turns[1]

    def test_empty_transcript(self):
        assert _split_into_turns("") == []

    def test_whitespace_only(self):
        assert _split_into_turns("  \n  \n  ") == []


class TestChunkCompressedTranscript:
    def test_short_transcript_returns_single_chunk(self):
        transcript = "User: hello\n\nAssistant: hi"
        chunks = chunk_compressed_transcript(transcript, context_length=128000)
        assert chunks == [transcript]

    def test_empty_transcript(self):
        chunks = chunk_compressed_transcript("", context_length=128000)
        assert chunks == [""]

    def test_splits_at_user_message_boundaries(self):
        turn = "User: " + "x" * 8000 + "\nAssistant: reply"
        transcript = "\n\n".join([turn] * 50)
        chunks = chunk_compressed_transcript(transcript, context_length=32000)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.startswith("User:")

    def test_tool_chain_not_split_from_its_user(self):
        turn = "User: fix it\nTool: grep — found match\nTool: edit — patched\nAssistant: done"
        long_turn = turn + " " + "x" * 4000
        transcript = "\n\n".join([long_turn] * 30)
        chunks = chunk_compressed_transcript(transcript, context_length=32000)
        for chunk in chunks:
            assert "Tool: grep" not in chunk or "Tool: edit" in chunk

    def test_chunk_size_respects_context_length(self):
        turn = "User: " + "x" * 4000 + "\nAssistant: reply"
        transcript = "\n\n".join([turn] * 100)
        context_length = 32000
        chunks = chunk_compressed_transcript(transcript, context_length)
        for chunk in chunks:
            estimated_tokens = estimate_token_count(chunk)
            budget = max(75_000, int(0.7 * context_length))
            assert estimated_tokens <= budget

    def test_seventy_percent_preferred_over_75k_for_large_context(self):
        turn = "User: " + "x" * 4000 + "\nAssistant: reply"
        transcript = "\n\n".join([turn] * 200)
        context_length = 128000
        chunks = chunk_compressed_transcript(transcript, context_length)
        expected_budget = int(0.7 * context_length)
        for chunk in chunks:
            assert estimate_token_count(chunk) <= expected_budget

    def test_seventy_five_k_floor_for_small_context(self):
        context_length = 200000
        budget = max(75_000, int(0.7 * context_length))
        assert budget == 140000
        turn = "User: " + "x" * 4000 + "\nAssistant: reply"
        transcript = "\n\n".join([turn] * 300)
        chunks = chunk_compressed_transcript(transcript, context_length)
        for chunk in chunks:
            assert estimate_token_count(chunk) <= budget

    def test_existing_memories_reduces_budget(self):
        turn = "User: " + "x" * 4000 + "\nAssistant: reply"
        transcript = "\n\n".join([turn] * 100)
        context_length = 32000
        chunks_no_existing = chunk_compressed_transcript(transcript, context_length, existing_memories_token_estimate=0)
        chunks_with_existing = chunk_compressed_transcript(transcript, context_length, existing_memories_token_estimate=2000)
        assert len(chunks_with_existing) >= len(chunks_no_existing)

    def test_no_turns_dropped(self):
        turns = [f"User: message {i} with some content\nAssistant: reply {i}" for i in range(30)]
        transcript = "\n\n".join(turns)
        chunks = chunk_compressed_transcript(transcript, context_length=32000)
        reconstructed = "\n\n".join(chunks)
        for turn in turns:
            assert turn in reconstructed

    def test_preserves_turn_order(self):
        turns = [f"User: message {i:03d}\nAssistant: reply {i:03d}" for i in range(30)]
        transcript = "\n\n".join(turns)
        chunks = chunk_compressed_transcript(transcript, context_length=32000)
        for chunk in chunks:
            first_idx = None
            last_idx = None
            for i, turn in enumerate(turns):
                if turn in chunk:
                    if first_idx is None:
                        first_idx = i
                    last_idx = i
            if first_idx is not None and last_idx is not None:
                assert last_idx >= first_idx

    def test_single_turn_exceeding_budget_stays_intact(self):
        huge_turn = "User: " + "x" * 200000 + "\nAssistant: done"
        chunks = chunk_compressed_transcript(huge_turn, context_length=128000)
        assert len(chunks) == 1
        assert chunks[0] == huge_turn
