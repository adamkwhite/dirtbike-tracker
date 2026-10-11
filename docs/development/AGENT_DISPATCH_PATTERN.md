# Agent dispatch pattern (transparent / single-phase)

when to spawn a sub-agent vs. do work directly
how to keep agent runs visible to the human operator
how to split larger tasks into reviewable phases.

- multi-agent parallel case (dispatching 2+ agents simultaneously, ship-and-handoff stanzas), see [`PARALLEL_AGENT_PROMPTS.md`](PARALLEL_AGENT_PROMPTS.md).
- For  conflict matrix (mandatory pre-dispatch planning for 2+ parallel agents), see the dedicated section below.

in-task coding workflows (function-signature changes, refactoring, mock updates), see [`AGENT_BEST_PRACTICES.md`](AGENT_BEST_PRACTICES.md).

## The three patterns (pick one per task)

### 1. Do-it-myself (default for small work)

When: 1-3 files, single-concern, no isolation benefit. Examples: typo fixes, single-method refactors, label renames, doc updates, config tweaks, single-issue bug fixes touching one parser, adding a single test.

Why: every Bash, Read, Edit is visible to the operator in the conversation. Zero opacity. No orchestration overhead.

Cost: my context window. Avoid this for work that requires installing new dependencies, running large test suites, or branching deeply through unfamiliar code.

### 2. Single-phase agent dispatch (default for medium work)

**When**: bounded tasks needing isolation (fresh venv, untouched git tree, multi-file edits where I'd rather not pollute my working set) AND can be expressed as ONE phase with a concrete deliverable.

**Phase boundaries to consider**:
- *Investigate*: agent reads code, samples data, returns a 200-word report.
- *Implement + test*: agent writes code, returns the diff or opens a PR.
- *PR + handoff*: agent opens PR, exits.

A single phase = one of those three (or a tight combination like *implement + open PR* when there's no reasonable decision point between them).

**Pre-flight (mandatory)**: before launching, the orchestrator posts to the operator:
- The exact agent prompt (copy/paste-able for review)
- The model the agent will run on (per phase: Haiku for *investigate*, Sonnet for *implement + test*, Haiku for *PR + handoff*; see `~/Code/CLAUDE.md` § Model Routing)
- `~/Code/scripts/tail-agent.sh <agent-id>`, pastable into a second shell. Filters the JSONL transcript to one readable line per tool call (e.g. `[Bash] {"command":"..."}`). Plain `tail -f` produces unwrapped multi-KB JSON blobs that read as one continuous stream —  wrapper fixes that. Pass `--full` for every event pretty-printed via `jq -C`.

### 3. Multi-agent parallel dispatch (for genuinely parallelizable work)

**When**: 2+ independent tasks can run in worktree-isolated trees without conflicting on shared files.

See [`PARALLEL_AGENT_PROMPTS.md`](PARALLEL_AGENT_PROMPTS.md) for the conflict-matrix planning checklist and the ship-and-handoff prompt stanzas.

## Pre-flight checklist (every agent dispatch)

Before calling `Agent(...)`:

- [ ] **Exit plan mode**: If plan mode is active, call `ExitPlanMode`. Agents cannot override this via prompt instructions because the system constraint says "supercedes any other instructions."
- [ ] **Conflict-matrix scan**: which files would the agent edit? Are any of those held by an in-flight worktree, another queued agent, or my own pending edits?
- [ ] **Single-phase scope**: is this an *investigate*, an *implement*, or a *PR-and-handoff*? If it tries to be more than one, split.
- [ ] **Pre-flight announcement** posted to the operator with:
  - The agent's prompt (full text, not a paraphrase)
  - The model the agent will run on (Haiku/Sonnet/Opus — see `~/Code/CLAUDE.md` § Model Routing)
  - `~/Code/scripts/tail-agent.sh <agent-id>` command (filters JSONL to one-line-per-tool-call; `--full` for pretty-printed events)
- [ ] **Stop condition**: what does success look like? What does `[blocked]` look like? Make both explicit in the prompt.
- [ ] **Handoff protocol**: agent prints `[handoff] PR #N` (or `[checkpoint] <summary>` for non-PR phases) and exits. Agent does NOT poll CI, does NOT merge.
- [ ] **Resource contention (5+ agents)**: dispatching 5 or more concurrent agents on one box? Include Stanza 5 (resource management) from `PARALLEL_AGENT_PROMPTS.md` so agents run targeted tests only, serialize the full-suite/coverage gate behind a lock, skip test-parallelism flags, and serialize browser runs. Without it, 8 agents each running the full suite plus a browser OOMs the machine (measured: load 14.6, 3 of 4 GB swap).

## Conflict matrix (mandatory for multi-agent dispatch)

Before dispatching 2+ parallel agents, build a conflict matrix.

### How to build it

**Step 1 — List files per agent.** For each agent, enumerate every file it will create or modify. Be specific to the method/section level when multiple agents touch the same file.

Example from the May 21 dispatch (6 agents):

```
Agent A (ATS scrapers):     src/scrapers/ats_lever_scraper.py (NEW),
                            src/scrapers/ats_ashby_scraper.py (NEW),
                            src/jobs/company_scraper.py (_scrape_with_fallback),
                            tests/...
Agent B (Distributed):      src/jobs/company_scraper.py (scrape_all_companies),
                            scripts/run_unified_scraper.sh, scripts/setup_...
Agent C (Onboarding UX):    src/web/templates/onboard.html, hiring_card.html, signup.html
Agent D (Scoring M2):       src/agents/profile_scorer.py (_score_role_type)
Agent E (Connections):      src/utils/connections_manager.py
Agent F (City digest):      src/digest/html_generator.py
```

**Step 2 — Build the matrix.** Cross-reference agents against shared files:

```
| Agent | company_scraper.py | web templates | scorer | connections | html_generator |
|-------|-------------------|---------------|--------|-------------|----------------|
| A     | _scrape_with_fallback | — | — | — | — |
| B     | scrape_all_companies  | — | — | — | — |
| C     | —                     | onboard/hiring_card/signup | — | — | — |
| D     | —                     | — | _score_role_type | — | — |
| E     | —                     | — | — | matching algo | — |
| F     | —                     | — | — | — | location render |
```

**Step 3 — Identify conflicts.** Any cell where 2+ agents have entries in the same column is a conflict. In the example above, A and B both touch `company_scraper.py`, but in different methods. Low risk, but needs a merge order.

**Step 4 — Add constraints to agent prompts.** For each agent in a conflict pair, add a `DO NOT TOUCH` clause:

```markdown
## Strict file scope
You MUST NOT modify `scrape_all_companies()` in company_scraper.py.
Agent B owns that method. Your changes are limited to `_scrape_with_fallback()`.
```

**Step 5 — Set merge order.** For conflicting pairs, decide which merges first. Additive changes (new methods, new files) merge before refactors. The later PR rebases on top.

### When to skip it

Single-agent dispatches don't need a matrix.

## Anti-patterns

### Multi-phase agent dispatch ("kitchen-sink prompt")

❌ "Agent, investigate the URL pattern, design the parser, implement it, write tests, open the PR." Five phases in one black box. Operator sees nothing for 10+ minutes. If phase 1 produces a wrong conclusion, the agent commits 200 LOC down a bad path before anyone notices.

✅ Dispatch phase 1 alone. Get the investigation report. Confirm direction. Dispatch phase 2. Confirm diff. Dispatch phase 3 (PR open).

### Polling from inside the agent

❌ Agent prompt says "after pushing, run `gh pr checks --watch` and merge when green." This burns turn budget on polling AND `pull_request:synchronize` events on ready PRs don't fire CI, so fix-agents wait for CI that never starts. (See `PARALLEL_AGENT_PROMPTS.md` § Background for the May 7 marathon evidence.)

✅ Agent exits with `[handoff] PR #N`. Orchestrator runs `gh pr checks --watch` in a background Bash, gets notified, merges on green.

### Dispatching agents for small things that I could just do

❌ One-line typo fix dispatched as a worktree agent. Operator sees an opaque 3-minute run for what should be a 5-second `Edit` call.

✅ For 1-3 file edits with no isolation requirement, just do them in the conversation. Operator sees every step.

### Skipping pre-flight to save a turn

❌ Launching the agent without first showing the operator the prompt and tail command "because they said to dispatch quickly."

✅ The pre-flight is a single short message; it doesn't cost a real turn. The operator has ~30 seconds to spot a scope error before the agent burns 5-10 minutes on the wrong thing.

## Costs and trade-offs

| | Do-it-myself | Single-phase agent | Multi-agent parallel |
|---|---|---|---|
| Operator visibility | full (every tool call) | checkpoint-level (pre-flight + tail + handoff) | checkpoint-level per agent |
| Orchestration overhead | none | 1-2 extra turns per phase | + conflict-matrix planning |
| Wall-clock time | depends on my speed | 3-15 min (foreground or background) | parallelizable across N agents |
| My context usage | high (loads all files I touch) | low (only the result/report) | very low |
| Reversibility mid-run | trivial | per-phase boundary | per-agent boundary |
| Best for | small, single-concern | medium, isolated | independent N-way tasks |

The right choice depends on the task. Don't over-engineer: most days have one or two tasks where agent dispatch is the right call and the rest can run directly.

## Worked examples

### Example 1: small parser fix → do-it-myself

A typo in `linkedin_parser.py:312`. Two-character fix, one file.

**Wrong**: dispatch a worktree agent ("fix typo in linkedin_parser.py line 312, open PR").
**Right**: `Edit` the line in the conversation, branch + commit + push + open PR via Bash, all visible.

### Example 2: medium aggregator-parser implementation → single-phase agent

User wants a new scraper mirroring GuruLink for `confidential.careers`. Multi-file work (`src/jobs/confidential_scraper.py`, wiring in `company_scraper.py`, tests in `tests/unit/test_confidential_scraper.py`), needs to pull URL samples from VPS, needs isolated test runs.

**Pre-flight announcement**:
```
About to dispatch agent to implement aggregator-aware confidential.careers parser.
Model: claude-sonnet-4-6 (implement+test phase per Model Routing table).
Prompt: <full prompt text inline>
Tail in second shell: tail -f /tmp/claude-1000/.../tasks/<id>.output
```

### Example 3: 3 follow-on aggregator parsers → multi-agent parallel

Issues #1005, #1006, #1007 each describe a different aggregator. Once the Confidential pattern from #1004 lands, the operator wants all three fixed.

**Conflict-matrix scan**:
- #1005 (4dayweek) edits `src/jobs/`, `tests/unit/`. New files only.
- #1006 (Communitech) edits `src/jobs/`, `tests/unit/`. New files only.
- #1007 (Built in Toronto) edits `src/jobs/`, `tests/unit/`. New files only.

If all three new scrapers ONLY add new files and don't modify shared routing code, they're parallel-safe. If each needs to add a routing branch in `company_scraper.py::_scrape_with_fallback`, they conflict. Must serialize.

Decision: if parallel-safe, dispatch 3 agents simultaneously with pre-flight + tail commands per the [`PARALLEL_AGENT_PROMPTS.md`](PARALLEL_AGENT_PROMPTS.md) stanzas.

## When this pattern changes
Update this doc when the pattern changes
