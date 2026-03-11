"""Content-engagement correlation analyzer.

Analyzes post characteristics (length, emoji usage, line breaks,
question marks, hooks, time of day) and correlates with engagement metrics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class ContentInsight:
    """A single insight about how a content factor correlates with engagement."""

    factor: str            # e.g. "char_count", "has_emoji", "line_count"
    value_range: str       # e.g. "80-120 chars", "with emoji"
    avg_engagement: float
    avg_views: float
    sample_count: int
    lift_vs_average: float  # e.g. 1.3 means 30% above average


_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002600-\U000026FF"  # misc symbols
    "]+",
)

_NUMBER_PATTERN = re.compile(r"\d+[%万億千百]|\d{2,}")


def _has_emoji(text: str) -> bool:
    return bool(_EMOJI_PATTERN.search(text))


def _has_question(text: str) -> bool:
    return "?" in text or "？" in text


def _has_numbers(text: str) -> bool:
    return bool(_NUMBER_PATTERN.search(text))


def _line_count(text: str) -> int:
    return len(text.strip().split("\n"))


def _char_bucket(length: int) -> str:
    if length < 80:
        return "short (<80 chars)"
    elif length <= 150:
        return "medium (80-150 chars)"
    else:
        return "long (150+ chars)"


def _time_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning (5-11)"
    elif 12 <= hour < 17:
        return "afternoon (12-16)"
    elif 17 <= hour < 21:
        return "evening (17-20)"
    else:
        return "night (21-4)"


def _is_weekend(dt: datetime) -> bool:
    return dt.weekday() >= 5


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_scheduled(value: Any) -> datetime | None:
    """Parse a scheduled_at value (str or datetime) to datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _extract_record_fields(record: Any) -> dict | None:
    """Normalize a record (dict or PostRecord dataclass) to a plain dict."""
    if isinstance(record, dict):
        text = record.get("text", "")
        views = _safe_int(record.get("views", 0))
        engagement = _safe_float(record.get("engagement", 0.0))
        scheduled_at = record.get("scheduled_at", "")
    elif hasattr(record, "text"):
        text = getattr(record, "text", "")
        views = _safe_int(getattr(record, "views", 0))
        engagement = _safe_float(getattr(record, "engagement", 0.0))
        scheduled_at = getattr(record, "scheduled_at", "")
    else:
        return None

    if not text:
        return None

    return {
        "text": text,
        "views": views,
        "engagement": engagement,
        "scheduled_at": scheduled_at,
    }


def analyze_content_factors(records: list) -> list[ContentInsight]:
    """Analyze which content factors correlate with higher engagement.

    Factors analyzed:
    - Character count buckets (short <80, medium 80-150, long 150+)
    - Number of lines (1-2 lines vs 3+ lines)
    - Has emoji (yes/no)
    - Has question mark
    - Has numbers/statistics
    - Has line breaks / formatting
    - Time of day (morning/afternoon/evening/night)
    - Day of week (weekday vs weekend)

    Args:
        records: List of PostRecord objects or dicts with fields:
                 text, views, engagement, scheduled_at.

    Returns:
        Sorted list of ContentInsight (highest lift first).
    """
    # Normalize records
    normalized: list[dict] = []
    for r in records:
        fields = _extract_record_fields(r)
        if fields and fields["views"] > 0:
            normalized.append(fields)

    if not normalized:
        return []

    # Global averages
    global_avg_views = sum(r["views"] for r in normalized) / len(normalized)
    global_avg_engagement = sum(r["engagement"] for r in normalized) / len(normalized)

    if global_avg_views == 0:
        global_avg_views = 1.0  # avoid division by zero

    # Bucket accumulators: {(factor, value_range): [records]}
    buckets: dict[tuple[str, str], list[dict]] = {}

    def _add(factor: str, value_range: str, rec: dict) -> None:
        key = (factor, value_range)
        if key not in buckets:
            buckets[key] = []
        buckets[key].append(rec)

    for rec in normalized:
        text = rec["text"]
        char_len = len(text)

        # Character count
        _add("char_count", _char_bucket(char_len), rec)

        # Line count
        lines = _line_count(text)
        line_label = "1-2 lines" if lines <= 2 else "3+ lines"
        _add("line_count", line_label, rec)

        # Emoji
        emoji_label = "with emoji" if _has_emoji(text) else "without emoji"
        _add("has_emoji", emoji_label, rec)

        # Question mark
        q_label = "with question" if _has_question(text) else "without question"
        _add("has_question", q_label, rec)

        # Numbers/statistics
        num_label = "with numbers" if _has_numbers(text) else "without numbers"
        _add("has_numbers", num_label, rec)

        # Line breaks (formatted = has blank line or 3+ lines)
        has_breaks = "\n\n" in text or lines >= 3
        fmt_label = "formatted" if has_breaks else "plain"
        _add("formatting", fmt_label, rec)

        # Time of day
        dt = _parse_scheduled(rec["scheduled_at"])
        if dt:
            _add("time_of_day", _time_of_day(dt.hour), rec)

            # Day of week
            dow_label = "weekend" if _is_weekend(dt) else "weekday"
            _add("day_of_week", dow_label, rec)

    # Build insights
    insights: list[ContentInsight] = []
    min_samples = 3

    for (factor, value_range), bucket_records in buckets.items():
        if len(bucket_records) < min_samples:
            continue

        avg_views = sum(r["views"] for r in bucket_records) / len(bucket_records)
        avg_engagement = sum(r["engagement"] for r in bucket_records) / len(bucket_records)
        lift = avg_views / global_avg_views

        insights.append(ContentInsight(
            factor=factor,
            value_range=value_range,
            avg_engagement=round(avg_engagement, 4),
            avg_views=round(avg_views, 1),
            sample_count=len(bucket_records),
            lift_vs_average=round(lift, 3),
        ))

    # Sort by lift descending
    insights.sort(key=lambda x: x.lift_vs_average, reverse=True)
    return insights


def summarize_top_factors(insights: list[ContentInsight], top_n: int = 5) -> str:
    """Return a human-readable summary of the top content factors.

    Useful for logging or feeding back into the autopilot's prompt context.
    """
    if not insights:
        return "データ不足: コンテンツ要因分析にはもっと投稿データが必要です。"

    lines = ["■ コンテンツ要因分析 (エンゲージメントへの影響):"]
    for insight in insights[:top_n]:
        direction = "↑" if insight.lift_vs_average >= 1.0 else "↓"
        pct = (insight.lift_vs_average - 1.0) * 100
        lines.append(
            f"  {direction} {insight.factor}={insight.value_range}: "
            f"平均{insight.avg_views:.0f}views "
            f"(全体比 {pct:+.0f}%, n={insight.sample_count})"
        )
    return "\n".join(lines)
