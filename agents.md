# Agent Guidelines

Instructions for AI coding agents (Claude Code, Copilot, Cursor, etc.) working on this codebase.

## Critical: Function Signature Changes

When modifying function signatures (add/remove/change parameters):

### Mandatory 4-Step Workflow

1. **Find ALL callers** - Use grep to search entire codebase:
   ```bash
   # Python
   grep -r "function_name" src/ tests/ --include="*.py"

   # TypeScript/JavaScript
   grep -r "functionName" src/ tests/ --include="*.ts" --include="*.js"
   ```

2. **Update every caller** - Including test mocks, don't assume you found them all

3. **Run affected tests** - Before committing:
   ```bash
   # Python
   pytest tests/unit/test_<module>.py -v

   # TypeScript/JavaScript
   npm test -- test/<module>.test.ts
   ```

4. **Commit only if tests pass** - Exit code must be 0

### Why This Matters

**Without this workflow:**
- ❌ Test failures in CI (discovered late)
- ❌ Manual cleanup required
- ❌ PR iterations

**With this workflow:**
- ✅ Issues caught before CI
- ✅ Clean PRs on first push
- ✅ Higher confidence in automated changes

### Common Mistake

Modifying a function signature but forgetting to update test mocks:

```python
# ❌ BAD - Test still calls with old signature
def process_data(data, options):  # Removed: cache, score
    ...

# Test file still has:
process_data(data=d, cache=c, score=75, options=o)  # TypeError!
```

**Solution:** Always grep first, update all callers, then test.

## Detailed Reference

For complete workflows, examples, and language-specific patterns:
- See `docs/development/AGENT_BEST_PRACTICES.md`

## Project Context

For project-specific guidance and architecture:
- See `CLAUDE.md`
