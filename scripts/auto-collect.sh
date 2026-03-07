#!/usr/bin/env bash
# Auto-collect performance data for all profiles (or a specific one).
# Intended to be run via cron.
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

run_collect() {
    local profile="$1"
    local logfile="$LOG_DIR/collect_${profile}_${TIMESTAMP}.log"

    echo "[$(date)] Collecting for profile: $profile" >> "$logfile"

    # 1. Sync CSV from Metricool API
    echo "[$(date)] Syncing CSV..." >> "$logfile"
    snshack --profile "$profile" sync-csv >> "$logfile" 2>&1 || true

    # 2. Collect performance data
    echo "[$(date)] Collecting performance..." >> "$logfile"
    snshack --profile "$profile" collect-performance >> "$logfile" 2>&1 || true

    # 3. Scrape competitor profiles (if playwright is installed)
    if python -c "import playwright" 2>/dev/null; then
        echo "[$(date)] Scraping competitors..." >> "$logfile"
        snshack --profile "$profile" competitor scrape >> "$logfile" 2>&1 || true
    fi

    echo "[$(date)] Done" >> "$logfile"
}

if [ $# -ge 1 ]; then
    # Specific profile
    run_collect "$1"
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
            run_collect "$profile_name"
        fi
    done
fi

# Clean up logs older than 30 days
find "$LOG_DIR" -name "collect_*.log" -mtime +30 -delete 2>/dev/null || true
