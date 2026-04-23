# Seed: One-Time Codebase Scan for Memory Bootstrap

## Problem

Fresh CodeMira installs sit empty until enough conversations surface conventions. Some conventions are so universally followed they never come up in chat — no friction, no discussion, no memory. Seeding from the codebase addresses cold start without changing the per-project reinforcement model: a one-shot scan that proposes codebase-visible conventions, a human gate that approves or rejects each, and then the normal pipeline takes over.

## Scope

### Seedable categories (codebase-visible signal)

- `naming_convention` — e.g. "DB read functions are prefixed `read_*`; `get_*` is reserved for pure in-memory accessors."
- `vocabulary` — project-internal terms (`arc_fragments`, `subcortical`, `PeanutGallery`).
- `testing_convention` — framework, test file structure, fixture conventions.
- `error_handling` — fail-fast vs defensive patterns inferable from `try/except` usage.
- `dependency_philosophy` — stdlib-vs-third-party choices visible in imports (e.g. "HTTP transport uses `urllib.request`; no `requests`/`httpx` dependency").
- `hidden_constraint` — schema/validator-enforced rules (pagination caps, ID formats, timezone discipline).

### Not seedable (require conversation)

- `rejected_alternative` — absent from code by definition.
- `decision_rationale` — only visible in near-misses or commit history.
- `debugging_style` — not in code.
- `priority` — tradeoffs surface in discussion, not structure.

The seed prompt allowlists the six seedable categories. Any candidate outside the allowlist is dropped before reaching the gate.

## Flow

1. **Invocation**: `python -m codemira seed <worktree>`.
2. **Discovery**: agent crawls the worktree, respecting `.gitignore`.
3. **Candidate generation**: agent proposes one memory at a time; each claim triggers a verification sweep before emission.
4. **Interactive gate**: candidates rendered as a terminal checkbox list with evidence visible on the same screen.
5. **Write**: approved candidates flow through the existing pipeline — `insert_memory` → `EmbeddingsProvider.get().encode_deep` → `MemoryIndex.rebuild_after_write` → entity extraction → link classification.

## Agent shape

Headless Claude Code (or equivalent tool-calling loop). Tool access is load-bearing: the agent MUST verify each claim via `grep`/`rg`/`find` before emitting it. A single-pass `call_llm` cannot do this and would fail on sampling confidence — eight files that agree are not a convention.

The system prompt instructs:

- Only the six seedable categories. Out-of-allowlist candidates are invalid.
- No surface facts (no "uses Python 3.11", no "has a `src/` directory", no "imports `os`").
- Every candidate includes verified evidence. Claims without a recorded verification query are invalid.
- A pattern counts only if occurrences ≥ threshold and counter-examples = 0 (or each counter-example is explained).
- Return zero candidates if nothing meets the bar. No quota.

## Candidate contract

Each candidate is a JSON object:

```json
{
  "category": "naming_convention",
  "text": "Database read functions are prefixed `read_*`. `get_*` is reserved for pure, in-memory accessors with no I/O.",
  "entities": [
    {"name": "read_*", "type": "pattern"},
    {"name": "get_*", "type": "pattern"}
  ],
  "evidence": {
    "occurrences": 14,
    "counter_examples": 0,
    "examples": [
      "daemon/codemira/store/memory.py:48 read_memory_by_id",
      "daemon/codemira/opencode_db.py:67 read_session_conversation"
    ],
    "verification_queries": [
      "rg '^def read_' daemon/",
      "rg '^def get_' daemon/"
    ]
  }
}
```

`evidence` is required. The gate renders it; the writer does not persist it (the memory row stores `text`, `category`, `entities` as normal).

## Interactive gate

Terminal TUI via `questionary.checkbox` (or equivalent). Each entry shows:

- Category tag.
- One-line summary (expandable to full text).
- Evidence summary — `14 occurrences, 0 counter-examples`.
- Two or three example file refs.

Default state is unchecked. Nothing is committed without explicit toggle-and-confirm. Evidence on the same screen as the claim is what makes the gate meaningful rather than theater — the user can judge in two seconds with evidence visible, thirty seconds without.

Flags:

- `--dry-run`: write the candidate set to `.codememory/seed_preview.jsonl` and exit. No DB writes.
- `--max-candidates <n>`: cap at `n` (default 50). Agent is told to prioritize high-evidence claims if capped.
- `--model <name>`: override the seed agent model. Defaults to `extraction_model`.

## Storage integration

Seed memories are indistinguishable from conversation-extracted memories after write, with one schema change: a new column `memories.source TEXT NOT NULL DEFAULT 'conversation'` with values `'conversation'` or `'seed'`. Rationale:

- Consolidation, supersession, linking, and retrieval treat seed and conversation memories identically. A later conversation contradicting a seed memory is handled by the existing consolidation path.
- `source` is useful for observability (counting seed memories per store, auditing seed impact on retrieval) without changing behavior.
- `source_session_id` stays null for seed rows. Keep it as-is — it's genuinely absent, not a sentinel.

Greenfield repo, so no migration shim. Add the column, default existing rows to `'conversation'`, done.

## Prompts

New file: `prompts/seed_system.txt`. Mirrors `extraction_system.txt`'s discipline — reject surface facts, extract invisible-only — with these additions:

- Explicit six-category allowlist with one-line definitions.
- The verification-before-emit requirement, with the candidate JSON shape as a schema.
- One positive example per category, each showing the evidence block.
- A "tempting but wrong" negative-example list (patterns the agent will want to extract but shouldn't): file structure, language/runtime versions, dependency names without philosophy, obvious type annotations, etc.

Load via the existing `load_prompt(name, prompts_dir)`. No new prompt-loading machinery.

## CLI

New entrypoint: `python -m codemira seed`.

Arguments:

- `<worktree>` (positional, required): project root. Must contain `.codememory/memories.db` (seed refuses to run on an uninitialized store; user runs init first).
- `--dry-run`: candidate preview, no writes.
- `--model <name>`: override agent model.
- `--max-candidates <n>`: cap candidate count.
- `--force`: allow seeding a store that already has `source='seed'` rows (default refuses, to prevent accidental re-seed).

Fails fast on: missing worktree, missing `.codememory/memories.db`, missing `CODEMIRA_EXTRACTION_API_KEY` (seed uses the extraction endpoint by default).

## Failure modes

- **Sampling confidence** (primary risk): mitigated by the mandatory verification sweep. Candidates without recorded queries are dropped by the candidate contract validator before hitting the gate.
- **Stale-but-true drift**: if a seeded convention is quietly abandoned, supersession catches it only when new conversation contradicts it. Accepted for a one-timer; no rescan logic.
- **Agent hallucination under quota pressure**: prompt says "zero candidates is a valid output; do not fill quota." `--max-candidates` is a ceiling, never a floor.
- **Seed and future extraction overlap**: a convention that's seeded and later re-surfaced in conversation should consolidate, not duplicate. Existing text/vector dedup + consolidation handles this; seed rows are ordinary targets.
- **Category drift**: the seed prompt's allowlist must stay in sync with the extraction taxonomy. Single source of truth: a `SEEDABLE_CATEGORIES` constant in `daemon/codemira/extraction/` referenced by both the prompt-building code (for the allowlist injection) and the candidate validator.

## Out of scope

- Global preferences store — explicitly rejected by CodeMira design.
- Automatic re-seed on codebase change — supersession handles drift.
- Seeding from git history / commit messages — different signal source, separate concern.
- Non-interactive seed (`--yes-all`) — the gate is the feature. A seed without the gate is just low-quality bulk extraction.

## Open questions

- **Which headless runner**: Claude Code CLI vs. Claude Agent SDK vs. a home-grown tool-loop over `call_llm` with a minimal bash/grep tool surface. The third is the simplest dependency story (reuses the existing `extraction_api_key` / `extraction_base_url` config triplet, no new auth) but requires implementing the tool dispatch loop. Worth prototyping the third before committing to external runners.
- **Gate-time edit**: should the gate allow the user to tweak candidate text before committing, or is it pure approve/reject? Pure is simpler; editable is closer to how a human would actually curate.
