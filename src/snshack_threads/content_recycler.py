"""Content recycling — repurpose top-performing posts with different hooks.

Top posts from 30+ days ago can be recycled with a new hook pattern,
effectively doubling content output without doubling effort.
Tracks recycling history to enforce cooldown periods.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .csv_analyzer import _HOOK_PATTERNS, _detect_hooks, _text_length_bucket
from .post_history import PostHistory, PostRecord


# Minimum days before a post can be recycled
RECYCLE_COOLDOWN_DAYS = 30


def find_recyclable_posts(
    history: PostHistory,
    min_views: int = 0,
    min_age_days: int = RECYCLE_COOLDOWN_DAYS,
    top_n: int = 10,
) -> list[PostRecord]:
    """Find top-performing posts eligible for recycling.

    Args:
        history: Post history manager.
        min_views: Minimum views to qualify.
        min_age_days: Minimum age in days since original post.
        top_n: Max number of candidates to return.

    Returns:
        List of PostRecords sorted by views (descending).
    """
    cutoff = datetime.now() - timedelta(days=min_age_days)

    candidates = [
        r for r in history.get_all()
        if r.has_metrics
        and r.views >= min_views
        and datetime.fromisoformat(r.scheduled_at) < cutoff
    ]

    # Sort by views descending
    candidates.sort(key=lambda r: r.views, reverse=True)
    return candidates[:top_n]


def suggest_recycle(record: PostRecord) -> dict:
    """Generate recycling suggestions for a top-performing post.

    Returns info about the original post and potential new angles.
    """
    hooks_used = _detect_hooks(record.text)
    length = _text_length_bucket(record.text)

    # Derive all hook names from csv_analyzer (single source of truth)
    all_hooks = [name for name, _ in _HOOK_PATTERNS]
    unused_hooks = [h for h in all_hooks if h not in hooks_used]

    return {
        "original_text": record.text,
        "original_views": record.views,
        "original_likes": record.likes,
        "original_hooks": hooks_used,
        "original_length": length,
        "suggested_hooks": unused_hooks[:3],
        "scheduled_at": record.scheduled_at,
    }
