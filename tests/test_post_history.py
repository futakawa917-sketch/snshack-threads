"""Tests for post history tracking and performance collection."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from snshack_threads.models import ThreadsPost
from snshack_threads.post_history import (
    PostHistory,
    PostRecord,
    collect_performance,
)


@pytest.fixture
def history(tmp_path):
    return PostHistory(history_path=tmp_path / "history.json")


class TestPostRecord:
    def test_total_interactions(self):
        r = PostRecord(text="test", scheduled_at="2026-03-01T09:00:00")
        r.likes = 10
        r.replies = 5
        r.reposts = 3
        r.quotes = 2
        assert r.total_interactions == 20

    def test_has_metrics_false_by_default(self):
        r = PostRecord(text="test", scheduled_at="2026-03-01T09:00:00")
        assert not r.has_metrics

    def test_has_metrics_true_with_views(self):
        r = PostRecord(text="test", scheduled_at="2026-03-01T09:00:00", views=100)
        assert r.has_metrics

    def test_roundtrip_dict(self):
        r = PostRecord(
            text="補助金の話", scheduled_at="2026-03-01T09:00:00",
            views=500, likes=10,
        )
        d = r.to_dict()
        r2 = PostRecord.from_dict(d)
        assert r2.text == r.text
        assert r2.views == r.views
        assert r2.likes == r.likes


class TestPostHistory:
    def test_record_and_retrieve(self, history):
        history.record_scheduled("テスト投稿", datetime(2026, 3, 1, 9, 0))
        assert history.count == 1
        assert history.get_all()[0].text == "テスト投稿"

    def test_persistence(self, tmp_path):
        path = tmp_path / "history.json"
        h1 = PostHistory(history_path=path)
        h1.record_scheduled("投稿1", datetime(2026, 3, 1, 9, 0))
        h1.record_scheduled("投稿2", datetime(2026, 3, 1, 12, 0))

        # Reload from disk
        h2 = PostHistory(history_path=path)
        assert h2.count == 2
        assert h2.get_all()[0].text == "投稿1"
        assert h2.get_all()[1].text == "投稿2"

    def test_get_pending_collection(self, history):
        # Post 48 hours ago — should be pending
        old = datetime.now() - timedelta(hours=48)
        history.record_scheduled("古い投稿", old)

        # Post 1 hour ago — too recent
        recent = datetime.now() - timedelta(hours=1)
        history.record_scheduled("新しい投稿", recent)

        pending = history.get_pending_collection(min_age_hours=24)
        assert len(pending) == 1
        assert pending[0].text == "古い投稿"

    def test_update_metrics(self, history):
        history.record_scheduled("テスト", datetime(2026, 3, 1, 9, 0))
        record = history.get_all()[0]

        history.update_metrics(record, views=1000, likes=50, replies=10, reposts=5, quotes=2, engagement=0.05)

        assert record.views == 1000
        assert record.likes == 50
        assert record.status == "collected"
        assert record.collected_at != ""

        # Verify persistence
        h2 = PostHistory(history_path=history._path)
        assert h2.get_all()[0].views == 1000

    def test_get_recent(self, history):
        old = datetime.now() - timedelta(days=60)
        history.record_scheduled("古い", old)

        recent = datetime.now() - timedelta(days=5)
        history.record_scheduled("最近", recent)

        results = history.get_recent(days=30)
        assert len(results) == 1
        assert results[0].text == "最近"

    def test_get_uncollected(self, history):
        history.record_scheduled("未収集", datetime(2026, 3, 1, 9, 0))
        r2 = history.record_scheduled("収集済み", datetime(2026, 3, 1, 12, 0))
        history.update_metrics(r2, views=100, likes=5, replies=1, reposts=0, quotes=0, engagement=0.01)

        uncollected = history.get_uncollected()
        assert len(uncollected) == 1
        assert uncollected[0].text == "未収集"


class TestCollectPerformance:
    def test_matches_by_text(self, history):
        old_time = datetime.now() - timedelta(hours=48)
        history.record_scheduled("補助金を活用しましょう", old_time)

        mock_client = MagicMock()
        mock_client.get_threads_posts.return_value = [
            ThreadsPost(
                id="1",
                text="補助金を活用しましょう",
                date=old_time,
                views=3000,
                likes=25,
                replies=5,
                reposts=2,
                quotes=1,
                engagement=0.03,
            ),
        ]

        updated = collect_performance(history, mock_client, min_age_hours=24)
        assert len(updated) == 1
        assert updated[0].views == 3000
        assert updated[0].likes == 25

    def test_matches_by_time_fallback(self, history):
        old_time = datetime.now() - timedelta(hours=48)
        history.record_scheduled("テキストが微妙に違う場合", old_time)

        mock_client = MagicMock()
        mock_client.get_threads_posts.return_value = [
            ThreadsPost(
                id="1",
                text="テキストが完全に別物",
                date=old_time + timedelta(minutes=5),
                views=2000,
                likes=15,
                replies=3,
                reposts=1,
                quotes=0,
                engagement=0.02,
            ),
        ]

        updated = collect_performance(history, mock_client, min_age_hours=24)
        assert len(updated) == 1
        assert updated[0].views == 2000

    def test_no_pending_returns_empty(self, history):
        mock_client = MagicMock()
        updated = collect_performance(history, mock_client)
        assert updated == []
        mock_client.get_threads_posts.assert_not_called()

    def test_no_match_returns_empty(self, history):
        old_time = datetime.now() - timedelta(hours=48)
        history.record_scheduled("マッチしない投稿", old_time)

        mock_client = MagicMock()
        mock_client.get_threads_posts.return_value = [
            ThreadsPost(
                id="1",
                text="完全に別の投稿",
                date=datetime.now(),  # time doesn't match either
                views=5000,
                likes=100,
            ),
        ]

        updated = collect_performance(history, mock_client, min_age_hours=24)
        assert updated == []

    def test_skips_already_collected(self, history):
        old_time = datetime.now() - timedelta(hours=48)
        record = history.record_scheduled("既に収集済み", old_time)
        history.update_metrics(record, views=100, likes=5, replies=0, reposts=0, quotes=0, engagement=0.01)

        mock_client = MagicMock()
        mock_client.get_threads_posts.return_value = []

        # Should not try to collect since record is already "collected"
        pending = history.get_pending_collection(min_age_hours=24)
        assert len(pending) == 0
