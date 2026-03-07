"""Analyze Threads CSV export data to find optimal posting times."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


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
    def score(self) -> float:
        """Composite score: weighted combination of views, likes, and engagement rate."""
        if not self.post_count:
            return 0.0
        return (self.avg_views * 0.3) + (self.avg_likes * 50) + (self.avg_engagement * 1000)


@dataclass
class CSVAnalysisResult:
    """Result of CSV-based time analysis."""

    total_posts: int = 0
    hour_stats: dict[int, HourStats] = field(default_factory=dict)
    top_hours: list[int] = field(default_factory=list)

    def get_optimal_slots(self, n: int = 5) -> list[tuple[int, int]]:
        """Return top N (hour, minute) tuples sorted by score.

        Ensures at least 2 hours gap between slots for better spread.
        """
        ranked = sorted(
            [h for h in self.hour_stats.values() if h.post_count > 0],
            key=lambda h: h.score,
            reverse=True,
        )

        selected: list[int] = []
        for hs in ranked:
            if len(selected) >= n:
                break
            # Ensure minimum 2-hour gap between selected slots
            if all(abs(hs.hour - s) >= 2 for s in selected):
                selected.append(hs.hour)

        selected.sort()
        return [(h, 0) for h in selected]


def parse_csv(csv_path: str | Path) -> list[dict]:
    """Parse the Threads CSV export file.

    Handles multi-line content fields correctly.
    """
    path = Path(csv_path)
    rows = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def analyze_optimal_times(csv_path: str | Path) -> CSVAnalysisResult:
    """Analyze CSV data to find optimal posting hours.

    Args:
        csv_path: Path to the Threads CSV export.

    Returns:
        CSVAnalysisResult with per-hour stats and top hours.
    """
    rows = parse_csv(csv_path)
    result = CSVAnalysisResult()

    # Initialize all hours
    for h in range(24):
        result.hour_stats[h] = HourStats(hour=h)

    for row in rows:
        date_str = row.get("Date", "").strip()
        if not date_str:
            continue

        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        except ValueError:
            continue

        hour = dt.hour
        views = _safe_int(row.get("Views", "0"))
        likes = _safe_int(row.get("Likes", "0"))
        replies = _safe_int(row.get("Replies", "0"))
        engagement = _safe_float(row.get("Engagement", "0"))

        hs = result.hour_stats[hour]
        hs.post_count += 1
        hs.total_views += views
        hs.total_likes += likes
        hs.total_replies += replies
        hs.total_engagement += engagement
        result.total_posts += 1

    # Rank hours by score (only those with posts)
    active_hours = [hs for hs in result.hour_stats.values() if hs.post_count > 0]
    active_hours.sort(key=lambda h: h.score, reverse=True)
    result.top_hours = [hs.hour for hs in active_hours]

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
