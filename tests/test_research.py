"""Tests for competitor research module."""

from unittest.mock import MagicMock, patch

import pytest

from snshack_threads.research import CompetitorPost, search_and_analyze


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.keyword_search_paginated.return_value = [
        {
            "id": "1",
            "text": "最大9000万円の補助金があります",
            "timestamp": "2026-03-01T09:00:00+0000",
            "like_count": 50,
            "reply_count": 10,
            "repost_count": 5,
            "quote_count": 2,
        },
        {
            "id": "2",
            "text": "知らないとやばい！助成金の落とし穴",
            "timestamp": "2026-03-02T18:00:00+0000",
            "like_count": 30,
            "reply_count": 5,
            "repost_count": 3,
            "quote_count": 1,
        },
        {
            "id": "3",
            "text": "短い投稿",
            "timestamp": "2026-03-03T12:00:00+0000",
            "like_count": 10,
            "reply_count": 2,
            "repost_count": 0,
            "quote_count": 0,
        },
    ]
    return client


class TestSearchAndAnalyze:
    def test_basic_search(self, mock_client):
        report = search_and_analyze(mock_client, "補助金")
        assert report.keyword == "補助金"
        assert report.total_posts_found == 3
        assert len(report.posts) == 3

    def test_avg_metrics(self, mock_client):
        report = search_and_analyze(mock_client, "補助金")
        assert report.avg_likes == pytest.approx(30.0)
        assert report.avg_replies == pytest.approx(17 / 3)

    def test_hook_detection(self, mock_client):
        report = search_and_analyze(mock_client, "補助金")
        hook_names = [name for name, _, _ in report.top_hooks]
        assert "数字訴求" in hook_names  # "最大9000万円"
        assert "危機感" in hook_names  # "やばい"

    def test_top_posts_sorted_by_engagement(self, mock_client):
        report = search_and_analyze(mock_client, "補助金")
        engagements = [p.total_engagement for p in report.top_posts]
        assert engagements == sorted(engagements, reverse=True)

    def test_hook_gap_analysis(self, mock_client):
        own_hooks = {"数字訴求"}  # We already use this
        report = search_and_analyze(mock_client, "補助金", own_hooks=own_hooks)
        # 危機感 is used by competitors but not by us
        assert "危機感" in report.hook_gaps
        # 数字訴求 should NOT be in gaps (we already use it)
        assert "数字訴求" not in report.hook_gaps

    def test_length_performance(self, mock_client):
        report = search_and_analyze(mock_client, "補助金")
        assert "short" in report.length_performance

    def test_empty_results(self):
        client = MagicMock()
        client.keyword_search_paginated.return_value = []
        report = search_and_analyze(client, "nonexistent")
        assert report.total_posts_found == 0
        assert report.avg_likes == 0.0


class TestCompetitorPost:
    def test_total_engagement(self):
        post = CompetitorPost(
            id="1", text="test", timestamp="",
            likes=10, replies=5, reposts=3, quotes=2,
        )
        assert post.total_engagement == 20
