"""Tests for data models."""

from snshack_threads.models import (
    MediaType,
    PostDraft,
    ReplyControl,
    ThreadsMetrics,
    ThreadsPost,
)


class TestThreadsMetrics:
    def test_engagement_rate_with_views(self):
        m = ThreadsMetrics(post_id="1", views=1000, likes=50, replies=10, reposts=5, quotes=2)
        assert m.engagement_rate == (50 + 10 + 5 + 2) / 1000

    def test_engagement_rate_zero_views(self):
        m = ThreadsMetrics(post_id="1", views=0)
        assert m.engagement_rate == 0.0

    def test_total_interactions(self):
        m = ThreadsMetrics(post_id="1", likes=10, replies=5, reposts=3, quotes=2)
        assert m.total_interactions == 20


class TestPostDraft:
    def test_text_only(self):
        d = PostDraft(text="Hello Threads!")
        assert d.media_type == MediaType.TEXT
        assert d.reply_control == ReplyControl.EVERYONE

    def test_image_post(self):
        d = PostDraft(
            text="Check this out",
            media_type=MediaType.IMAGE,
            image_url="https://example.com/img.jpg",
        )
        assert d.media_type == MediaType.IMAGE
        assert d.image_url == "https://example.com/img.jpg"


class TestThreadsPost:
    def test_minimal(self):
        p = ThreadsPost(id="123")
        assert p.id == "123"
        assert p.text is None
        assert p.is_quote_post is False
