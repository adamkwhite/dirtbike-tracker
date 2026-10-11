# Parallel-agent prompt stanzas

> See [`AGENT_DISPATCH_PATTERN.md`](AGENT_DISPATCH_PATTERN.md) for *when* to dispatch agents, conflict matrices, and pre-flight announcements. This doc covers the stanzas only.

**These are text you PASTE, not a doc you link.** A subagent does not read files you point it at — only the text inlined in its prompt reaches it. "Follow PARALLEL_AGENT_PROMPTS.md" does nothing. Paste the stanza bodies verbatim.

**Pre-dispatch requirement:** exit plan mode (`ExitPlanMode`) before dispatching. Worktree agents inherit plan mode from the parent session and will refuse to write code regardless of prompt instructions.

**Which to paste:** stanzas 1–4 always, after the task scope and before the final-response-format spec. Add stanza 5 when 5+ agents run concurrently. Add stanza 6 when the task might produce a GitHub issue (research and audit tasks especially). Do not paraphrase.

**Adapting this file:** the placeholders `<REPO_ROOT>`, `<VENV>`, and `<SLOW_QUALITY_CMD>` must be replaced with real values for your repo before pasting. Agents take these literally.

---

## Stanza 1 — ship-and-handoff protocol

````markdown
## Critical: ship-and-handoff protocol

Your scope ends when the PR is OPEN, READY (not draft), and pre-push tests
passed locally. After that, you EXIT. The operator handles polling, fix
iterations on remote-CI failures, and merge.

Every step below is a FAST, FOREGROUND operation. There is deliberately NO
slow step in this sequence — no full test suite, no coverage gate. Coverage
and complexity gates are enforced by CI on your PR and handled by the
operator, NOT by you. Do not run <SLOW_QUALITY_CMD>, the full test suite, or
the whole E2E suite — those are the slow, lock-contended commands that tempt
the fatal "I'll wait for the monitor" yield, and they are not your job.

Use this exact sequence as your final actions:

1. Implement the change in your worktree
2. Run ONLY your touched test files (never the whole suite), IN THE
   FOREGROUND — never `run_in_background`/Monitor for verification. It
   finishes in seconds; you must see the result in the same turn; iterate
   until it passes. Backgrounding any run is the exact mistake this stanza
   exists to prevent.
3. Run the formatter + linter on touched files
4. `git add` (explicit paths, never `-A` / `.`) then `git commit` (from inside
   this worktree). Commit the moment your targeted tests are green — a commit
   is cheap and amendable, uncommitted work is lost when the worktree is
   cleaned.
5. `git push -u origin <branch>`
6. `gh pr create --base main --head <branch>` (NOT `--draft` — open as ready)
7. Report `[handoff] PR #<num> opened ready` + 1-line summary of what you
   shipped + any local-test results worth flagging
8. EXIT

Steps 5-6 (push, PR create) are seconds each. If EITHER hangs, wait for it
inline or retry it inline — do NOT end your turn saying you are "waiting for a
monitor" or "waiting for a background run". That is not a terminal status, and
it has repeatedly left committed work unpushed with no PR. Never background any
command in this sequence.

DO NOT poll `gh pr checks`. DO NOT call `gh pr ready`. DO NOT call `gh pr merge`.

End your final response with EXACTLY ONE OF:
- `[handoff] PR #<num> opened ready` + 1-line summary
- `[blocked] <which step failed> — see notes above`

No other final status is valid.
````

## Stanza 2 — worktree isolation rules

````markdown
## Worktree isolation rules

You are running in an isolated worktree at $PWD. NEVER cd outside it.

- DO NOT use `cd <REPO_ROOT>` (that's the parent worktree and holds another
  agent's branch — `cd`-ing there steers git operations to the wrong working
  tree)
- DO NOT use absolute paths in git commands (`git -C <REPO_ROOT> ...`)
- Use relative paths or `git -C $PWD ...` if you need to be explicit
- For the project virtualenv/toolchain, use its absolute path
  (`<VENV>/bin/<tool>`) WITHOUT cd-ing there

If you find yourself wanting to operate against another worktree, STOP
and report. Don't try.

- If the Edit tool refuses with a worktree isolation error, STOP immediately
  and report `[blocked] edit refused — wrong worktree`. DO NOT fall back to
  Bash, sed, awk, or any shell command to make the same edit. The isolation
  block means you are targeting the wrong file — escalating to Bash bypasses
  the guard and will hang waiting for operator permission.
````

## Stanza 3 — conflict resolution rules

````markdown
## Conflict resolution rules

If your worktree has merge conflicts after rebase or merge:

1. DO NOT write regex scripts to resolve conflicts programmatically
2. DO NOT strip conflict markers with sed/awk/python one-liners
3. Git's diff3/zdiff3 format uses FOUR marker types, not three:
   - `<<<<<<<` (ours)
   - `|||||||` (ancestor — MISSED by naive scripts)
   - `=======` (separator)
   - `>>>>>>>` (theirs)
4. If conflicts are trivial (1-2 files, obvious resolution): resolve
   manually, then verify no markers survive and the files still parse:
   ```
   grep -rnP '^(<{7}|={7}|>{7}|\|{7})' src/ tests/
   ```
5. If conflicts are non-trivial: EXIT with `[blocked] merge conflicts
   on <files> — needs manual resolution` and stop.
````

## Stanza 4 — output path rules

````markdown
## Output path rules

When your code writes output files (dry-run HTML, logs, exports, temp files):

- Write to a project-local directory, NEVER `/tmp` or other system-global
  paths
- `/tmp` triggers security-scanner hotspots and puts data where operators
  cannot find it
- For truly temporary files use the language's tempfile facility with
  auto-delete — but prefer a project-local dir for anything a human might
  want to inspect
````

## Stanza 5 — resource contention (5+ concurrent agents)

````markdown
## Resource management (shared box)

You share CPU/RAM with other running agents. Running whole-repo work
concurrently OOMs the machine. Measured on one project: 8 agents each running
the full suite plus a browser drove load to 14.6 with 3 of 4 GB swap consumed.
Keep your footprint small:

- During development run ONLY the targeted test files for your change, never
  the full suite, and do NOT run the coverage/quality gate at all — that is a
  CI + operator responsibility (see the ship-and-handoff stanza).
- Never pass a parallelism flag (e.g. pytest `-n auto`) — per-agent
  parallelism multiplies load across every agent.
- Never generate an in-memory HTML report (e.g. pytest `--html=`). If the
  project caps address space in its test config, this blows the cap and
  produces a MemoryError that reads like a test failure but is not one.
- Serialize any browser/E2E run yourself — a headless browser is the single
  biggest memory spike and must never overlap another agent's.
````

## Stanza 6 — filing issues

````markdown
## If you file a GitHub issue

- Get the authoritative label set with `gh label list`. **Do not invent
  labels** — most repos already have label sprawl.
- Apply the repo's required labels (commonly one category + one priority).
  Check an existing recent issue if the convention is not documented.
- Do NOT apply provenance labels by hand (e.g. `ci-failure`,
  `pr-review-feedback`) — auto-filers own those.
- Use `--body-file`, not `--body "..."`. Backticks and `$` inside a
  double-quoted shell string get interpreted, and the call fails silently
  or posts mangled text.

```
gh issue create --title "..." --body-file <file> --label "..." --label "..."
```

If the repo has a labelling workflow that comments when labels are missing,
treat it as a backstop, not the process — get it right at creation.
````
