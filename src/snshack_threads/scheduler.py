"""Post scheduling and automation via Metricool API.

Supports batch scheduling of multiple posts per day (default: 5).
Time slots are determined by CSV analytics data for optimal engagement.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .api import MetricoolClient
from .config import Settings, get_settings
from .csv_analyzer import analyze_optimal_times
from .models import DailySchedule, PostDraft, ScheduleSlot

# Fallback CSV location (repo root) when THREADS_CSV_PATH is not set
_REPO_ROOT_CSV = Path(__file__).resolve().parent.parent.parent / "スレッズ.csv"


def _resolve_csv_path(csv_path: str | Path | None = None) -> Path | None:
    """Resolve CSV path from argument > env var > repo root fallback."""
    if csv_path:
        p = Path(csv_path)
        return p if p.exists() else None

    settings = get_settings()
    if settings.csv_path:
        p = Path(settings.csv_path)
        if not p.is_absolute():
            p = _REPO_ROOT_CSV.parent / p
        return p if p.exists() else None

    return _REPO_ROOT_CSV if _REPO_ROOT_CSV.exists() else None


def get_optimal_schedule(csv_path: str | Path | None = None, n_slots: int = 5) -> DailySchedule:
    """Build a DailySchedule by analyzing the account's CSV data.

    Time slots are always derived from the data — never hardcoded.
    Different accounts/genres will produce different optimal schedules.

    Falls back to default fixed slots only if no CSV is available.
    """
    resolved = _resolve_csv_path(csv_path)
    if resolved is None:
        return DailySchedule()

    result = analyze_optimal_times(resolved)
    optimal = result.get_optimal_slots(n=n_slots)

    if not optimal:
        return DailySchedule()

    return DailySchedule(
        slots=[ScheduleSlot(hour=h, minute=m) for h, m in optimal]
    )


def schedule_posts_for_day(
    client: MetricoolClient,
    drafts: list[PostDraft],
    date: datetime,
    schedule: DailySchedule | None = None,
) -> list[dict[str, Any]]:
    """Schedule multiple posts for a single day at optimal time slots.

    Args:
        client: Metricool API client.
        drafts: List of post drafts (up to len(schedule.slots)).
        date: The target date.
        schedule: Time slots. If None, uses CSV-derived optimal times.

    Returns:
        List of API responses for each scheduled post.
    """
    if schedule is None:
        schedule = get_optimal_schedule()

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
        schedule = get_optimal_schedule()

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
