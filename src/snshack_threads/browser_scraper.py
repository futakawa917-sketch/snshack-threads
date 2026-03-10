"""Browser-based Threads scraper using Playwright.

Fetches public profile pages and extracts post data that the Graph API
cannot provide (e.g. view counts on competitor posts).

Requires: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ScrapedPost:
    """A post scraped from a Threads profile page."""

    text: str = ""
    likes: int = 0
    replies: int = 0
    reposts: int = 0
    timestamp: str = ""
    post_url: str = ""
    has_image: bool = False


@dataclass
class ScrapedProfile:
    """A scraped Threads profile."""

    username: str
    display_name: str = ""
    bio: str = ""
    followers_text: str = ""  # e.g. "12.3K followers"
    posts: list[ScrapedPost] = field(default_factory=list)
    scraped_at: str = ""

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "display_name": self.display_name,
            "bio": self.bio,
            "followers_text": self.followers_text,
            "posts": [
                {
                    "text": p.text,
                    "likes": p.likes,
                    "replies": p.replies,
                    "reposts": p.reposts,
                    "timestamp": p.timestamp,
                    "post_url": p.post_url,
                    "has_image": p.has_image,
                }
                for p in self.posts
            ],
            "scraped_at": self.scraped_at,
        }


def _ensure_playwright():
    """Check that playwright is installed."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise ImportError(
            "playwright is required for browser scraping.\n"
            "Install: pip install playwright && playwright install chromium"
        )


def _parse_count(text: str) -> int:
    """Parse count strings like '1.2K', '3M', '456'."""
    if not text:
        return 0
    text = text.strip().replace(",", "")
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    for suffix, mult in multipliers.items():
        if text.upper().endswith(suffix):
            try:
                return int(float(text[:-1]) * mult)
            except ValueError:
                return 0
    try:
        return int(text)
    except ValueError:
        return 0


def scrape_profile(
    username: str,
    max_posts: int = 20,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> ScrapedProfile:
    """Scrape a public Threads profile.

    Args:
        username: Threads username (without @).
        max_posts: Maximum number of posts to scrape.
        headless: Run browser in headless mode.
        timeout_ms: Page load timeout in milliseconds.

    Returns:
        ScrapedProfile with posts and profile info.
    """
    _ensure_playwright()
    from playwright.sync_api import sync_playwright

    profile = ScrapedProfile(
        username=username,
        scraped_at=datetime.now().isoformat(),
    )

    url = f"https://www.threads.net/@{username}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)

            # Wait for content to load
            page.wait_for_timeout(3000)

            # Scroll to load more posts
            for _ in range(min(max_posts // 5, 5)):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)

            # Extract profile info from page content
            page_text = page.content()
            profile.display_name = _extract_meta(page_text, "og:title") or username
            profile.bio = _extract_meta(page_text, "og:description") or ""

            # Extract posts using page evaluation
            posts_data = page.evaluate("""
                () => {
                    const posts = [];
                    // Look for post containers
                    const articles = document.querySelectorAll('[data-pressable-container="true"]');
                    articles.forEach(article => {
                        const textEl = article.querySelector('[dir="auto"]');
                        const text = textEl ? textEl.innerText : '';
                        if (!text) return;

                        // Try to find engagement counts
                        const spans = article.querySelectorAll('span');
                        const counts = [];
                        spans.forEach(span => {
                            const t = span.innerText.trim();
                            if (/^[\\d,.]+[KMB]?$/.test(t) || /^\\d+$/.test(t)) {
                                counts.push(t);
                            }
                        });

                        // Try to find timestamp
                        const timeEl = article.querySelector('time');
                        const timestamp = timeEl ? timeEl.getAttribute('datetime') || timeEl.innerText : '';

                        // Try to find link
                        const links = article.querySelectorAll('a[href*="/post/"]');
                        const postUrl = links.length > 0 ? links[0].href : '';

                        // Check for images
                        const hasImage = article.querySelectorAll('img[src*="scontent"]').length > 0;

                        posts.push({
                            text: text.substring(0, 1000),
                            counts: counts,
                            timestamp: timestamp,
                            postUrl: postUrl,
                            hasImage: hasImage,
                        });
                    });
                    return posts;
                }
            """)

            for pd in posts_data[:max_posts]:
                counts = pd.get("counts", [])
                post = ScrapedPost(
                    text=pd.get("text", ""),
                    likes=_parse_count(counts[0]) if len(counts) > 0 else 0,
                    replies=_parse_count(counts[1]) if len(counts) > 1 else 0,
                    reposts=_parse_count(counts[2]) if len(counts) > 2 else 0,
                    timestamp=pd.get("timestamp", ""),
                    post_url=pd.get("postUrl", ""),
                    has_image=pd.get("hasImage", False),
                )
                profile.posts.append(post)

        except Exception as e:
            logger.warning("Scraping failed for @%s: %s", username, e)
        finally:
            browser.close()

    return profile


def scrape_multiple(
    usernames: list[str],
    max_posts: int = 20,
    headless: bool = True,
) -> list[ScrapedProfile]:
    """Scrape multiple profiles sequentially."""
    profiles = []
    for username in usernames:
        logger.info("Scraping @%s...", username)
        profile = scrape_profile(username, max_posts=max_posts, headless=headless)
        profiles.append(profile)
    return profiles


def _extract_meta(html: str, property_name: str) -> str:
    """Extract content from an OG meta tag."""
    match = re.search(
        rf'<meta\s+(?:property|name)="{re.escape(property_name)}"\s+content="([^"]*)"',
        html,
    )
    if match:
        return match.group(1)
    # Try reversed attribute order
    match = re.search(
        rf'<meta\s+content="([^"]*)"\s+(?:property|name)="{re.escape(property_name)}"',
        html,
    )
    return match.group(1) if match else ""
