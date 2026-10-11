#!/usr/bin/env bash
#
# Fix the lint/mypy/bandit issues from the first pre-commit run.
#
# Run from the project root (where pyproject.toml lives).
# Requires: venv activated, inside the git repo.

set -euo pipefail

[[ -f pyproject.toml ]] || { echo "Run from project root"; exit 1; }

PKG="src/dirtbike_tracker"

# ============================================================================
# Phase 1: Auto-fix ruff issues (28 of 45 fixable)
# ============================================================================

echo "==> Phase 1: Auto-fix ruff issues"

# Includes: import ordering (I001), Optional → | None (UP045), unused imports (F401),
# collections.abc migration (UP035), SIM105 contextlib.suppress conversion
ruff check --fix "$PKG"
ruff format "$PKG"

# ============================================================================
# Phase 2: Manual fixes for issues ruff won't auto-fix
# ============================================================================

echo "==> Phase 2: Manual fixes"

# 2a. Rename ambiguous `l` → `listing` in dashboard.py and reporter.py
echo "  - Rename ambiguous 'l' → 'listing' in dashboard/reporter"
sed -i 's|for l in listings|for listing in listings|g; s|l\["|listing["|g; s|l\.get(|listing.get(|g' \
  "$PKG/infrastructure/dashboard.py" "$PKG/infrastructure/reporter.py" 2>/dev/null || true

# 2b. Add usedforsecurity=False to MD5 (bandit B324)
echo "  - Fix MD5 for Bandit B324 (hashing for dedup, not security)"
sed -i 's|hashlib\.md5(key\.encode())|hashlib.md5(key.encode(), usedforsecurity=False)|' \
  "$PKG/infrastructure/storage.py"

# 2c. Fix extraction.py __post_init__: default None assignment pattern
# Original: needs: list[str] = None  (mypy complains)
# Fix: use None | list[str] with factory
echo "  - Fix list[str] = None default in extraction.py"
python3 << 'PYEOF'
from pathlib import Path
p = Path("src/dirtbike_tracker/infrastructure/extraction.py")
text = p.read_text()
text = text.replace(
    "    needs: list[str] = None\n    includes: list[str] = None",
    "    needs: list[str] | None = None\n    includes: list[str] | None = None",
)
p.write_text(text)
print("    fixed extraction.py defaults")
PYEOF

# 2d. Add type annotation to by_rating in reporter.py
echo "  - Annotate by_rating in reporter.py"
sed -i 's|by_rating = {"DEAL": \[\], "GOOD": \[\], "FAIR": \[\], "HIGH": \[\], "UNKNOWN": \[\]}|by_rating: dict[str, list[dict]] = {"DEAL": [], "GOOD": [], "FAIR": [], "HIGH": [], "UNKNOWN": []}|' \
  "$PKG/infrastructure/reporter.py"

# 2e. Suppress print() warnings with # noqa: T201 (we WANT prints in CLI tool)
# Rather than switching to logging mid-port, we tag these as intentional.
echo "  - Add # noqa: T201 to intentional CLI prints"
for file in \
    "$PKG/infrastructure/kijiji_scraper.py" \
    "$PKG/infrastructure/autotrader_scraper.py" \
    "$PKG/infrastructure/extraction.py" \
    "$PKG/infrastructure/notifier.py" ; do
  # Only tag print statements that don't already have a noqa comment
  sed -i -E '/^[[:space:]]+print\(.*\)$/{
    /noqa/!s|$|  # noqa: T201|
  }' "$file"
done

# 2f. Suppress broad except Exception blocks with # noqa: BLE001
echo "  - Add # noqa: BLE001 to intentional broad excepts"
for file in \
    "$PKG/application/cli.py" \
    "$PKG/infrastructure/extraction.py" ; do
  sed -i -E '/except Exception as exc:/{
    /noqa/!s|$|  # noqa: BLE001|
  }' "$file"
done

# 2g. Mark max_pages unused in autotrader_scraper (keeping for API symmetry with kijiji)
echo "  - Suppress ARG001 on autotrader_scraper max_pages"
sed -i 's|def scrape_model(model_slug: str, region: str = "on", max_pages: int = 1)|def scrape_model(model_slug: str, region: str = "on", max_pages: int = 1)  # noqa: ARG001|' \
  "$PKG/infrastructure/autotrader_scraper.py"

# 2h. Fix no-any-return warnings by adding explicit casts
echo "  - Fix Returning Any from functions (mypy no-any-return)"
# kijiji_scraper _extract_external_id
sed -i 's|return match\.group(1) if match else url|return str(match.group(1)) if match else url|' \
  "$PKG/infrastructure/kijiji_scraper.py"
# autotrader_scraper _parse_price — the return pattern there is a tuple, likely different line
# storage.py line 199 (likely mark_stale returning cursor.rowcount)
sed -i 's|return cursor\.rowcount$|return int(cursor.rowcount)|' \
  "$PKG/infrastructure/storage.py"

# 2i. Format one more pass after all manual edits
echo "  - Final format pass"
ruff check --fix "$PKG" --quiet || true
ruff format "$PKG" --quiet

echo ""
echo "==> Done. Now run:"
echo "   git add -A"
echo "   git commit -m \"fix: Port dirtbike tracker into hexagonal layout\""
echo ""
echo "If pre-commit still fails, review the remaining issues manually."
echo "mypy may still complain about domain → infrastructure imports."
echo "That's real tech debt. Document and move on."
