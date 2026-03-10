"""Web research module: fetch latest news via Google News RSS.

Searches Google News for keywords and returns recent headlines/summaries.
Results are cached for 24 hours to avoid redundant requests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".snshack-threads" / "cache" / "news"
_CACHE_TTL_HOURS = 24


def _cache_path(keyword: str) -> Path:
    """Return cache file path for a keyword."""
    key = hashlib.md5(keyword.encode()).hexdigest()
    return _CACHE_DIR / f"{key}.json"


def _load_cache(keyword: str) -> list[dict] | None:
    """Load cached results if still valid (within TTL)."""
    path = _cache_path(keyword)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data["cached_at"])
        if datetime.now() - cached_at < timedelta(hours=_CACHE_TTL_HOURS):
            return data["items"]
    except (json.JSONDecodeError, KeyError, ValueError):
        pass
    return None


def _save_cache(keyword: str, items: list[dict]) -> None:
    """Save results to cache."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "keyword": keyword,
        "cached_at": datetime.now().isoformat(),
        "items": items,
    }
    _cache_path(keyword).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fetch_news(keyword: str, max_items: int = 10) -> list[dict]:
    """Fetch latest news for a keyword from Google News RSS.

    Args:
        keyword: Search keyword.
        max_items: Maximum number of items to return.

    Returns:
        List of dicts with 'title' and 'published' keys.
    """
    # Check cache first
    cached = _load_cache(keyword)
    if cached is not None:
        logger.debug("ニュースキャッシュヒット: %s (%d件)", keyword, len(cached))
        return cached[:max_items]

    import httpx

    url = (
        f"https://news.google.com/rss/search?"
        f"q={quote(keyword)}&hl=ja&gl=JP&ceid=JP:ja"
    )

    items: list[dict] = []
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        for item in root.iter("item"):
            title_el = item.find("title")
            pub_el = item.find("pubDate")
            if title_el is not None and title_el.text:
                items.append({
                    "title": unescape(title_el.text.strip()),
                    "published": pub_el.text.strip() if pub_el is not None and pub_el.text else "",
                })
                if len(items) >= max_items:
                    break

        logger.info("ニュース取得: '%s' → %d件", keyword, len(items))
    except Exception as e:
        logger.warning("ニュース取得失敗 '%s': %s", keyword, e)

    # Cache even if empty (to avoid hammering on errors)
    _save_cache(keyword, items)
    return items


def search_news_for_keywords(
    keywords: list[str],
    max_per_keyword: int = 5,
) -> list[str]:
    """Search news for multiple keywords and return headline strings.

    Args:
        keywords: List of search keywords.
        max_per_keyword: Max headlines per keyword.

    Returns:
        List of headline strings ready to inject into AI prompt.
    """
    headlines: list[str] = []
    seen: set[str] = set()

    for kw in keywords:
        items = fetch_news(kw, max_items=max_per_keyword)
        for item in items:
            title = item["title"]
            if title not in seen:
                seen.add(title)
                headlines.append(title)

    return headlines
