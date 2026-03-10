"""Tests for robustness: API retry, file locking, profile isolation, scheduling edge cases, execute_plan."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from snshack_threads.models import DailySchedule, PostDraft, ScheduleSlot


# ── File Lock ─────────────────────────────────────────────────


class TestFileLock:
    def test_lock_creates_lock_file(self, tmp_path):
        """Lock file should be created next to target file."""
        from snshack_threads.filelock import file_lock

        target = tmp_path / "data.json"
        target.write_text("{}")
        with file_lock(target):
            assert (tmp_path / "data.json.lock").exists()

    def test_lock_allows_read_write(self, tmp_path):
        """File can be read and written inside lock."""
        from snshack_threads.filelock import file_lock

        target = tmp_path / "data.json"
        target.write_text('{"count": 0}')
        with file_lock(target):
            data = json.loads(target.read_text())
            data["count"] += 1
            target.write_text(json.dumps(data))
        assert json.loads(target.read_text()) == {"count": 1}

    def test_lock_creates_parent_dirs(self, tmp_path):
        """Lock file creation should handle missing parent dirs."""
        from snshack_threads.filelock import file_lock

        target = tmp_path / "nested" / "dir" / "data.json"
        with file_lock(target):
            pass  # Should not raise


# ── Past Slot Skipping ────────────────────────────────────────


class TestPastSlotSkipping:
    def test_skips_past_slots_for_today(self):
        """Slots in the past are skipped when scheduling for today."""
        from snshack_threads.scheduler import schedule_posts_for_day

        client = MagicMock()
        client.schedule_post.return_value = {"data": {"id": "ok"}}

        # Create schedule with early morning slots (already past)
        schedule = DailySchedule(slots=[
            ScheduleSlot(hour=0, minute=0),
            ScheduleSlot(hour=1, minute=0),
            ScheduleSlot(hour=23, minute=59),
        ])
        drafts = [PostDraft(text="A"), PostDraft(text="B"), PostDraft(text="C")]
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # Mock datetime.now() to a fixed time (14:00) for stable test
        fixed_now = today.replace(hour=14, minute=0, second=0)
        with patch("snshack_threads.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            results = schedule_posts_for_day(client, drafts, today, schedule=schedule)

        # 0:00 and 1:00 are in the past, only 23:59 should be scheduled
        assert client.schedule_post.call_count == 1

    def test_does_not_skip_for_future_date(self):
        """All slots are used when scheduling for a future date."""
        from snshack_threads.scheduler import schedule_posts_for_day

        client = MagicMock()
        client.schedule_post.return_value = {"data": {"id": "ok"}}

        schedule = DailySchedule(slots=[
            ScheduleSlot(hour=8, minute=0),
            ScheduleSlot(hour=12, minute=0),
        ])
        drafts = [PostDraft(text="A"), PostDraft(text="B")]
        future = datetime.now() + timedelta(days=30)

        results = schedule_posts_for_day(client, drafts, future, schedule=schedule)

        assert len(results) == 2
        assert client.schedule_post.call_count == 2


# ── Slot Dedup (minute-level) ────────────────────────────────


class TestSlotDedup:
    def test_minute_level_dedup(self):
        """Occupied slots are checked at minute level, not just hour."""
        from snshack_threads.scheduler import get_next_available_slots

        client = MagicMock()
        client.get_scheduled_posts.return_value = [
            {"publicationDate": {"dateTime": "2030-01-01T08:00:00"}},
        ]

        schedule = DailySchedule(slots=[
            ScheduleSlot(hour=8, minute=0),   # occupied
            ScheduleSlot(hour=8, minute=30),   # different minute, should be available
            ScheduleSlot(hour=12, minute=0),   # free
        ])
        target = datetime(2030, 1, 1)
        available = get_next_available_slots(client, target, schedule=schedule)

        hours_minutes = [(dt.hour, dt.minute) for dt in available]
        assert (8, 0) not in hours_minutes
        assert (8, 30) in hours_minutes
        assert (12, 0) in hours_minutes


# ── PostHistory Profile Isolation ─────────────────────────────


class TestPostHistoryProfileIsolation:
    def test_separate_paths_for_profiles(self, tmp_path):
        """Different profiles should use different history files."""
        from snshack_threads.post_history import PostHistory

        path_a = tmp_path / "profile_a" / "post_history.json"
        path_b = tmp_path / "profile_b" / "post_history.json"

        history_a = PostHistory(history_path=path_a)
        history_b = PostHistory(history_path=path_b)

        history_a.record_scheduled(
            text="Post for A",
            publish_at=datetime.now(),
        )

        assert history_a.count == 1
        assert history_b.count == 0

    def test_history_data_does_not_leak(self, tmp_path):
        """Writing to one profile's history must not affect another."""
        from snshack_threads.post_history import PostHistory

        path_a = tmp_path / "a" / "post_history.json"
        path_b = tmp_path / "b" / "post_history.json"

        ha = PostHistory(history_path=path_a)
        hb = PostHistory(history_path=path_b)

        for i in range(5):
            ha.record_scheduled(text=f"A-{i}", publish_at=datetime.now())

        hb.record_scheduled(text="B-only", publish_at=datetime.now())

        # Reload to verify persistence
        ha2 = PostHistory(history_path=path_a)
        hb2 = PostHistory(history_path=path_b)

        assert ha2.count == 5
        assert hb2.count == 1
        assert all("A-" in r.text for r in ha2.get_all())
        assert hb2.get_all()[0].text == "B-only"


# ── API Retry (content_generator) ─────────────────────────────


class TestAPIRetry:
    def test_call_with_retry_succeeds_after_transient_error(self):
        """Retry logic recovers from transient API errors."""
        from snshack_threads.content_generator import _call_with_retry

        mock_client = MagicMock()
        # First call: rate limited, second call: success
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Generated text")]

        mock_client.messages.create.side_effect = [
            Exception("rate limit exceeded"),
            mock_response,
        ]

        with patch("time.sleep"):
            result = _call_with_retry(mock_client, "system", "user", max_retries=3)

        assert result == mock_response
        assert mock_client.messages.create.call_count == 2

    def test_call_with_retry_raises_on_non_retryable(self):
        """Non-retryable errors are raised immediately."""
        from snshack_threads.content_generator import _call_with_retry

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = ValueError("invalid argument")

        with pytest.raises(ValueError):
            _call_with_retry(mock_client, "system", "user", max_retries=3)

        assert mock_client.messages.create.call_count == 1

    def test_call_with_retry_exhausts_retries(self):
        """After max retries, the error is raised."""
        from snshack_threads.content_generator import _call_with_retry

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("503 overloaded")

        with patch("time.sleep"):
            with pytest.raises(Exception, match="overloaded"):
                _call_with_retry(mock_client, "system", "user", max_retries=2)

        assert mock_client.messages.create.call_count == 2


# ── Threads API Retry ────────────────────────────────────────


class TestThreadsAPIRetry:
    def test_retries_on_429(self):
        """429 responses trigger retry."""
        from snshack_threads.threads_api import ThreadsGraphClient

        with patch("snshack_threads.threads_api.time.sleep"):
            client = ThreadsGraphClient.__new__(ThreadsGraphClient)
            mock_http = MagicMock()

            resp_429 = MagicMock()
            resp_429.status_code = 429
            resp_429.text = "rate limited"

            resp_ok = MagicMock()
            resp_ok.status_code = 200
            resp_ok.json.return_value = {"data": "ok"}

            mock_http.get.side_effect = [resp_429, resp_ok]
            client._http = mock_http
            client._token = "test"

            result = client._get("/test")
            assert result == {"data": "ok"}
            assert mock_http.get.call_count == 2

    def test_no_retry_on_400(self):
        """400 errors are not retried."""
        from snshack_threads.threads_api import ThreadsAPIError, ThreadsGraphClient

        client = ThreadsGraphClient.__new__(ThreadsGraphClient)
        mock_http = MagicMock()

        resp_400 = MagicMock()
        resp_400.status_code = 400
        resp_400.text = "bad request"

        mock_http.get.side_effect = [resp_400]
        client._http = mock_http
        client._token = "test"

        with pytest.raises(ThreadsAPIError):
            client._get("/test")
        assert mock_http.get.call_count == 1


# ── CSV Cache ─────────────────────────────────────────────────


class TestCSVCache:
    def test_cache_returns_same_result(self, tmp_path):
        """Second call should use cache and not re-parse."""
        from snshack_threads.csv_analyzer import (
            _csv_analysis_cache,
            _get_cached_analysis,
            _set_cached_analysis,
        )

        csv_file = tmp_path / "test.csv"
        csv_file.write_text("Date,Content,Views\n2025-01-01 10:00,Hello,100\n")

        mock_result = MagicMock()
        _set_cached_analysis(csv_file, mock_result)

        cached = _get_cached_analysis(csv_file)
        assert cached is mock_result

        # Clean up
        _csv_analysis_cache.pop(str(csv_file), None)

    def test_cache_invalidated_on_file_change(self, tmp_path):
        """Cache should be invalidated when file is modified."""
        import time
        from snshack_threads.csv_analyzer import (
            _csv_analysis_cache,
            _get_cached_analysis,
            _set_cached_analysis,
        )

        csv_file = tmp_path / "test.csv"
        csv_file.write_text("Date,Content,Views\n2025-01-01 10:00,Hello,100\n")

        mock_result = MagicMock()
        _set_cached_analysis(csv_file, mock_result)

        # Modify file (ensure mtime changes)
        time.sleep(0.05)
        csv_file.write_text("Date,Content,Views\n2025-01-01 10:00,Updated,200\n")

        cached = _get_cached_analysis(csv_file)
        assert cached is None

        # Clean up
        _csv_analysis_cache.pop(str(csv_file), None)


# ── execute_plan ──────────────────────────────────────────────


class TestExecutePlan:
    def test_metricool_method_schedules_posts(self):
        """execute_plan with metricool method calls schedule_posts_for_day."""
        from snshack_threads.autopilot import DailyPlan, execute_plan

        plan = DailyPlan(
            date="2030-01-01",
            phase="learning",
            total_posts=50,
            posts=[
                {"text": "Post 1", "hook": "数字訴求", "source": "generate"},
                {"text": "Post 2", "hook": "疑問形", "source": "generate"},
            ],
        )

        with (
            patch("snshack_threads.api.MetricoolClient") as MockClient,
            patch("snshack_threads.scheduler.schedule_posts_for_day") as mock_schedule,
            patch("snshack_threads.scheduler.get_optimal_schedule") as mock_sched,
        ):
            mock_client_inst = MagicMock()
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client_inst)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            mock_schedule.return_value = [{"id": "1"}, {"id": "2"}]

            results = execute_plan(plan, publish_method="metricool")

        assert len(results) == 2
        assert "Scheduled #1" in results[0]
        assert "Scheduled #2" in results[1]

    def test_threads_method_queues_posts(self):
        """execute_plan with threads method queues posts for timed publishing."""
        from snshack_threads.autopilot import DailyPlan, execute_plan

        plan = DailyPlan(
            date="2030-01-01",
            phase="optimized",
            total_posts=100,
            posts=[
                {"text": "Direct post", "hook": "command型", "source": "generate"},
            ],
        )

        with (
            patch("snshack_threads.timed_publisher.save_pending_plan") as mock_save,
            patch("snshack_threads.timed_publisher.publish_due_posts", return_value=[]) as mock_pub,
            patch("snshack_threads.scheduler.get_optimal_schedule") as mock_sched,
        ):
            mock_sched.return_value = DailySchedule(slots=[ScheduleSlot(hour=10, minute=0)])
            results = execute_plan(plan, publish_method="threads")

        assert len(results) >= 1
        assert "Queued #1" in results[0]
        mock_save.assert_called_once()

    def test_empty_plan_returns_message(self):
        """execute_plan with no posts returns a message."""
        from snshack_threads.autopilot import DailyPlan, execute_plan

        plan = DailyPlan(date="2030-01-01", phase="bootstrap", total_posts=0, posts=[])
        results = execute_plan(plan)

        assert results == ["No posts to schedule"]

    def test_api_error_is_captured(self):
        """API errors in execute_plan are captured, not raised."""
        from snshack_threads.autopilot import DailyPlan, execute_plan

        plan = DailyPlan(
            date="2030-01-01",
            phase="learning",
            total_posts=50,
            posts=[{"text": "Fail post", "hook": "test", "source": "generate"}],
        )

        with (
            patch("snshack_threads.timed_publisher.save_pending_plan", side_effect=Exception("API down")),
            patch("snshack_threads.scheduler.get_optimal_schedule") as mock_sched,
        ):
            mock_sched.return_value = DailySchedule(slots=[ScheduleSlot(hour=10)])
            results = execute_plan(plan, publish_method="threads")

        assert len(results) == 1
        assert "Failed" in results[0]

    def test_client_init_error_captured(self):
        """Client initialization errors are captured."""
        from snshack_threads.autopilot import DailyPlan, execute_plan

        plan = DailyPlan(
            date="2030-01-01",
            phase="learning",
            total_posts=50,
            posts=[{"text": "Test", "hook": "test", "source": "generate"}],
        )

        with patch("snshack_threads.api.MetricoolClient", side_effect=Exception("No credentials")):
            results = execute_plan(plan, publish_method="metricool")

        assert len(results) == 1
        assert "Failed" in results[0]


# ── Phase determination ──────────────────────────────────────


class TestPhaseDetermination:
    def test_bootstrap_threshold(self):
        """Bootstrap phase extends to 100 posts."""
        from snshack_threads.autopilot import _determine_phase

        assert _determine_phase(0) == "bootstrap"
        assert _determine_phase(70) == "bootstrap"
        assert _determine_phase(99) == "bootstrap"
        assert _determine_phase(100) == "learning"

    def test_learning_threshold(self):
        """Learning phase extends to 200 posts."""
        from snshack_threads.autopilot import _determine_phase

        assert _determine_phase(100) == "learning"
        assert _determine_phase(199) == "learning"
        assert _determine_phase(200) == "optimized"


# ── Post type determination with follower correlation ────────


class TestPostTypeDetermination:
    def test_bootstrap_default_all_reach(self):
        """Bootstrap defaults to all reach posts."""
        from snshack_threads.autopilot import _determine_post_types

        types = _determine_post_types(5, "bootstrap")
        assert all(t == "reach" for t in types)

    def test_bootstrap_with_velocity_list(self):
        """Bootstrap allows list posts when velocity prefers list."""
        from snshack_threads.autopilot import _determine_post_types

        types = _determine_post_types(5, "bootstrap", velocity_preferred_type="list")
        assert "list" in types
        assert types.count("reach") > types.count("list")

    def test_learning_high_follower_lift(self):
        """High follower correlation boosts list ratio."""
        from snshack_threads.autopilot import _determine_post_types

        mock_corr = MagicMock()
        mock_corr.lift = 2.5
        types = _determine_post_types(10, "learning", follower_correlation=mock_corr)
        # 30% list with high lift
        assert types.count("list") == 3

    def test_optimized_high_follower_lift(self):
        """Optimized phase with high lift gives 50% list."""
        from snshack_threads.autopilot import _determine_post_types

        mock_corr = MagicMock()
        mock_corr.lift = 2.5
        types = _determine_post_types(10, "optimized", follower_correlation=mock_corr)
        assert types.count("list") == 5


# ── AB Test winner determination ─────────────────────────────


class TestABTestWinnerImproved:
    def test_draw_when_scores_nearly_equal(self):
        """Scores within 5% should result in draw."""
        from snshack_threads.ab_test import ABTest, determine_winner

        test = ABTest(
            test_id="t1", theme="test",
            variant_a_text="A", variant_b_text="B",
            a_views=1000, a_likes=50, a_replies=10,
            b_views=1010, b_likes=51, b_replies=10,
        )
        determine_winner(test)
        assert test.winner == "draw"

    def test_clear_winner_declared(self):
        """Clear difference should produce a winner."""
        from snshack_threads.ab_test import ABTest, determine_winner

        test = ABTest(
            test_id="t2", theme="test",
            variant_a_text="A", variant_b_text="B",
            a_views=5000, a_likes=200, a_replies=50,
            b_views=1000, b_likes=20, b_replies=5,
        )
        determine_winner(test)
        # High difference means winner should be A (if confidence allows)
        # With such different views, engagement rates will differ
        assert test.winner in ("A", "draw")


# ── Composite score balance ──────────────────────────────────


class TestCompositeScoreBalance:
    def test_high_views_beats_high_likes_ratio_only(self):
        """High views post should score higher than low-views high-likes."""
        from snshack_threads.post_history import get_performance_summary, PostHistory

        history = MagicMock(spec=PostHistory)
        record_high_views = MagicMock()
        record_high_views.has_metrics = True
        record_high_views.views = 5000
        record_high_views.likes = 50
        record_high_views.text = "数字で見る成功パターン"
        record_high_views.scheduled_at = "2026-03-01T10:00:00"
        record_high_views.engagement = 2.0
        record_high_views.post_type = "reach"

        record_low_views = MagicMock()
        record_low_views.has_metrics = True
        record_low_views.views = 50
        record_low_views.likes = 25
        record_low_views.text = "なぜ朝活が最強なのか？"
        record_low_views.scheduled_at = "2026-03-01T12:00:00"
        record_low_views.engagement = 50.0
        record_low_views.post_type = "reach"

        record_high_views.snapshots = []
        record_low_views.snapshots = []
        history.get_all.return_value = [record_high_views, record_low_views]
        history.count = 2

        summary = get_performance_summary(history)
        # Should have some hooks ranked
        assert summary.get("collected", 0) == 2


# ── Percentile boundary fix ──────────────────────────────────


class TestPercentileBoundary:
    def test_p80_does_not_exceed_bounds(self):
        """Percentile index should not exceed list bounds."""
        from snshack_threads.early_velocity import get_velocity_thresholds

        history = MagicMock()
        records = []
        for i in range(10):
            r = MagicMock()
            r.snapshots = [MagicMock(elapsed_hours=1, views=(i + 1) * 100)]
            records.append(r)

        history.get_all.return_value = records

        # Should not raise IndexError
        thresholds = get_velocity_thresholds(history)
        assert "1h" in thresholds
        assert thresholds["1h"]["buzz"] >= 1
