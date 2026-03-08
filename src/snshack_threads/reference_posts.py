"""Reference posts store: manage viral/example posts per profile.

Stores high-performing posts as style references for AI generation.
Data is stored as JSON at ~/.snshack-threads/profiles/{name}/reference_posts.json
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ReferencePost:
    """A reference/viral post used as style example."""

    text: str
    likes: int = 0
    source: str = ""  # e.g. "manual", "competitor", "auto"
    added_at: str = ""


class ReferenceStore:
    """Manage reference posts for a profile."""

    def __init__(self, profile: str | None = None):
        from .config import get_settings

        settings = get_settings(profile=profile)
        self._path = Path(settings.data_dir) / "reference_posts.json"

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, data: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add_reference(
        self, text: str, likes: int = 0, source: str = "manual"
    ) -> int:
        """Add a reference post. Returns the index of the added post."""
        data = self._load()
        data.append({
            "text": text,
            "likes": likes,
            "source": source,
            "added_at": datetime.now().isoformat(),
        })
        self._save(data)
        logger.info("リファレンス投稿追加 (source=%s, likes=%d)", source, likes)
        return len(data) - 1

    def get_references(self, limit: int = 10) -> list[ReferencePost]:
        """Get reference posts, sorted by likes (descending)."""
        data = self._load()
        data.sort(key=lambda x: x.get("likes", 0), reverse=True)
        return [
            ReferencePost(
                text=d.get("text", ""),
                likes=d.get("likes", 0),
                source=d.get("source", ""),
                added_at=d.get("added_at", ""),
            )
            for d in data[:limit]
        ]

    def remove_reference(self, index: int) -> bool:
        """Remove a reference post by index. Returns True if removed."""
        data = self._load()
        if 0 <= index < len(data):
            removed = data.pop(index)
            self._save(data)
            logger.info("リファレンス投稿削除: %s...", removed.get("text", "")[:30])
            return True
        return False

    def count(self) -> int:
        """Return number of stored references."""
        return len(self._load())


def auto_collect_from_competitors(profile: str | None = None) -> int:
    """Auto-collect high-performing competitor posts as references.

    Called after competitor research to save top posts.
    Returns number of new references added.
    """
    from .research_store import ResearchStore

    store = ResearchStore()
    ref_store = ReferenceStore(profile=profile)

    existing_texts = {r.text for r in ref_store.get_references(limit=1000)}
    added = 0

    for competitor in store.list_competitors():
        snapshots = store.get_competitor_snapshots(competitor.username, limit=1)
        if not snapshots:
            continue
        latest = snapshots[0]
        for post in latest.posts:
            text = post.get("text", "")
            likes = post.get("likes", 0)
            if text and likes >= 10 and text not in existing_texts:
                ref_store.add_reference(
                    text=text,
                    likes=likes,
                    source=f"competitor:{competitor.username}",
                )
                existing_texts.add(text)
                added += 1

    if added:
        logger.info("競合投稿からリファレンス %d件を自動追加", added)
    return added
