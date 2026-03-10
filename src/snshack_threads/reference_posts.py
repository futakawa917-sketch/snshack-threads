"""Reference post management — curate high-performing posts for AI generation.

Stores top posts as reference examples that inform AI content generation,
ensuring generated content matches proven patterns and tone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ReferencePost:
    """A curated reference post for content generation."""

    content: str
    views: int = 0
    likes: int = 0
    replies: int = 0
    hooks: list[str] = field(default_factory=list)
    source: str = ""  # "history", "csv", "manual"
    notes: str = ""


class ReferenceStore:
    """Persistent store for reference posts."""

    def __init__(self, store_path: Path | None = None) -> None:
        if store_path is None:
            from .config import get_settings
            settings = get_settings()
            self._path = settings.profile_dir / "reference_posts.json"
        else:
            self._path = store_path
        self._posts: list[ReferencePost] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._posts = [
                    ReferencePost(**{k: v for k, v in d.items() if k in ReferencePost.__dataclass_fields__})
                    for d in data
                ]
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupted reference_posts.json — starting fresh")
                self._posts = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(p) for p in self._posts]
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add(self, post: ReferencePost) -> None:
        """Add a reference post."""
        self._posts.append(post)
        self._save()

    def get_all(self) -> list[ReferencePost]:
        """Return all reference posts."""
        return list(self._posts)

    def get_top(self, n: int = 10) -> list[ReferencePost]:
        """Return top N reference posts by views."""
        return sorted(self._posts, key=lambda p: p.views, reverse=True)[:n]

    @property
    def count(self) -> int:
        return len(self._posts)

    def import_from_history(self, min_views: int = 1000, top_n: int = 20) -> int:
        """Import top posts from post history as reference posts.

        Returns number of posts imported.
        """
        from .csv_analyzer import _detect_hooks
        from .post_history import PostHistory

        history = PostHistory()
        collected = [r for r in history.get_all() if r.has_metrics and r.views >= min_views]
        collected.sort(key=lambda r: r.views, reverse=True)

        existing_texts = {p.content for p in self._posts}
        imported = 0

        for record in collected[:top_n]:
            if record.text in existing_texts:
                continue
            self._posts.append(ReferencePost(
                content=record.text,
                views=record.views,
                likes=record.likes,
                replies=record.replies,
                hooks=_detect_hooks(record.text),
                source="history",
            ))
            imported += 1

        if imported:
            self._save()
        return imported

    def import_from_csv(self, csv_path: str | Path, min_views: int = 1000, top_n: int = 20) -> int:
        """Import top posts from CSV export as reference posts.

        Returns number of posts imported.
        """
        from .csv_analyzer import _detect_hooks, parse_csv

        rows = parse_csv(csv_path)
        posts_with_views = []
        for row in rows:
            content = row.get("Content", "")
            try:
                views = int(row.get("Views", "0").strip().replace(",", ""))
            except ValueError:
                views = 0
            if content and views >= min_views:
                posts_with_views.append((content, views, row))

        posts_with_views.sort(key=lambda x: x[1], reverse=True)

        existing_texts = {p.content for p in self._posts}
        imported = 0

        for content, views, row in posts_with_views[:top_n]:
            if content in existing_texts:
                continue
            try:
                likes = int(row.get("Likes", "0").strip().replace(",", ""))
                replies = int(row.get("Replies", "0").strip().replace(",", ""))
            except ValueError:
                likes = replies = 0

            self._posts.append(ReferencePost(
                content=content,
                views=views,
                likes=likes,
                replies=replies,
                hooks=_detect_hooks(content),
                source="csv",
            ))
            imported += 1

        if imported:
            self._save()
        return imported
