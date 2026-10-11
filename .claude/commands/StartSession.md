---
description: Start-of-session project context review including git status, recent PRs, and documentation
---

Get project context

## Task
- **Check current date/time as the first action** — run `date "+%Y-%m-%d %H:%M:%S %A %Z"` via Bash so the rest of this flow has an authoritative anchor for "now". Use this to (a) compute the actual elapsed time since the most recent wrap-up, (b) resolve relative dates in `todo.md` / memory / PR comments, (c) stamp any new memories saved in this session with the correct absolute timestamp.
- **Fetch most recent wrap-up summary from Claude Memory MCP** using `mcp__claude-memory__search_by_tag` — query both tags `session-wrapup` and (for older entries) `daily-wrapup`, then pick the entry with the latest timestamp. Read it to rehydrate the previous session's context before doing anything else. **Do not assume the previous session was "yesterday."** A wrap-up is a logical breakpoint, not a calendar day — read the entry's actual timestamp and report the real gap (e.g. "last wrap-up was ~1 hour ago" / "~3 days ago"). If the gap is small (same day), trust the working files (`todo.md`, recent commits) for current specifics and treat memory as the "why" layer. If no results for this project, fall back to the general tag feed.
- Check git status and update local if needed
- Read the most recent PR(s) from the previous session
- Read FILE /home/adam/Code/CLAUDE.md
- Read FILE /<project>/CLAUDE.MD
- Read FILE /<project>/readme.md
- Read FILE /<project>/todo.md

Do not create these files if they don't exist

Things to consider
- Project Overview: A brief description of what the project is and its goals
- Current Status: Where you are in the development process
- Current Branch: Which branch we were last working on
- Technology Stack: Languages, frameworks, and libraries being used, including version information
- Implementation Details: Current   architecture and design decisions
- Recent Changes: What you've been working on recently
- Next Steps: What you plan to implement or change next
- Dependencies: External services, APIs, or libraries that the project relies on
- Known Issues: Current bugs or limitations you're aware of

List out the items in the to do by priority rank, starting with high priority.

## Conditional Skill Surfacing

After the context summary, scan the environment and surface relevant skills based on conditions. The agent (or human) should run these proactively when triggered. See `~/Code/CLAUDE.md` § Skill Invocation Rules for the full trigger table.

Run these checks in parallel via Bash and report any that match:

1. **Open Dependabot PRs across workspace** — count via `gh pr list --author "dependabot[bot]" --state open --json number --jq 'length'` for repos under `~/Code/` (or just for current repo). If total >5, surface: *"You have N open Dependabot PRs — run `/dependabot-triage` to sweep them."*

2. **Stale local branches in current repo** — count via `git for-each-ref --format='%(refname:short)' refs/heads/ | grep -v '^main$' | wc -l`. If >5, surface: *"N stale local branches detected — run `~/Code/scripts/cleanup-merged-branches.sh --dry-run .` to preview cleanup."*

3. **Production deploy health** (only if project deploys to prod, e.g. has `netlify.toml`/`vercel.json`/deploy workflow) — note: *"If today's work touches production, run `/preflight` BEFORE deep-debugging any user-facing bug."*

4. **Open PRs needing attention** — `gh pr list --state open --author '@me'`. If any have failing CI or are stuck >3 days, surface them with status.

5. **Last `/maintenance` run** — check Claude Memory MCP for the most recent `maintenance-sweep` tag. If >7 days ago, suggest `/maintenance`.

6. **Global CLAUDE.md drift** — run `diff ~/Code/CLAUDE.md ~/Code/devops/CLAUDE.md`. If files differ, surface: *"Global CLAUDE.md has diverged from the devops tracked copy. Run `diff ~/Code/CLAUDE.md ~/Code/devops/CLAUDE.md` to review. Sync the devops copy via PR before the drift grows."* This catches unauthorized self-edits to instruction files.

Output the surfaced suggestions as a short bulleted list under the heading **"Today's surfaced skills"** so the agent knows what to invoke without being asked. Skip silently if no conditions match.
