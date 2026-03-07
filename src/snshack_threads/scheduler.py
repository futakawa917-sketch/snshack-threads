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

import logging

from .api import MetricoolClient, MetricoolAPIError
from .config import Settings, get_settings
from .content_guard import check_ng
from .csv_analyzer import analyze_optimal_times
from .models import DailySchedule, PostDraft, ScheduleSlot
from .post_history import PostHistory

logger = logging.getLogger(__name__)

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


def validate_drafts(drafts: list[PostDraft], history: PostHistory | None = None) -> list[str]:
    """Validate all drafts against NG rules and check for duplicates.

    Raises ContentNGError on NG violation.
    Returns list of similarity warnings (non-blocking).
    """
    warnings: list[str] = []

    for draft in drafts:
        violations = check_ng(draft.text)
        if violations:
            raise ContentNGError(draft.text, violations)

    # Check for content similarity with recent posts
    if history is not None:
        for draft in drafts:
            similar = history.check_similarity(draft.text)
            if similar:
                warnings.append(
                    f"Similar to recent post: '{draft.text[:40]}...' "
                    f"matches '{similar[0].text[:40]}...'"
                )
                logger.warning("Duplicate content detected: %s", warnings[-1])

    return warnings


def schedule_posts_for_day(
    client: MetricoolClient,
    drafts: list[PostDraft],
    date: datetime,
    schedule: DailySchedule | None = None,
    history: PostHistory | None = None,
) -> list[dict[str, Any]]:
    """Schedule multiple posts for a single day at optimal time slots.

    Uses day-of-week specific optimal times when schedule is not provided.
    Validates all drafts against NG rules before scheduling.
    Records each post in history for later performance tracking.
    """
    if history is None:
        history = PostHistory()

    validate_drafts(drafts, history=history)

    if schedule is None:
        schedule = get_optimal_schedule(day_of_week=date.weekday())

    results: list[dict[str, Any]] = []
    errors: list[tuple[int, str]] = []

    for i, draft in enumerate(drafts):
        if i >= len(schedule.slots):
            break
        slot = schedule.slots[i]
        publish_at = date.replace(
            hour=slot.hour, minute=slot.minute, second=0, microsecond=0
        )
        try:
            result = client.schedule_post(draft, publish_at)
        except (MetricoolAPIError, Exception) as e:
            logger.warning("Failed to schedule post %d: %s", i + 1, e)
            errors.append((i + 1, str(e)))
            continue

        results.append(result)

        # Record in history for performance tracking
        history.record_scheduled(
            text=draft.text,
            publish_at=publish_at,
            metricool_response=result,
        )

    if errors and not results:
        raise MetricoolAPIError(
            f"All {len(errors)} posts failed to schedule. "
            f"First error: {errors[0][1]}"
        )

    return results


def schedule_posts_for_week(
    client: MetricoolClient,
    daily_drafts: dict[str, list[PostDraft]],
    start_date: datetime,
    schedule: DailySchedule | None = None,
    history: PostHistory | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Schedule posts for multiple days.

    Each day uses its own day-of-week optimal schedule unless overridden.
    Shares a single PostHistory instance across all days.
    """
    if history is None:
        history = PostHistory()

    results: dict[str, list[dict[str, Any]]] = {}

    for date_str, drafts in daily_drafts.items():
        date = datetime.strptime(date_str, "%Y-%m-%d")
        day_results = schedule_posts_for_day(client, drafts, date, schedule, history=history)
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
