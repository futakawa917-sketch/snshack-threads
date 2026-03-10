"""3-tier data resolution with fallback strategy.

Priority:
  1. account  — this account's own PostHistory / CSV data
  2. genre    — other accounts in the same genre (industry)
  3. universal — cross-genre shared intelligence data

When an account has no data (new industry), universal insights are used.
As genre-level data accumulates it takes over, and once the account itself
has enough data it becomes the primary source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DataTier(str, Enum):
    ACCOUNT = "account"
    GENRE = "genre"
    UNIVERSAL = "universal"


# Minimum collected posts to consider a tier "usable"
_MIN_ACCOUNT_POSTS = 10
_MIN_GENRE_POSTS = 10


@dataclass
class ResolvedHooks:
    """Resolved hook ranking with source tier."""

    tier: DataTier
    hooks: list[dict]  # [{hook, avg_views, avg_likes, count}, ...]


@dataclass
class ResolvedTimes:
    """Resolved optimal posting time slots with source tier."""

    tier: DataTier
    slots: list[tuple[int, int]]  # [(hour, minute), ...]


@dataclass
class ResolvedExamples:
    """Resolved reference post examples with source tier."""

    tier: DataTier
    examples: list[str]


# ── Account Tier ──────────────────────────────────────────────


def _account_hooks(profile: str | None) -> list[dict] | None:
    """Get hook ranking from this account's own PostHistory."""
    from .config import get_settings
    from .post_history import PostHistory, get_performance_summary

    try:
        settings = get_settings(profile=profile)
        history = PostHistory(history_path=settings.profile_dir / "post_history.json")
        collected = [r for r in history.get_all() if r.has_metrics]
        if len(collected) < _MIN_ACCOUNT_POSTS:
            return None
        summary = get_performance_summary(history)
        hooks = summary.get("top_hooks", [])
        return hooks if hooks else None
    except Exception as e:
        logger.debug("Account hooks unavailable: %s", e)
        return None


def _account_times(profile: str | None, day_of_week: int | None) -> list[tuple[int, int]] | None:
    """Get optimal times from this account's own CSV data."""
    from .config import get_settings

    try:
        settings = get_settings(profile=profile)
        path = None
        if settings.csv_path:
            from pathlib import Path as P
            path = P(settings.csv_path)
        elif settings.reference_csv_path.exists():
            path = settings.reference_csv_path
        if not path or not path.exists():
            return None
        from .csv_analyzer import analyze_optimal_times
        result = analyze_optimal_times(path)
        if result.total_posts < _MIN_ACCOUNT_POSTS:
            return None
        if day_of_week is not None:
            slots = result.get_optimal_slots_for_day(day_of_week, n=5)
        else:
            slots = result.get_optimal_slots(n=5)
        return slots if slots else None
    except Exception as e:
        logger.debug("Account times unavailable: %s", e)
        return None


def _account_examples(profile: str | None, n: int) -> list[str] | None:
    """Get reference examples from this account's own data."""
    from .config import get_settings

    settings = get_settings(profile=profile)
    examples = settings.load_reference_examples(n=n)
    return examples if examples else None


# ── Genre Tier ────────────────────────────────────────────────


def _get_genre_profiles(current_profile: str | None) -> list[str]:
    """Find other profiles with the same genre as the current one."""
    from .config import get_settings, list_profiles

    current = get_settings(profile=current_profile)
    if not current.genre:
        return []

    result = []
    for name in list_profiles():
        if name == (current_profile or current.profile_name):
            continue
        try:
            other = get_settings(profile=name)
            if other.genre == current.genre:
                result.append(name)
        except Exception:
            continue
    return result


def _genre_hooks(profile: str | None) -> list[dict] | None:
    """Aggregate hook rankings from same-genre profiles."""
    from .csv_analyzer import detect_hooks_with_patterns, get_hooks_for_profile
    from .post_history import PostHistory

    genre_profiles = _get_genre_profiles(profile)
    if not genre_profiles:
        return None

    from .config import get_settings

    hook_stats: dict[str, dict] = {}
    total_collected = 0

    for pname in genre_profiles:
        try:
            settings = get_settings(profile=pname)
            patterns = get_hooks_for_profile(pname)
            history = PostHistory(history_path=settings.profile_dir / "post_history.json")
            for record in history.get_all():
                if not record.has_metrics:
                    continue
                total_collected += 1
                hooks = detect_hooks_with_patterns(record.text, patterns)
                for hook in hooks:
                    if hook not in hook_stats:
                        hook_stats[hook] = {"views": [], "likes": [], "count": 0}
                    hook_stats[hook]["views"].append(record.views)
                    hook_stats[hook]["likes"].append(record.likes)
                    hook_stats[hook]["count"] += 1
        except Exception as e:
            logger.debug("Genre profile %s failed: %s", pname, e)

    if total_collected < _MIN_GENRE_POSTS:
        return None

    ranking = []
    for hook, stats in hook_stats.items():
        ranking.append({
            "hook": hook,
            "avg_views": round(sum(stats["views"]) / len(stats["views"]), 1),
            "avg_likes": round(sum(stats["likes"]) / len(stats["likes"]), 1),
            "count": stats["count"],
        })
    ranking.sort(key=lambda x: x["avg_views"], reverse=True)
    return ranking if ranking else None


def _genre_times(profile: str | None, day_of_week: int | None) -> list[tuple[int, int]] | None:
    """Aggregate optimal times from same-genre profiles' CSV data."""
    from .config import get_settings
    from .csv_analyzer import HourStats

    genre_profiles = _get_genre_profiles(profile)
    if not genre_profiles:
        return None

    # Aggregate hour stats across genre profiles
    hour_agg: dict[int, dict] = {h: {"views": 0, "count": 0} for h in range(24)}
    total_posts = 0

    for pname in genre_profiles:
        try:
            settings = get_settings(profile=pname)
            csv_path = None
            if settings.csv_path:
                csv_path = Path(settings.csv_path)
            elif settings.reference_csv_path.exists():
                csv_path = settings.reference_csv_path

            if not csv_path or not csv_path.exists():
                continue

            from .csv_analyzer import analyze_optimal_times

            result = analyze_optimal_times(csv_path)
            total_posts += result.total_posts
            for h, hs in result.hour_stats.items():
                hour_agg[h]["views"] += hs.total_views
                hour_agg[h]["count"] += hs.post_count
        except Exception as e:
            logger.debug("Genre times for %s failed: %s", pname, e)

    if total_posts < _MIN_GENRE_POSTS:
        return None

    # Rank hours by average views
    scored = []
    for h, agg in hour_agg.items():
        if agg["count"] > 0:
            scored.append((h, agg["views"] / agg["count"], agg["count"]))
    scored.sort(key=lambda x: x[1], reverse=True)

    # Select top 5 with 2-hour gap
    from .csv_analyzer import _hour_distance

    selected: list[int] = []
    for h, _, _ in scored:
        if len(selected) >= 5:
            break
        if all(_hour_distance(h, s) >= 2 for s in selected):
            selected.append(h)
    selected.sort()
    return [(h, 0) for h in selected] if selected else None


def _genre_examples(profile: str | None, n: int) -> list[str] | None:
    """Collect reference examples from same-genre profiles."""
    from .config import get_settings
    from .post_history import PostHistory

    genre_profiles = _get_genre_profiles(profile)
    if not genre_profiles:
        return None

    all_posts: list[tuple[int, str]] = []  # (views, text)

    for pname in genre_profiles:
        try:
            settings = get_settings(profile=pname)
            # From reference_posts.json
            examples = settings.load_reference_examples(n=20)
            # These don't have views info, add them with a default score
            for ex in examples:
                all_posts.append((1000, ex))

            # From post_history (with actual views)
            history = PostHistory(history_path=settings.profile_dir / "post_history.json")
            for record in history.get_all():
                if record.has_metrics and record.views > 0:
                    all_posts.append((record.views, record.text))
        except Exception as e:
            logger.debug("Genre examples for %s failed: %s", pname, e)

    if not all_posts:
        return None

    # Return top N by views
    all_posts.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in all_posts[:n]]


# ── Universal Tier ────────────────────────────────────────────


def _universal_hooks() -> list[dict] | None:
    """Get hook ranking from cross-genre shared intelligence."""
    from .shared_intelligence import load_shared_insights

    data = load_shared_insights()
    if not data:
        return None

    hooks = data.get("hook_structures", {}).get("overall", {})
    if not hooks:
        return None

    ranking = []
    for name, stats in hooks.items():
        count = stats.get("post_count", 0)
        if count < 3:
            continue
        ranking.append({
            "hook": name,
            "avg_views": stats.get("avg_views", 0),
            "avg_likes": stats.get("avg_likes", 0),
            "count": count,
        })
    ranking.sort(key=lambda x: x["avg_views"], reverse=True)
    return ranking if ranking else None


def _universal_times(day_of_week: int | None) -> list[tuple[int, int]] | None:
    """Get optimal times from cross-genre shared intelligence."""
    from .shared_intelligence import load_shared_insights

    data = load_shared_insights()
    if not data:
        return None

    heatmap = data.get("time_heatmap", {})
    if not heatmap:
        return None

    from .csv_analyzer import _hour_distance

    # Aggregate by hour (across all days, or for specific day)
    hour_agg: dict[int, dict] = {h: {"views": 0, "count": 0} for h in range(24)}
    _DOW_NAMES = ["月", "火", "水", "木", "金", "土", "日"]

    for key, stats in heatmap.items():
        count = stats.get("post_count", 0)
        if count == 0:
            continue
        parts = key.split(":")
        if len(parts) != 2:
            continue
        day_name, hour_str = parts
        hour = int(hour_str)

        if day_of_week is not None:
            if day_name != _DOW_NAMES[day_of_week]:
                continue

        hour_agg[hour]["views"] += stats.get("avg_views", 0) * count
        hour_agg[hour]["count"] += count

    scored = []
    for h, agg in hour_agg.items():
        if agg["count"] > 0:
            scored.append((h, agg["views"] / agg["count"]))
    scored.sort(key=lambda x: x[1], reverse=True)

    selected: list[int] = []
    for h, _ in scored:
        if len(selected) >= 5:
            break
        if all(_hour_distance(h, s) >= 2 for s in selected):
            selected.append(h)
    selected.sort()
    return [(h, 0) for h in selected] if selected else None


def _universal_examples(n: int) -> list[str] | None:
    """Get top-performing example posts from shared intelligence.

    Universal tier doesn't store full post texts (only stats), so this
    returns None — templates/hooks are the main value at this tier.
    """
    return None


# ── Public API ────────────────────────────────────────────────


def resolve_hooks(profile: str | None = None) -> ResolvedHooks:
    """Resolve hook ranking with 3-tier fallback.

    Returns the best available hook data, preferring account > genre > universal.
    """
    # Tier 1: Account's own data
    hooks = _account_hooks(profile)
    if hooks:
        logger.info("Hooks resolved from account data (%d hooks)", len(hooks))
        return ResolvedHooks(tier=DataTier.ACCOUNT, hooks=hooks)

    # Tier 2: Same genre
    hooks = _genre_hooks(profile)
    if hooks:
        logger.info("Hooks resolved from genre data (%d hooks)", len(hooks))
        return ResolvedHooks(tier=DataTier.GENRE, hooks=hooks)

    # Tier 3: Universal
    hooks = _universal_hooks()
    if hooks:
        logger.info("Hooks resolved from universal data (%d hooks)", len(hooks))
        return ResolvedHooks(tier=DataTier.UNIVERSAL, hooks=hooks)

    # No data at all — return empty
    logger.warning("No hook data available at any tier")
    return ResolvedHooks(tier=DataTier.UNIVERSAL, hooks=[])


def resolve_times(
    profile: str | None = None,
    day_of_week: int | None = None,
) -> ResolvedTimes:
    """Resolve optimal posting times with 3-tier fallback."""
    # Tier 1: Account
    slots = _account_times(profile, day_of_week)
    if slots:
        logger.info("Times resolved from account data")
        return ResolvedTimes(tier=DataTier.ACCOUNT, slots=slots)

    # Tier 2: Genre
    slots = _genre_times(profile, day_of_week)
    if slots:
        logger.info("Times resolved from genre data")
        return ResolvedTimes(tier=DataTier.GENRE, slots=slots)

    # Tier 3: Universal
    slots = _universal_times(day_of_week)
    if slots:
        logger.info("Times resolved from universal data")
        return ResolvedTimes(tier=DataTier.UNIVERSAL, slots=slots)

    # Default fallback
    logger.warning("No time data available — using default schedule")
    return ResolvedTimes(
        tier=DataTier.UNIVERSAL,
        slots=[(8, 0), (11, 0), (14, 0), (18, 0), (21, 0)],
    )


def resolve_examples(
    profile: str | None = None,
    n: int = 5,
) -> ResolvedExamples:
    """Resolve reference post examples with 3-tier fallback."""
    # Tier 1: Account
    examples = _account_examples(profile, n)
    if examples:
        logger.info("Examples resolved from account data (%d posts)", len(examples))
        return ResolvedExamples(tier=DataTier.ACCOUNT, examples=examples)

    # Tier 2: Genre
    examples = _genre_examples(profile, n)
    if examples:
        logger.info("Examples resolved from genre data (%d posts)", len(examples))
        return ResolvedExamples(tier=DataTier.GENRE, examples=examples)

    # Tier 3: Universal (no full texts available)
    logger.info("No reference examples available")
    return ResolvedExamples(tier=DataTier.UNIVERSAL, examples=[])


def resolve_phase(profile: str | None = None) -> tuple[str, DataTier]:
    """Determine the autopilot phase considering fallback data.

    If the account has no data but genre/universal does, use 'bootstrap'
    phase for the account but supply genre/universal hooks for guidance.

    Returns:
        (phase, data_tier) — phase is account-level, data_tier indicates
        which tier is providing hook/time intelligence.
    """
    from .config import get_settings
    from .post_history import PostHistory

    try:
        settings = get_settings(profile=profile)
        history = PostHistory(history_path=settings.profile_dir / "post_history.json")
        collected = [r for r in history.get_all() if r.has_metrics]
        account_count = len(collected)
    except Exception:
        account_count = 0

    # Phase is always based on account's own post count
    if account_count < 100:
        phase = "bootstrap"
    elif account_count < 200:
        phase = "learning"
    else:
        phase = "optimized"

    # But the data tier tells us where intelligence comes from
    if account_count >= _MIN_ACCOUNT_POSTS:
        return phase, DataTier.ACCOUNT

    hooks = _genre_hooks(profile)
    if hooks:
        return phase, DataTier.GENRE

    hooks = _universal_hooks()
    if hooks:
        return phase, DataTier.UNIVERSAL

    return phase, DataTier.UNIVERSAL


@dataclass
class ResolvedFollowerInsights:
    """Resolved follower growth insights with source tier."""

    tier: DataTier
    correlation: Any | None = None  # FollowerCorrelation or None
    recent_delta: float = 0.0  # avg daily follower change (last 30 days)
    snapshot_count: int = 0


def _account_follower_insights(profile: str | None) -> dict | None:
    """Get follower insights from this account's own FollowerTracker."""
    from .config import get_settings
    from .follower_tracker import FollowerTracker

    try:
        settings = get_settings(profile=profile)
        tracker = FollowerTracker(tracker_path=settings.profile_dir / "follower_snapshots.json")
        if tracker.count < 5:
            return None
        recent = tracker.get_recent(days=30)
        avg_delta = sum(s.delta for s in recent) / len(recent) if recent else 0
        correlation = tracker.analyze_correlation()
        return {
            "correlation": correlation,
            "recent_delta": avg_delta,
            "snapshot_count": tracker.count,
        }
    except Exception as e:
        logger.debug("Account follower insights unavailable: %s", e)
        return None


def _genre_follower_insights(profile: str | None) -> dict | None:
    """Aggregate follower insights from same-genre profiles."""
    from .config import get_settings
    from .follower_tracker import FollowerTracker

    genre_profiles = _get_genre_profiles(profile)
    if not genre_profiles:
        return None

    total_delta = 0.0
    total_days = 0
    total_snapshots = 0

    for pname in genre_profiles:
        try:
            settings = get_settings(profile=pname)
            tracker = FollowerTracker(
                tracker_path=settings.profile_dir / "follower_snapshots.json"
            )
            recent = tracker.get_recent(days=30)
            for s in recent:
                total_delta += s.delta
                total_days += 1
            total_snapshots += tracker.count
        except Exception:
            continue

    if total_days < 5:
        return None

    return {
        "correlation": None,
        "recent_delta": total_delta / total_days,
        "snapshot_count": total_snapshots,
    }


def resolve_follower_insights(
    profile: str | None = None,
) -> ResolvedFollowerInsights:
    """Resolve follower growth insights with 3-tier fallback."""
    # Tier 1: Account
    data = _account_follower_insights(profile)
    if data:
        logger.info("Follower insights resolved from account data")
        return ResolvedFollowerInsights(
            tier=DataTier.ACCOUNT,
            correlation=data["correlation"],
            recent_delta=data["recent_delta"],
            snapshot_count=data["snapshot_count"],
        )

    # Tier 2: Genre
    data = _genre_follower_insights(profile)
    if data:
        logger.info("Follower insights resolved from genre data")
        return ResolvedFollowerInsights(
            tier=DataTier.GENRE,
            recent_delta=data["recent_delta"],
            snapshot_count=data["snapshot_count"],
        )

    # No data
    logger.info("No follower insights available")
    return ResolvedFollowerInsights(tier=DataTier.UNIVERSAL)


def get_resolution_status(profile: str | None = None) -> dict:
    """Get a summary of what data is available at each tier.

    Useful for dashboard display and debugging.
    """
    from .post_history import PostHistory

    status: dict = {"account": {}, "genre": {}, "universal": {}}

    # Account
    try:
        from .config import get_settings as _gs_status
        _status_settings = _gs_status(profile=profile)
        history = PostHistory(history_path=_status_settings.profile_dir / "post_history.json")
        collected = [r for r in history.get_all() if r.has_metrics]
        status["account"] = {
            "total_posts": history.count,
            "collected_posts": len(collected),
            "has_data": len(collected) >= _MIN_ACCOUNT_POSTS,
        }
    except Exception:
        status["account"] = {"total_posts": 0, "collected_posts": 0, "has_data": False}

    # Genre
    genre_profiles = _get_genre_profiles(profile)
    status["genre"] = {
        "profile_count": len(genre_profiles),
        "profiles": genre_profiles,
        "has_data": _genre_hooks(profile) is not None,
    }

    # Universal
    from .shared_intelligence import load_shared_insights

    data = load_shared_insights()
    status["universal"] = {
        "has_data": bool(data),
        "total_posts": data.get("metadata", {}).get("total_posts", 0) if data else 0,
        "genres": data.get("metadata", {}).get("genres", []) if data else [],
    }

    # Current resolution
    hooks_resolved = resolve_hooks(profile)
    times_resolved = resolve_times(profile)
    examples_resolved = resolve_examples(profile)
    follower_resolved = resolve_follower_insights(profile)

    status["current_resolution"] = {
        "hooks_tier": hooks_resolved.tier.value,
        "times_tier": times_resolved.tier.value,
        "examples_tier": examples_resolved.tier.value,
        "follower_tier": follower_resolved.tier.value,
        "follower_recent_delta": follower_resolved.recent_delta,
    }

    return status
