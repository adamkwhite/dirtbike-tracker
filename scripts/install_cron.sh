#!/usr/bin/env bash
#
# Install the dirtbike-tracker watch job on cron.
# Runs every 20 minutes during Ontario waking hours (6am - 11pm ET).
#
# Usage:
#   ./scripts/install_cron.sh          # install
#   ./scripts/install_cron.sh --remove # remove
#
# Requires NTFY_TOPIC env var set in your shell rc file so cron inherits
# nothing. We bake the env into the cron entry explicitly.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
MAIN="${PROJECT_ROOT}/src/main.py"
LOG_DIR="${PROJECT_ROOT}/logs"
MARKER="# dirtbike-tracker"

mkdir -p "${LOG_DIR}"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Error: ${PYTHON} not found. Run from the project root after setting up .venv."
  exit 1
fi

if [[ "${1:-}" == "--remove" ]]; then
  crontab -l 2>/dev/null | grep -v "${MARKER}" | crontab -
  echo "Removed dirtbike-tracker cron entries."
  exit 0
fi

if [[ -z "${NTFY_TOPIC:-}" ]]; then
  echo "Warning: NTFY_TOPIC env var not set. Notifications will be disabled."
  echo "Set it with: export NTFY_TOPIC=<your-topic>"
  echo "Continuing anyway..."
fi

# Build the cron entry. Runs every 20 min from 6am - 11pm ET.
# ET = UTC-5 (standard) or UTC-4 (daylight). We use local time for the
# user's crontab, which Ubuntu/WSL typically has set.
CRON_LINE="*/20 6-23 * * * NTFY_TOPIC=${NTFY_TOPIC:-} cd ${PROJECT_ROOT} && ${PYTHON} ${MAIN} watch >> ${LOG_DIR}/watch.log 2>&1 ${MARKER}"

# Nightly markdown report at 11:30pm
REPORT_LINE="30 23 * * * cd ${PROJECT_ROOT} && ${PYTHON} ${MAIN} report --model crf150f --write >> ${LOG_DIR}/report.log 2>&1 ${MARKER}"

# Add the entries, replacing any previous ones with the same marker
(crontab -l 2>/dev/null | grep -v "${MARKER}"; echo "${CRON_LINE}"; echo "${REPORT_LINE}") | crontab -

echo "Installed cron entries:"
crontab -l | grep "${MARKER}"
echo ""
echo "Logs: ${LOG_DIR}/watch.log  and  ${LOG_DIR}/report.log"
echo "Remove with: ./scripts/install_cron.sh --remove"
