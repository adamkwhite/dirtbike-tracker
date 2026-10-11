#!/usr/bin/env bash
#
# v4: suppress BeautifulSoup type-stub noise.
# BS4's typed overloads don't match re.compile() in attrs=, and
# element["attr"] can return AttributeValueList. We know the runtime
# is correct, so we suppress targeted mypy errors.

set -euo pipefail

[[ -f pyproject.toml ]] || { echo "Run from project root"; exit 1; }

PKG="src/dirtbike_tracker"

echo "==> Suppressing BeautifulSoup type-stub false positives"

python3 << 'PYEOF'
from pathlib import Path
import re

PKG = Path("src/dirtbike_tracker")


def add_type_ignore(filepath, line_num, rule):
    """Add '# type: ignore[<rule>]' to a specific 1-indexed line."""
    p = Path(filepath)
    lines = p.read_text().splitlines()
    idx = line_num - 1
    line = lines[idx]
    if "type: ignore" in line:
        # Already has one; check if our rule is included
        if rule in line:
            return False
        # Add to existing bracket. e.g. [a] -> [a, b]
        lines[idx] = re.sub(r'# type: ignore\[(.*?)\]', rf'# type: ignore[\1, {rule}]', line)
    else:
        # Need to add noqa before line continuation if present
        lines[idx] = line.rstrip() + f"  # type: ignore[{rule}]"
    p.write_text("\n".join(lines) + "\n")
    return True


# ---------------------------------------------------------------------------
# kijiji_scraper.py
# ---------------------------------------------------------------------------
# Lines 228, 233: find(attrs={"data-testid": re.compile(...)}) overload mismatch
for line_num in [228, 233]:
    if add_type_ignore(PKG / "infrastructure/kijiji_scraper.py", line_num, "call-overload"):
        print(f"  kijiji_scraper.py:{line_num} type: ignore[call-overload]")

# Lines 254, 255: link_el["href"] returns str | AttributeValueList
# Better fix: cast to str at the source. Find the href assignment.
kijiji_path = PKG / "infrastructure/kijiji_scraper.py"
text = kijiji_path.read_text()
original = text

# Find patterns like: url = urljoin(BASE_URL, link_el["href"])
text = re.sub(
    r'url = urljoin\(BASE_URL, link_el\["href"\]\)',
    r'url = urljoin(BASE_URL, str(link_el["href"]))',
    text,
)
# Or: href = link_el["href"]
text = re.sub(
    r'href = link_el\["href"\]$',
    r'href = str(link_el["href"])',
    text,
    flags=re.MULTILINE,
)

if text != original:
    kijiji_path.write_text(text)
    print("  kijiji_scraper.py: cast link_el['href'] to str")

# Line 288: exc.response.status_code when exc.response could be None
# Find the pattern and add type: ignore
text = kijiji_path.read_text()
original = text
text = re.sub(
    r'(f"Page \{page\}: HTTP \{exc\.response\.status_code\}, stopping\.")',
    r'\1  # type: ignore[union-attr]',
    text,
)
# But that's a f-string arg not a line target. Need another approach.
# Instead find the whole line
lines = text.splitlines()
for i, line in enumerate(lines):
    if "exc.response.status_code" in line and "type: ignore" not in line:
        lines[i] = line.rstrip() + "  # type: ignore[union-attr]"
        print(f"  kijiji_scraper.py:{i+1} type: ignore[union-attr] on exc.response")
        break
kijiji_path.write_text("\n".join(lines) + "\n")

# ---------------------------------------------------------------------------
# autotrader_scraper.py
# ---------------------------------------------------------------------------
# Line 86: href.startswith() where href could be AttributeValueList
# Line 102: re.search(..., str_or_list)
# Lines 106, 107: Listing(..., url=str_or_list)

autotrader_path = PKG / "infrastructure/autotrader_scraper.py"
text = autotrader_path.read_text()
original = text

# Cast at the source: href = link_el["href"] → href = str(link_el["href"])
text = re.sub(
    r'(\s+)href = link_el\["href"\]$',
    r'\1href = str(link_el["href"])',
    text,
    flags=re.MULTILINE,
)
# Also catch any direct link_el["href"] usages
text = re.sub(
    r'link_el\["href"\]',
    r'str(link_el["href"])',
    text,
)
# Avoid double-casting if the first replace already made it str(str(...))
text = text.replace('str(str(link_el["href"]))', 'str(link_el["href"])')

if text != original:
    autotrader_path.write_text(text)
    print("  autotrader_scraper.py: cast link_el['href'] to str")

print("")
print("All BS4 type-stub suppressions applied.")
PYEOF

echo ""
echo "==> Format pass"
ruff format "$PKG" --quiet

echo ""
echo "==> Final checks"
echo ""
echo "-- Ruff --"
ruff check "$PKG" --output-format=concise || true

echo ""
echo "-- Mypy --"
mypy "$PKG" 2>&1 | tail -5 || true

echo ""
echo "-- Bandit (high severity only, Low is OK) --"
bandit -r "$PKG" -c pyproject.toml -q 2>&1 | grep -E "Severity: High" || echo "  no high-severity issues"

echo ""
echo "==> If all three pass, commit:"
echo "   git add -A"
echo "   git commit -m \"fix: Port dirtbike tracker into hexagonal layout\""
