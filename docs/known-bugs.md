# Known Bugs

Pre-existing bugs found during the chokepoint/concurrency refactor (Bundle 1).
Document only — fixes deferred unless explicitly part of an active bundle.

---

## BUG-001 — Incomplete `_split_into_turns` → `split_into_turns` rename — RESOLVED

**Original**: `daemon/codemira/extraction/chunker.py` called `_split_into_turns(transcript)` inside `chunk_compressed_transcript` but only `split_into_turns` (no underscore) was defined. Tests `test_chunker.py:3` and `test_summarization.py:1` imported the underscored name.

**Resolution**: Bundle 5 created `daemon/codemira/extraction/transcript.py` with `USER_PREFIX`, `iter_turns`, `render_transcript`. `chunker.split_into_turns` now uses `USER_PREFIX`; both formatters route through `iter_turns` + `render_transcript`. The stopgap aliases were removed; tests updated to import `split_into_turns` directly.

---

## BUG-002 — `_chunk_transcript` test calls missing required arg

**Where**: `tests/test_summarization.py:61, 66` invoke `_chunk_transcript(transcript, 128000)` with 2 positional args. The function signature in `summarization/arc.py:39` is `_chunk_transcript(transcript: str, context_length: int, chunk_target_tokens: int)` — `chunk_target_tokens` has no default.

**Symptom**: `TypeError: _chunk_transcript() missing 1 required positional argument: 'chunk_target_tokens'`. Two tests fail at collection runtime, not import — `TestChunkTranscript::test_short_returns_single` and `TestChunkTranscript::test_splits_long_transcript`.

**Status**: Pre-existing — present before Bundle 1. Not blocking other test verification because the failures are isolated to two tests.

**Real fix**: Either (a) add a default value `chunk_target_tokens: int = 30_000` to match how `generate_arc` calls it (`handler.py:73-74`), or (b) update the test calls to pass the third arg explicitly. Bundle 5 is rewriting the chunking layer anyway and may collapse this — revisit then.
