"""Early velocity detection — identifies "buzz seeds" from first-hour metrics.

Analyzes posts in the 1-6 hour window after publishing to predict which ones
will go viral. Uses historical percentile comparisons to classify posts as
"likely_buzz", "moderate", or "slow", then feeds insights back into the
learning loop to improve next-day content strategy.

Integration:
  - autopilot.py calls feed_velocity_to_learning() for real-time optimization
  - CLI `collect-early` populates snapshots, `velocity` shows the report
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .post_history import PostHistory, PostRecord

logger = logging.getLogger(__name__)


# ── Default thresholds (used when <10 posts with snapshots) ──

_DEFAULT_THRESHOLDS: dict[str, dict[str, int]] = {
    "1h": {"buzz": 500, "moderate": 200},
    "3h": {"buzz": 1500, "moderate": 600},
    "6h": {"buzz": 3000, "moderate": 1200},
}


@dataclass
class VelocityScore:
    """Velocity assessment for a single post."""

    post_id: str  # scheduled_at ISO string as unique identifier
    text_preview: str  # first 50 chars
    hours_since_post: float
    views: int
    views_per_hour: float
    percentile: float  # 0-100, compared to historical posts at same age
    prediction: str  # "likely_buzz" | "moderate" | "slow"
    hook_type: str  # detected hook pattern
    post_type: str  # "reach" | "list"


# ── Helper: extract snapshot views at a given elapsed-hour bucket ──


def _snapshot_views_at(record: PostRecord, target_hours: int) -> int | None:
    """Return views from the snapshot closest to *target_hours*.

    Allows ±1h tolerance so a snapshot at elapsed_hours=2 counts for the 3h
    bucket.  Returns ``None`` if no snapshot is close enough.
    """
    best: int | None = None
    best_delta = float("inf")
    for s in record.snapshots:
        delta = abs(s.elapsed_hours - target_hours)
        if delta <= 1 and delta < best_delta:
            best = s.views
            best_delta = delta
    return best


def _detect_hook(text: str) -> str:
    """Detect the primary hook pattern used in *text*."""
    try:
        from .csv_analyzer import _detect_hooks

        hooks = _detect_hooks(text)
        return hooks[0] if hooks else "unknown"
    except Exception:
        return "unknown"


# ── Threshold learning ───────────────────────────────────────


def get_velocity_thresholds(history: PostHistory) -> dict[str, dict[str, int]]:
    """Learn velocity thresholds from historical snapshot data.

    For each time window (1h, 3h, 6h), computes percentile-based cutoffs:
      - "buzz":     top 20%  (P80)
      - "moderate": top 60%  (P40)

    Falls back to ``_DEFAULT_THRESHOLDS`` when fewer than 10 posts have
    snapshot data.

    Returns::

        {
            '1h': {'buzz': 500, 'moderate': 200},
            '3h': {'buzz': 1500, 'moderate': 600},
            '6h': {'buzz': 3000, 'moderate': 1200},
        }
    """
    all_records = history.get_all()
    records_with_snaps = [r for r in all_records if r.snapshots]

    if len(records_with_snaps) < 10:
        return dict(_DEFAULT_THRESHOLDS)

    thresholds: dict[str, dict[str, int]] = {}

    for target_h, key in [(1, "1h"), (3, "3h"), (6, "6h")]:
        views_list: list[int] = []
        for r in records_with_snaps:
            v = _snapshot_views_at(r, target_h)
            if v is not None:
                views_list.append(v)

        if len(views_list) < 5:
            thresholds[key] = dict(_DEFAULT_THRESHOLDS[key])
            continue

        views_list.sort()
        n = len(views_list)
        p80 = views_list[min(int(n * 0.80), n - 1)]
        p40 = views_list[min(int(n * 0.40), n - 1)]

        thresholds[key] = {
            "buzz": max(p80, 1),
            "moderate": max(p40, 1),
        }

    return thresholds


# ── Percentile computation ───────────────────────────────────


def _percentile_of(value: int, sorted_values: list[int]) -> float:
    """Return the percentile rank (0-100) of *value* within *sorted_values*."""
    if not sorted_values:
        return 50.0
    count_below = sum(1 for v in sorted_values if v < value)
    return round(count_below / len(sorted_values) * 100, 1)


def _classify(percentile: float) -> str:
    """Map percentile to prediction label."""
    if percentile >= 80:
        return "likely_buzz"
    elif percentile >= 40:
        return "moderate"
    else:
        return "slow"


# ── Core functions ───────────────────────────────────────────


def calculate_velocity_scores(history: PostHistory) -> list[VelocityScore]:
    """Calculate velocity scores for all recent posts (last 24h).

    Only considers posts that already have at least one snapshot.
    """
    now = datetime.now()
    cutoff = now - timedelta(hours=24)

    # Build historical baseline: views at each hour bucket across all time
    all_records = history.get_all()
    historical: dict[int, list[int]] = {1: [], 3: [], 6: []}
    for r in all_records:
        for target_h in (1, 3, 6):
            v = _snapshot_views_at(r, target_h)
            if v is not None:
                historical[target_h].append(v)

    for key in historical:
        historical[key].sort()

    # Score recent posts
    scores: list[VelocityScore] = []
    for record in all_records:
        if not record.snapshots:
            continue
        try:
            scheduled = datetime.fromisoformat(record.scheduled_at)
        except ValueError:
            continue
        if scheduled < cutoff:
            continue

        hours_since = (now - scheduled).total_seconds() / 3600

        # Pick the most relevant snapshot (latest one)
        latest_snap = max(record.snapshots, key=lambda s: s.elapsed_hours)
        views = latest_snap.views
        elapsed = max(latest_snap.elapsed_hours, 1)
        vph = round(views / elapsed, 1)

        # Determine which bucket to compare against
        if elapsed <= 2:
            bucket = 1
        elif elapsed <= 4:
            bucket = 3
        else:
            bucket = 6

        pct = _percentile_of(views, historical.get(bucket, []))
        prediction = _classify(pct)

        scores.append(
            VelocityScore(
                post_id=record.scheduled_at,
                text_preview=record.text[:50],
                hours_since_post=round(hours_since, 1),
                views=views,
                views_per_hour=vph,
                percentile=pct,
                prediction=prediction,
                hook_type=_detect_hook(record.text),
                post_type=record.post_type,
            )
        )

    # Sort by views_per_hour descending
    scores.sort(key=lambda s: s.views_per_hour, reverse=True)
    return scores


def detect_buzz_seeds(history: PostHistory) -> list[VelocityScore]:
    """Find posts showing early buzz signals (likely_buzz or strong moderate).

    Returns only posts predicted as "likely_buzz" or those in the moderate
    category with views_per_hour above the buzz threshold for their window.
    """
    scores = calculate_velocity_scores(history)
    thresholds = get_velocity_thresholds(history)

    buzz_seeds: list[VelocityScore] = []
    for s in scores:
        if s.prediction == "likely_buzz":
            buzz_seeds.append(s)
            continue

        # Check if moderate post actually exceeds the buzz vph threshold
        if s.hours_since_post <= 2:
            buzz_vph = thresholds["1h"]["buzz"]
        elif s.hours_since_post <= 4:
            buzz_vph = thresholds["3h"]["buzz"] / 3
        else:
            buzz_vph = thresholds["6h"]["buzz"] / 6

        if s.views_per_hour >= buzz_vph:
            buzz_seeds.append(s)

    return buzz_seeds


def feed_velocity_to_learning(history: PostHistory) -> dict:
    """Extract velocity-based learnings to feed into the autopilot loop.

    Analyzes all posts with snapshot data (last 30 days) to determine:
      - Which hook types have the highest early velocity
      - Which posting hours produce the fastest initial spread
      - Which post types (reach vs list) perform better early
      - Current buzz seed count and their characteristics

    Returns a dict with structured insights that autopilot.py can use
    to adjust content type allocation and hook selection.
    """
    records = [r for r in history.get_recent(days=30) if r.snapshots]

    if not records:
        return {
            "has_data": False,
            "message": "スナップショットデータなし。collect-earlyを実行してください。",
        }

    # ── Hook velocity ranking ──
    hook_velocity: dict[str, list[float]] = {}
    for r in records:
        hook = _detect_hook(r.text)
        if hook == "unknown":
            continue
        for s in r.snapshots:
            vph = s.views / max(s.elapsed_hours, 1)
            hook_velocity.setdefault(hook, []).append(vph)

    hook_ranking = sorted(
        [
            {
                "hook": hook,
                "avg_vph": round(sum(vphs) / len(vphs), 1),
                "sample_count": len(vphs),
            }
            for hook, vphs in hook_velocity.items()
        ],
        key=lambda x: x["avg_vph"],
        reverse=True,
    )

    # ── Time-of-day velocity ──
    hour_velocity: dict[int, list[float]] = {}
    for r in records:
        try:
            hour = datetime.fromisoformat(r.scheduled_at).hour
        except ValueError:
            continue
        for s in r.snapshots:
            vph = s.views / max(s.elapsed_hours, 1)
            hour_velocity.setdefault(hour, []).append(vph)

    best_hours = sorted(
        [
            {
                "hour": h,
                "avg_vph": round(sum(vphs) / len(vphs), 1),
                "sample_count": len(vphs),
            }
            for h, vphs in hour_velocity.items()
            if len(vphs) >= 2
        ],
        key=lambda x: x["avg_vph"],
        reverse=True,
    )

    # ── Post type comparison ──
    type_velocity: dict[str, list[float]] = {}
    for r in records:
        for s in r.snapshots:
            vph = s.views / max(s.elapsed_hours, 1)
            type_velocity.setdefault(r.post_type, []).append(vph)

    type_comparison = {
        ptype: round(sum(vphs) / len(vphs), 1)
        for ptype, vphs in type_velocity.items()
    }

    # ── Current buzz seeds ──
    buzz = detect_buzz_seeds(history)

    # ── Actionable recommendations ──
    recommendations: list[str] = []

    if hook_ranking:
        top_hook = hook_ranking[0]["hook"]
        recommendations.append(f"最速フック: {top_hook} (平均 {hook_ranking[0]['avg_vph']} views/h)")

    if best_hours:
        best_h = best_hours[0]["hour"]
        recommendations.append(f"最速時間帯: {best_h}時 (平均 {best_hours[0]['avg_vph']} views/h)")

    if type_comparison:
        best_type = max(type_comparison, key=type_comparison.get)  # type: ignore[arg-type]
        recommendations.append(f"初速が速いタイプ: {best_type} ({type_comparison[best_type]} views/h)")

    if buzz:
        recommendations.append(f"現在のバズ候補: {len(buzz)}件")

    return {
        "has_data": True,
        "total_tracked": len(records),
        "hook_ranking": hook_ranking[:10],
        "best_hours": best_hours[:5],
        "type_comparison": type_comparison,
        "buzz_seeds": [
            {
                "text_preview": s.text_preview,
                "views_per_hour": s.views_per_hour,
                "prediction": s.prediction,
                "hook_type": s.hook_type,
            }
            for s in buzz
        ],
        "recommendations": recommendations,
        # Structured hints for autopilot integration
        "boost_hooks": [h["hook"] for h in hook_ranking[:3]] if hook_ranking else [],
        "preferred_type": max(type_comparison, key=type_comparison.get) if type_comparison else "reach",  # type: ignore[arg-type]
    }


def generate_velocity_report(history: PostHistory) -> str:
    """Generate a human-readable velocity report for CLI output.

    Covers:
      - Current thresholds (learned or default)
      - Recent posts with velocity scores
      - Buzz seed alerts
      - Actionable insights summary
    """
    lines: list[str] = []

    # Header
    lines.append("=" * 60)
    lines.append("  Early Velocity Report")
    lines.append("=" * 60)
    lines.append("")

    # Thresholds
    thresholds = get_velocity_thresholds(history)
    all_with_snaps = [r for r in history.get_all() if r.snapshots]
    threshold_source = "学習済み" if len(all_with_snaps) >= 10 else "デフォルト"
    lines.append(f"[閾値: {threshold_source} ({len(all_with_snaps)}件のデータ)]")
    for window, vals in sorted(thresholds.items()):
        lines.append(f"  {window}: buzz >= {vals['buzz']:,} views, moderate >= {vals['moderate']:,} views")
    lines.append("")

    # Recent velocity scores
    scores = calculate_velocity_scores(history)
    if not scores:
        lines.append("直近24時間のスナップショットデータなし。")
        lines.append("投稿の1-3時間後に `snshack collect-early` を実行してください。")
        lines.append("")
    else:
        lines.append(f"[直近24時間: {len(scores)}件]")
        lines.append(f"{'投稿':<30} {'タイプ':>4} {'時間':>4} {'Views':>8} {'VPH':>8} {'%ile':>6} {'判定':>10}")
        lines.append("-" * 76)
        for s in scores:
            preview = s.text_preview[:28].ljust(28)
            ptype = "R" if s.post_type == "reach" else "L"
            pred_label = {
                "likely_buzz": "** BUZZ **",
                "moderate": "moderate",
                "slow": "slow",
            }.get(s.prediction, s.prediction)
            lines.append(
                f"{preview}  {ptype:>4}  {s.hours_since_post:4.1f}h {s.views:>8,} {s.views_per_hour:>8.0f} {s.percentile:>5.0f}% {pred_label:>10}"
            )
        lines.append("")

    # Buzz seeds alert
    buzz = detect_buzz_seeds(history)
    if buzz:
        lines.append(f"[BUZZ ALERT: {len(buzz)}件のバズ候補を検出]")
        for s in buzz:
            lines.append(f"  -> {s.text_preview[:40]}... ({s.views_per_hour:.0f} views/h, {s.hook_type})")
        lines.append("")

    # Learning insights
    insights = feed_velocity_to_learning(history)
    if insights.get("has_data"):
        lines.append("[学習インサイト]")
        for rec in insights.get("recommendations", []):
            lines.append(f"  * {rec}")
        lines.append("")

        if insights.get("hook_ranking"):
            lines.append("[フック別初速ランキング]")
            for i, h in enumerate(insights["hook_ranking"][:5], 1):
                lines.append(f"  {i}. {h['hook']}: {h['avg_vph']} views/h ({h['sample_count']}件)")
            lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)
