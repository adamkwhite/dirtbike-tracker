#!/usr/bin/env bash
#
# v3: finish fixing after v1 + v2 cleared most issues.
# Uses Python for precise multi-line and context-aware edits.

set -euo pipefail

[[ -f pyproject.toml ]] || { echo "Run from project root"; exit 1; }

echo "==> Applying final fixes"

python3 << 'PYEOF'
from pathlib import Path
import re

PKG = Path("src/dirtbike_tracker")

# ---------------------------------------------------------------------------
# 1. reporter.py: the lingering `l` on .append() + by_rating annotation
# ---------------------------------------------------------------------------
reporter = PKG / "infrastructure/reporter.py"
text = reporter.read_text()
original = text

# Replace standalone `l` in .append(l) - the one my regex missed
text = re.sub(r'\.append\(l\)', '.append(listing)', text)

# Also catch any other bare `l` references we missed
# Look for lines like "for X in Y:" where the loop var is something else and `l` is leftover
# Safer: just rename this specific case
text = text.replace(
    'by_rating.setdefault(listing["rating"], []).append(l)',
    'by_rating.setdefault(listing["rating"], []).append(listing)'
)

# Add type annotation to by_rating (ruff may have reformatted the line)
text = re.sub(
    r'^(\s*)by_rating = \{"DEAL": \[\], "GOOD": \[\], "FAIR": \[\], "HIGH": \[\], "UNKNOWN": \[\]\}',
    r'\1by_rating: dict[str, list[dict]] = {"DEAL": [], "GOOD": [], "FAIR": [], "HIGH": [], "UNKNOWN": []}',
    text,
    flags=re.MULTILINE,
)

if text != original:
    reporter.write_text(text)
    print("  reporter.py: fixed .append(l) and by_rating annotation")

# ---------------------------------------------------------------------------
# 2. extraction.py: list[str] = None → list[str] | None = None
# ---------------------------------------------------------------------------
extraction = PKG / "infrastructure/extraction.py"
text = extraction.read_text()
original = text

text = re.sub(
    r'(\s+)(needs|includes): list\[str\] = None',
    r'\1\2: list[str] | None = None',
    text,
)

if text != original:
    extraction.write_text(text)
    print("  extraction.py: list[str] = None → list[str] | None = None")

# ---------------------------------------------------------------------------
# 3. storage.py: MD5 usedforsecurity=False + mark_stale int cast
# ---------------------------------------------------------------------------
storage = PKG / "infrastructure/storage.py"
text = storage.read_text()
original = text

# MD5 bandit fix
text = text.replace(
    "hashlib.md5(key.encode()).hexdigest()[:16]",
    "hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()[:16]"
)

# mark_stale returning cursor.rowcount as Any
text = re.sub(
    r'return cursor\.rowcount(\s*$)',
    r'return int(cursor.rowcount)\1',
    text,
    flags=re.MULTILINE,
)

if text != original:
    storage.write_text(text)
    print("  storage.py: MD5 usedforsecurity + rowcount cast")

# ---------------------------------------------------------------------------
# 4. kijiji_scraper.py: _extract_external_id returning Any
# ---------------------------------------------------------------------------
kijiji = PKG / "infrastructure/kijiji_scraper.py"
text = kijiji.read_text()
original = text

# The function returns match.group(1) which is typed as Any because
# re match groups aren't narrowed. Cast to str.
text = re.sub(
    r'return match\.group\(1\) if match else url',
    r'return str(match.group(1)) if match else url',
    text,
)
# Fallback in case ruff reformatted it
text = re.sub(
    r'return match\.group\(1\)',
    r'return str(match.group(1))',
    text,
)

if text != original:
    kijiji.write_text(text)
    print("  kijiji_scraper.py: str() cast on match.group()")

# ---------------------------------------------------------------------------
# 5. autotrader_scraper.py: same pattern
# ---------------------------------------------------------------------------
autotrader = PKG / "infrastructure/autotrader_scraper.py"
text = autotrader.read_text()
original = text

# Find the return match.group() pattern
text = re.sub(
    r'return match\.group\(\)',
    r'return str(match.group())',
    text,
)
text = re.sub(
    r'return match\.group\(1\)',
    r'return str(match.group(1))',
    text,
)

if text != original:
    autotrader.write_text(text)
    print("  autotrader_scraper.py: str() cast on match.group()")

print("")
print("All targeted fixes applied.")
PYEOF

echo ""
echo "==> Format pass"
ruff format src/dirtbike_tracker/ --quiet

echo ""
echo "==> Ruff check"
ruff check src/dirtbike_tracker/ --output-format=concise || true

echo ""
echo "==> Mypy check"
mypy src/dirtbike_tracker/ 2>&1 | tail -20 || true

echo ""
echo "==> Bandit check (high severity)"
bandit -r src/dirtbike_tracker/ -c pyproject.toml -q 2>&1 | grep -E "Issue|Severity|High" | head -10 || true

echo ""
echo "==> If all three show 0 errors, run:"
echo "   git add -A"
echo "   git commit -m \"fix: Port dirtbike tracker into hexagonal layout\""
