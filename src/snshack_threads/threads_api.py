"""Threads Graph API client for keyword search and competitor research.

Uses Meta's official Threads API (graph.threads.net).
Requires: threads_basic + threads_keyword_search permissions.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import httpx

from .config import get_settings

GRAPH_API_BASE = "https://graph.threads.net/v1.0"

# Rate limit: 500 queries per rolling 7 days
SEARCH_RATE_LIMIT = 500


class ThreadsAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


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

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ThreadsGraphClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        all_params = {"access_token": self._token}
        if params:
            all_params.update(params)
        resp = self._http.get(path, params=all_params)
        if resp.status_code >= 400:
            raise ThreadsAPIError(resp.text, status_code=resp.status_code)
        try:
            return resp.json()
        except (ValueError, json.JSONDecodeError):
            raise ThreadsAPIError(
                f"Invalid JSON response: {resp.text[:200]}",
                status_code=resp.status_code,
            )

    def keyword_search(
        self,
        query: str,
        fields: str = "id,text,timestamp,like_count,reply_count,repost_count,quote_count",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Search public Threads posts by keyword.

        Requires threads_keyword_search permission with Advanced Access
        to search posts from other users.

        Args:
            query: Search keyword (e.g. "補助金").
            fields: Comma-separated fields to return.
            limit: Max results per page (max 25).

        Returns:
            List of post dicts with requested fields.
        """
        all_posts: list[dict[str, Any]] = []
        params = {
            "q": query,
            "search_type": "keyword",
            "fields": fields,
            "limit": str(min(limit, 25)),
        }

        data = self._get("/search", params=params)
        all_posts.extend(data.get("data", []))

        return all_posts

    def keyword_search_paginated(
        self,
        query: str,
        fields: str = "id,text,timestamp,like_count,reply_count,repost_count,quote_count",
        max_results: int = 100,
        delay: float = 1.0,
    ) -> list[dict[str, Any]]:
        """Search with automatic pagination.

        Args:
            query: Search keyword.
            fields: Fields to return.
            max_results: Max total results to collect.
            delay: Seconds between pagination requests.
        """
        all_posts: list[dict[str, Any]] = []
        params: dict[str, str] = {
            "q": query,
            "search_type": "keyword",
            "fields": fields,
            "limit": "25",
        }

        while len(all_posts) < max_results:
            data = self._get("/search", params=params)
            posts = data.get("data", [])
            if not posts:
                break

            all_posts.extend(posts)

            # Check for next page
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
