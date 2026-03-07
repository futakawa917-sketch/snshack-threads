"""Metricool API client for Threads automation & analytics."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings, get_settings
from .models import Brand, PostDraft, ThreadsAccountMetrics, ThreadsPost


class MetricoolAPIError(Exception):
    """Raised when the Metricool API returns an error."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class MetricoolClient:
    """Client for the Metricool REST API.

    Endpoints discovered from the official mcp-metricool project.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.validate_credentials():
            raise MetricoolAPIError(
                "Missing credentials. Set METRICOOL_USER_TOKEN, METRICOOL_USER_ID, and METRICOOL_BLOG_ID."
            )
        self._http = httpx.Client(
            base_url=self._settings.api_base,
            headers={
                "X-Mc-Auth": self._settings.user_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> MetricoolClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ── helpers ──────────────────────────────────────────────

    def _common_params(self) -> dict[str, str]:
        return {
            "blogId": self._settings.blog_id,
            "userId": self._settings.user_id,
        }

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        all_params = self._common_params()
        if params:
            all_params.update(params)
        resp = self._http.get(path, params=all_params)
        if resp.status_code >= 400:
            raise MetricoolAPIError(resp.text, status_code=resp.status_code)
        return resp.json()

    def _post(self, path: str, data: dict[str, Any] | None = None, params: dict[str, str] | None = None) -> dict[str, Any]:
        all_params = self._common_params()
        if params:
            all_params.update(params)
        resp = self._http.post(path, params=all_params, content=json.dumps(data) if data else None)
        if resp.status_code >= 400:
            raise MetricoolAPIError(resp.text, status_code=resp.status_code)
        return resp.json()

    # ── brands ───────────────────────────────────────────────

    def get_brands(self) -> list[Brand]:
        """Fetch all brands for this account."""
        data = self._get("/v2/settings/brands")
        return [
            Brand(
                id=item["id"],
                label=item.get("label", ""),
                user_id=item.get("userId", 0),
                timezone=item.get("timezone"),
                networks=item.get("networksData"),
            )
            for item in data.get("data", [])
        ]

    # ── Threads posts (analytics) ────────────────────────────

    def get_threads_posts(
        self, start: str, end: str
    ) -> list[ThreadsPost]:
        """Fetch Threads posts with analytics for a date range.

        Args:
            start: Start date in YYYY-MM-DD format.
            end: End date in YYYY-MM-DD format.
        """
        data = self._get(
            "/v2/analytics/posts/threads",
            params={
                "from": f"{start}T00:00:00",
                "to": f"{end}T23:59:59",
            },
        )
        posts = []
        for item in data.get("data", data.get("posts", [])):
            posts.append(ThreadsPost(
                id=str(item.get("id", "")),
                text=item.get("text"),
                date=item.get("date"),
                views=item.get("views", 0),
                likes=item.get("likes", 0),
                replies=item.get("replies", 0),
                reposts=item.get("reposts", 0),
                quotes=item.get("quotes", 0),
                engagement=item.get("engagement", 0.0),
                interactions=item.get("interactions", 0),
                permalink=item.get("permalink"),
            ))
        return posts

    def get_threads_account_metrics(
        self, start: str, end: str
    ) -> ThreadsAccountMetrics:
        """Fetch Threads account-level metrics."""
        tz = quote(self._settings.timezone, safe="")
        data = self._get(
            "/v2/analytics/timelines",
            params={
                "from": f"{start}T00:00:00",
                "to": f"{end}T23:59:59",
                "network": "threads",
                "subject": "account",
                "timezone": tz,
            },
        )
        metrics_data = data.get("data", {})
        return ThreadsAccountMetrics(
            followers_count=metrics_data.get("followers_count", 0),
            delta_followers=metrics_data.get("delta_followers", 0),
        )

    # ── scheduling ───────────────────────────────────────────

    def schedule_post(
        self,
        draft: PostDraft,
        publish_at: datetime,
    ) -> dict[str, Any]:
        """Schedule a Threads post via Metricool.

        Args:
            draft: The post content.
            publish_at: When to publish (datetime).
        """
        tz = self._settings.timezone
        dt_str = publish_at.strftime("%Y-%m-%dT%H:%M:%S")

        post_data = {
            "autoPublish": True,
            "descendants": [],
            "draft": False,
            "firstCommentText": "",
            "hasNotReadNotes": False,
            "media": [],
            "mediaAltText": [],
            "providers": [{"network": "threads"}],
            "publicationDate": {
                "dateTime": dt_str,
                "timezone": tz,
            },
            "shortener": False,
            "smartLinkData": {"ids": []},
            "text": draft.text,
            "threadsData": {},
        }

        return self._post(
            "/v2/scheduler/posts",
            data=post_data,
        )

    def get_scheduled_posts(
        self, start: str, end: str
    ) -> list[dict[str, Any]]:
        """Fetch scheduled (pending) posts."""
        tz = quote(self._settings.timezone, safe="")
        data = self._get(
            "/v2/scheduler/posts",
            params={
                "start": f"{start}T00:00:00",
                "end": f"{end}T23:59:59",
                "timezone": tz,
                "extendedRange": "false",
            },
        )
        return data.get("data", [])

    def get_best_time_to_post(self, start: str, end: str) -> list[dict[str, Any]]:
        """Get best times to post on Threads.

        Returns list of {dayOfWeek, hour, value} entries.
        Higher value = better time.
        """
        tz = quote(self._settings.timezone, safe="")
        # Note: best times endpoint doesn't support "threads" directly,
        # using "instagram" as proxy since Threads engagement patterns
        # are similar. Adjust if Metricool adds Threads support.
        data = self._get(
            "/v2/scheduler/besttimes/instagram",
            params={
                "start": f"{start}T00:00:00",
                "end": f"{end}T23:59:59",
                "timezone": tz,
            },
        )
        return data.get("data", [])
