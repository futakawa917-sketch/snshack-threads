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
    post_type: str = "reach"  # reach | list — reach=バズ狙い, list=リスト獲得狙い

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
        from .filelock import file_lock

        if self._path.exists():
            try:
                with file_lock(self._path):
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
        from .filelock import file_lock

        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            [r.to_dict() for r in self._records], ensure_ascii=False, indent=2
        )
        # Atomic write with file lock
        with file_lock(self._path):
            fd, tmp_path = tempfile.mkstemp(
                dir=self._path.parent, suffix=".tmp", prefix="history_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_path, self._path)
            except Exception:
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
        post_type: str = "reach",
    ) -> PostRecord:
        """Record a newly scheduled post."""
        record = PostRecord(
            text=text,
            scheduled_at=publish_at.isoformat(),
            created_at=datetime.now().isoformat(),
            post_type=post_type,
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

    def get_by_type(self, post_type: str, days: int = 30) -> list[PostRecord]:
        """Return posts of a specific type from the last N days."""
        cutoff = datetime.now() - timedelta(days=days)
        return [
            r for r in self._records
            if r.post_type == post_type
            and datetime.fromisoformat(r.scheduled_at) > cutoff
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

    def archive_old_records(self, keep_days: int = 365) -> int:
        """Move records older than keep_days to an archive file.

        Archives to {history_path}.archive.json. Returns count of archived records.
        """
        cutoff = datetime.now() - timedelta(days=keep_days)
        keep: list[PostRecord] = []
        archive: list[PostRecord] = []

        for r in self._records:
            try:
                scheduled = datetime.fromisoformat(r.scheduled_at)
                if scheduled < cutoff:
                    archive.append(r)
                else:
                    keep.append(r)
            except ValueError:
                keep.append(r)

        if not archive:
            return 0

        # Append to archive file
        archive_path = self._path.with_suffix(".archive.json")
        existing_archive: list[dict] = []
        if archive_path.exists():
            try:
                existing_archive = json.loads(archive_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        existing_archive.extend([r.to_dict() for r in archive])
        archive_path.write_text(
            json.dumps(existing_archive, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self._records = keep
        self._save()
        logger.info("Archived %d old records (kept %d)", len(archive), len(keep))
        return len(archive)

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


def collect_threads_metrics(
    history: PostHistory,
    min_age_hours: int = 24,
) -> list[PostRecord]:
    """Fetch performance data directly from Threads Graph API.

    No Metricool dependency — uses official Threads API to:
    1. Get our published posts
    2. Match them with history records
    3. Pull detailed insights (views, likes, replies, reposts, quotes)

    Args:
        history: Post history manager.
        min_age_hours: Only collect for posts older than this.

    Returns:
        List of records that were updated with metrics.
    """
    from .threads_api import ThreadsGraphClient

    pending = history.get_pending_collection(min_age_hours=min_age_hours)
    if not pending:
        logger.info("No pending posts to collect metrics for")
        return []

    updated: list[PostRecord] = []

    try:
        with ThreadsGraphClient() as client:
            my_posts = client.get_my_posts(limit=100)
            logger.info("Fetched %d posts from Threads API", len(my_posts))
    except Exception as e:
        logger.error("Failed to connect to Threads API: %s", e)
        return []

    for record in pending:
        record_text = record.text.strip()
        matched_post = None

        for tp in my_posts:
            tp_text = (tp.get("text") or "").strip()
            if not tp_text or not record_text:
                continue

            # Exact match
            if record_text == tp_text:
                matched_post = tp
                break

            # Prefix match (CTA might be appended)
            if len(record_text) > 20 and (
                tp_text.startswith(record_text[:80])
                or record_text.startswith(tp_text[:80])
            ):
                matched_post = tp
                break

        if not matched_post:
            continue

        post_id = matched_post.get("id", "")
        if not post_id:
            continue

        try:
            insights = client.get_post_insights(post_id)
            views = insights.get("views", 0)
            likes = insights.get("likes", matched_post.get("like_count", 0))
            replies = insights.get("replies", matched_post.get("reply_count", 0))
            reposts = insights.get("reposts", matched_post.get("repost_count", 0))
            quotes = insights.get("quotes", matched_post.get("quote_count", 0))

            total = likes + replies + reposts + quotes
            engagement = (total / views * 100) if views > 0 else 0.0

            history.update_metrics(
                record,
                views=views,
                likes=likes,
                replies=replies,
                reposts=reposts,
                quotes=quotes,
                engagement=engagement,
            )
            updated.append(record)
            logger.info(
                "Collected: %d views, %d likes — %s",
                views, likes, record.text[:40],
            )
        except Exception as e:
            logger.warning("Failed to get insights for %s: %s", post_id, e)

    return updated


def _recency_weight(scheduled_at: str, half_life_days: int = 30) -> float:
    """Exponential decay weight. Posts from half_life_days ago get 0.5 weight."""
    import math

    try:
        scheduled = datetime.fromisoformat(scheduled_at)
    except ValueError:
        return 0.5
    days_ago = (datetime.now() - scheduled).total_seconds() / 86400
    if days_ago < 0:
        days_ago = 0
    return math.exp(-0.693 * days_ago / max(half_life_days, 1))


def get_performance_summary(history: PostHistory) -> dict:
    """Analyze performance data to feed into autopilot learning loop.

    Returns a summary of what's working:
    - Top hooks by views/engagement (recency-weighted, Bayesian-adjusted)
    - Best posting times
    - Optimal post lengths
    - Overall stats and milestone progress
    """
    from .csv_analyzer import _detect_hooks

    collected = [r for r in history.get_all() if r.has_metrics]
    if not collected:
        return {"total_posts": history.count, "collected": 0}

    # Hook performance with recency weighting
    hook_stats: dict[str, dict] = {}
    for record in collected:
        hooks = _detect_hooks(record.text)
        w = _recency_weight(record.scheduled_at)
        for hook in hooks:
            if hook not in hook_stats:
                hook_stats[hook] = {
                    "views": [], "likes": [], "count": 0,
                    "weighted_views": [], "weights": [],
                    "engagement": [],
                }
            hook_stats[hook]["views"].append(record.views)
            hook_stats[hook]["likes"].append(record.likes)
            hook_stats[hook]["count"] += 1
            hook_stats[hook]["weighted_views"].append(record.views * w)
            hook_stats[hook]["weights"].append(w)
            hook_stats[hook]["engagement"].append(record.engagement)

    # Global mean for Bayesian shrinkage
    all_views = [r.views for r in collected]
    global_mean = sum(all_views) / len(all_views) if all_views else 0
    PRIOR_WEIGHT = 5  # equivalent to 5 virtual samples at global mean

    hook_ranking = []
    for hook, stats in hook_stats.items():
        total_weight = sum(stats["weights"])
        if total_weight > 0:
            avg_views = sum(stats["weighted_views"]) / total_weight
        else:
            avg_views = sum(stats["views"]) / len(stats["views"])
        avg_likes = sum(stats["likes"]) / len(stats["likes"])
        avg_engagement = sum(stats["engagement"]) / len(stats["engagement"])
        n = stats["count"]

        # Bayesian shrinkage: blend with global mean weighted by sample size
        adjusted_views = (avg_views * n + global_mean * PRIOR_WEIGHT) / (n + PRIOR_WEIGHT)

        # Composite score: normalized views + engagement + likes ratio
        # Normalize likes_ratio by sample size to prevent small-sample bias
        likes_ratio = sum(stats["likes"]) / max(sum(stats["views"]), 1)
        normalized_likes = min(likes_ratio * 100, 20)  # Cap at 20% to prevent dominance
        composite = adjusted_views * 0.6 + avg_engagement * 200 + normalized_likes * 50

        # Confidence level
        if n >= 10:
            confidence = "high"
        elif n >= 5:
            confidence = "medium"
        else:
            confidence = "low"

        hook_ranking.append({
            "hook": hook,
            "avg_views": round(avg_views, 1),
            "avg_likes": round(avg_likes, 1),
            "avg_engagement": round(avg_engagement, 3),
            "adjusted_views": round(adjusted_views, 1),
            "composite_score": round(composite, 1),
            "count": n,
            "confidence": confidence,
        })
    # Sort by composite score (Bayesian-adjusted, multi-metric)
    hook_ranking.sort(key=lambda x: x["composite_score"], reverse=True)

    # Time performance
    time_stats: dict[int, dict] = {}
    for record in collected:
        try:
            hour = datetime.fromisoformat(record.scheduled_at).hour
        except ValueError:
            continue
        if hour not in time_stats:
            time_stats[hour] = {"views": [], "count": 0}
        time_stats[hour]["views"].append(record.views)
        time_stats[hour]["count"] += 1

    best_times = sorted(
        [
            {"hour": h, "avg_views": round(sum(s["views"]) / len(s["views"]), 1), "count": s["count"]}
            for h, s in time_stats.items()
            if s["count"] >= 2  # Need at least 2 samples
        ],
        key=lambda x: x["avg_views"],
        reverse=True,
    )

    # Length performance
    length_stats: dict[str, dict] = {}
    for record in collected:
        bucket = "short" if len(record.text) < 100 else "medium" if len(record.text) < 300 else "long"
        if bucket not in length_stats:
            length_stats[bucket] = {"views": [], "likes": [], "count": 0}
        length_stats[bucket]["views"].append(record.views)
        length_stats[bucket]["likes"].append(record.likes)
        length_stats[bucket]["count"] += 1

    length_ranking = {
        bucket: {
            "avg_views": round(sum(s["views"]) / len(s["views"]), 1),
            "avg_likes": round(sum(s["likes"]) / len(s["likes"]), 1),
            "count": s["count"],
        }
        for bucket, s in length_stats.items()
    }

    # Overall stats
    all_likes = [r.likes for r in collected]

    return {
        "total_posts": history.count,
        "collected": len(collected),
        "milestone_progress": f"{history.count}/100",
        "avg_views": round(sum(all_views) / len(all_views), 1),
        "avg_likes": round(sum(all_likes) / len(all_likes), 1),
        "max_views": max(all_views) if all_views else 0,
        "top_hooks": hook_ranking[:10],
        "best_times": best_times[:5],
        "length_performance": length_ranking,
    }


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
