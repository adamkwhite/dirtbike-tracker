---
description: Store a note, idea, or conversation snippet to Claude Memory
---

Save content to Claude Memory using `mcp__claude-memory__add_conversation`.

## Pre-flight

Before saving, test the MCP connection by calling `mcp__claude-memory__search_by_tag` with tag `"health-check"`. If the call returns an error containing "malformed", "corrupt", or "disk image", stop and report:

> Claude Memory MCP database is corrupted. Cannot save. Run `sqlite3 <db_path> "PRAGMA integrity_check"` to diagnose, or check the memory file at `~/.claude/projects/-home-adam-Code-job-agent/memory/project_claude_memory_mcp_corrupted.md` for context.

Then fall back to saving the content to the **file-based memory system** at the project memory directory instead (follow the auto-memory instructions in the system prompt).

## Behavior

### With arguments (`/remember <content>`):
1. Save the provided content as-is using `mcp__claude-memory__add_conversation`
2. Auto-generate a short title (5-10 words) that captures the topic
3. Pass the current date **and time** (to the minute) as the `date` field — run `date "+%Y-%m-%dT%H:%M"` first and use that (e.g. `2026-06-14T14:32`). Never pass a date-only value: the store treats it as midnight, which collapses every same-day entry to `T00:00:00` and breaks ordering.
4. Confirm what was saved

### Without arguments (`/remember`):
1. Review the current session for notable learnings, decisions, workflows, insights
2. Save each distinct topic as a **separate** memory entry (one call per topic)
3. Skip if nothing notable to save
4. Summarize what was saved

## Guidelines
- Keep each entry focused on a single topic for better searchability
- Include context for future recall: project names, people, decisions, dates, ticket/PR numbers
- Don't save things that are already in project CLAUDE.md or local memory files
- Don't save ephemeral task details — focus on durable knowledge
