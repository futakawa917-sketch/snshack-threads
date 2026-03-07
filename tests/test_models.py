"""Tests for data models."""

from snshack_threads.models import (
    DailySchedule,
    MediaType,
    PostDraft,
    ReplyControl,
    ScheduleSlot,
    ThreadsPost,
)


class TestThreadsPost:
    def test_minimal(self):
        p = ThreadsPost(id="123")
        assert p.id == "123"
        assert p.text is None
        assert p.views == 0

    def test_engagement_rate_pct(self):
        p = ThreadsPost(id="1", engagement=0.042)
        assert p.engagement_rate_pct == "4.20%"

    def test_zero_engagement(self):
        p = ThreadsPost(id="1", engagement=0.0)
        assert p.engagement_rate_pct == "0.00%"


class TestPostDraft:
    def test_text_only(self):
        d = PostDraft(text="Hello Threads!")
        assert d.media_type == MediaType.TEXT
        assert d.reply_control == ReplyControl.EVERYONE
        assert d.media_ids == []

    def test_image_post(self):
        d = PostDraft(text="With image", media_type=MediaType.IMAGE)
        assert d.media_type == MediaType.IMAGE


class TestDailySchedule:
    def test_default_5_slots(self):
        schedule = DailySchedule()
        assert schedule.count == 5
        hours = [s.hour for s in schedule.slots]
        assert hours == [8, 11, 14, 18, 21]

    def test_custom_slots(self):
        schedule = DailySchedule(
            slots=[
                ScheduleSlot(hour=9, minute=30),
                ScheduleSlot(hour=15, minute=0),
            ]
        )
        assert schedule.count == 2
