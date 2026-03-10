#!/usr/bin/env python3
"""Daily automation runner for launchd.

Flow:
  1. Morning: collect metrics, generate plan, save pending posts
  2. Throughout the day: publish each post at its scheduled time
  3. Evening: final collection run

Runs as a long-lived process started by launchd at 7:00 AM.
"""

import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SNSHACK = PROJECT_DIR / ".venv" / "bin" / "snshack"
LOG_DIR = PROJECT_DIR / "logs"
PROFILES_DIR = Path.home() / ".snshack-threads" / "profiles"

LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    filename=str(LOG_DIR / "daily_runner.log"),
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
)
log = logging.getLogger(__name__)


def run_cmd(args: list[str], profile: str, timeout: int = 300) -> str:
    """Run a snshack command, logging output. Returns stdout."""
    cmd = [str(SNSHACK), "--profile", profile] + args
    log.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                **os.environ,
                "PATH": f"{PROJECT_DIR / '.venv' / 'bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
                "HOME": str(Path.home()),
            },
        )
        if result.stdout.strip():
            log.info("  %s", result.stdout.strip()[:500])
        if result.returncode != 0 and result.stderr.strip():
            log.warning("  ERR: %s", result.stderr.strip()[:300])
        return result.stdout
    except subprocess.TimeoutExpired:
        log.warning("  TIMEOUT: %s", " ".join(args))
    except Exception as e:
        log.error("  ERROR: %s", e)
    return ""


def publish_pending(profile: str) -> int:
    """Publish any pending posts whose time has arrived. Returns count published."""
    # Use Python import directly for reliability
    sys.path.insert(0, str(PROJECT_DIR / "src"))
    try:
        from snshack_threads.timed_publisher import publish_due_posts
        results = publish_due_posts(profile=profile)
        for r in results:
            log.info("  %s", r)
        return len([r for r in results if "Published" in r])
    except Exception as e:
        log.error("  publish_pending failed: %s", e)
        return 0


def get_pending(profile: str) -> int:
    """Get count of remaining pending posts."""
    sys.path.insert(0, str(PROJECT_DIR / "src"))
    try:
        from snshack_threads.timed_publisher import get_pending_count
        return get_pending_count(profile=profile)
    except Exception:
        return 0


def run_daily(profile: str) -> None:
    """Run the full daily automation for a profile."""
    log.info("========================================")
    log.info("Daily automation START: %s", profile)
    log.info("========================================")

    # Phase 1: Morning tasks (collect + generate plan)
    run_cmd(["collect-threads"], profile)
    run_cmd(["collect-early"], profile)
    run_cmd(["track-followers"], profile)
    run_cmd(["threads", "refresh-token"], profile)

    # Generate plan and save pending posts (autopilot)
    run_cmd(["autopilot"], profile, timeout=600)

    # Publish any posts whose time has already passed (e.g. 7:30 slot)
    published = publish_pending(profile)
    log.info("Initial publish: %d posts", published)

    # Phase 2: Wait and publish remaining posts at their scheduled times
    remaining = get_pending(profile)
    log.info("Remaining pending: %d posts", remaining)

    while remaining > 0:
        # Calculate wait: check every 10 minutes
        log.info("Waiting 10 minutes... (%d pending)", remaining)
        time.sleep(600)

        published = publish_pending(profile)
        if published > 0:
            log.info("Published %d posts", published)

        remaining = get_pending(profile)

        # Safety: don't run past 23:00
        if datetime.now().hour >= 23:
            log.warning("Past 23:00, stopping. %d posts still pending.", remaining)
            break

    log.info("========================================")
    log.info("Daily automation COMPLETE: %s", profile)
    log.info("========================================")


def main() -> None:
    # Random delay: 0-10 minutes (shorter than before, posts have their own times)
    delay = random.randint(0, 600)
    log.info("Startup delay: %d seconds", delay)
    time.sleep(delay)

    if len(sys.argv) > 1:
        run_daily(sys.argv[1])
    else:
        if not PROFILES_DIR.exists():
            log.warning("No profiles directory found")
            return
        for profile_dir in sorted(PROFILES_DIR.iterdir()):
            config = profile_dir / "config.json"
            if config.exists():
                profile_name = profile_dir.name
                # Skip profiles without Threads API token
                import json
                cfg = json.loads(config.read_text())
                if cfg.get("threads_access_token"):
                    run_daily(profile_name)
                else:
                    log.info("Skipping %s (no Threads token)", profile_name)


if __name__ == "__main__":
    main()
