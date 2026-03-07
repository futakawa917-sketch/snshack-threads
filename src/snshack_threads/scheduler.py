"""Post scheduling and automation via Metricool API.

Supports batch scheduling of multiple posts per day (default: 5).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .api import MetricoolClient
from .config import Settings, get_settings
from .models import DailySchedule, PostDraft, ScheduleSlot


def schedule_posts_for_day(
    client: MetricoolClient,
    drafts: list[PostDraft],
    date: datetime,
    schedule: DailySchedule | None = None,
) -> list[dict[str, Any]]:
    """Schedule multiple posts for a single day at predefined time slots.

    Args:
        client: Metricool API client.
        drafts: List of post drafts (up to len(schedule.slots)).
        date: The target date.
        schedule: Time slots for the day. Defaults to 5 slots (8, 11, 14, 18, 21).

    Returns:
        List of API responses for each scheduled post.
    """
    if schedule is None:
        schedule = DailySchedule()

    results: list[dict[str, Any]] = []
    for i, draft in enumerate(drafts):
        if i >= len(schedule.slots):
            break
        slot = schedule.slots[i]
        publish_at = date.replace(
            hour=slot.hour, minute=slot.minute, second=0, microsecond=0
        )
        result = client.schedule_post(draft, publish_at)
        results.append(result)

    return results


def schedule_posts_for_week(
    client: MetricoolClient,
    daily_drafts: dict[str, list[PostDraft]],
    start_date: datetime,
    schedule: DailySchedule | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Schedule posts for multiple days.

    Args:
        client: Metricool API client.
        daily_drafts: Mapping of date string (YYYY-MM-DD) to list of drafts.
        start_date: Starting date for the scheduling period.
        schedule: Time slots per day.

    Returns:
        Mapping of date string to list of API responses.
    """
    results: dict[str, list[dict[str, Any]]] = {}

    for date_str, drafts in daily_drafts.items():
        date = datetime.strptime(date_str, "%Y-%m-%d")
        day_results = schedule_posts_for_day(client, drafts, date, schedule)
        results[date_str] = day_results

    return results


def get_next_available_slots(
    client: MetricoolClient,
    date: datetime,
    schedule: DailySchedule | None = None,
) -> list[datetime]:
    """Find available time slots for a given day.

    Checks already-scheduled posts and returns slots that are still free.
    """
    if schedule is None:
        schedule = DailySchedule()

    date_str = date.strftime("%Y-%m-%d")
    existing = client.get_scheduled_posts(date_str, date_str)

    # Get existing scheduled times
    scheduled_hours: set[int] = set()
    for post in existing:
        pub_date = post.get("publicationDate", {})
        dt_str = pub_date.get("dateTime", "")
        if dt_str:
            try:
                dt = datetime.fromisoformat(dt_str)
                scheduled_hours.add(dt.hour)
            except ValueError:
                pass

    # Return slots that don't conflict
    available: list[datetime] = []
    for slot in schedule.slots:
        if slot.hour not in scheduled_hours:
            available.append(
                date.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)
            )

    return available
