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
# One watch at a time: a run frozen by sleep resumes on wake beside the next one (2026-11-07,
# 11-13), double-pushing alerts. The main watch skips if one is running; the friend's waits.
LOCK="${PROJECT_ROOT}/data/watch.lock"

mkdir -p "${LOG_DIR}" "${PROJECT_ROOT}/data"

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
CRON_LINE="*/20 8-22 * * * cd ${PROJECT_ROOT} && NTFY_TOPIC=${NTFY_TOPIC:-} NTFY_STREET_TOPIC=${NTFY_STREET_TOPIC:-} HEALTHCHECK_URL=${HEALTHCHECK_URL:-} flock -n ${LOCK} ${DIRTBIKE} watch 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/watch.log ${MARKER}"
REPORT_LINE="30 23 * * * cd ${PROJECT_ROOT} && ${DIRTBIKE} report --model crf150f --write 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/report.log ${MARKER}"
# Off-box backup after the day's last scrape; weekly slot on Sundays (#26)
BACKUP_LINE="55 23 * * * cd ${PROJECT_ROOT} && NTFY_TOPIC=${NTFY_TOPIC:-} ${DIRTBIKE} backup 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/backup.log ${MARKER}"
WEEKLY_BACKUP_LINE="58 23 * * 0 cd ${PROJECT_ROOT} && NTFY_TOPIC=${NTFY_TOPIC:-} ${DIRTBIKE} backup --slot weekly 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/backup.log ${MARKER}"
# Site-drift check (#28), daily before the first watch: alerts if a parser stopped reading a site
DRIFT_LINE="0 5 * * * cd ${PROJECT_ROOT} && NTFY_TOPIC=${NTFY_TOPIC:-} ${DIRTBIKE} drift 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/drift.log ${MARKER}"
# The laptop sleeps through the nightly lines (2026-11-06, 11-12): retry hourly while awake, a
# no-op once the last run is younger than --if-older (hours; weekly 8 days). Offset past :00/:20.
BACKUP_CATCHUP_LINE="10 8-22 * * * cd ${PROJECT_ROOT} && NTFY_TOPIC=${NTFY_TOPIC:-} ${DIRTBIKE} backup --if-older 26 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/backup.log ${MARKER}"
WEEKLY_CATCHUP_LINE="12 8-22 * * * cd ${PROJECT_ROOT} && NTFY_TOPIC=${NTFY_TOPIC:-} ${DIRTBIKE} backup --slot weekly --if-older 194 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/backup.log ${MARKER}"
DRIFT_CATCHUP_LINE="14 8-22 * * * cd ${PROJECT_ROOT} && NTFY_TOPIC=${NTFY_TOPIC:-} ${DIRTBIKE} drift --if-older 26 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/drift.log ${MARKER}"
# A friend's watchlist (config/friend.yml) to his own ntfy topic, only when FRIEND_NTFY_TOPIC is
# set. Every third day of the month at 9:10 (his short list rarely changes); offset 10 min so
# it never overlaps the main watch on the shared DB.
FRIEND_LINE=""
if [[ -n "${FRIEND_NTFY_TOPIC:-}" ]]; then
  FRIEND_LINE="10 9 */3 * * cd ${PROJECT_ROOT} && NTFY_TOPIC=${FRIEND_NTFY_TOPIC} flock -w 900 ${LOCK} ${DIRTBIKE} --config config/friend.yml watch 2>&1 ${TS_PREFIX} >> ${LOG_DIR}/watch-friend.log ${MARKER}"
fi

(crontab -l 2>/dev/null | grep -v "${MARKER}"; echo "${CRON_LINE}"; echo "${REPORT_LINE}"; echo "${BACKUP_LINE}"; echo "${WEEKLY_BACKUP_LINE}"; echo "${DRIFT_LINE}"; echo "${BACKUP_CATCHUP_LINE}"; echo "${WEEKLY_CATCHUP_LINE}"; echo "${DRIFT_CATCHUP_LINE}"; if [[ -n "${FRIEND_LINE}" ]]; then echo "${FRIEND_LINE}"; fi) | crontab -

echo "Installed cron entries:"
crontab -l | grep "${MARKER}"
echo ""
echo "Logs: ${LOG_DIR}/{watch,report,backup,drift}.log"
echo "Remove with: ./scripts/install_cron.sh --remove"
