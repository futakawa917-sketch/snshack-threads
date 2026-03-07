"""Post history tracking — records scheduled posts and collects performance.

Flow:
1. Post is scheduled → record_scheduled() saves it to history
2. After publishing → collect_performance() fetches views/engagement from Metricool
3. CLI review command → shows performance with views for each post

Data is stored as JSON in ~/.snshack-threads/post_history.json
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import get_settings


@dataclass
class PostRecord:
    """A single post's lifecycle record."""

    text: str
    scheduled_at: str  # ISO format datetime when post is scheduled to publish
    created_at: str = ""  # When the schedule was created
    status: str = "scheduled"  # scheduled | published | collected

    # Performance metrics (filled by collect_performance)
    views: int = 0
    likes: int = 0
    replies: int = 0
    reposts: int = 0
    quotes: int = 0
    engagement: float = 0.0

    # Tracking
    metricool_response: dict = field(default_factory=dict)
    collected_at: str = ""  # When metrics were last collected

    @property
    def total_interactions(self) -> int:
        return self.likes + self.replies + self.reposts + self.quotes

    @property
    def has_metrics(self) -> bool:
        return self.views > 0 or self.likes > 0

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> PostRecord:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class PostHistory:
    """Persistent post history manager."""

    def __init__(self, history_path: Path | None = None) -> None:
        if history_path is None:
            settings = get_settings()
            self._path = settings.data_dir / "post_history.json"
        else:
            self._path = history_path
        self._records: list[PostRecord] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._records = [PostRecord.from_dict(r) for r in data]
            except (json.JSONDecodeError, KeyError):
                self._records = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([r.to_dict() for r in self._records], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def record_scheduled(
        self,
        text: str,
        publish_at: datetime,
        metricool_response: dict | None = None,
    ) -> PostRecord:
        """Record a newly scheduled post."""
        record = PostRecord(
            text=text,
            scheduled_at=publish_at.isoformat(),
            created_at=datetime.now().isoformat(),
            metricool_response=metricool_response or {},
        )
        self._records.append(record)
        self._save()
        return record

    def get_pending_collection(self, min_age_hours: int = 24) -> list[PostRecord]:
        """Get posts that need performance data collection.

        Only returns posts whose scheduled time is at least min_age_hours ago
        (need time for views to accumulate).
        """
        cutoff = datetime.now() - timedelta(hours=min_age_hours)
        return [
            r for r in self._records
            if r.status in ("scheduled", "published")
            and datetime.fromisoformat(r.scheduled_at) < cutoff
        ]

    def update_metrics(
        self,
        record: PostRecord,
        views: int,
        likes: int,
        replies: int,
        reposts: int,
        quotes: int,
        engagement: float,
    ) -> None:
        """Update a post record with collected performance metrics."""
        record.views = views
        record.likes = likes
        record.replies = replies
        record.reposts = reposts
        record.quotes = quotes
        record.engagement = engagement
        record.status = "collected"
        record.collected_at = datetime.now().isoformat()
        self._save()

    def get_all(self) -> list[PostRecord]:
        """Return all post records."""
        return list(self._records)

    def get_recent(self, days: int = 30) -> list[PostRecord]:
        """Return posts from the last N days."""
        cutoff = datetime.now() - timedelta(days=days)
        return [
            r for r in self._records
            if datetime.fromisoformat(r.scheduled_at) > cutoff
        ]

    def get_uncollected(self) -> list[PostRecord]:
        """Return posts that haven't had metrics collected yet."""
        return [r for r in self._records if not r.has_metrics]

    @property
    def count(self) -> int:
        return len(self._records)


def collect_performance(
    history: PostHistory,
    metricool_client: Any,
    min_age_hours: int = 24,
) -> list[PostRecord]:
    """Fetch performance data from Metricool for pending posts.

    Matches posts by comparing scheduled time and text content
    with posts returned from Metricool analytics API.

    Args:
        history: Post history manager.
        metricool_client: MetricoolClient instance.
        min_age_hours: Only collect for posts older than this (default 24h).

    Returns:
        List of records that were updated with metrics.
    """
    pending = history.get_pending_collection(min_age_hours=min_age_hours)
    if not pending:
        return []

    # Determine date range to query
    dates = [datetime.fromisoformat(r.scheduled_at) for r in pending]
    start = min(dates) - timedelta(days=1)
    end = max(dates) + timedelta(days=1)

    api_posts = metricool_client.get_threads_posts(
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
    )

    updated: list[PostRecord] = []

    for record in pending:
        match = _match_post(record, api_posts)
        if match:
            history.update_metrics(
                record,
                views=match.views,
                likes=match.likes,
                replies=match.replies,
                reposts=match.reposts,
                quotes=match.quotes,
                engagement=match.engagement,
            )
            updated.append(record)

    return updated


def _match_post(record: PostRecord, api_posts: list) -> Any | None:
    """Match a history record to an API post by text similarity.

    Uses first 50 chars of text for matching since Metricool may truncate.
    """
    record_text = record.text.strip()[:50]

    for post in api_posts:
        post_text = (post.text or "").strip()[:50]
        if record_text and post_text and record_text == post_text:
            return post

    # Fuzzy fallback: match by scheduled time (within 1 hour)
    try:
        scheduled = datetime.fromisoformat(record.scheduled_at)
    except ValueError:
        return None

    for post in api_posts:
        if post.date and abs((post.date - scheduled).total_seconds()) < 3600:
            return post

    return None
