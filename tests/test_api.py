"""Tests for the Threads API client (using respx to mock HTTP)."""

import pytest
import respx
from httpx import Response

from snshack_threads.api import ThreadsAPIError, ThreadsClient
from snshack_threads.config import Settings
from snshack_threads.models import PostDraft


@pytest.fixture
def settings():
    return Settings(
        access_token="test_token",
        user_id="12345",
        api_base="https://graph.threads.net/v1.0",
    )


@pytest.fixture
def client(settings):
    c = ThreadsClient(settings=settings)
    yield c
    c.close()


class TestThreadsClient:
    def test_missing_credentials_raises(self):
        with pytest.raises(ThreadsAPIError, match="Missing credentials"):
            ThreadsClient(settings=Settings(access_token="", user_id=""))

    @respx.mock
    def test_get_profile(self, client, settings):
        respx.get(f"{settings.api_base}/{settings.user_id}").mock(
            return_value=Response(
                200,
                json={
                    "id": "12345",
                    "username": "testuser",
                    "threads_biography": "Hello!",
                },
            )
        )
        profile = client.get_profile()
        assert profile.username == "testuser"
        assert profile.threads_biography == "Hello!"

    @respx.mock
    def test_get_posts(self, client, settings):
        respx.get(f"{settings.api_base}/{settings.user_id}/threads").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {"id": "p1", "text": "Hello", "media_type": "TEXT"},
                        {"id": "p2", "text": "World", "media_type": "TEXT"},
                    ]
                },
            )
        )
        posts = client.get_posts(limit=2)
        assert len(posts) == 2
        assert posts[0].text == "Hello"

    @respx.mock
    def test_get_post_metrics(self, client, settings):
        respx.get(f"{settings.api_base}/p1/insights").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {"name": "views", "values": [{"value": 500}]},
                        {"name": "likes", "values": [{"value": 30}]},
                        {"name": "replies", "values": [{"value": 5}]},
                        {"name": "reposts", "values": [{"value": 2}]},
                        {"name": "quotes", "values": [{"value": 1}]},
                    ]
                },
            )
        )
        metrics = client.get_post_metrics("p1")
        assert metrics.views == 500
        assert metrics.likes == 30
        assert metrics.engagement_rate == (30 + 5 + 2 + 1) / 500

    @respx.mock
    def test_api_error_handling(self, client, settings):
        respx.get(f"{settings.api_base}/{settings.user_id}").mock(
            return_value=Response(401, json={"error": "Invalid token"})
        )
        with pytest.raises(ThreadsAPIError) as exc:
            client.get_profile()
        assert exc.value.status_code == 401

    @respx.mock
    def test_create_post(self, client, settings):
        # Mock container creation
        respx.post(f"{settings.api_base}/{settings.user_id}/threads").mock(
            return_value=Response(200, json={"id": "container_1"})
        )
        # Mock publish
        respx.post(f"{settings.api_base}/{settings.user_id}/threads_publish").mock(
            return_value=Response(200, json={"id": "post_1"})
        )
        # Mock get_post for result
        respx.get(f"{settings.api_base}/post_1").mock(
            return_value=Response(
                200,
                json={"id": "post_1", "text": "New post", "media_type": "TEXT"},
            )
        )
        draft = PostDraft(text="New post")
        result = client.create_post(draft)
        assert result.id == "post_1"
        assert result.text == "New post"
