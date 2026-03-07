"""Post scheduling and automation via Metricool API.

Supports batch scheduling of multiple posts per day (default: 5).
Time slots are determined by CSV analytics data for optimal engagement.
Day-of-week specific optimization is used when available.
All posts are validated against NG rules before scheduling.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .api import MetricoolClient
from .config import Settings, get_settings
from .content_guard import check_ng
from .csv_analyzer import analyze_optimal_times
from .models import DailySchedule, PostDraft, ScheduleSlot

# Fallback CSV location (repo root) when THREADS_CSV_PATH is not set
_REPO_ROOT_CSV = Path(__file__).resolve().parent.parent.parent / "スレッズ.csv"


class ContentNGError(Exception):
    """Raised when post content violates NG rules."""

    def __init__(self, text: str, violations: list[str]):
        self.text = text
        self.violations = violations
        super().__init__(f"NG detected: {', '.join(violations)}")


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


def _get_analysis(csv_path: str | Path | None = None):
    """Get CSV analysis result, or None if unavailable."""
    resolved = _resolve_csv_path(csv_path)
    if resolved is None:
        return None
    return analyze_optimal_times(resolved)


def get_optimal_schedule(
    csv_path: str | Path | None = None,
    n_slots: int = 5,
    day_of_week: int | None = None,
) -> DailySchedule:
    """Build a DailySchedule by analyzing the account's CSV data.

    Time slots are always derived from the data — never hardcoded.
    When day_of_week is provided, uses day-specific patterns.

    Falls back to default fixed slots only if no CSV is available.
    """
    analysis = _get_analysis(csv_path)
    if analysis is None:
        return DailySchedule()

    if day_of_week is not None:
        optimal = analysis.get_optimal_slots_for_day(day_of_week, n=n_slots)
    else:
        optimal = analysis.get_optimal_slots(n=n_slots)

    if not optimal:
        return DailySchedule()

    return DailySchedule(
        slots=[ScheduleSlot(hour=h, minute=m) for h, m in optimal]
    )


def validate_drafts(drafts: list[PostDraft]) -> None:
    """Validate all drafts against NG rules. Raises ContentNGError on violation."""
    for draft in drafts:
        violations = check_ng(draft.text)
        if violations:
            raise ContentNGError(draft.text, violations)


def schedule_posts_for_day(
    client: MetricoolClient,
    drafts: list[PostDraft],
    date: datetime,
    schedule: DailySchedule | None = None,
) -> list[dict[str, Any]]:
    """Schedule multiple posts for a single day at optimal time slots.

    Uses day-of-week specific optimal times when schedule is not provided.
    Validates all drafts against NG rules before scheduling.
    """
    validate_drafts(drafts)

    if schedule is None:
        schedule = get_optimal_schedule(day_of_week=date.weekday())

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

    Each day uses its own day-of-week optimal schedule unless overridden.
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

    Uses day-of-week specific optimal times.
    """
    if schedule is None:
        schedule = get_optimal_schedule(day_of_week=date.weekday())

    date_str = date.strftime("%Y-%m-%d")
    existing = client.get_scheduled_posts(date_str, date_str)

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

    available: list[datetime] = []
    for slot in schedule.slots:
        if slot.hour not in scheduled_hours:
            available.append(
                date.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)
            )

    return available
