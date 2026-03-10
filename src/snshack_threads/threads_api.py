"""Threads Graph API client — keyword search, publishing, and analytics.

Uses Meta's official Threads API (graph.threads.net).
Permissions needed:
  - threads_basic: profile, own posts
  - threads_content_publish: create & publish posts
  - threads_keyword_search (Advanced Access): search other users' posts
  - threads_read_replies: read replies
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.threads.net/v1.0"

# Rate limits
SEARCH_RATE_LIMIT = 500  # per rolling 7 days
PUBLISH_RATE_LIMIT = 250  # per rolling 24 hours


class ThreadsAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


# ── Rate Limiter ──────────────────────────────────────────


@dataclass
class RateLimitEntry:
    endpoint: str
    timestamp: str


class RateLimiter:
    """Track API calls to avoid hitting rate limits."""

    def __init__(self, store_path: Path | None = None) -> None:
        if store_path is None:
            settings = get_settings()
            self._path = Path(settings.data_dir) / "rate_limits.json"
        else:
            self._path = store_path
        self._entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._entries = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, KeyError):
                self._entries = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._entries, f)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def record(self, endpoint: str) -> None:
        self._entries.append({
            "endpoint": endpoint,
            "timestamp": datetime.now().isoformat(),
        })
        self._cleanup()
        self._save()

    def _cleanup(self) -> None:
        """Remove entries older than 7 days."""
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        self._entries = [e for e in self._entries if e["timestamp"] > cutoff]

    def count(self, endpoint: str, window_hours: int = 168) -> int:
        """Count calls to an endpoint within the time window."""
        cutoff = (datetime.now() - timedelta(hours=window_hours)).isoformat()
        return sum(
            1 for e in self._entries
            if e["endpoint"] == endpoint and e["timestamp"] > cutoff
        )

    def check_search_limit(self) -> tuple[bool, int]:
        """Check if search rate limit is OK. Returns (allowed, remaining)."""
        used = self.count("search", window_hours=168)  # 7 days
        return used < SEARCH_RATE_LIMIT, SEARCH_RATE_LIMIT - used

    def check_publish_limit(self) -> tuple[bool, int]:
        """Check if publish rate limit is OK. Returns (allowed, remaining)."""
        used = self.count("publish", window_hours=24)
        return used < PUBLISH_RATE_LIMIT, PUBLISH_RATE_LIMIT - used

    def get_usage_summary(self) -> dict:
        """Get rate limit usage summary."""
        search_used = self.count("search", window_hours=168)
        publish_used = self.count("publish", window_hours=24)
        return {
            "search": {"used": search_used, "limit": SEARCH_RATE_LIMIT, "remaining": SEARCH_RATE_LIMIT - search_used},
            "publish": {"used": publish_used, "limit": PUBLISH_RATE_LIMIT, "remaining": PUBLISH_RATE_LIMIT - publish_used},
        }


# ── Threads Graph Client ─────────────────────────────────


class ThreadsGraphClient:
    """Client for the official Meta Threads Graph API."""

    def __init__(self, access_token: str | None = None) -> None:
        settings = get_settings()
        self._token = access_token or settings.threads_access_token
        if not self._token:
            raise ThreadsAPIError(
                "Missing THREADS_ACCESS_TOKEN. "
                "Get one from Meta Developer Console."
            )
        self._http = httpx.Client(
            base_url=GRAPH_API_BASE,
            timeout=30.0,
        )
        self._rate_limiter = RateLimiter()
        self._user_id: str | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ThreadsGraphClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _is_retryable(self, status_code: int) -> bool:
        """Check if an HTTP status code is retryable."""
        return status_code in (429, 500, 502, 503, 504)

    def _request_with_retry(self, method: str, path: str, max_retries: int = 3, **kwargs) -> dict[str, Any]:
        """Execute an HTTP request with exponential backoff retry."""
        for attempt in range(max_retries):
            if method == "GET":
                resp = self._http.get(path, **kwargs)
            else:
                resp = self._http.post(path, **kwargs)

            if resp.status_code < 400:
                try:
                    return resp.json()
                except (ValueError, json.JSONDecodeError):
                    raise ThreadsAPIError(
                        f"Invalid JSON response: {resp.text[:200]}",
                        status_code=resp.status_code,
                    )

            if self._is_retryable(resp.status_code) and attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Threads API %s %s returned %d, retrying in %ds (attempt %d/%d)",
                    method, path, resp.status_code, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
                continue

            raise ThreadsAPIError(resp.text, status_code=resp.status_code)
        # Should not reach here
        raise ThreadsAPIError(f"Max retries exceeded for {method} {path}")

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        all_params = {"access_token": self._token}
        if params:
            all_params.update(params)
        return self._request_with_retry("GET", path, params=all_params)

    def _post(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {"access_token": self._token}
        return self._request_with_retry("POST", path, params=params, json=data or {})

    # ── User Info ─────────────────────────────────────────

    def get_me(self) -> dict[str, Any]:
        """Get the authenticated user's profile."""
        data = self._get("/me", params={"fields": "id,username,name,threads_biography"})
        self._user_id = data.get("id")
        return data

    def _ensure_user_id(self) -> str:
        if not self._user_id:
            me = self.get_me()
            self._user_id = me["id"]
        return self._user_id

    # ── Publishing ────────────────────────────────────────

    def create_text_post(
        self,
        text: str,
        reply_control: str = "everyone",
    ) -> str:
        """Create and publish a text post.

        Args:
            text: Post text content (max 500 chars).
            reply_control: "everyone", "accounts_you_follow", or "mentioned_only".

        Returns:
            Published post ID.
        """
        allowed, remaining = self._rate_limiter.check_publish_limit()
        if not allowed:
            raise ThreadsAPIError(
                f"Publish rate limit reached (250/24h). Remaining: {remaining}"
            )

        user_id = self._ensure_user_id()

        # Step 1: Create media container
        create_resp = self._post(
            f"/{user_id}/threads",
            data={
                "media_type": "TEXT",
                "text": text,
                "reply_control": reply_control,
            },
        )
        container_id = create_resp.get("id")
        if not container_id:
            raise ThreadsAPIError(f"Failed to create container: {create_resp}")

        # Step 2: Wait for processing
        self._wait_for_container(container_id)

        # Step 3: Publish
        publish_resp = self._post(
            f"/{user_id}/threads_publish",
            data={"creation_id": container_id},
        )

        self._rate_limiter.record("publish")

        post_id = publish_resp.get("id")
        if not post_id:
            raise ThreadsAPIError(f"Failed to publish: {publish_resp}")

        logger.info("Published post %s", post_id)
        return post_id

    def create_image_post(
        self,
        text: str,
        image_url: str,
        reply_control: str = "everyone",
    ) -> str:
        """Create and publish an image post.

        Args:
            text: Post text.
            image_url: Public URL of the image.
            reply_control: Reply control setting.

        Returns:
            Published post ID.
        """
        allowed, remaining = self._rate_limiter.check_publish_limit()
        if not allowed:
            raise ThreadsAPIError(f"Publish rate limit reached. Remaining: {remaining}")

        user_id = self._ensure_user_id()

        create_resp = self._post(
            f"/{user_id}/threads",
            data={
                "media_type": "IMAGE",
                "text": text,
                "image_url": image_url,
                "reply_control": reply_control,
            },
        )
        container_id = create_resp.get("id")
        if not container_id:
            raise ThreadsAPIError(f"Failed to create container: {create_resp}")

        self._wait_for_container(container_id)

        publish_resp = self._post(
            f"/{user_id}/threads_publish",
            data={"creation_id": container_id},
        )
        self._rate_limiter.record("publish")

        post_id = publish_resp.get("id")
        if not post_id:
            raise ThreadsAPIError(f"Failed to publish: {publish_resp}")

        return post_id

    def create_carousel_post(
        self,
        text: str,
        image_urls: list[str],
        reply_control: str = "everyone",
    ) -> str:
        """Create and publish a carousel post with multiple images.

        Args:
            text: Post text.
            image_urls: List of public image URLs (2-20 images).
            reply_control: Reply control setting.

        Returns:
            Published post ID.
        """
        if len(image_urls) < 2:
            raise ThreadsAPIError("Carousel requires at least 2 images")
        if len(image_urls) > 20:
            raise ThreadsAPIError("Carousel supports max 20 images")

        allowed, remaining = self._rate_limiter.check_publish_limit()
        if not allowed:
            raise ThreadsAPIError(f"Publish rate limit reached. Remaining: {remaining}")

        user_id = self._ensure_user_id()

        # Create individual image containers
        item_ids = []
        for url in image_urls:
            resp = self._post(
                f"/{user_id}/threads",
                data={
                    "media_type": "IMAGE",
                    "image_url": url,
                    "is_carousel_item": True,
                },
            )
            item_id = resp.get("id")
            if not item_id:
                raise ThreadsAPIError(f"Failed to create carousel item: {resp}")
            item_ids.append(item_id)

        # Create carousel container
        create_resp = self._post(
            f"/{user_id}/threads",
            data={
                "media_type": "CAROUSEL",
                "text": text,
                "children": ",".join(item_ids),
                "reply_control": reply_control,
            },
        )
        container_id = create_resp.get("id")
        if not container_id:
            raise ThreadsAPIError(f"Failed to create carousel: {create_resp}")

        self._wait_for_container(container_id)

        publish_resp = self._post(
            f"/{user_id}/threads_publish",
            data={"creation_id": container_id},
        )
        self._rate_limiter.record("publish")

        return publish_resp.get("id", "")

    def _wait_for_container(
        self,
        container_id: str,
        max_wait: int = 30,
        interval: float = 2.0,
    ) -> None:
        """Poll container status until it's ready to publish."""
        user_id = self._ensure_user_id()
        for _ in range(int(max_wait / interval)):
            status_resp = self._get(
                f"/{container_id}",
                params={"fields": "status"},
            )
            status = status_resp.get("status")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise ThreadsAPIError(f"Container processing failed: {status_resp}")
            time.sleep(interval)
        raise ThreadsAPIError(f"Container not ready after {max_wait}s")

    # ── Own Posts & Insights ──────────────────────────────

    def get_my_posts(
        self,
        fields: str = "id,text,timestamp,like_count,reply_count,repost_count,quote_count",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Get the authenticated user's recent posts."""
        user_id = self._ensure_user_id()
        data = self._get(
            f"/{user_id}/threads",
            params={"fields": fields, "limit": str(limit)},
        )
        return data.get("data", [])

    def get_post_insights(
        self,
        post_id: str,
        metrics: str = "views,likes,replies,reposts,quotes,shares",
    ) -> dict[str, int]:
        """Get insights for a specific post.

        Returns dict of metric_name -> value.
        """
        data = self._get(
            f"/{post_id}/insights",
            params={"metric": metrics},
        )
        result = {}
        for entry in data.get("data", []):
            name = entry.get("name", "")
            values = entry.get("values", [{}])
            result[name] = values[0].get("value", 0) if values else 0
        return result

    # ── Keyword Search ────────────────────────────────────

    def keyword_search(
        self,
        query: str,
        fields: str = "id,text,timestamp,like_count,reply_count,repost_count,quote_count",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Search public Threads posts by keyword.

        Requires threads_keyword_search permission with Advanced Access.
        """
        allowed, remaining = self._rate_limiter.check_search_limit()
        if not allowed:
            raise ThreadsAPIError(
                f"Search rate limit reached (500/7days). Remaining: {remaining}"
            )

        params = {
            "q": query,
            "search_type": "keyword",
            "fields": fields,
            "limit": str(min(limit, 25)),
        }

        data = self._get("/search", params=params)
        self._rate_limiter.record("search")
        return data.get("data", [])

    def keyword_search_paginated(
        self,
        query: str,
        fields: str = "id,text,timestamp,like_count,reply_count,repost_count,quote_count",
        max_results: int = 100,
        delay: float = 1.0,
    ) -> list[dict[str, Any]]:
        """Search with automatic pagination and rate limit checking."""
        all_posts: list[dict[str, Any]] = []
        params: dict[str, str] = {
            "q": query,
            "search_type": "keyword",
            "fields": fields,
            "limit": "25",
        }

        while len(all_posts) < max_results:
            allowed, remaining = self._rate_limiter.check_search_limit()
            if not allowed:
                logger.warning("Search rate limit reached, stopping. Remaining: %d", remaining)
                break

            data = self._get("/search", params=params)
            self._rate_limiter.record("search")

            posts = data.get("data", [])
            if not posts:
                break

            all_posts.extend(posts)

            paging = data.get("paging", {})
            next_cursor = paging.get("cursors", {}).get("after")
            if not next_cursor:
                break

            params["after"] = next_cursor
            time.sleep(delay)

        return all_posts[:max_results]

    def get_user_profile(self, user_id: str) -> dict[str, Any]:
        """Get a user's public profile."""
        return self._get(
            f"/{user_id}",
            params={"fields": "id,username,name,threads_biography,threads_profile_picture_url"},
        )

    # ── Token Management ─────────────────────────────────

    def refresh_long_lived_token(self) -> dict[str, Any]:
        """Refresh a long-lived token before it expires.

        Long-lived tokens are valid for 60 days. This endpoint exchanges
        a valid (non-expired) long-lived token for a new one.
        Should be called periodically (e.g. every 50 days).

        Returns:
            Dict with new access_token and expires_in.
        """
        resp = self._http.get(
            "/oauth/access_token",
            params={
                "grant_type": "th_exchange_token",
                "access_token": self._token,
            },
        )
        if resp.status_code >= 400:
            raise ThreadsAPIError(
                f"Token refresh failed: {resp.text}",
                status_code=resp.status_code,
            )
        data = resp.json()
        new_token = data.get("access_token")
        if new_token:
            self._token = new_token
            # Update profile config with new token
            self._save_refreshed_token(new_token)
        return data

    def _save_refreshed_token(self, new_token: str) -> None:
        """Save the refreshed token back to the profile config."""
        settings = get_settings()
        config_path = Path(settings.data_dir) / "config.json"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                config["threads_access_token"] = new_token
                config_path.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info("Refreshed token saved to %s", config_path)
            except Exception as e:
                logger.warning("Failed to save refreshed token: %s", e)

    def get_token_info(self) -> dict[str, Any]:
        """Debug token to check expiration."""
        resp = self._http.get(
            "/debug_token",
            params={
                "input_token": self._token,
                "access_token": self._token,
            },
        )
        if resp.status_code >= 400:
            # debug_token may not be available, return basic info
            return {"status": "unknown", "token_prefix": self._token[:8] + "..."}
        return resp.json().get("data", {})

    # ── Rate Limit Info ───────────────────────────────────

    def get_rate_limit_usage(self) -> dict:
        """Get current rate limit usage."""
        return self._rate_limiter.get_usage_summary()
