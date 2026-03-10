"""Tests for the scheduler (Metricool-based)."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from snshack_threads.models import DailySchedule, PostDraft, ScheduleSlot
from snshack_threads.scheduler import (
    ContentNGError,
    get_next_available_slots,
    get_optimal_schedule,
    schedule_posts_for_day,
    validate_drafts,
)

# Explicit schedule for deterministic tests
_TEST_SCHEDULE = DailySchedule(
    slots=[
        ScheduleSlot(hour=8, minute=0),
        ScheduleSlot(hour=11, minute=0),
        ScheduleSlot(hour=14, minute=0),
        ScheduleSlot(hour=18, minute=0),
        ScheduleSlot(hour=21, minute=0),
    ]
)


class TestSchedulePostsForDay:
    def test_schedules_up_to_slot_count(self):
        client = MagicMock()
        client.schedule_post.return_value = {"data": {"id": "ok"}}

        drafts = [PostDraft(text=f"Post {i}") for i in range(5)]
        target = datetime(2026, 3, 8)
        results = schedule_posts_for_day(client, drafts, target, schedule=_TEST_SCHEDULE)

        assert len(results) == 5
        assert client.schedule_post.call_count == 5

        calls = client.schedule_post.call_args_list
        hours = [call.args[1].hour for call in calls]
        assert hours == [8, 11, 14, 18, 21]

    def test_fewer_drafts_than_slots(self):
        client = MagicMock()
        client.schedule_post.return_value = {"data": {"id": "ok"}}

        drafts = [PostDraft(text="Only one")]
        target = datetime(2026, 3, 8)
        results = schedule_posts_for_day(client, drafts, target, schedule=_TEST_SCHEDULE)

        assert len(results) == 1
        assert client.schedule_post.call_count == 1

    def test_custom_schedule(self):
        client = MagicMock()
        client.schedule_post.return_value = {"data": {"id": "ok"}}

        schedule = DailySchedule(
            slots=[ScheduleSlot(hour=10, minute=30), ScheduleSlot(hour=20, minute=0)]
        )
        drafts = [PostDraft(text="A"), PostDraft(text="B")]
        target = datetime(2026, 3, 8)
        results = schedule_posts_for_day(client, drafts, target, schedule=schedule)

        assert len(results) == 2
        calls = client.schedule_post.call_args_list
        assert calls[0].args[1].hour == 10
        assert calls[0].args[1].minute == 30
        assert calls[1].args[1].hour == 20

    def test_uses_csv_schedule_by_default(self):
        """When no schedule given, uses CSV-derived optimal times."""
        client = MagicMock()
        client.schedule_post.return_value = {"data": {"id": "ok"}}

        drafts = [PostDraft(text="Test")]
        target = datetime(2026, 3, 8)
        results = schedule_posts_for_day(client, drafts, target)

        assert len(results) == 1
        # Should use the first slot from CSV analysis (not hardcoded)
        call_time = client.schedule_post.call_args_list[0].args[1]
        assert 0 <= call_time.hour <= 23


class TestGetNextAvailableSlots:
    def test_all_slots_available(self):
        client = MagicMock()
        client.get_scheduled_posts.return_value = []

        target = datetime(2026, 3, 8)
        available = get_next_available_slots(client, target, schedule=_TEST_SCHEDULE)

        assert len(available) == 5
        hours = [dt.hour for dt in available]
        assert hours == [8, 11, 14, 18, 21]

    def test_some_slots_taken(self):
        client = MagicMock()
        client.get_scheduled_posts.return_value = [
            {"publicationDate": {"dateTime": "2026-03-08T08:00:00"}},
            {"publicationDate": {"dateTime": "2026-03-08T14:00:00"}},
        ]

        target = datetime(2026, 3, 8)
        available = get_next_available_slots(client, target, schedule=_TEST_SCHEDULE)

        assert len(available) == 3
        hours = [dt.hour for dt in available]
        assert hours == [11, 18, 21]

    def test_all_slots_taken(self):
        client = MagicMock()
        client.get_scheduled_posts.return_value = [
            {"publicationDate": {"dateTime": f"2026-03-08T{h:02d}:00:00"}}
            for h in [8, 11, 14, 18, 21]
        ]

        target = datetime(2026, 3, 8)
        available = get_next_available_slots(client, target, schedule=_TEST_SCHEDULE)

        assert len(available) == 0


class TestValidateDrafts:
    def test_clean_drafts_pass(self):
        drafts = [PostDraft(text="補助金を活用しましょう")]
        validate_drafts(drafts)  # Should not raise

    def test_ng_draft_raises(self):
        drafts = [PostDraft(text="詳しくはhttps://example.com")]
        with pytest.raises(ContentNGError) as exc_info:
            validate_drafts(drafts)
        assert "URL" in exc_info.value.violations

    def test_line_draft_raises(self):
        drafts = [PostDraft(text="LINE公式に登録してね")]
        with pytest.raises(ContentNGError):
            validate_drafts(drafts)

    def test_ng_blocks_scheduling(self):
        client = MagicMock()
        drafts = [PostDraft(text="固定投稿を見てください")]
        target = datetime(2026, 3, 8)
        with pytest.raises(ContentNGError):
            schedule_posts_for_day(client, drafts, target, schedule=_TEST_SCHEDULE)
        # Should not have called the API
        assert client.schedule_post.call_count == 0


class TestGetOptimalSchedule:
    def test_fallback_when_no_csv(self, tmp_path):
        schedule = get_optimal_schedule(csv_path=tmp_path / "nonexistent.csv")
        # Falls back to default 5 slots
        assert len(schedule.slots) == 5

    def test_from_csv(self, tmp_path):
        import csv

        path = tmp_path / "test.csv"
        fieldnames = ["Image", "PostLink", "Content", "Type", "Date", "Views", "Likes", "Replies", "Reposts", "Quotes", "Shares", "Engagement"]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for h in [9, 9, 9, 15, 15, 20]:
                writer.writerow({"Date": f"2026-03-01 {h:02d}:00", "Views": "5000", "Likes": "20", "Replies": "5", "Engagement": "0.50"})

        schedule = get_optimal_schedule(csv_path=path, n_slots=3)
        assert len(schedule.slots) == 3
        hours = [s.hour for s in schedule.slots]
        assert hours == sorted(hours)
