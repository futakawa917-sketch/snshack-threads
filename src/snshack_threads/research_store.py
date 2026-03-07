"""Persistent storage for research results and competitor data.

Stores keyword search results and competitor snapshots as JSON,
enabling trend analysis over time.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ResearchSnapshot:
    """A single research run's results for one keyword."""

    keyword: str
    timestamp: str  # ISO datetime
    total_posts: int = 0
    avg_likes: float = 0.0
    avg_replies: float = 0.0
    avg_engagement: float = 0.0
    top_hooks: list[dict] = field(default_factory=list)  # [{name, count, avg_likes}]
    top_posts: list[dict] = field(default_factory=list)  # [{text, likes, replies, ...}]
    hook_gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ResearchSnapshot:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CompetitorAccount:
    """A competitor account to watch."""

    username: str
    display_name: str = ""
    notes: str = ""  # e.g. "direct competitor", "industry leader"
    added_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CompetitorAccount:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CompetitorSnapshot:
    """A snapshot of a competitor's posts at a point in time."""

    username: str
    timestamp: str
    posts: list[dict] = field(default_factory=list)  # [{text, likes, replies, ...}]
    post_count: int = 0
    avg_likes: float = 0.0
    avg_replies: float = 0.0
    top_hooks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CompetitorSnapshot:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ResearchStore:
    """Persistent research data manager."""

    def __init__(self, store_path: Path | None = None) -> None:
        if store_path is None:
            settings = get_settings()
            self._dir = Path(settings.data_dir) / "research"
        else:
            self._dir = store_path
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── Keyword Research ──────────────────────────────────────

    def _keyword_path(self) -> Path:
        return self._dir / "keyword_snapshots.json"

    def _load_keyword_snapshots(self) -> list[ResearchSnapshot]:
        p = self._keyword_path()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return [ResearchSnapshot.from_dict(d) for d in data]
        except (json.JSONDecodeError, KeyError):
            logger.warning("Corrupted keyword snapshots file")
            return []

    def _save_keyword_snapshots(self, snapshots: list[ResearchSnapshot]) -> None:
        self._atomic_write(
            self._keyword_path(),
            json.dumps([s.to_dict() for s in snapshots], ensure_ascii=False, indent=2),
        )

    def save_research_snapshot(self, snapshot: ResearchSnapshot) -> None:
        """Append a new keyword research snapshot."""
        all_snaps = self._load_keyword_snapshots()
        all_snaps.append(snapshot)
        self._save_keyword_snapshots(all_snaps)

    def get_keyword_history(
        self, keyword: str, days: int = 90
    ) -> list[ResearchSnapshot]:
        """Get snapshots for a keyword over time."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        return [
            s for s in self._load_keyword_snapshots()
            if s.keyword == keyword and s.timestamp >= cutoff
        ]

    def get_keyword_trend(self, keyword: str, days: int = 90) -> dict:
        """Get trend data for a keyword (avg likes/engagement over time)."""
        snapshots = self.get_keyword_history(keyword, days)
        if not snapshots:
            return {"keyword": keyword, "snapshots": 0}

        return {
            "keyword": keyword,
            "snapshots": len(snapshots),
            "latest_avg_likes": snapshots[-1].avg_likes,
            "latest_avg_engagement": snapshots[-1].avg_engagement,
            "trend_likes": [
                {"date": s.timestamp[:10], "avg_likes": s.avg_likes}
                for s in snapshots
            ],
            "trending_hooks": snapshots[-1].top_hooks[:5] if snapshots[-1].top_hooks else [],
        }

    # ── Competitor Accounts ───────────────────────────────────

    def _competitors_path(self) -> Path:
        return self._dir / "competitors.json"

    def _load_competitors(self) -> list[CompetitorAccount]:
        p = self._competitors_path()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return [CompetitorAccount.from_dict(d) for d in data]
        except (json.JSONDecodeError, KeyError):
            return []

    def _save_competitors(self, competitors: list[CompetitorAccount]) -> None:
        self._atomic_write(
            self._competitors_path(),
            json.dumps([c.to_dict() for c in competitors], ensure_ascii=False, indent=2),
        )

    def add_competitor(self, username: str, display_name: str = "", notes: str = "") -> CompetitorAccount:
        """Add a competitor account to watch."""
        competitors = self._load_competitors()
        # Check for duplicates
        for c in competitors:
            if c.username == username:
                raise ValueError(f"Competitor '{username}' already registered")

        account = CompetitorAccount(
            username=username,
            display_name=display_name,
            notes=notes,
            added_at=datetime.now().isoformat(),
        )
        competitors.append(account)
        self._save_competitors(competitors)
        return account

    def remove_competitor(self, username: str) -> bool:
        """Remove a competitor account."""
        competitors = self._load_competitors()
        before = len(competitors)
        competitors = [c for c in competitors if c.username != username]
        if len(competitors) == before:
            return False
        self._save_competitors(competitors)
        return True

    def list_competitors(self) -> list[CompetitorAccount]:
        return self._load_competitors()

    # ── Competitor Snapshots ──────────────────────────────────

    def _competitor_snapshots_path(self) -> Path:
        return self._dir / "competitor_snapshots.json"

    def _load_competitor_snapshots(self) -> list[CompetitorSnapshot]:
        p = self._competitor_snapshots_path()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return [CompetitorSnapshot.from_dict(d) for d in data]
        except (json.JSONDecodeError, KeyError):
            return []

    def _save_competitor_snapshots(self, snapshots: list[CompetitorSnapshot]) -> None:
        self._atomic_write(
            self._competitor_snapshots_path(),
            json.dumps([s.to_dict() for s in snapshots], ensure_ascii=False, indent=2),
        )

    def save_competitor_snapshot(self, snapshot: CompetitorSnapshot) -> None:
        """Append a competitor snapshot."""
        all_snaps = self._load_competitor_snapshots()
        all_snaps.append(snapshot)
        self._save_competitor_snapshots(all_snaps)

    def get_competitor_history(
        self, username: str, days: int = 90
    ) -> list[CompetitorSnapshot]:
        """Get snapshots for a competitor over time."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        return [
            s for s in self._load_competitor_snapshots()
            if s.username == username and s.timestamp >= cutoff
        ]

    # ── Utilities ─────────────────────────────────────────────

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent, suffix=".tmp", prefix="research_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
