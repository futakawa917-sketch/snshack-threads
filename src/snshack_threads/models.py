"""Data models for Threads API responses."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MediaType(StrEnum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    CAROUSEL = "CAROUSEL"


class ReplyControl(StrEnum):
    EVERYONE = "everyone"
    ACCOUNTS_YOU_FOLLOW = "accounts_you_follow"
    MENTIONED_ONLY = "mentioned_only"


class ThreadsPost(BaseModel):
    """A single Threads post."""

    id: str
    text: str | None = None
    media_type: MediaType = MediaType.TEXT
    media_url: str | None = None
    permalink: str | None = None
    timestamp: datetime | None = None
    username: str | None = None
    is_quote_post: bool = False


class ThreadsMetrics(BaseModel):
    """Engagement metrics for a post."""

    post_id: str
    views: int = 0
    likes: int = 0
    replies: int = 0
    reposts: int = 0
    quotes: int = 0
    followers_count: int | None = None

    @property
    def engagement_rate(self) -> float:
        """Calculate engagement rate (interactions / views)."""
        if self.views == 0:
            return 0.0
        interactions = self.likes + self.replies + self.reposts + self.quotes
        return interactions / self.views

    @property
    def total_interactions(self) -> int:
        return self.likes + self.replies + self.reposts + self.quotes


class UserProfile(BaseModel):
    """Threads user profile."""

    id: str
    username: str
    threads_biography: str | None = None
    threads_profile_picture_url: str | None = None


class PostDraft(BaseModel):
    """Draft for creating a new Threads post."""

    text: str = Field(max_length=500)
    media_type: MediaType = MediaType.TEXT
    image_url: str | None = None
    video_url: str | None = None
    reply_control: ReplyControl = ReplyControl.EVERYONE
    reply_to_id: str | None = None
