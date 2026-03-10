"""Content recycling — find high-performing old posts and suggest rewrites."""

from __future__ import annotations

from datetime import datetime, timedelta

from .csv_analyzer import _detect_hooks, get_active_hooks
from .post_history import PostHistory, PostRecord


def find_recyclable_posts(
    history: PostHistory,
    min_age_days: int = 30,
    min_views: int = 0,
    top_n: int | None = None,
) -> list[PostRecord]:
    """Find old posts eligible for recycling.

    Args:
        history: Post history manager.
        min_age_days: Minimum age in days to be recyclable.
        min_views: Minimum views threshold.
        top_n: Limit results (None = all matching).

    Returns:
        List of PostRecord sorted by views descending.
    """
    cutoff = datetime.now() - timedelta(days=min_age_days)
    candidates = []

    for record in history.get_all():
        if record.status != "collected":
            continue
        try:
            scheduled = datetime.fromisoformat(record.scheduled_at)
        except ValueError:
            continue
        if scheduled > cutoff:
            continue
        if record.views < min_views:
            continue
        candidates.append(record)

    candidates.sort(key=lambda r: r.views, reverse=True)

    if top_n is not None:
        return candidates[:top_n]
    return candidates


def suggest_recycle(record: PostRecord) -> dict:
    """Suggest how to recycle a post with different hooks.

    Returns:
        Dict with original_hooks, original_views, and suggested_hooks.
    """
    original_hooks = _detect_hooks(record.text)

    all_hook_names = [name for name, _ in get_active_hooks()]
    # Deduplicate while preserving order
    seen = set()
    unique_names = []
    for name in all_hook_names:
        if name not in seen:
            seen.add(name)
            unique_names.append(name)

    suggested = [h for h in unique_names if h not in original_hooks]

    return {
        "original_hooks": original_hooks,
        "original_views": record.views,
        "suggested_hooks": suggested,
    }
