#!/usr/bin/env bash
# Daily automation: collect performance, sync CSV, scrape competitors,
# and auto-generate + schedule today's posts.
# Intended to be run via cron (e.g. every morning at 7:00).
#
# Usage:
#   ./scripts/auto-collect.sh              # all profiles
#   ./scripts/auto-collect.sh client-a     # specific profile

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_DIR/.venv"
LOG_DIR="$PROJECT_DIR/logs"
TIMESTAMP="$(date +%Y-%m-%d_%H%M)"

mkdir -p "$LOG_DIR"

# Activate venv
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
else
    echo "ERROR: venv not found at $VENV_DIR" >&2
    exit 1
fi

run_daily() {
    local profile="$1"
    local logfile="$LOG_DIR/daily_${profile}_${TIMESTAMP}.log"

    echo "[$(date)] Daily automation for profile: $profile" >> "$logfile"

    # 1. Sync CSV from Metricool API
    echo "[$(date)] Syncing CSV..." >> "$logfile"
    snshack --profile "$profile" sync-csv >> "$logfile" 2>&1 || true

    # 2. Collect performance data for previous posts
    echo "[$(date)] Collecting performance..." >> "$logfile"
    snshack --profile "$profile" collect-performance >> "$logfile" 2>&1 || true

    # 3. Scrape competitor profiles (if playwright is installed)
    if python -c "import playwright" 2>/dev/null; then
        echo "[$(date)] Scraping competitors..." >> "$logfile"
        snshack --profile "$profile" competitor scrape >> "$logfile" 2>&1 || true
    fi

    # 4. Auto-generate and schedule today's 5 posts
    echo "[$(date)] Running autopilot..." >> "$logfile"
    snshack --profile "$profile" autopilot >> "$logfile" 2>&1 || true

    echo "[$(date)] Done" >> "$logfile"
}

if [ $# -ge 1 ]; then
    # Specific profile
    run_daily "$1"
else
    # All profiles
    PROFILES_DIR="$HOME/.snshack-threads/profiles"
    if [ ! -d "$PROFILES_DIR" ]; then
        echo "No profiles directory found" >&2
        exit 0
    fi

    for profile_dir in "$PROFILES_DIR"/*/; do
        if [ -f "${profile_dir}config.json" ]; then
            profile_name="$(basename "$profile_dir")"
            run_daily "$profile_name"
        fi
    done
fi
