"""Threads API client."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .config import Settings, get_settings
from .models import (
    MediaType,
    PostDraft,
    ThreadsMetrics,
    ThreadsPost,
    UserProfile,
)


class ThreadsAPIError(Exception):
    """Raised when the Threads API returns an error."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class ThreadsClient:
    """Client for the Threads API (v1.0).

    Reference: https://developers.facebook.com/docs/threads/
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.validate_credentials():
            raise ThreadsAPIError(
                "Missing credentials. Set THREADS_ACCESS_TOKEN and THREADS_USER_ID."
            )
        self._http = httpx.Client(
            base_url=self._settings.api_base,
            params={"access_token": self._settings.access_token},
            timeout=30.0,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ThreadsClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ── helpers ──────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise ThreadsAPIError(resp.text, status_code=resp.status_code)
        return resp.json()

    def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self._request("GET", path, **kwargs)

    def _post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self._request("POST", path, **kwargs)

    # ── profile ─────────────────────────────────────────────

    def get_profile(self) -> UserProfile:
        """Fetch the authenticated user's profile."""
        data = self._get(
            f"/{self._settings.user_id}",
            params={"fields": "id,username,threads_biography,threads_profile_picture_url"},
        )
        return UserProfile(**data)

    # ── posts ───────────────────────────────────────────────

    def get_posts(self, limit: int = 25) -> list[ThreadsPost]:
        """Fetch recent posts for the authenticated user."""
        data = self._get(
            f"/{self._settings.user_id}/threads",
            params={
                "fields": "id,text,media_type,media_url,permalink,timestamp,username,is_quote_post",
                "limit": str(limit),
            },
        )
        return [ThreadsPost(**post) for post in data.get("data", [])]

    def get_post(self, post_id: str) -> ThreadsPost:
        """Fetch a single post by ID."""
        data = self._get(
            f"/{post_id}",
            params={
                "fields": "id,text,media_type,media_url,permalink,timestamp,username,is_quote_post"
            },
        )
        return ThreadsPost(**data)

    def create_post(self, draft: PostDraft) -> ThreadsPost:
        """Publish a new Threads post (two-step: create container, then publish).

        Reference: https://developers.facebook.com/docs/threads/posts
        """
        # Step 1: create media container
        container_params: dict[str, str] = {
            "media_type": draft.media_type.value,
            "text": draft.text,
        }
        if draft.image_url and draft.media_type == MediaType.IMAGE:
            container_params["image_url"] = draft.image_url
        if draft.video_url and draft.media_type == MediaType.VIDEO:
            container_params["video_url"] = draft.video_url
        if draft.reply_to_id:
            container_params["reply_to_id"] = draft.reply_to_id
        if draft.reply_control.value != "everyone":
            container_params["reply_control"] = draft.reply_control.value

        container = self._post(
            f"/{self._settings.user_id}/threads",
            params=container_params,
        )
        container_id = container["id"]

        # Step 2: publish the container
        # Short delay to allow processing
        time.sleep(2)
        result = self._post(
            f"/{self._settings.user_id}/threads_publish",
            params={"creation_id": container_id},
        )
        return self.get_post(result["id"])

    # ── insights / metrics ──────────────────────────────────

    def get_post_metrics(self, post_id: str) -> ThreadsMetrics:
        """Fetch engagement metrics for a single post."""
        data = self._get(
            f"/{post_id}/insights",
            params={"metric": "views,likes,replies,reposts,quotes"},
        )
        values: dict[str, int] = {}
        for entry in data.get("data", []):
            name = entry["name"]
            val = entry.get("values", [{}])[0].get("value", 0)
            values[name] = val
        return ThreadsMetrics(post_id=post_id, **values)

    def get_user_metrics(self) -> dict[str, int]:
        """Fetch user-level metrics (follower count, etc.)."""
        data = self._get(
            f"/{self._settings.user_id}/threads_insights",
            params={"metric": "views,likes,replies,reposts,quotes,followers_count"},
        )
        result: dict[str, int] = {}
        for entry in data.get("data", []):
            name = entry["name"]
            val = entry.get("total_value", {}).get("value", 0)
            result[name] = val
        return result
