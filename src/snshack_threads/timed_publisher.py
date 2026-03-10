"""Timed publisher — publishes posts at their scheduled times.

Reads a pending plan file and publishes each post when its scheduled time arrives.
Used by daily_runner.py to distribute posts throughout the day instead of
publishing all at once.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def save_pending_plan(
    posts: list[dict],
    schedule_slots: list[tuple[int, int]],
    profile: str = "default",
) -> Path:
    """Save posts with their scheduled times to a pending file.

    Args:
        posts: List of post dicts from DailyPlan.posts.
        schedule_slots: List of (hour, minute) tuples.
        profile: Profile name.

    Returns:
        Path to the pending plan file.
    """
    from .config import get_settings

    settings = get_settings(profile=profile)
    pending_path = settings.profile_dir / "pending_posts.json"

    entries = []
    for i, post in enumerate(posts):
        if i < len(schedule_slots):
            hour, minute = schedule_slots[i]
        else:
            break
        entries.append({
            "text": post["text"],
            "hook": post.get("hook", ""),
            "post_type": post.get("post_type", "reach"),
            "scheduled_hour": hour,
            "scheduled_minute": minute,
            "status": "pending",
        })

    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Saved %d pending posts to %s", len(entries), pending_path)
    return pending_path


def publish_due_posts(profile: str = "default") -> list[str]:
    """Publish any pending posts whose scheduled time has passed.

    Returns:
        List of result messages.
    """
    from .config import get_settings
    from .post_history import PostHistory
    from .threads_api import ThreadsGraphClient

    settings = get_settings(profile=profile)
    pending_path = settings.profile_dir / "pending_posts.json"

    if not pending_path.exists():
        return []

    try:
        entries = json.loads(pending_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    if not entries:
        return []

    now = datetime.now()
    results: list[str] = []
    changed = False

    # Find posts that are due
    due = [
        (i, e) for i, e in enumerate(entries)
        if e["status"] == "pending"
        and (e["scheduled_hour"] < now.hour
             or (e["scheduled_hour"] == now.hour and e["scheduled_minute"] <= now.minute))
    ]

    if not due:
        return []

    try:
        with ThreadsGraphClient() as client:
            history = PostHistory(history_path=settings.profile_dir / "post_history.json")

            for idx, entry in due:
                try:
                    post_id = client.create_text_post(entry["text"])
                    entries[idx]["status"] = "published"
                    changed = True

                    # Record in post history
                    pub_time = now.replace(
                        hour=entry["scheduled_hour"],
                        minute=entry["scheduled_minute"],
                        second=0, microsecond=0,
                    )
                    history.record_scheduled(
                        text=entry["text"],
                        publish_at=pub_time,
                        post_type=entry.get("post_type", "reach"),
                    )

                    results.append(
                        f"Published: {entry['hook']} at {entry['scheduled_hour']:02d}:{entry['scheduled_minute']:02d}"
                    )
                    logger.info("Published: %s at %02d:%02d",
                                entry["hook"], entry["scheduled_hour"], entry["scheduled_minute"])
                except Exception as e:
                    entries[idx]["status"] = f"failed: {e}"
                    changed = True
                    results.append(f"Failed: {entry['hook']} — {e}")
                    logger.error("Failed to publish: %s — %s", entry["hook"], e)
    except Exception as e:
        results.append(f"API connection failed: {e}")
        logger.error("Threads API connection failed: %s", e)

    # Update pending file
    if changed:
        pending_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Clean up if all done
    all_done = all(e["status"] != "pending" for e in entries)
    if all_done:
        logger.info("All posts published, removing pending file")
        # Keep file for reference but mark as complete

    return results


def get_pending_count(profile: str = "default") -> int:
    """Return the number of pending posts."""
    from .config import get_settings

    settings = get_settings(profile=profile)
    pending_path = settings.profile_dir / "pending_posts.json"

    if not pending_path.exists():
        return 0

    try:
        entries = json.loads(pending_path.read_text(encoding="utf-8"))
        return sum(1 for e in entries if e["status"] == "pending")
    except (json.JSONDecodeError, OSError):
        return 0
