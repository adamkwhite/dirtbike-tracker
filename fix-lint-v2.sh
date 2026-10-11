#!/usr/bin/env bash
#
# Finish fixing the remaining 17 ruff errors after v1.
# Uses Python for precise edits (sed is fragile on multi-line patterns).

set -euo pipefail

[[ -f pyproject.toml ]] || { echo "Run from project root"; exit 1; }

echo "==> Applying precise fixes"

python3 << 'PYEOF'
from pathlib import Path
import re

PKG = Path("src/dirtbike_tracker")


def add_noqa_to_line(filepath, line_num, rule):
    """Add '# noqa: <rule>' to a specific 1-indexed line."""
    p = Path(filepath)
    lines = p.read_text().splitlines()
    idx = line_num - 1
    line = lines[idx]
    if "noqa" in line:
        return False  # already has noqa
    # Append noqa comment
    lines[idx] = line.rstrip() + f"  # noqa: {rule}"
    p.write_text("\n".join(lines) + "\n")
    return True


def rename_ambiguous_l(filepath):
    """Rename ambiguous `l` loop variable to `listing` in a file."""
    p = Path(filepath)
    text = p.read_text()
    # Only match `for l in` patterns and the body references
    new_text = re.sub(r'\bfor l in\b', 'for listing in', text)
    # Replace l["..."] and l.get(...) references
    new_text = re.sub(r'\bl\[', 'listing[', new_text)
    new_text = re.sub(r'\bl\.get\(', 'listing.get(', new_text)
    if new_text != text:
        p.write_text(new_text)
        return True
    return False


# T201 print statements (intentional CLI output)
t201_targets = [
    ("infrastructure/autotrader_scraper.py", 131),
    ("infrastructure/extraction.py", 92),
    ("infrastructure/extraction.py", 126),
    ("infrastructure/kijiji_scraper.py", 252),
    ("infrastructure/kijiji_scraper.py", 255),
    ("infrastructure/kijiji_scraper.py", 262),
    ("infrastructure/kijiji_scraper.py", 272),
    ("infrastructure/kijiji_scraper.py", 274),
    ("infrastructure/notifier.py", 59),
]
for rel, line in t201_targets:
    if add_noqa_to_line(PKG / rel, line, "T201"):
        print(f"  T201 noqa added: {rel}:{line}")

# BLE001 broad except (intentional graceful degradation)
ble_targets = [
    ("application/cli.py", 243),
    ("infrastructure/extraction.py", 125),
]
for rel, line in ble_targets:
    if add_noqa_to_line(PKG / rel, line, "BLE001"):
        print(f"  BLE001 noqa added: {rel}:{line}")

# ARG001 unused arg (API symmetry with kijiji_scraper)
if add_noqa_to_line(PKG / "infrastructure/autotrader_scraper.py", 125, "ARG001"):
    print("  ARG001 noqa added: infrastructure/autotrader_scraper.py:125")

# E741 ambiguous `l` → `listing`
e741_files = [
    "infrastructure/dashboard.py",
    "infrastructure/reporter.py",
]
for rel in e741_files:
    if rename_ambiguous_l(PKG / rel):
        print(f"  E741 renamed l→listing: {rel}")

# SIM105: try/except/pass → contextlib.suppress
# application/cli.py around line 229
cli_path = PKG / "application/cli.py"
cli_text = cli_path.read_text()
old_sim = """                if notify:
                    try:
                        notify_good(listing_for_notify)
                    except NotifyConfigError:
                        pass"""
new_sim = """                if notify:
                    with contextlib.suppress(NotifyConfigError):
                        notify_good(listing_for_notify)"""
if old_sim in cli_text:
    cli_text = cli_text.replace(old_sim, new_sim)
    # Add contextlib import if missing
    if "import contextlib" not in cli_text:
        cli_text = re.sub(
            r'^from pathlib import Path',
            'import contextlib\nfrom pathlib import Path',
            cli_text,
            count=1,
            flags=re.MULTILINE,
        )
    cli_path.write_text(cli_text)
    print("  SIM105 converted to contextlib.suppress in cli.py")

# TRY300: move return True to else block in notifier.py
notifier_path = PKG / "infrastructure/notifier.py"
notifier_text = notifier_path.read_text()
# Find the try/except/return pattern
old_try = """    try:
        response = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=timeout)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"ntfy notify failed: {exc}")  # noqa: T201
        return False"""
new_try = """    try:
        response = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"ntfy notify failed: {exc}")  # noqa: T201
        return False
    else:
        return True"""
if old_try in notifier_text:
    notifier_text = notifier_text.replace(old_try, new_try)
    notifier_path.write_text(notifier_text)
    print("  TRY300 moved return to else block in notifier.py")

print("")
print("Done. Verifying with ruff...")
PYEOF

# Final verification
echo ""
echo "==> Ruff check after fixes:"
ruff check src/dirtbike_tracker/ --output-format=concise || true

echo ""
echo "==> Format pass:"
ruff format src/dirtbike_tracker/ --quiet

echo ""
echo "==> Done. Try the commit:"
echo "   git add -A"
echo "   git commit -m \"fix: Port dirtbike tracker into hexagonal layout\""
