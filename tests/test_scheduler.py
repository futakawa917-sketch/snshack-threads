"""Tests for the scheduler (Metricool-based)."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from snshack_threads.models import DailySchedule, PostDraft, ScheduleSlot
from snshack_threads.scheduler import (
    get_next_available_slots,
    schedule_posts_for_day,
)


class TestSchedulePostsForDay:
    def test_schedules_up_to_slot_count(self):
        client = MagicMock()
        client.schedule_post.return_value = {"data": {"id": "ok"}}

        drafts = [PostDraft(text=f"Post {i}") for i in range(5)]
        target = datetime(2026, 3, 8)
        results = schedule_posts_for_day(client, drafts, target)

        assert len(results) == 5
        assert client.schedule_post.call_count == 5

        # Verify times: 8:00, 11:00, 14:00, 18:00, 21:00
        calls = client.schedule_post.call_args_list
        hours = [call.args[1].hour for call in calls]
        assert hours == [8, 11, 14, 18, 21]

    def test_fewer_drafts_than_slots(self):
        client = MagicMock()
        client.schedule_post.return_value = {"data": {"id": "ok"}}

        drafts = [PostDraft(text="Only one")]
        target = datetime(2026, 3, 8)
        results = schedule_posts_for_day(client, drafts, target)

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


class TestGetNextAvailableSlots:
    def test_all_slots_available(self):
        client = MagicMock()
        client.get_scheduled_posts.return_value = []

        target = datetime(2026, 3, 8)
        available = get_next_available_slots(client, target)

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
        available = get_next_available_slots(client, target)

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
        available = get_next_available_slots(client, target)

        assert len(available) == 0
