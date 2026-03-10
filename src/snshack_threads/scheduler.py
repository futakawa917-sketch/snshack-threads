"""Scheduling logic for daily post planning and slot management."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .content_guard import check_ng
from .models import DailySchedule, PostDraft, ScheduleSlot

logger = logging.getLogger(__name__)


class ContentNGError(Exception):
    """Raised when draft content contains NG patterns."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__(f"NG content detected: {', '.join(violations)}")


def validate_drafts(drafts: list[PostDraft]) -> None:
    """Validate all drafts for NG content. Raises ContentNGError if violations found."""
    all_violations: list[str] = []
    for draft in drafts:
        violations = check_ng(draft.text)
        all_violations.extend(violations)
    if all_violations:
        raise ContentNGError(all_violations)


def _resolve_csv_path(csv_file: str | None = None) -> Path | None:
    """Resolve the CSV file path from argument, settings, or default location."""
    if csv_file:
        return Path(csv_file)
    try:
        from .config import get_settings

        settings = get_settings()
        if settings.csv_path:
            p = Path(settings.csv_path)
            if p.exists():
                return p
        ref = settings.reference_csv_path
        if ref.exists():
            return ref
    except Exception:
        pass
    return None


def get_optimal_schedule(
    day_of_week: int | None = None,
    csv_path: str | Path | None = None,
    n_slots: int = 5,
    profile: str | None = None,
) -> DailySchedule:
    """Derive an optimal posting schedule with 3-tier fallback.

    Priority: account CSV → genre data → universal data → default schedule.
    If csv_path is explicitly provided, it is used directly (no fallback).
    """
    # If explicit CSV path given, use it directly (legacy behavior)
    if csv_path:
        path = Path(csv_path)
        if path.exists():
            try:
                from .csv_analyzer import analyze_optimal_times

                result = analyze_optimal_times(path)
                if day_of_week is not None:
                    slots_data = result.get_optimal_slots_for_day(day_of_week, n=n_slots)
                else:
                    slots_data = result.get_optimal_slots(n=n_slots)
                if slots_data:
                    slots = [ScheduleSlot(hour=h, minute=m) for h, m in slots_data]
                    return DailySchedule(slots=slots)
            except Exception as e:
                logger.warning("CSV analysis failed: %s", e)

    # Use 3-tier resolution
    from .data_resolver import resolve_times

    resolved = resolve_times(profile=profile, day_of_week=day_of_week)
    logger.info("Schedule resolved from %s tier", resolved.tier.value)

    if resolved.slots:
        slots = [ScheduleSlot(hour=h, minute=m) for h, m in resolved.slots[:n_slots]]
        return DailySchedule(slots=slots)

    return DailySchedule()


def schedule_posts_for_day(
    client,
    drafts: list[PostDraft],
    target: datetime,
    schedule: DailySchedule | None = None,
) -> list[dict]:
    """Schedule posts for a target day using the given schedule.

    Args:
        client: MetricoolClient instance.
        drafts: List of post drafts to schedule.
        target: Target date for scheduling.
        schedule: Optional custom schedule; defaults to CSV-derived or standard.

    Returns:
        List of API responses.
    """
    validate_drafts(drafts)

    if schedule is None:
        schedule = get_optimal_schedule(day_of_week=target.weekday())

    now = datetime.now()
    is_today = target.date() == now.date()
    results = []
    for i, draft in enumerate(drafts):
        if i >= len(schedule.slots):
            break
        slot = schedule.slots[i]
        publish_at = target.replace(
            hour=slot.hour, minute=slot.minute, second=0, microsecond=0
        )
        # Skip past time slots only when scheduling for today
        if is_today and publish_at <= now:
            logger.warning("Skipping past slot %02d:%02d (already passed)", slot.hour, slot.minute)
            continue
        resp = client.schedule_post(draft, publish_at)
        results.append(resp)

    return results


def get_next_available_slots(
    client,
    target: datetime,
    schedule: DailySchedule | None = None,
) -> list[datetime]:
    """Find available (unoccupied) slots for a target day.

    Args:
        client: MetricoolClient instance.
        target: Target date.
        schedule: Optional schedule; defaults to standard 5-slot schedule.

    Returns:
        List of available datetime objects.
    """
    if schedule is None:
        schedule = DailySchedule()

    start_str = target.strftime("%Y-%m-%d")
    end_str = start_str
    scheduled = client.get_scheduled_posts(start_str, end_str)

    occupied: set[tuple[int, int]] = set()  # (hour, minute) pairs
    for post in scheduled:
        pub_date = post.get("publicationDate", {})
        dt_str = pub_date.get("dateTime", "")
        if dt_str:
            try:
                dt = datetime.fromisoformat(dt_str)
                occupied.add((dt.hour, dt.minute))
            except ValueError:
                pass

    now = datetime.now()
    is_today = target.date() == now.date()
    available = []
    for slot in schedule.slots:
        if (slot.hour, slot.minute) not in occupied:
            dt = target.replace(
                hour=slot.hour, minute=slot.minute, second=0, microsecond=0
            )
            if not is_today or dt > now:
                available.append(dt)

    return available
