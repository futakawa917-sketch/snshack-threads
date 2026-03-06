"""Analytics and reporting for Threads posts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .api import ThreadsClient
from .models import ThreadsMetrics, ThreadsPost


@dataclass
class PostSummary:
    """Enriched post with its metrics."""

    post: ThreadsPost
    metrics: ThreadsMetrics

    @property
    def engagement_rate_pct(self) -> str:
        return f"{self.metrics.engagement_rate * 100:.2f}%"


@dataclass
class AnalyticsReport:
    """Aggregate analytics report."""

    period_start: datetime | None
    period_end: datetime | None
    total_posts: int
    total_views: int
    total_likes: int
    total_replies: int
    total_reposts: int
    total_quotes: int
    avg_engagement_rate: float
    top_posts: list[PostSummary]

    @property
    def avg_engagement_rate_pct(self) -> str:
        return f"{self.avg_engagement_rate * 100:.2f}%"


def fetch_post_summaries(
    client: ThreadsClient,
    limit: int = 25,
) -> list[PostSummary]:
    """Fetch recent posts with their metrics."""
    posts = client.get_posts(limit=limit)
    summaries: list[PostSummary] = []
    for post in posts:
        metrics = client.get_post_metrics(post.id)
        summaries.append(PostSummary(post=post, metrics=metrics))
    return summaries


def generate_report(
    client: ThreadsClient,
    limit: int = 25,
    top_n: int = 5,
) -> AnalyticsReport:
    """Generate an analytics report for recent posts."""
    summaries = fetch_post_summaries(client, limit=limit)

    if not summaries:
        return AnalyticsReport(
            period_start=None,
            period_end=None,
            total_posts=0,
            total_views=0,
            total_likes=0,
            total_replies=0,
            total_reposts=0,
            total_quotes=0,
            avg_engagement_rate=0.0,
            top_posts=[],
        )

    timestamps = [s.post.timestamp for s in summaries if s.post.timestamp]
    period_start = min(timestamps) if timestamps else None
    period_end = max(timestamps) if timestamps else None

    total_views = sum(s.metrics.views for s in summaries)
    total_likes = sum(s.metrics.likes for s in summaries)
    total_replies = sum(s.metrics.replies for s in summaries)
    total_reposts = sum(s.metrics.reposts for s in summaries)
    total_quotes = sum(s.metrics.quotes for s in summaries)

    rates = [s.metrics.engagement_rate for s in summaries]
    avg_rate = sum(rates) / len(rates) if rates else 0.0

    top_posts = sorted(
        summaries,
        key=lambda s: s.metrics.total_interactions,
        reverse=True,
    )[:top_n]

    return AnalyticsReport(
        period_start=period_start,
        period_end=period_end,
        total_posts=len(summaries),
        total_views=total_views,
        total_likes=total_likes,
        total_replies=total_replies,
        total_reposts=total_reposts,
        total_quotes=total_quotes,
        avg_engagement_rate=avg_rate,
        top_posts=top_posts,
    )
