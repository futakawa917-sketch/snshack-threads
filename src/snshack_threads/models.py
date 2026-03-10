"""Core data models for snshack-threads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MediaType(str, Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    CAROUSEL = "CAROUSEL"


class ReplyControl(str, Enum):
    EVERYONE = "EVERYONE"
    ACCOUNTS_YOU_FOLLOW = "ACCOUNTS_YOU_FOLLOW"
    MENTIONED_ONLY = "MENTIONED_ONLY"


@dataclass
class ThreadsPost:
    """A published Threads post with performance metrics."""

    id: str
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


@dataclass
class Brand:
    """A Metricool brand/account."""

    id: int
    label: str
    user_id: int | None = None
    timezone: str | None = None
    networks: list[dict] | None = None


@dataclass
class ThreadsAccountMetrics:
    """Account-level metrics for Threads."""

    followers_count: int = 0
    delta_followers: int = 0


@dataclass
class PostDraft:
    """A draft post ready to be scheduled."""

    text: str
    first_comment: str = ""
    media_type: MediaType = MediaType.TEXT
    reply_control: ReplyControl = ReplyControl.EVERYONE
    media_ids: list[str] = field(default_factory=list)


@dataclass
class ThreadDraft:
    """A thread chain (multiple connected posts)."""

    posts: list[PostDraft]
    first_comment: str = ""

    def __post_init__(self) -> None:
        if not self.posts:
            raise ValueError("ThreadDraft requires at least 1 post")

    @property
    def main_post(self) -> PostDraft:
        return self.posts[0]

    @property
    def chain_posts(self) -> list[PostDraft]:
        return self.posts[1:]


@dataclass
class ScheduleSlot:
    """A time slot for scheduling posts."""

    hour: int
    minute: int = 0


@dataclass
class DailySchedule:
    """A daily posting schedule with time slots."""

    slots: list[ScheduleSlot] = field(default_factory=lambda: [
        ScheduleSlot(hour=7, minute=30),
        ScheduleSlot(hour=12, minute=0),
        ScheduleSlot(hour=18, minute=0),
        ScheduleSlot(hour=19, minute=30),
        ScheduleSlot(hour=21, minute=0),
    ])

    @property
    def count(self) -> int:
        return len(self.slots)
