"""Analytics and reporting for Threads posts via Metricool API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import logging

from .api import MetricoolAPIError, MetricoolClient
from .models import ThreadsPost

logger = logging.getLogger(__name__)


@dataclass
class AnalyticsReport:
    """Aggregate analytics report."""

    period_start: str
    period_end: str
    total_posts: int = 0
    total_views: int = 0
    total_likes: int = 0
    total_replies: int = 0
    total_reposts: int = 0
    total_quotes: int = 0
    total_interactions: int = 0
    avg_engagement_rate: float = 0.0
    top_posts: list[ThreadsPost] = field(default_factory=list)
    followers_count: int = 0
    delta_followers: int = 0

    # Virality metrics
    virality_rate: float = 0.0  # (reposts + quotes) / views
    discussion_rate: float = 0.0  # replies / (likes + 1)
    amplification_rate: float = 0.0  # reposts / (likes + 1)

    @property
    def avg_engagement_rate_pct(self) -> str:
        return f"{self.avg_engagement_rate * 100:.2f}%"


def generate_report(
    client: MetricoolClient,
    start: str,
    end: str,
    top_n: int = 5,
) -> AnalyticsReport:
    """Generate an analytics report for Threads posts in a date range.

    Args:
        client: Metricool API client.
        start: Start date (YYYY-MM-DD).
        end: End date (YYYY-MM-DD).
        top_n: Number of top posts to include.
    """
    posts = client.get_threads_posts(start, end)

    if not posts:
        return AnalyticsReport(period_start=start, period_end=end)

    total_views = sum(p.views for p in posts)
    total_likes = sum(p.likes for p in posts)
    total_replies = sum(p.replies for p in posts)
    total_reposts = sum(p.reposts for p in posts)
    total_quotes = sum(p.quotes for p in posts)
    total_interactions = sum(p.interactions for p in posts)

    rates = [p.engagement for p in posts if p.engagement > 0]
    avg_rate = sum(rates) / len(rates) if rates else 0.0

    top_posts = sorted(posts, key=lambda p: p.interactions, reverse=True)[:top_n]

    # Try to get account metrics
    followers_count = 0
    delta_followers = 0
    try:
        account = client.get_threads_account_metrics(start, end)
        followers_count = account.followers_count
        delta_followers = account.delta_followers
    except MetricoolAPIError as e:
        logger.warning("Failed to fetch account metrics: %s", e)

    # Virality metrics
    virality_rate = (total_reposts + total_quotes) / total_views if total_views else 0.0
    discussion_rate = total_replies / (total_likes + 1)
    amplification_rate = total_reposts / (total_likes + 1)

    return AnalyticsReport(
        period_start=start,
        period_end=end,
        total_posts=len(posts),
        total_views=total_views,
        total_likes=total_likes,
        total_replies=total_replies,
        total_reposts=total_reposts,
        total_quotes=total_quotes,
        total_interactions=total_interactions,
        avg_engagement_rate=avg_rate,
        top_posts=top_posts,
        followers_count=followers_count,
        delta_followers=delta_followers,
        virality_rate=virality_rate,
        discussion_rate=discussion_rate,
        amplification_rate=amplification_rate,
    )
