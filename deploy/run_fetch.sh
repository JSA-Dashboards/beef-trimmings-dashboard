#!/usr/bin/env bash
# run_fetch.sh — weekly NW_LS421 (Import Beef Trade) fetch for beef-trimmings-dashboard,
# invoked by cron on the Droplet: /opt/beef-trimmings-dashboard is a git clone of this
# repo, and cron runs deploy/run_fetch.sh (Fri 16:30 + Mon 07:00 CT, via cron-alert).
#
# Runs here instead of on Streamlit Cloud because marsapi.ams.usda.gov rejects
# requests from Streamlit Community Cloud's IPs (confirmed 2026-10-04 — identical
# request: 200 from this droplet, 401 from Streamlit Cloud). Writes straight to
# Snowflake JSA.BEEF_TRIMMINGS.IMPORT_COW90 (MERGE, idempotent) — the Cloud app
# reads that table instead of calling MARS directly.
#
# The report publishes weekly, Fridays — this runs Friday afternoon plus a Monday
# catch-up in case Friday's report hadn't posted in time when cron fired.
set -uo pipefail

APP_DIR="/opt/beef-trimmings-dashboard"
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/fetch_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

exec 9>"$LOG_DIR/.fetch.lock"
if ! flock -n 9; then
    echo "$(date -Is) another run is in progress — skipping" >>"$LOG"
    exit 0
fi

set -a; source .env; set +a

rc=0
{
    echo "=== fetch start $(date -Is) ==="
    "$VENV/bin/python3" deploy/fetch_import_cow90.py
    rc=$?
    echo "=== fetch finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

find "$LOG_DIR" -name 'fetch_*.log' -mtime +30 -delete 2>/dev/null || true

exit "$rc"
