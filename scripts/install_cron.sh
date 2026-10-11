#!/usr/bin/env bash
#
# Install the dirtbike-tracker watch job on cron.
# Runs every 20 minutes during Ontario waking hours (6am - 11pm ET).
#
# Usage:
#   ./scripts/install_cron.sh          # install
#   ./scripts/install_cron.sh --remove # remove

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIRTBIKE="${PROJECT_ROOT}/venv/bin/dirtbike"
LOG_DIR="${PROJECT_ROOT}/logs"
MARKER="# dirtbike-tracker"

mkdir -p "${LOG_DIR}"

if [[ ! -x "${DIRTBIKE}" ]]; then
  echo "Error: ${DIRTBIKE} not found."
  echo "Run from the project root after: source venv/bin/activate && pip install -e \".[dev]\""
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

# Build the cron entries. Every 20 min 6am-11pm for watch, 11:30pm for report.
CRON_LINE="*/20 6-23 * * * NTFY_TOPIC=${NTFY_TOPIC:-} cd ${PROJECT_ROOT} && ${DIRTBIKE} watch >> ${LOG_DIR}/watch.log 2>&1 ${MARKER}"
REPORT_LINE="30 23 * * * cd ${PROJECT_ROOT} && ${DIRTBIKE} report --model crf150f --write >> ${LOG_DIR}/report.log 2>&1 ${MARKER}"

# Install, replacing any previous entries with the same marker
(crontab -l 2>/dev/null | grep -v "${MARKER}"; echo "${CRON_LINE}"; echo "${REPORT_LINE}") | crontab -

echo "Installed cron entries:"
crontab -l | grep "${MARKER}"
echo ""
echo "Logs: ${LOG_DIR}/watch.log  and  ${LOG_DIR}/report.log"
echo "Remove with: ./scripts/install_cron.sh --remove"
