"""Post history tracking — records scheduled posts and collects performance.

Flow:
1. Post is scheduled → record_scheduled() saves it to history
2. After publishing → collect_performance() fetches views/engagement from Metricool
3. CLI review command → shows performance with views for each post

Data is stored as JSON in ~/.snshack-threads/post_history.json
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class MetricSnapshot:
    """A point-in-time metrics snapshot for early velocity tracking."""

    collected_at: str  # ISO datetime
    elapsed_hours: int  # Hours since post was published
    views: int = 0
    likes: int = 0
    replies: int = 0

    def to_dict(self) -> dict:
        return {
            "collected_at": self.collected_at,
            "elapsed_hours": self.elapsed_hours,
            "views": self.views,
            "likes": self.likes,
            "replies": self.replies,
        }

    @classmethod
    def from_dict(cls, d: dict) -> MetricSnapshot:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


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

    # Early velocity snapshots (1h, 3h, etc.)
    snapshots: list[MetricSnapshot] = field(default_factory=list)

    # Tracking
    metricool_response: dict = field(default_factory=dict)
    collected_at: str = ""  # When metrics were last collected

    @property
    def total_interactions(self) -> int:
        return self.likes + self.replies + self.reposts + self.quotes

    @property
    def has_metrics(self) -> bool:
        return self.status == "collected"

    @property
    def char_count(self) -> int:
        """Character count of the post text."""
        return len(self.text)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Serialize snapshots properly
        d["snapshots"] = [s.to_dict() for s in self.snapshots]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> PostRecord:
        raw_snapshots = d.pop("snapshots", [])
        record = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        record.snapshots = [MetricSnapshot.from_dict(s) for s in raw_snapshots]
        return record


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    """Extract character n-grams for similarity comparison."""
    text = text.strip()
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


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
                logger.warning("Corrupted history file: %s — starting with backup", self._path)
                # Back up corrupted file instead of silently losing data
                backup = self._path.with_suffix(".json.bak")
                try:
                    self._path.rename(backup)
                    logger.warning("Corrupted file backed up to: %s", backup)
                except OSError:
                    pass
                self._records = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            [r.to_dict() for r in self._records], ensure_ascii=False, indent=2
        )
        # Atomic write: write to temp file, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, suffix=".tmp", prefix="history_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, self._path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

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

    def add_snapshot(
        self,
        record: PostRecord,
        views: int,
        likes: int,
        replies: int,
    ) -> MetricSnapshot:
        """Add an early velocity snapshot to a post record."""
        scheduled = datetime.fromisoformat(record.scheduled_at)
        elapsed = int((datetime.now() - scheduled).total_seconds() / 3600)

        snapshot = MetricSnapshot(
            collected_at=datetime.now().isoformat(),
            elapsed_hours=max(elapsed, 1),
            views=views,
            likes=likes,
            replies=replies,
        )
        record.snapshots.append(snapshot)
        self._save()
        return snapshot

    def get_early_collection(self, max_age_hours: int = 6) -> list[PostRecord]:
        """Get posts eligible for early velocity tracking.

        Returns posts between 1 and max_age_hours old that haven't been
        fully collected yet.
        """
        now = datetime.now()
        result = []
        for r in self._records:
            if r.status == "collected":
                continue
            try:
                scheduled = datetime.fromisoformat(r.scheduled_at)
            except ValueError:
                continue
            elapsed = (now - scheduled).total_seconds() / 3600
            if 1 <= elapsed <= max_age_hours:
                result.append(r)
        return result

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

    def check_similarity(self, new_text: str, lookback: int = 50) -> list[PostRecord]:
        """Check if new text is too similar to recent posts.

        Uses character n-gram Jaccard similarity to detect near-duplicates.
        Threads suppresses repetitive content, so this prevents self-sabotage.

        Args:
            new_text: Text to check.
            lookback: Number of recent posts to compare against.

        Returns:
            List of similar posts (Jaccard > 0.6).
        """
        new_ngrams = _char_ngrams(new_text)
        if not new_ngrams:
            return []

        recent = self._records[-lookback:] if len(self._records) > lookback else self._records
        similar = []
        for record in recent:
            old_ngrams = _char_ngrams(record.text)
            if not old_ngrams:
                continue
            intersection = new_ngrams & old_ngrams
            union = new_ngrams | old_ngrams
            jaccard = len(intersection) / len(union) if union else 0
            if jaccard > 0.6:
                similar.append(record)

        return similar

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
    """Match a history record to an API post by text comparison.

    Tries full text match first, then prefix match, then time-based fallback.
    """
    record_text = record.text.strip()

    # Exact full-text match
    for post in api_posts:
        post_text = (post.text or "").strip()
        if record_text and post_text and record_text == post_text:
            return post

    # Prefix match (API may truncate text)
    if len(record_text) > 20:
        record_prefix = record_text[:80]
        for post in api_posts:
            post_text = (post.text or "").strip()
            if post_text and post_text.startswith(record_prefix):
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
