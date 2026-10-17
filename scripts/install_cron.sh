#!/usr/bin/env bash
#
# Install the dirtbike-tracker watch job on cron.
# Runs every 20 minutes, 8:00-22:40 ET (8am to 11pm).
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
# Env assignments must prefix the dirtbike command itself: `VAR=x cd dir && cmd`
# scopes VAR to `cd` only, so watch never saw NTFY_TOPIC.
CRON_LINE="*/20 8-22 * * * cd ${PROJECT_ROOT} && NTFY_TOPIC=${NTFY_TOPIC:-} NTFY_STREET_TOPIC=${NTFY_STREET_TOPIC:-} HEALTHCHECK_URL=${HEALTHCHECK_URL:-} ${DIRTBIKE} watch 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/watch.log ${MARKER}"
REPORT_LINE="30 23 * * * cd ${PROJECT_ROOT} && ${DIRTBIKE} report --model crf150f --write 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/report.log ${MARKER}"
# Off-box backup after the day's last scrape; weekly slot on Sundays (#26)
BACKUP_LINE="55 23 * * * cd ${PROJECT_ROOT} && NTFY_TOPIC=${NTFY_TOPIC:-} ${DIRTBIKE} backup 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/backup.log ${MARKER}"
WEEKLY_BACKUP_LINE="58 23 * * 0 cd ${PROJECT_ROOT} && NTFY_TOPIC=${NTFY_TOPIC:-} ${DIRTBIKE} backup --slot weekly 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/backup.log ${MARKER}"
# Site-drift check (#28), daily before the first watch: alerts if a parser stopped reading a site
DRIFT_LINE="0 5 * * * cd ${PROJECT_ROOT} && NTFY_TOPIC=${NTFY_TOPIC:-} ${DIRTBIKE} drift 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/drift.log ${MARKER}"
# A friend's watchlist (config/friend.yml) to his own ntfy topic, only when FRIEND_NTFY_TOPIC is
# set. Offset 10 min so it never overlaps the main watch on the shared DB; 8:10-22:50, so
# nothing pings him before 8am or after 11pm.
FRIEND_LINE=""
if [[ -n "${FRIEND_NTFY_TOPIC:-}" ]]; then
  FRIEND_LINE="10-59/20 8-22 * * * cd ${PROJECT_ROOT} && NTFY_TOPIC=${FRIEND_NTFY_TOPIC} ${DIRTBIKE} --config config/friend.yml watch 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/watch-friend.log ${MARKER}"
fi

(crontab -l 2>/dev/null | grep -v "${MARKER}"; echo "${CRON_LINE}"; echo "${REPORT_LINE}"; echo "${BACKUP_LINE}"; echo "${WEEKLY_BACKUP_LINE}"; echo "${DRIFT_LINE}"; if [[ -n "${FRIEND_LINE}" ]]; then echo "${FRIEND_LINE}"; fi) | crontab -

echo "Installed cron entries:"
crontab -l | grep "${MARKER}"
echo ""
echo "Logs: ${LOG_DIR}/{watch,report,backup,drift}.log"
echo "Remove with: ./scripts/install_cron.sh --remove"
