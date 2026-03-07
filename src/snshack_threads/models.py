"""Data models for Metricool / Threads."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MediaType(StrEnum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"


class ReplyControl(StrEnum):
    EVERYONE = "everyone"
    ACCOUNTS_YOU_FOLLOW = "accounts_you_follow"
    MENTIONED_ONLY = "mentioned_only"


class ThreadsPost(BaseModel):
    """A Threads post returned from Metricool analytics."""

    id: str | None = None
    text: str | None = None
    date: datetime | None = None
    views: int = 0
    likes: int = 0
    replies: int = 0
    reposts: int = 0
    quotes: int = 0
    engagement: float = 0.0
    interactions: int = 0
    permalink: str | None = None

    @property
    def engagement_rate_pct(self) -> str:
        return f"{self.engagement * 100:.2f}%"


class ThreadsAccountMetrics(BaseModel):
    """Account-level metrics for Threads."""

    followers_count: int = 0
    delta_followers: int = 0


class PostDraft(BaseModel):
    """Draft for creating/scheduling a new Threads post via Metricool."""

    text: str = Field(max_length=500)
    media_type: MediaType = MediaType.TEXT
    media_ids: list[str] = Field(default_factory=list)
    reply_control: ReplyControl = ReplyControl.EVERYONE
    first_comment: str = Field(default="", max_length=500)


class ThreadDraft(BaseModel):
    """Draft for a thread chain (multiple connected posts).

    The first item is the main post; subsequent items become
    descendants (replies in the thread chain). Each reply gets
    its own algorithmic distribution chance.
    """

    posts: list[PostDraft] = Field(min_length=1, max_length=10)
    first_comment: str = Field(default="", max_length=500)

    @property
    def main_post(self) -> PostDraft:
        return self.posts[0]

    @property
    def chain_posts(self) -> list[PostDraft]:
        return self.posts[1:]


class ScheduleSlot(BaseModel):
    """A time slot for scheduled posting."""

    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59, default=0)


class DailySchedule(BaseModel):
    """Daily posting schedule with 5 default slots."""

    slots: list[ScheduleSlot] = Field(
        default_factory=lambda: [
            ScheduleSlot(hour=8, minute=0),
            ScheduleSlot(hour=11, minute=0),
            ScheduleSlot(hour=14, minute=0),
            ScheduleSlot(hour=18, minute=0),
            ScheduleSlot(hour=21, minute=0),
        ]
    )

    @property
    def count(self) -> int:
        return len(self.slots)


class Brand(BaseModel):
    """A Metricool brand."""

    id: int
    label: str
    user_id: int
    timezone: str | None = None
    networks: list[dict] | None = None
