#!/usr/bin/env bash
#
# Install the dirtbike-tracker watch job on cron.
# Runs every 20 minutes during Ontario waking hours (6am - 11pm ET).
# Log lines get prefixed with timestamp via `ts` from moreutils.
#
# Run this from the prod clone (~/Code/dirtbike-tracker-prod), not the dev
# checkout: cron executes whatever is checked out where this script lives.
# See README "Cron".

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIRTBIKE="${PROJECT_ROOT}/venv/bin/dirtbike"
LOG_DIR="${PROJECT_ROOT}/logs"
MARKER="# dirtbike-tracker"

mkdir -p "${LOG_DIR}"

if [[ ! -x "${DIRTBIKE}" ]]; then
  echo "Error: ${DIRTBIKE} not found."
  exit 1
fi

if ! command -v ts >/dev/null 2>&1; then
  echo "Warning: 'ts' (from moreutils) not installed. Logs will have no timestamps."
  echo "  sudo apt install moreutils"
  TS_PREFIX=""
else
  # crontab treats a bare % as a newline, which truncated the whole command
  TS_PREFIX="| ts '[\%Y-\%m-\%d \%H:\%M:\%S]'"
fi

if [[ "${1:-}" == "--remove" ]]; then
  crontab -l 2>/dev/null | grep -v "${MARKER}" | crontab -
  echo "Removed dirtbike-tracker cron entries."
  exit 0
fi

# Timestamp prefix on every line of output
CRON_LINE="*/20 6-23 * * * NTFY_TOPIC=${NTFY_TOPIC:-} HEALTHCHECK_URL=${HEALTHCHECK_URL:-} cd ${PROJECT_ROOT} && ${DIRTBIKE} watch 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/watch.log ${MARKER}"
REPORT_LINE="30 23 * * * cd ${PROJECT_ROOT} && ${DIRTBIKE} report --model crf150f --write 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/report.log ${MARKER}"

(crontab -l 2>/dev/null | grep -v "${MARKER}"; echo "${CRON_LINE}"; echo "${REPORT_LINE}") | crontab -

echo "Installed cron entries:"
crontab -l | grep "${MARKER}"
echo ""
echo "Logs: ${LOG_DIR}/watch.log  and  ${LOG_DIR}/report.log"
echo "Remove with: ./scripts/install_cron.sh --remove"
