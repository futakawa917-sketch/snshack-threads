#!/usr/bin/env bash
# Cron wrapper: adds random delay (0-45min) before running auto-collect.
# This prevents posting at exactly the same time every day,
# which social media algorithms may penalize.
#
# Cron entry: 0 7 * * * /path/to/scripts/cron-runner.sh
# Actual execution: 7:00~7:45 (randomized daily)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Random delay: 0~2700 seconds (0~45 minutes)
DELAY=$((RANDOM % 2700))
DELAY_MIN=$((DELAY / 60))

echo "[$(date)] Waiting ${DELAY_MIN}m ${((DELAY % 60))}s before starting..." >> "$SCRIPT_DIR/../logs/cron-delay.log"
sleep "$DELAY"

exec "$SCRIPT_DIR/auto-collect.sh" "$@"
