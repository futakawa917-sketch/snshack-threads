"""Analyze Threads CSV export data for optimal posting strategy.

Covers:
- Time-of-day analysis with minimum sample size filtering
- Day-of-week × hour 2D analysis
- Content pattern analysis (hooks, length, themes)
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Minimum posts in a time bucket to consider it statistically reliable
MIN_SAMPLE_SIZE = 5

# Day-of-week names (Monday=0)
_DOW_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


# ── Time Stats ───────────────────────────────────────────────


@dataclass
class HourStats:
    """Aggregated stats for a specific hour of day."""

    hour: int
    post_count: int = 0
    total_views: int = 0
    total_likes: int = 0
    total_replies: int = 0
    total_engagement: float = 0.0

    @property
    def avg_views(self) -> float:
        return self.total_views / self.post_count if self.post_count else 0

    @property
    def avg_likes(self) -> float:
        return self.total_likes / self.post_count if self.post_count else 0

    @property
    def avg_engagement(self) -> float:
        return self.total_engagement / self.post_count if self.post_count else 0

    @property
    def reliable(self) -> bool:
        """Has enough data points to be statistically meaningful."""
        return self.post_count >= MIN_SAMPLE_SIZE

    @property
    def score(self) -> float:
        """Composite score: weighted combination of views, likes, and engagement rate.

        Penalizes low-sample-size buckets to avoid noise dominating.
        """
        if not self.post_count:
            return 0.0
        raw = (self.avg_views * 0.3) + (self.avg_likes * 50) + (self.avg_engagement * 1000)
        if not self.reliable:
            # Discount proportionally: 1 post = 20% weight, 4 posts = 80%
            raw *= self.post_count / MIN_SAMPLE_SIZE
        return raw


@dataclass
class DayHourStats:
    """Stats for a specific day-of-week + hour combination."""

    day: int  # 0=Monday, 6=Sunday
    hour: int
    post_count: int = 0
    total_views: int = 0
    total_likes: int = 0
    total_replies: int = 0
    total_engagement: float = 0.0

    @property
    def day_name(self) -> str:
        return _DOW_NAMES[self.day]

    @property
    def avg_views(self) -> float:
        return self.total_views / self.post_count if self.post_count else 0

    @property
    def avg_likes(self) -> float:
        return self.total_likes / self.post_count if self.post_count else 0

    @property
    def avg_engagement(self) -> float:
        return self.total_engagement / self.post_count if self.post_count else 0

    @property
    def score(self) -> float:
        if not self.post_count:
            return 0.0
        raw = (self.avg_views * 0.3) + (self.avg_likes * 50) + (self.avg_engagement * 1000)
        if self.post_count < 3:
            raw *= self.post_count / 3
        return raw


# ── Content Pattern Stats ────────────────────────────────────


@dataclass
class ContentPattern:
    """Analysis of what content patterns drive performance."""

    # Length buckets: short (<100), medium (100-300), long (300+)
    length_buckets: dict[str, HourStats] = field(default_factory=dict)
    # Hook patterns (first line patterns)
    hook_patterns: dict[str, HourStats] = field(default_factory=dict)
    # Top performing posts for reference
    top_posts: list[dict] = field(default_factory=list)
    # Posts with external links (for tracking penalty)
    link_post_avg_views: float = 0.0
    no_link_post_avg_views: float = 0.0


# ── Main Result ──────────────────────────────────────────────


@dataclass
class CSVAnalysisResult:
    """Result of CSV-based comprehensive analysis."""

    total_posts: int = 0
    hour_stats: dict[int, HourStats] = field(default_factory=dict)
    day_hour_stats: dict[tuple[int, int], DayHourStats] = field(default_factory=dict)
    top_hours: list[int] = field(default_factory=list)
    content: ContentPattern = field(default_factory=ContentPattern)

    def get_optimal_slots(self, n: int = 5) -> list[tuple[int, int]]:
        """Return top N (hour, minute) tuples sorted by score.

        Only considers statistically reliable hours (>= MIN_SAMPLE_SIZE).
        Falls back to unreliable hours if not enough reliable ones exist.
        Ensures at least 2 hours gap between slots for better spread.
        """
        reliable = sorted(
            [h for h in self.hour_stats.values() if h.reliable],
            key=lambda h: h.score,
            reverse=True,
        )
        unreliable = sorted(
            [h for h in self.hour_stats.values() if h.post_count > 0 and not h.reliable],
            key=lambda h: h.score,
            reverse=True,
        )
        ranked = reliable + unreliable

        selected: list[int] = []
        for hs in ranked:
            if len(selected) >= n:
                break
            if all(abs(hs.hour - s) >= 2 for s in selected):
                selected.append(hs.hour)

        selected.sort()
        return [(h, 0) for h in selected]

    def get_optimal_slots_for_day(self, day_of_week: int, n: int = 5) -> list[tuple[int, int]]:
        """Return top N slots for a specific day of week.

        Falls back to overall hour analysis if not enough day-specific data.
        """
        day_stats = [
            dh for (d, h), dh in self.day_hour_stats.items()
            if d == day_of_week and dh.post_count > 0
        ]
        day_stats.sort(key=lambda dh: dh.score, reverse=True)

        selected: list[int] = []
        for dh in day_stats:
            if len(selected) >= n:
                break
            if all(abs(dh.hour - s) >= 2 for s in selected):
                selected.append(dh.hour)

        # If not enough day-specific slots, fill from overall
        if len(selected) < n:
            overall = self.get_optimal_slots(n=n)
            for h, m in overall:
                if len(selected) >= n:
                    break
                if all(abs(h - s) >= 2 for s in selected):
                    selected.append(h)

        selected.sort()
        return [(h, 0) for h in selected]


# ── CSV Parsing ──────────────────────────────────────────────


def parse_csv(csv_path: str | Path) -> list[dict]:
    """Parse the Threads CSV export file."""
    path = Path(csv_path)
    rows = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


# ── Hook Pattern Detection ───────────────────────────────────

_HOOK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("数字訴求", re.compile(r"[\d,]+[万億円%]|最大|[0-9]{2,}")),
    ("疑問形", re.compile(r"[？\?]|知ってた|ですか")),
    ("危機感", re.compile(r"やばい|ヤバい|危険|損|知らない|放置|倒産|リスク")),
    ("限定感", re.compile(r"今だけ|いよいよ|緊急|速報|激アツ|到来")),
    ("断言", re.compile(r"です[。\n]|ください|べき|一択|必見")),
    ("呼びかけ", re.compile(r"必見|オーナー|社長|経営者|あなた")),
]


def _detect_hooks(text: str) -> list[str]:
    """Detect hook patterns in the first line of a post."""
    first_line = text.split("\n")[0] if text else ""
    return [name for name, pat in _HOOK_PATTERNS if pat.search(first_line)]


def _text_length_bucket(text: str) -> str:
    length = len(text)
    if length < 100:
        return "short"
    elif length <= 300:
        return "medium"
    else:
        return "long"


# ── External Link Detection ──────────────────────────────────

_LINK_PATTERN = re.compile(r"https?://|LINE|ライン|リンク.*(プロフ|概要)|固定.*(見て|チェック)|プロフ.*(リンク|URL)")


def has_external_promotion(text: str) -> bool:
    """Detect external link/LINE/profile link promotion in text."""
    return bool(_LINK_PATTERN.search(text))


# ── Main Analysis Functions ──────────────────────────────────


def analyze_optimal_times(csv_path: str | Path) -> CSVAnalysisResult:
    """Comprehensive analysis: time, day-of-week, and content patterns."""
    rows = parse_csv(csv_path)
    result = CSVAnalysisResult()

    # Initialize all hours and day×hour combos
    for h in range(24):
        result.hour_stats[h] = HourStats(hour=h)
    for d in range(7):
        for h in range(24):
            result.day_hour_stats[(d, h)] = DayHourStats(day=d, hour=h)

    # Content analysis accumulators
    length_buckets: dict[str, list[dict]] = {"short": [], "medium": [], "long": []}
    hook_accum: dict[str, list[dict]] = {}
    link_views: list[int] = []
    no_link_views: list[int] = []
    all_posts_with_metrics: list[dict] = []

    for row in rows:
        date_str = row.get("Date", "").strip()
        if not date_str:
            continue

        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        except ValueError:
            continue

        hour = dt.hour
        dow = dt.weekday()
        views = _safe_int(row.get("Views", "0"))
        likes = _safe_int(row.get("Likes", "0"))
        replies = _safe_int(row.get("Replies", "0"))
        engagement = _safe_float(row.get("Engagement", "0"))
        content = row.get("Content", "")

        # Hour stats
        hs = result.hour_stats[hour]
        hs.post_count += 1
        hs.total_views += views
        hs.total_likes += likes
        hs.total_replies += replies
        hs.total_engagement += engagement

        # Day × Hour stats
        dh = result.day_hour_stats[(dow, hour)]
        dh.post_count += 1
        dh.total_views += views
        dh.total_likes += likes
        dh.total_replies += replies
        dh.total_engagement += engagement

        result.total_posts += 1

        # Content analysis
        post_data = {
            "date": date_str, "content": content, "views": views,
            "likes": likes, "replies": replies, "engagement": engagement,
        }
        all_posts_with_metrics.append(post_data)

        bucket = _text_length_bucket(content)
        length_buckets[bucket].append(post_data)

        hooks = _detect_hooks(content)
        for hook_name in hooks:
            hook_accum.setdefault(hook_name, []).append(post_data)

        if has_external_promotion(content):
            link_views.append(views)
        else:
            no_link_views.append(views)

    # Rank hours by score (reliable first)
    active_hours = [hs for hs in result.hour_stats.values() if hs.post_count > 0]
    active_hours.sort(key=lambda h: h.score, reverse=True)
    result.top_hours = [hs.hour for hs in active_hours]

    # Build content patterns
    for bucket_name, posts in length_buckets.items():
        bhs = HourStats(hour=0, post_count=len(posts))
        bhs.total_views = sum(p["views"] for p in posts)
        bhs.total_likes = sum(p["likes"] for p in posts)
        bhs.total_replies = sum(p["replies"] for p in posts)
        bhs.total_engagement = sum(p["engagement"] for p in posts)
        result.content.length_buckets[bucket_name] = bhs

    for hook_name, posts in hook_accum.items():
        hhs = HourStats(hour=0, post_count=len(posts))
        hhs.total_views = sum(p["views"] for p in posts)
        hhs.total_likes = sum(p["likes"] for p in posts)
        hhs.total_replies = sum(p["replies"] for p in posts)
        hhs.total_engagement = sum(p["engagement"] for p in posts)
        result.content.hook_patterns[hook_name] = hhs

    result.content.top_posts = sorted(
        all_posts_with_metrics, key=lambda p: p["views"], reverse=True
    )[:10]

    result.content.link_post_avg_views = (
        sum(link_views) / len(link_views) if link_views else 0
    )
    result.content.no_link_post_avg_views = (
        sum(no_link_views) / len(no_link_views) if no_link_views else 0
    )

    return result


def _safe_int(val: str) -> int:
    try:
        return int(val.strip().replace(",", ""))
    except (ValueError, AttributeError):
        return 0


def _safe_float(val: str) -> float:
    try:
        return float(val.strip().strip('"').replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0
