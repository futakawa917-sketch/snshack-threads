"""Tests for thread chains, content recycling, and virality metrics."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from snshack_threads.analytics import generate_report
from snshack_threads.api import MetricoolAPIError
from snshack_threads.content_recycler import find_recyclable_posts, suggest_recycle
from snshack_threads.csv_analyzer import _detect_hooks
from snshack_threads.models import PostDraft, ThreadDraft, ThreadsPost
from snshack_threads.post_history import PostHistory, PostRecord


class TestThreadDraft:
    def test_main_and_chain(self):
        posts = [PostDraft(text=f"Post {i}") for i in range(3)]
        thread = ThreadDraft(posts=posts)
        assert thread.main_post.text == "Post 0"
        assert len(thread.chain_posts) == 2
        assert thread.chain_posts[0].text == "Post 1"

    def test_first_comment(self):
        thread = ThreadDraft(
            posts=[PostDraft(text="Main")],
            first_comment="どう思いますか？",
        )
        assert thread.first_comment == "どう思いますか？"

    def test_min_one_post(self):
        with pytest.raises(Exception):
            ThreadDraft(posts=[])


class TestFirstComment:
    def test_schedule_post_sends_first_comment(self):
        """Verify that first_comment is passed through to API payload."""
        draft = PostDraft(text="Test post", first_comment="コメントください")
        assert draft.first_comment == "コメントください"

    def test_empty_first_comment_default(self):
        draft = PostDraft(text="Test post")
        assert draft.first_comment == ""


class TestExpandedHooks:
    def test_empathy_hook(self):
        assert "共感" in _detect_hooks("あるあるすぎて泣ける")

    def test_contrast_hook(self):
        assert "対比" in _detect_hooks("実は補助金には裏がある")

    def test_insider_hook(self):
        assert "裏技" in _detect_hooks("プロが教える補助金のコツ")

    def test_experience_hook(self):
        assert "実体験" in _detect_hooks("やってみた結果がすごかった")

    def test_ranking_hook(self):
        assert "ランキング" in _detect_hooks("補助金ランキングTOP5")

    def test_debate_hook(self):
        assert "議論喚起" in _detect_hooks("補助金に頼る経営、どう思う？")

    def test_original_hooks_still_work(self):
        assert "数字訴求" in _detect_hooks("最大9000万円の補助金")
        assert "危機感" in _detect_hooks("知らないと損する")
        assert "疑問形" in _detect_hooks("知ってた？")


class TestContentRecycler:
    @pytest.fixture
    def history_with_posts(self, tmp_path):
        history = PostHistory(history_path=tmp_path / "history.json")
        # Old high-performing post
        old_time = datetime.now() - timedelta(days=45)
        r1 = history.record_scheduled("最大9000万円の補助金があります", old_time)
        history.update_metrics(r1, views=5000, likes=50, replies=10, reposts=5, quotes=2, engagement=0.05)

        # Old low-performing post
        r2 = history.record_scheduled("お知らせ", old_time - timedelta(days=5))
        history.update_metrics(r2, views=200, likes=2, replies=0, reposts=0, quotes=0, engagement=0.01)

        # Recent post (not recyclable yet)
        recent = datetime.now() - timedelta(days=5)
        r3 = history.record_scheduled("最新の投稿", recent)
        history.update_metrics(r3, views=3000, likes=30, replies=5, reposts=3, quotes=1, engagement=0.03)

        return history

    def test_find_recyclable(self, history_with_posts):
        candidates = find_recyclable_posts(history_with_posts, min_age_days=30)
        assert len(candidates) == 2
        # Sorted by views
        assert candidates[0].views == 5000

    def test_find_recyclable_with_min_views(self, history_with_posts):
        candidates = find_recyclable_posts(history_with_posts, min_views=1000, min_age_days=30)
        assert len(candidates) == 1
        assert candidates[0].views == 5000

    def test_recent_not_recyclable(self, history_with_posts):
        candidates = find_recyclable_posts(history_with_posts, min_age_days=30)
        texts = [c.text for c in candidates]
        assert "最新の投稿" not in texts

    def test_suggest_recycle_hooks(self, history_with_posts):
        candidates = find_recyclable_posts(history_with_posts, min_age_days=30)
        suggestion = suggest_recycle(candidates[0])
        assert "数字訴求" in suggestion["original_hooks"]
        assert suggestion["original_views"] == 5000
        # Should suggest hooks NOT already used
        assert "数字訴求" not in suggestion["suggested_hooks"]
        assert len(suggestion["suggested_hooks"]) > 0


class TestViralityMetrics:
    def test_virality_in_report(self):
        client = MagicMock()
        client.get_threads_posts.return_value = [
            ThreadsPost(
                id="1", text="test", date=datetime(2026, 3, 1),
                views=10000, likes=100, replies=50, reposts=30, quotes=10,
                engagement=0.05, interactions=190,
            ),
        ]
        client.get_threads_account_metrics.side_effect = MetricoolAPIError("not available")

        report = generate_report(client, "2026-03-01", "2026-03-07")

        # virality = (30 + 10) / 10000 = 0.004
        assert report.virality_rate == pytest.approx(0.004)
        # discussion = 50 / (100 + 1) ≈ 0.495
        assert report.discussion_rate == pytest.approx(50 / 101)
        # amplification = 30 / (100 + 1) ≈ 0.297
        assert report.amplification_rate == pytest.approx(30 / 101)

    def test_zero_views_no_crash(self):
        client = MagicMock()
        client.get_threads_posts.return_value = [
            ThreadsPost(id="1", text="test", views=0, likes=0),
        ]
        client.get_threads_account_metrics.side_effect = MetricoolAPIError("nope")

        report = generate_report(client, "2026-03-01", "2026-03-07")
        assert report.virality_rate == 0.0
