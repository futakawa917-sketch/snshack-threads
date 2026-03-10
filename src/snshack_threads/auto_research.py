"""Automated competitor research via Meta Threads API.

Runs daily via cron to:
1. Search keywords → find posts
2. Extract top-performing accounts
3. Auto-register as competitors
4. Analyze their hook patterns
5. Feed insights into autopilot strategy

No manual intervention needed — just set research_keywords in profile config.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredAccount:
    """An account discovered through keyword search."""

    username: str
    display_name: str = ""
    total_posts_found: int = 0
    total_likes: int = 0
    total_replies: int = 0
    avg_likes: float = 0.0
    avg_replies: float = 0.0
    top_hooks: list[str] = field(default_factory=list)
    sample_posts: list[dict] = field(default_factory=list)  # [{text, likes, replies}]
    discovered_via: list[str] = field(default_factory=list)  # keywords that found them
    score: float = 0.0  # ranking score


@dataclass
class ResearchReport:
    """Result of a daily auto-research run."""

    date: str
    keywords_searched: list[str] = field(default_factory=list)
    total_posts_found: int = 0
    discovered_accounts: list[DiscoveredAccount] = field(default_factory=list)
    auto_registered: list[str] = field(default_factory=list)  # usernames
    trending_hooks: list[dict] = field(default_factory=list)  # [{hook, count, avg_likes}]
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "keywords_searched": self.keywords_searched,
            "total_posts_found": self.total_posts_found,
            "discovered_accounts": [asdict(a) for a in self.discovered_accounts],
            "auto_registered": self.auto_registered,
            "trending_hooks": self.trending_hooks,
            "errors": self.errors,
        }


def run_auto_research(
    profile: str | None = None,
    max_competitors: int = 10,
    min_likes_threshold: float = 5.0,
) -> ResearchReport:
    """Run full automated research cycle.

    1. Search each keyword via Threads API
    2. Aggregate posts by author → rank accounts
    3. Auto-register top accounts as competitors
    4. Analyze hook patterns across all found posts
    5. Save report for dashboard visibility

    Args:
        profile: Profile name.
        max_competitors: Max number of competitors to auto-register.
        min_likes_threshold: Minimum avg likes to consider an account relevant.

    Returns:
        ResearchReport with all findings.
    """
    from .config import get_settings
    from .csv_analyzer import _detect_hooks
    from .research_store import (
        CompetitorSnapshot,
        ResearchSnapshot,
        ResearchStore,
    )

    settings = get_settings(profile=profile)
    keywords = settings.get_research_keywords()

    report = ResearchReport(date=datetime.now().strftime("%Y-%m-%d"))

    if not keywords:
        report.errors.append("No research keywords configured")
        logger.warning("No research keywords configured for profile %s", profile)
        return report

    if not settings.threads_access_token:
        report.errors.append("No Threads access token configured")
        logger.warning("No Threads access token for profile %s", profile)
        return report

    # Step 1: Search each keyword
    all_posts: list[dict] = []

    try:
        from .threads_api import ThreadsGraphClient

        with ThreadsGraphClient() as client:
            for keyword in keywords:
                try:
                    posts = client.keyword_search(keyword, limit=25)
                    all_posts.extend(posts)
                    report.keywords_searched.append(keyword)
                    logger.info("Searched '%s': %d posts found", keyword, len(posts))
                except Exception as e:
                    report.errors.append(f"Search '{keyword}': {e}")
                    logger.warning("Failed to search '%s': %s", keyword, e)
    except Exception as e:
        report.errors.append(f"API connection: {e}")
        logger.error("Failed to connect to Threads API: %s", e)
        _save_report(report, settings)
        return report

    report.total_posts_found = len(all_posts)

    if not all_posts:
        _save_report(report, settings)
        return report

    # Step 2: Aggregate by author
    accounts: dict[str, DiscoveredAccount] = {}

    for post in all_posts:
        # Extract author info from post
        owner = post.get("username") or post.get("owner", {}).get("username", "")
        if not owner:
            # Try to extract from id pattern
            continue

        if owner not in accounts:
            accounts[owner] = DiscoveredAccount(
                username=owner,
                display_name=post.get("name", ""),
            )

        acc = accounts[owner]
        likes = post.get("like_count", 0) or 0
        replies = post.get("reply_count", 0) or 0

        acc.total_posts_found += 1
        acc.total_likes += likes
        acc.total_replies += replies

        # Track which keywords found this account
        for kw in keywords:
            text = post.get("text", "")
            if kw.lower() in text.lower() and kw not in acc.discovered_via:
                acc.discovered_via.append(kw)

        # Keep top posts as samples
        if len(acc.sample_posts) < 5:
            acc.sample_posts.append({
                "text": (post.get("text") or "")[:200],
                "likes": likes,
                "replies": replies,
            })

    # Calculate averages and scores
    for acc in accounts.values():
        if acc.total_posts_found > 0:
            acc.avg_likes = acc.total_likes / acc.total_posts_found
            acc.avg_replies = acc.total_replies / acc.total_posts_found
        # Score = avg_likes * log(posts_found + 1) — rewards both quality and presence
        import math
        acc.score = acc.avg_likes * math.log(acc.total_posts_found + 1)

        # Detect hooks in sample posts
        hook_counter: Counter = Counter()
        for p in acc.sample_posts:
            for hook in _detect_hooks(p.get("text", "")):
                hook_counter[hook] += 1
        acc.top_hooks = [h for h, _ in hook_counter.most_common(5)]

    # Step 3: Rank and filter
    ranked = sorted(accounts.values(), key=lambda a: a.score, reverse=True)
    ranked = [a for a in ranked if a.avg_likes >= min_likes_threshold]
    report.discovered_accounts = ranked[:20]  # Keep top 20 for report

    # Step 4: Auto-register top accounts as competitors
    store = ResearchStore()
    existing = {c.username for c in store.list_competitors()}

    registered = []
    for acc in ranked[:max_competitors]:
        if acc.username not in existing:
            try:
                store.add_competitor(
                    username=acc.username,
                    display_name=acc.display_name,
                    notes=f"Auto-discovered ({', '.join(acc.discovered_via[:3])})",
                )
                registered.append(acc.username)
                existing.add(acc.username)
                logger.info("Auto-registered competitor: @%s (score=%.1f)", acc.username, acc.score)
            except ValueError:
                pass  # Already exists

        # Save competitor snapshot with their data
        snapshot = CompetitorSnapshot(
            username=acc.username,
            timestamp=datetime.now().isoformat(),
            posts=acc.sample_posts,
            post_count=acc.total_posts_found,
            avg_likes=acc.avg_likes,
            avg_replies=acc.avg_replies,
            top_hooks=acc.top_hooks,
        )
        store.save_competitor_snapshot(snapshot)

    report.auto_registered = registered

    # Step 5: Analyze trending hooks across all posts
    global_hooks: dict[str, list[int]] = defaultdict(list)
    for post in all_posts:
        text = post.get("text", "")
        likes = post.get("like_count", 0) or 0
        for hook in _detect_hooks(text):
            global_hooks[hook].append(likes)

    trending = []
    for hook, likes_list in sorted(
        global_hooks.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True
    ):
        trending.append({
            "hook": hook,
            "count": len(likes_list),
            "avg_likes": round(sum(likes_list) / len(likes_list), 1),
            "total_likes": sum(likes_list),
        })
    report.trending_hooks = trending[:15]

    # Save keyword research snapshots
    for keyword in report.keywords_searched:
        kw_posts = [p for p in all_posts if keyword.lower() in (p.get("text") or "").lower()]
        if kw_posts:
            kw_likes = [p.get("like_count", 0) or 0 for p in kw_posts]
            kw_replies = [p.get("reply_count", 0) or 0 for p in kw_posts]

            kw_hooks: dict[str, list[int]] = defaultdict(list)
            for p in kw_posts:
                for h in _detect_hooks(p.get("text", "")):
                    kw_hooks[h].append(p.get("like_count", 0) or 0)

            top_hooks = [
                {"name": h, "count": len(ls), "avg_likes": round(sum(ls) / len(ls), 1)}
                for h, ls in sorted(kw_hooks.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True)[:10]
            ]

            snap = ResearchSnapshot(
                keyword=keyword,
                timestamp=datetime.now().isoformat(),
                total_posts=len(kw_posts),
                avg_likes=sum(kw_likes) / len(kw_likes),
                avg_replies=sum(kw_replies) / len(kw_replies),
                top_hooks=top_hooks,
                top_posts=[
                    {"text": (p.get("text") or "")[:200], "likes": p.get("like_count", 0), "replies": p.get("reply_count", 0)}
                    for p in sorted(kw_posts, key=lambda x: x.get("like_count", 0), reverse=True)[:5]
                ],
            )
            store.save_research_snapshot(snap)

    # Save report
    _save_report(report, settings)

    logger.info(
        "Auto-research complete: %d posts, %d accounts found, %d auto-registered",
        report.total_posts_found,
        len(report.discovered_accounts),
        len(report.auto_registered),
    )

    return report


def run_self_analysis(profile: str | None = None) -> ResearchReport:
    """Run self-analysis as fallback when keyword_search is unavailable.

    Analyzes our own published posts to extract patterns:
    - Which hooks got the most views/likes
    - Which topics performed best
    - What post lengths work
    - Trending topics from our own data

    This runs automatically when keyword_search fails.
    """
    from .csv_analyzer import _detect_hooks
    from .post_history import PostHistory, get_performance_summary

    report = ResearchReport(date=datetime.now().strftime("%Y-%m-%d"))
    report.keywords_searched = ["[self-analysis]"]

    history = PostHistory()
    collected = [r for r in history.get_all() if r.has_metrics]

    if len(collected) < 3:
        report.errors.append(f"Not enough data for self-analysis ({len(collected)} posts)")
        return report

    report.total_posts_found = len(collected)

    # Analyze hooks from our own posts
    hook_data: dict[str, list[int]] = defaultdict(list)
    for record in collected:
        hooks = _detect_hooks(record.text)
        for hook in hooks:
            hook_data[hook].append(record.views)

    for hook, views_list in sorted(
        hook_data.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True
    ):
        report.trending_hooks.append({
            "hook": hook,
            "count": len(views_list),
            "avg_likes": round(sum(views_list) / len(views_list), 1),  # Using views as proxy
            "total_likes": sum(views_list),
            "source": "self-analysis",
        })

    logger.info(
        "Self-analysis complete: %d posts analyzed, %d hooks found",
        len(collected), len(report.trending_hooks),
    )

    # Save report
    from .config import get_settings
    settings = get_settings(profile=profile)
    _save_report(report, settings)

    return report


def get_competitor_hooks(profile: str | None = None) -> dict[str, float]:
    """Get hook performance data from competitor research.

    Returns dict of {hook_name: avg_likes} to feed into autopilot.
    """
    from .config import get_settings

    settings = get_settings(profile=profile)
    data_dir = Path(settings.data_dir) / "research"
    reports = _load_reports(data_dir)

    if not reports:
        return {}

    # Aggregate hooks from recent reports (last 7 days)
    hook_scores: dict[str, list[float]] = defaultdict(list)
    cutoff = (datetime.now().replace(hour=0, minute=0, second=0) - __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%d")

    for report in reports:
        if report.get("date", "") >= cutoff:
            for h in report.get("trending_hooks", []):
                hook_scores[h["hook"]].append(h["avg_likes"])

    return {
        hook: sum(scores) / len(scores)
        for hook, scores in hook_scores.items()
        if scores
    }


def get_latest_report(profile: str | None = None) -> dict | None:
    """Get the most recent research report for dashboard display."""
    from .config import get_settings

    settings = get_settings(profile=profile)
    data_dir = Path(settings.data_dir) / "research"
    reports = _load_reports(data_dir)
    return reports[-1] if reports else None


def _save_report(report: ResearchReport, settings) -> None:
    """Append report to research_reports.json."""
    import json

    data_dir = Path(settings.data_dir) / "research"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "research_reports.json"

    reports = _load_reports(data_dir)
    reports.append(report.to_dict())

    # Keep last 90 days
    cutoff = (datetime.now().replace(hour=0, minute=0, second=0) - __import__("datetime").timedelta(days=90)).strftime("%Y-%m-%d")
    reports = [r for r in reports if r.get("date", "") >= cutoff]

    path.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_reports(data_dir: Path) -> list[dict]:
    """Load research reports."""
    import json

    path = data_dir / "research_reports.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
