"""Competitor research via Threads Keyword Search API.

Automatically searches for top-performing posts in the same genre,
analyzes their content patterns, and compares with own account data.
No manual competitor account input required — just keywords.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .csv_analyzer import _detect_hooks, _text_length_bucket
from .threads_api import ThreadsGraphClient


@dataclass
class CompetitorPost:
    """A competitor's post from keyword search."""

    id: str
    text: str
    timestamp: str
    likes: int = 0
    replies: int = 0
    reposts: int = 0
    quotes: int = 0
    hooks: list[str] = field(default_factory=list)
    length_bucket: str = ""

    @property
    def total_engagement(self) -> int:
        return self.likes + self.replies + self.reposts + self.quotes


@dataclass
class ResearchReport:
    """Analysis report comparing competitor posts with own data."""

    keyword: str
    total_posts_found: int = 0
    posts: list[CompetitorPost] = field(default_factory=list)

    # Aggregated insights
    avg_likes: float = 0.0
    avg_replies: float = 0.0
    avg_engagement: float = 0.0

    # Pattern analysis
    top_hooks: list[tuple[str, int, float]] = field(default_factory=list)  # (hook_name, count, avg_likes)
    length_performance: dict[str, float] = field(default_factory=dict)  # bucket -> avg_likes

    # Actionable gaps (patterns competitors use but we don't)
    hook_gaps: list[str] = field(default_factory=list)
    top_posts: list[CompetitorPost] = field(default_factory=list)


def search_and_analyze(
    client: ThreadsGraphClient,
    keyword: str,
    max_results: int = 100,
    own_hooks: set[str] | None = None,
) -> ResearchReport:
    """Search for posts by keyword and analyze content patterns.

    Args:
        client: Threads Graph API client.
        keyword: Genre keyword to search (e.g. "補助金").
        max_results: Max posts to fetch.
        own_hooks: Set of hook patterns already used by own account (for gap analysis).
    """
    raw_posts = client.keyword_search_paginated(
        query=keyword,
        max_results=max_results,
    )

    report = ResearchReport(keyword=keyword, total_posts_found=len(raw_posts))

    # Parse posts
    for raw in raw_posts:
        text = raw.get("text", "")
        post = CompetitorPost(
            id=raw.get("id", ""),
            text=text,
            timestamp=raw.get("timestamp", ""),
            likes=raw.get("like_count", 0),
            replies=raw.get("reply_count", 0),
            reposts=raw.get("repost_count", 0),
            quotes=raw.get("quote_count", 0),
            hooks=_detect_hooks(text),
            length_bucket=_text_length_bucket(text),
        )
        report.posts.append(post)

    if not report.posts:
        return report

    # Aggregate metrics
    report.avg_likes = sum(p.likes for p in report.posts) / len(report.posts)
    report.avg_replies = sum(p.replies for p in report.posts) / len(report.posts)
    report.avg_engagement = sum(p.total_engagement for p in report.posts) / len(report.posts)

    # Hook pattern analysis
    hook_data: dict[str, list[int]] = {}
    for p in report.posts:
        for hook in p.hooks:
            hook_data.setdefault(hook, []).append(p.likes)

    report.top_hooks = sorted(
        [
            (name, len(likes_list), sum(likes_list) / len(likes_list))
            for name, likes_list in hook_data.items()
        ],
        key=lambda x: x[2],  # Sort by avg likes
        reverse=True,
    )

    # Length bucket analysis
    length_data: dict[str, list[int]] = {}
    for p in report.posts:
        length_data.setdefault(p.length_bucket, []).append(p.likes)

    report.length_performance = {
        bucket: sum(likes) / len(likes)
        for bucket, likes in length_data.items()
    }

    # Top posts by engagement
    report.top_posts = sorted(
        report.posts, key=lambda p: p.total_engagement, reverse=True
    )[:10]

    # Hook gap analysis
    if own_hooks is not None:
        competitor_hooks = {name for name, _, _ in report.top_hooks}
        report.hook_gaps = list(competitor_hooks - own_hooks)

    return report


def research_genre(
    client: ThreadsGraphClient,
    keywords: list[str],
    max_per_keyword: int = 50,
    own_hooks: set[str] | None = None,
) -> list[ResearchReport]:
    """Research multiple keywords for a genre.

    Args:
        client: Threads Graph API client.
        keywords: List of genre keywords (e.g. ["補助金", "助成金", "経営者"]).
        max_per_keyword: Max posts per keyword.
        own_hooks: Own hook patterns for gap analysis.
    """
    reports = []
    for kw in keywords:
        report = search_and_analyze(
            client, kw, max_results=max_per_keyword, own_hooks=own_hooks,
        )
        reports.append(report)
    return reports
