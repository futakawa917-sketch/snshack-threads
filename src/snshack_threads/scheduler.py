"""Post scheduling and automation."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import get_settings
from .models import PostDraft


class ScheduledPost(BaseModel):
    """A post scheduled for future publishing."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    draft: PostDraft
    scheduled_at: datetime
    published: bool = False
    published_post_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class PostQueue:
    """Persistent queue for scheduled posts.

    Stores scheduled posts as JSON in the data directory.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or get_settings().data_dir
        self._queue_file = self._data_dir / "queue.json"
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict[str, Any]]:
        if not self._queue_file.exists():
            return []
        return json.loads(self._queue_file.read_text())

    def _save(self, items: list[dict[str, Any]]) -> None:
        self._queue_file.write_text(json.dumps(items, default=str, indent=2))

    def add(self, draft: PostDraft, scheduled_at: datetime) -> ScheduledPost:
        """Add a post to the queue."""
        entry = ScheduledPost(draft=draft, scheduled_at=scheduled_at)
        items = self._load()
        items.append(entry.model_dump())
        self._save(items)
        return entry

    def list_pending(self) -> list[ScheduledPost]:
        """Return all unpublished posts ordered by scheduled time."""
        items = self._load()
        pending = [
            ScheduledPost(**item)
            for item in items
            if not item.get("published", False)
        ]
        return sorted(pending, key=lambda p: p.scheduled_at)

    def list_all(self) -> list[ScheduledPost]:
        """Return all posts (published and pending)."""
        return [ScheduledPost(**item) for item in self._load()]

    def mark_published(self, entry_id: str, post_id: str) -> None:
        """Mark a queued entry as published."""
        items = self._load()
        for item in items:
            if item["id"] == entry_id:
                item["published"] = True
                item["published_post_id"] = post_id
                break
        self._save(items)

    def remove(self, entry_id: str) -> bool:
        """Remove a queued entry. Returns True if found and removed."""
        items = self._load()
        new_items = [i for i in items if i["id"] != entry_id]
        if len(new_items) == len(items):
            return False
        self._save(new_items)
        return True

    def get_due(self) -> list[ScheduledPost]:
        """Return pending posts whose scheduled time has passed."""
        now = datetime.now()
        return [p for p in self.list_pending() if p.scheduled_at <= now]


def publish_due_posts(queue: PostQueue | None = None) -> list[str]:
    """Publish all due posts. Returns list of published post IDs.

    This is the main automation entry point — call it periodically
    (e.g. via cron) to auto-publish scheduled posts.
    """
    from .api import ThreadsClient

    if queue is None:
        queue = PostQueue()

    due = queue.get_due()
    if not due:
        return []

    published_ids: list[str] = []
    with ThreadsClient() as client:
        for entry in due:
            post = client.create_post(entry.draft)
            queue.mark_published(entry.id, post.id)
            published_ids.append(post.id)

    return published_ids
