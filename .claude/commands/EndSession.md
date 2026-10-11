---
description: End-of-session wrap-up tasks including cleanup, documentation updates, and memory storage
---

End-of-session wrap-up for the current project session. A "session" is a logical breakpoint when work is completed — it may happen any time of day, and there may be several in a single day. Do not assume this is the end of the day.

## Task

### 0. Anchor to current date/time (first action)
- Run `date "+%Y-%m-%d %H:%M:%S %A %Z"` via Bash before anything else.
- Use the result as the authoritative "now" for the rest of this flow — especially the memory-save step 6, where the summary title (`Session Wrap-Up — <project-name> — <weekday> <YYYY-MM-DD HH:MM>`) and the `date` field (full timestamp, including time) must reflect the real current date AND time, not one inferred from conversation context. Including the time is what lets multiple wrap-ups in one day stay distinct and correctly ordered.

### 1. Check Current Work Status
- Run `git status` to check for uncommitted changes
- Run `git log --oneline -5` to review this session's commits
- Check if any open PRs need attention: `gh pr list --author @me`

### 2. Handle Open PRs
- For each open PR, check CI status: `gh pr checks <pr-number>`
- Report any failing checks that need attention in the next session
- Do NOT merge PRs without explicit user approval

### 3. Branch Cleanup
- List merged branches: `git branch --merged main`
- Ask user before deleting any merged feature branches
- Do NOT delete branches without confirmation

### 4. Update Project Documentation
- Update `todo.md`: remove any completed items, add new open items. Do NOT add "Recently Completed" session logs. Session history lives in git/PRs and `docs/history/todo-archive-2026.md`.
- Update project `CLAUDE.md` if any architectural decisions or learnings should be captured
- Do NOT create new documentation files

### 5. Session Summary (output to screen)
Print a human-readable end-of-session summary including:
- What was accomplished this session (PRs shipped, issues closed, features landed)
- Current state of open PRs and their CI status
- State at session end (working tree, deploys, open issues)
- Any blockers or issues to pick up in the next session
- Memories saved this session (new entries in local `memory/` files, if any)
- Recommended next steps for the next `/StartSession`

### 6. Save Summary to Claude Memory MCP
After printing the summary, save the **exact same text verbatim** to claude memory using `mcp__claude-memory__add_conversation`:
- `title`: `Session Wrap-Up — <project-name> — <weekday> <YYYY-MM-DD HH:MM>` (e.g. `Session Wrap-Up — job-agent — Sunday 2026-06-14 14:56`) — the project name disambiguates same-day wrap-ups across projects; the time keeps multiple wrap-ups in one project distinct. Use the same `<project-name>` value here as in the tags below.
- `content`: the full summary text as printed to screen, unchanged
- `date`: the current date **and time to the minute** (run `date "+%Y-%m-%dT%H:%M"`, e.g. `2026-06-14T14:56`) — never just the date, which would collapse every same-day wrap-up to midnight and break ordering.
- `tags`: `["session-wrapup", "session-summary", "<project-name>"]`
- `conversation_type`: `session-wrapup`

This lets the next `/StartSession` retrieve the latest wrap-up via `mcp__claude-memory__search_by_tag` (querying `session-wrapup`, with `daily-wrapup` as a legacy fallback) and select it by the latest timestamp.
