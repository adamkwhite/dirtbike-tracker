#!/usr/bin/env bash
#
# Port dirtbike-tracker code into the project-template hexagonal layout.
#
# Preconditions:
#   - Running from ~/Code/dirtbike-tracker (the scaffolded template clone)
#   - ~/Code/dirtbike exists and has src/*.py files to port
#   - setup-repo.sh has already run (src/dirtbike_tracker/ exists)
#   - venv is activated OR pip is available
#
# Usage:
#   cd ~/Code/dirtbike-tracker
#   bash port-to-template.sh
#   # or with --dry-run to preview

set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

SRC_PROJECT="${HOME}/Code/dirtbike"
DEST_PROJECT="$(pwd)"
PKG_DIR="${DEST_PROJECT}/src/dirtbike_tracker"

# Colors
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

say()  { echo -e "${BLUE}==>${NC} $1"; }
done_() { echo -e "${GREEN}  ✓${NC} $1"; }
warn() { echo -e "${YELLOW}  !${NC} $1"; }
die()  { echo -e "${RED}FATAL:${NC} $1" >&2; exit 1; }
run()  {
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] $*"
  else
    eval "$@"
  fi
}

# ============================================================================
# Preflight checks
# ============================================================================

say "Preflight checks"

[[ -d "${SRC_PROJECT}" ]] || die "Source project not found: ${SRC_PROJECT}"
[[ -d "${SRC_PROJECT}/src" ]] || die "Source has no src/ directory"
[[ -d "${PKG_DIR}" ]] || die "Destination package not found: ${PKG_DIR}. Did setup-repo.sh run?"
[[ -f "${DEST_PROJECT}/pyproject.toml" ]] || die "No pyproject.toml here. Run from the template clone root."

# Verify we have the expected source files
required_files=(scraper.py autotrader_scraper.py storage.py scorer.py reporter.py dashboard.py notifier.py extractor.py main.py)
for f in "${required_files[@]}"; do
  [[ -f "${SRC_PROJECT}/src/${f}" ]] || die "Missing source file: ${SRC_PROJECT}/src/${f}"
done

done_ "Source project: ${SRC_PROJECT}"
done_ "Destination: ${DEST_PROJECT}"
done_ "Package dir: ${PKG_DIR}"
[[ $DRY_RUN -eq 1 ]] && warn "DRY RUN MODE. No files will be modified."

# ============================================================================
# Step 1: Remove agent-specific subdirs we won't use
# ============================================================================

say "Step 1: Remove unused template subdirectories (agent/LLM-shaped)"

for sub in \
    "${PKG_DIR}/application/agents" \
    "${PKG_DIR}/application/chains" \
    "${PKG_DIR}/domain/prompts" \
    "${PKG_DIR}/domain/tools" \
    "${PKG_DIR}/infrastructure/api" \
    "${PKG_DIR}/infrastructure/llm" \
    "${PKG_DIR}/infrastructure/monitoring" \
    "${PKG_DIR}/infrastructure/storage" ; do
  if [[ -d "$sub" ]]; then
    run "rm -rf \"$sub\""
    done_ "removed $(basename "$(dirname "$sub")")/$(basename "$sub")"
  fi
done

# ============================================================================
# Step 2: Replace __init__.py and ensure layer markers exist
# ============================================================================

say "Step 2: Fix __init__.py files"

if [[ $DRY_RUN -eq 0 ]]; then
  cat > "${PKG_DIR}/__init__.py" << 'EOF'
"""Dirtbike price tracker: scrape, score, alert."""

__version__ = "0.1.0"
EOF
else
  echo "  [dry-run] write ${PKG_DIR}/__init__.py"
fi
done_ "package __init__.py replaced"

for layer in application domain infrastructure; do
  layer_init="${PKG_DIR}/${layer}/__init__.py"
  if [[ ! -f "$layer_init" ]]; then
    run "touch \"$layer_init\""
    done_ "created ${layer}/__init__.py"
  fi
done

# ============================================================================
# Step 3: Copy source files into their new layer homes
# ============================================================================

say "Step 3: Copy source files into hexagonal layers"

# Mapping: source → destination
declare -A moves=(
  ["scorer.py"]="domain/scoring.py"
  ["scraper.py"]="infrastructure/kijiji_scraper.py"
  ["autotrader_scraper.py"]="infrastructure/autotrader_scraper.py"
  ["storage.py"]="infrastructure/storage.py"
  ["notifier.py"]="infrastructure/notifier.py"
  ["reporter.py"]="infrastructure/reporter.py"
  ["dashboard.py"]="infrastructure/dashboard.py"
  ["extractor.py"]="infrastructure/extraction.py"
  ["main.py"]="application/cli.py"
)

for src_name in "${!moves[@]}"; do
  dest_rel="${moves[$src_name]}"
  src_path="${SRC_PROJECT}/src/${src_name}"
  dest_path="${PKG_DIR}/${dest_rel}"
  run "cp \"$src_path\" \"$dest_path\""
  done_ "${src_name} → ${dest_rel}"
done

# ============================================================================
# Step 4: Fix intra-package imports (flat → hexagonal)
# ============================================================================

say "Step 4: Rewrite imports to hexagonal paths"

if [[ $DRY_RUN -eq 0 ]]; then
  cd "${PKG_DIR}"

  # application/cli.py: imports from all layers
  sed -i \
    -e 's|^from scraper import|from ..infrastructure.kijiji_scraper import|' \
    -e 's|^from scraper |from ..infrastructure.kijiji_scraper |' \
    -e 's|^from autotrader_scraper import|from ..infrastructure.autotrader_scraper import|' \
    -e 's|^from autotrader_scraper |from ..infrastructure.autotrader_scraper |' \
    -e 's|^from storage import|from ..infrastructure.storage import|' \
    -e 's|^from storage |from ..infrastructure.storage |' \
    -e 's|^from scorer import|from ..domain.scoring import|' \
    -e 's|^from scorer |from ..domain.scoring |' \
    -e 's|^from reporter import|from ..infrastructure.reporter import|' \
    -e 's|^from reporter |from ..infrastructure.reporter |' \
    -e 's|^from notifier import|from ..infrastructure.notifier import|' \
    -e 's|^from notifier |from ..infrastructure.notifier |' \
    -e 's|^from dashboard import|from ..infrastructure.dashboard import|' \
    -e 's|^from dashboard |from ..infrastructure.dashboard |' \
    application/cli.py

  # infrastructure/storage.py: imports from kijiji_scraper for Listing dataclass
  sed -i \
    -e 's|^from scraper import|from .kijiji_scraper import|' \
    -e 's|^from scraper |from .kijiji_scraper |' \
    infrastructure/storage.py

  # infrastructure/reporter.py: imports from scoring
  sed -i \
    -e 's|^from scorer import|from ..domain.scoring import|' \
    -e 's|^from scorer |from ..domain.scoring |' \
    -e 's|^from storage import|from .storage import|' \
    -e 's|^from storage |from .storage |' \
    infrastructure/reporter.py

  # infrastructure/dashboard.py: imports from scoring and storage
  sed -i \
    -e 's|^from scorer import|from ..domain.scoring import|' \
    -e 's|^from scorer |from ..domain.scoring |' \
    -e 's|^from storage import|from .storage import|' \
    -e 's|^from storage |from .storage |' \
    infrastructure/dashboard.py

  # domain/scoring.py: currently imports from storage (hexagonal violation, but needed for now)
  sed -i \
    -e 's|^from storage import|from ..infrastructure.storage import|' \
    -e 's|^from storage |from ..infrastructure.storage |' \
    domain/scoring.py

  # infrastructure/autotrader_scraper.py: imports from kijiji_scraper for shared constants
  sed -i \
    -e 's|^from scraper import|from .kijiji_scraper import|' \
    -e 's|^from scraper |from .kijiji_scraper |' \
    infrastructure/autotrader_scraper.py

  cd "${DEST_PROJECT}"
fi

done_ "import paths rewritten"

# ============================================================================
# Step 5: Fix ROOT path in cli.py (deeper nesting = more parent calls)
# ============================================================================

say "Step 5: Fix ROOT path (now 4 levels deep instead of 2)"

if [[ $DRY_RUN -eq 0 ]]; then
  # cli.py: application/cli.py → application → dirtbike_tracker → src → project_root (4 .parent)
  sed -i \
    's|ROOT = Path(__file__).resolve().parent.parent$|ROOT = Path(__file__).resolve().parent.parent.parent.parent|' \
    "${PKG_DIR}/application/cli.py"

  # storage.py uses the same pattern for DB_PATH
  sed -i \
    's|DB_PATH = Path(__file__).resolve().parent.parent / "data"|DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data"|' \
    "${PKG_DIR}/infrastructure/storage.py"
fi

done_ "ROOT paths updated"

# ============================================================================
# Step 6: Copy config, scripts, docs
# ============================================================================

say "Step 6: Copy config/, scripts/, docs/ from old project"

mkdir -p "${DEST_PROJECT}/config" "${DEST_PROJECT}/scripts"

if [[ -d "${SRC_PROJECT}/config" ]]; then
  run "cp -r \"${SRC_PROJECT}/config/\"* \"${DEST_PROJECT}/config/\" 2>/dev/null || true"
  done_ "config/ copied"
fi

if [[ -f "${SRC_PROJECT}/scripts/install_cron.sh" ]]; then
  run "cp \"${SRC_PROJECT}/scripts/install_cron.sh\" \"${DEST_PROJECT}/scripts/\""
  run "chmod +x \"${DEST_PROJECT}/scripts/install_cron.sh\""
  done_ "scripts/install_cron.sh copied"
fi

for doc in architecture.md setup.md fb_marketplace_notes.md; do
  if [[ -f "${SRC_PROJECT}/docs/${doc}" ]]; then
    run "cp \"${SRC_PROJECT}/docs/${doc}\" \"${DEST_PROJECT}/docs/\""
    done_ "docs/${doc} copied"
  fi
done

# ============================================================================
# Step 7: Update pyproject.toml with runtime deps and CLI entry point
# ============================================================================

say "Step 7: Patch pyproject.toml with deps and CLI entry"

if [[ $DRY_RUN -eq 0 ]]; then
  # Add runtime deps if not present
  python3 - << 'PYEOF'
from pathlib import Path
import re

path = Path("pyproject.toml")
text = path.read_text()

# Replace the empty dependencies = [] line with real deps
new_deps = '''dependencies = [
    "requests>=2.31.0",
    "beautifulsoup4>=4.12.0",
    "click>=8.1.0",
    "pyyaml>=6.0",
]'''

if "dependencies = []" in text:
    text = text.replace("dependencies = []", new_deps)
    print("  added runtime dependencies")
elif 'dependencies = [\n    "requests' in text:
    print("  dependencies already present, skipping")
else:
    print("  WARNING: couldn't find dependencies block to update")

# Add [project.scripts] entry if not present
if "[project.scripts]" not in text:
    scripts_block = '''
[project.scripts]
dirtbike = "dirtbike_tracker.application.cli:cli"
'''
    # Insert before [project.urls] or [tool.ruff]
    if "[project.urls]" in text:
        text = text.replace("[project.urls]", scripts_block.strip() + "\n\n[project.urls]")
    else:
        text += scripts_block
    print("  added [project.scripts] entry")
else:
    print("  [project.scripts] already present, skipping")

path.write_text(text)
PYEOF
fi

done_ "pyproject.toml patched"

# ============================================================================
# Step 8: Reinstall package to pick up new entry point
# ============================================================================

say "Step 8: Reinstall package"

if [[ $DRY_RUN -eq 0 ]]; then
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    run "pip install -e \".[dev]\" --quiet"
    done_ "reinstalled in venv"
  else
    warn "No venv active. Skipping pip install -- do it manually:"
    warn "  source venv/bin/activate && pip install -e \".[dev]\""
  fi
fi

# ============================================================================
# Step 9: Verify
# ============================================================================

say "Step 9: Verify"

if [[ $DRY_RUN -eq 0 && -n "${VIRTUAL_ENV:-}" ]]; then
  if command -v dirtbike &> /dev/null; then
    done_ "'dirtbike' command available"
    echo ""
    echo "  Run these to verify:"
    echo "    dirtbike --help"
    echo "    dirtbike models"
    echo "    dirtbike scrape --model crf150f --region ontario"
  else
    warn "'dirtbike' not in PATH. Re-run: pip install -e \".[dev]\""
  fi
fi

echo ""
say "Port complete!"
echo ""
echo "  Next steps:"
echo "    1. dirtbike --help                   # confirm CLI works"
echo "    2. git add -A"
echo "    3. git status                        # review changes"
echo "    4. git commit -m \"Port dirtbike tracker into hexagonal layout\""
echo "       (pre-commit will auto-fix formatting; may need re-commit)"
echo ""
echo "  Rollback: delete this directory and re-clone from template."
echo "  Original source untouched at: ${SRC_PROJECT}"
