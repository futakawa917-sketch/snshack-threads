"""Tests for the Metricool API client (using respx to mock HTTP)."""

import json

import pytest
import respx
from httpx import Response

from snshack_threads.api import MetricoolAPIError, MetricoolClient
from snshack_threads.config import Settings
from snshack_threads.models import PostDraft

API_BASE = "https://app.metricool.com/api"


@pytest.fixture
def settings():
    return Settings(
        user_token="test_token",
        user_id="12345",
        blog_id="67890",
        timezone="Asia/Tokyo",
        api_base=API_BASE,
    )


@pytest.fixture
def client(settings):
    c = MetricoolClient(settings=settings)
    yield c
    c.close()


class TestMetricoolClient:
    def test_missing_credentials_raises(self):
        with pytest.raises(MetricoolAPIError, match="Missing credentials"):
            MetricoolClient(settings=Settings(user_token="", user_id="", blog_id=""))

    @respx.mock
    def test_get_brands(self, client):
        respx.get(f"{API_BASE}/v2/settings/brands").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {
                            "id": 111,
                            "label": "My Brand",
                            "userId": 12345,
                            "timezone": "Asia/Tokyo",
                            "networksData": [{"network": "threads"}],
                        }
                    ]
                },
            )
        )
        brands = client.get_brands()
        assert len(brands) == 1
        assert brands[0].label == "My Brand"
        assert brands[0].timezone == "Asia/Tokyo"

    @respx.mock
    def test_get_threads_posts(self, client):
        respx.get(f"{API_BASE}/v2/analytics/posts/threads").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {
                            "id": "p1",
                            "text": "Hello Threads",
                            "views": 500,
                            "likes": 30,
                            "replies": 5,
                            "reposts": 2,
                            "quotes": 1,
                            "engagement": 0.076,
                            "interactions": 38,
                        },
                        {
                            "id": "p2",
                            "text": "Second post",
                            "views": 200,
                            "likes": 10,
                            "replies": 2,
                            "reposts": 0,
                            "quotes": 0,
                            "engagement": 0.06,
                            "interactions": 12,
                        },
                    ]
                },
            )
        )
        posts = client.get_threads_posts("2026-03-01", "2026-03-07")
        assert len(posts) == 2
        assert posts[0].text == "Hello Threads"
        assert posts[0].views == 500
        assert posts[0].likes == 30

    @respx.mock
    def test_schedule_post(self, client):
        respx.post(f"{API_BASE}/v2/scheduler/posts").mock(
            return_value=Response(200, json={"data": {"id": "sched_1"}})
        )
        from datetime import datetime

        draft = PostDraft(text="Scheduled post")
        result = client.schedule_post(draft, datetime(2026, 3, 8, 14, 0))
        assert "data" in result

    @respx.mock
    def test_get_scheduled_posts(self, client):
        respx.get(f"{API_BASE}/v2/scheduler/posts").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {
                            "text": "Pending post",
                            "publicationDate": {
                                "dateTime": "2026-03-08T14:00:00",
                                "timezone": "Asia/Tokyo",
                            },
                            "providers": [{"network": "threads"}],
                        }
                    ]
                },
            )
        )
        posts = client.get_scheduled_posts("2026-03-08", "2026-03-14")
        assert len(posts) == 1
        assert posts[0]["text"] == "Pending post"

    @respx.mock
    def test_api_error_handling(self, client):
        respx.get(f"{API_BASE}/v2/settings/brands").mock(
            return_value=Response(401, json={"error": "Invalid token"})
        )
        with pytest.raises(MetricoolAPIError) as exc:
            client.get_brands()
        assert exc.value.status_code == 401
