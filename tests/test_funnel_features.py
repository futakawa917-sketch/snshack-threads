"""Tests for funnel tracking features: post_type, velocity, follower tracking, templates, A/B tests."""

from datetime import datetime, timedelta

import pytest

from snshack_threads.ab_test import ABTest, ABTestManager, determine_winner
from snshack_threads.follower_tracker import FollowerCorrelation, FollowerTracker
from snshack_threads.post_history import MetricSnapshot, PostHistory, PostRecord
from snshack_threads.profile_audit import audit_profile
from snshack_threads.templates import generate_draft_outline, generate_templates


# ── PostRecord: post_type + char_count + snapshots ───────────


class TestPostRecordPostType:
    def test_default_type_is_reach(self):
        r = PostRecord(text="test", scheduled_at="2026-03-01T09:00:00")
        assert r.post_type == "reach"

    def test_list_type(self):
        r = PostRecord(text="test", scheduled_at="2026-03-01T09:00:00", post_type="list")
        assert r.post_type == "list"

    def test_char_count(self):
        r = PostRecord(text="補助金を活用しましょう", scheduled_at="2026-03-01T09:00:00")
        assert r.char_count == 11

    def test_roundtrip_with_type(self):
        r = PostRecord(text="test", scheduled_at="2026-03-01T09:00:00", post_type="list")
        d = r.to_dict()
        r2 = PostRecord.from_dict(d)
        assert r2.post_type == "list"

    def test_roundtrip_with_snapshots(self):
        r = PostRecord(text="test", scheduled_at="2026-03-01T09:00:00")
        r.snapshots = [
            MetricSnapshot(collected_at="2026-03-01T10:00:00", elapsed_hours=1, views=100, likes=5, replies=2),
            MetricSnapshot(collected_at="2026-03-01T12:00:00", elapsed_hours=3, views=500, likes=20, replies=8),
        ]
        d = r.to_dict()
        r2 = PostRecord.from_dict(d)
        assert len(r2.snapshots) == 2
        assert r2.snapshots[0].views == 100
        assert r2.snapshots[1].elapsed_hours == 3


class TestPostHistoryPostType:
    def test_record_with_type(self, tmp_path):
        history = PostHistory(history_path=tmp_path / "h.json")
        history.record_scheduled("reach post", datetime(2026, 3, 1, 9, 0), post_type="reach")
        history.record_scheduled("list post", datetime(2026, 3, 1, 12, 0), post_type="list")
        assert history.get_all()[0].post_type == "reach"
        assert history.get_all()[1].post_type == "list"

    def test_get_by_type(self, tmp_path):
        history = PostHistory(history_path=tmp_path / "h.json")
        history.record_scheduled("reach1", datetime.now() - timedelta(days=1), post_type="reach")
        history.record_scheduled("list1", datetime.now() - timedelta(days=1), post_type="list")
        history.record_scheduled("reach2", datetime.now() - timedelta(days=1), post_type="reach")

        reach = history.get_by_type("reach", days=30)
        assert len(reach) == 2
        list_posts = history.get_by_type("list", days=30)
        assert len(list_posts) == 1

    def test_add_snapshot(self, tmp_path):
        history = PostHistory(history_path=tmp_path / "h.json")
        now = datetime.now()
        record = history.record_scheduled("test", now - timedelta(hours=2))
        snapshot = history.add_snapshot(record, views=200, likes=10, replies=3)
        assert snapshot.elapsed_hours == 2
        assert snapshot.views == 200
        assert len(record.snapshots) == 1

        # Verify persistence
        h2 = PostHistory(history_path=tmp_path / "h.json")
        assert len(h2.get_all()[0].snapshots) == 1

    def test_get_early_collection(self, tmp_path):
        history = PostHistory(history_path=tmp_path / "h.json")
        now = datetime.now()
        # 3 hours ago — eligible
        history.record_scheduled("early", now - timedelta(hours=3))
        # 30 minutes ago — too new
        history.record_scheduled("too_new", now - timedelta(minutes=30))
        # 24 hours ago — too old
        history.record_scheduled("too_old", now - timedelta(hours=24))

        eligible = history.get_early_collection(max_age_hours=6)
        assert len(eligible) == 1
        assert eligible[0].text == "early"


# ── FollowerTracker ──────────────────────────────────────────


class TestFollowerTracker:
    def test_record_and_retrieve(self, tmp_path):
        tracker = FollowerTracker(tracker_path=tmp_path / "f.json")
        tracker.record_snapshot("2026-03-01", 1000, 15, "バズ投稿", 5000, ["数字訴求"])
        assert tracker.count == 1
        s = tracker.get_all()[0]
        assert s.followers_count == 1000
        assert s.delta == 15
        assert s.top_post_views == 5000

    def test_update_existing_date(self, tmp_path):
        tracker = FollowerTracker(tracker_path=tmp_path / "f.json")
        tracker.record_snapshot("2026-03-01", 1000, 15)
        tracker.record_snapshot("2026-03-01", 1010, 25)
        assert tracker.count == 1
        assert tracker.get_all()[0].delta == 25

    def test_persistence(self, tmp_path):
        path = tmp_path / "f.json"
        t1 = FollowerTracker(tracker_path=path)
        t1.record_snapshot("2026-03-01", 1000, 10)
        t2 = FollowerTracker(tracker_path=path)
        assert t2.count == 1

    def test_get_recent(self, tmp_path):
        tracker = FollowerTracker(tracker_path=tmp_path / "f.json")
        tracker.record_snapshot("2020-01-01", 500, 5)  # old
        tracker.record_snapshot(datetime.now().strftime("%Y-%m-%d"), 1000, 10)
        recent = tracker.get_recent(days=30)
        assert len(recent) == 1

    def test_analyze_correlation(self, tmp_path):
        tracker = FollowerTracker(tracker_path=tmp_path / "f.json")
        # 3 days with high views
        for i in range(3):
            tracker.record_snapshot(
                f"2026-03-0{i+1}", 1000 + i * 10, 20,
                top_post_views=5000, top_post_text="viral",
            )
        # 3 days with low views
        for i in range(3):
            tracker.record_snapshot(
                f"2026-03-0{i+4}", 1030 + i * 2, 3,
                top_post_views=500, top_post_text="normal",
            )

        corr = tracker.analyze_correlation(views_threshold=3000)
        assert corr is not None
        assert corr.avg_delta_above == 20.0
        assert corr.avg_delta_below == 3.0
        assert corr.days_above == 3
        assert corr.lift > 1

    def test_correlation_insufficient_data(self, tmp_path):
        tracker = FollowerTracker(tracker_path=tmp_path / "f.json")
        tracker.record_snapshot("2026-03-01", 1000, 10, top_post_views=5000)
        assert tracker.analyze_correlation() is None


# ── ProfileAudit ─────────────────────────────────────────────


class TestProfileAudit:
    def test_full_profile_passes(self, tmp_path):
        profile = {
            "username": "hojokin_pro",
            "name": "補助金のプロ｜年間100件申請支援",
            "threads_biography": "補助金・助成金について毎日発信しています。経営者向けの情報をわかりやすく解説。",
            "threads_profile_picture_url": "https://example.com/pic.jpg",
        }
        result = audit_profile(profile)
        assert result.score >= 80

    def test_empty_profile_fails(self, tmp_path):
        profile = {"username": "", "name": "", "threads_biography": "", "threads_profile_picture_url": ""}
        result = audit_profile(profile)
        assert result.score < 50
        assert len(result.failed_checks) >= 3

    def test_with_history_frequency(self, tmp_path):
        profile = {
            "username": "test", "name": "Test User",
            "threads_biography": "毎日情報発信中",
            "threads_profile_picture_url": "https://example.com/pic.jpg",
        }
        history = PostHistory(history_path=tmp_path / "h.json")
        # 10 posts in 2 weeks = 5/week → passes
        for i in range(10):
            history.record_scheduled(f"post {i}", datetime.now() - timedelta(days=i))

        result = audit_profile(profile, history=history)
        freq_check = next((c for c in result.checks if "投稿頻度" in c.name), None)
        assert freq_check is not None
        assert freq_check.passed


# ── Templates ────────────────────────────────────────────────


class TestTemplates:
    def test_generate_templates(self, tmp_path):
        history = PostHistory(history_path=tmp_path / "h.json")
        # Create posts with different hooks
        posts_data = [
            ("知らないと損する補助金3選！活用しないともったいない", 5000, 50),
            ("なぜ9割の経営者は補助金を逃すのか？", 3000, 30),
            ("補助金申請の5つのコツを実際にやってみた", 2000, 20),
        ]
        for text, views, likes in posts_data:
            r = history.record_scheduled(text, datetime(2026, 3, 1, 9, 0))
            history.update_metrics(r, views=views, likes=likes, replies=5, reposts=2, quotes=1, engagement=0.03)

        tmpls = generate_templates(history)
        assert len(tmpls) > 0
        assert tmpls[0].avg_views > 0
        assert tmpls[0].post_count > 0
        assert tmpls[0].best_length_bucket in ("short", "medium", "long")

    def test_generate_templates_empty_history(self, tmp_path):
        history = PostHistory(history_path=tmp_path / "h.json")
        assert generate_templates(history) == []

    def test_draft_outline(self):
        outline = generate_draft_outline("数字訴求", "2026年の補助金")
        assert "2026年の補助金" in outline
        assert len(outline) > 0

    def test_draft_outline_unknown_hook(self):
        outline = generate_draft_outline("未知のフック", "テスト")
        assert "テスト" in outline


# ── ABTest ───────────────────────────────────────────────────


class TestABTest:
    def test_create_and_retrieve(self, tmp_path):
        manager = ABTestManager(test_path=tmp_path / "ab.json")
        test = manager.create_test(
            theme="補助金", variant_a_text="補助金3選！",
            variant_b_text="補助金申請したい人いますか？",
        )
        assert test.test_id
        assert test.status == "running"
        assert manager.count == 1

    def test_persistence(self, tmp_path):
        path = tmp_path / "ab.json"
        m1 = ABTestManager(test_path=path)
        m1.create_test(theme="test", variant_a_text="A", variant_b_text="B")
        m2 = ABTestManager(test_path=path)
        assert m2.count == 1

    def test_determine_winner_a_wins(self):
        test = ABTest(
            test_id="t1", theme="test",
            variant_a_text="A", variant_b_text="B",
            a_views=5000, a_likes=50, a_replies=10,
            b_views=1000, b_likes=10, b_replies=2,
        )
        determine_winner(test)
        assert test.winner == "A"
        assert test.confidence == "high"
        assert test.status == "completed"

    def test_determine_winner_draw(self):
        test = ABTest(
            test_id="t1", theme="test",
            variant_a_text="A", variant_b_text="B",
            a_views=1000, a_likes=10, a_replies=5,
            b_views=1050, b_likes=11, b_replies=5,
        )
        determine_winner(test)
        assert test.winner == "draw"

    def test_determine_winner_no_data(self):
        test = ABTest(test_id="t1", theme="test", variant_a_text="A", variant_b_text="B")
        determine_winner(test)
        assert test.winner == "draw"
        assert test.confidence == "low"

    def test_update_results(self, tmp_path):
        manager = ABTestManager(test_path=tmp_path / "ab.json")
        test = manager.create_test(theme="test", variant_a_text="A", variant_b_text="B")

        result = manager.update_results(
            test.test_id,
            a_views=5000, a_likes=50, a_replies=10, a_engagement=0.05,
            b_views=1000, b_likes=10, b_replies=2, b_engagement=0.02,
        )
        assert result is not None
        assert result.winner == "A"
        assert result.status == "completed"

    def test_get_running(self, tmp_path):
        manager = ABTestManager(test_path=tmp_path / "ab.json")
        manager.create_test(theme="t1", variant_a_text="A1", variant_b_text="B1")
        t2 = manager.create_test(theme="t2", variant_a_text="A2", variant_b_text="B2")
        manager.update_results(t2.test_id, 100, 10, 5, 0.05, 200, 20, 10, 0.1)

        running = manager.get_running()
        assert len(running) == 1

    def test_hooks_detected(self):
        test = ABTest(
            test_id="t1", theme="test",
            variant_a_text="知らないと損する補助金3選！",
            variant_b_text="なぜ経営者は補助金を逃すのか？",
        )
        assert "数字訴求" in test.a_hooks or "危機感" in test.a_hooks
        assert "疑問形" in test.b_hooks
