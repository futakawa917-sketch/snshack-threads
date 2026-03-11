"""Follower growth tracking and post-attribution analysis.

Records daily follower snapshots and correlates growth with post performance
to identify which content types drive follower acquisition.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class FollowerSnapshot:
    """A single day's follower data with attribution context."""

    date: str  # YYYY-MM-DD
    followers_count: int = 0
    delta: int = 0

    # Attribution context: best-performing post on this day
    top_post_text: str = ""
    top_post_views: int = 0
    top_post_hooks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> FollowerSnapshot:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class FollowerCorrelation:
    """Correlation between post performance and follower growth."""

    views_threshold: int
    avg_delta_above: float  # avg follower delta when top post >= threshold
    avg_delta_below: float  # avg follower delta when top post < threshold
    days_above: int
    days_below: int

    @property
    def lift(self) -> float:
        """How much more followers you gain when views exceed threshold."""
        if self.avg_delta_below == 0:
            return float("inf") if self.avg_delta_above > 0 else 0.0
        return self.avg_delta_above / self.avg_delta_below


def _get_scheduled_date(record: Any) -> str | None:
    """Extract YYYY-MM-DD from a PostRecord or dict's scheduled_at field."""
    if isinstance(record, dict):
        raw = record.get("scheduled_at", "")
    else:
        raw = getattr(record, "scheduled_at", "")
    if not raw:
        return None
    try:
        if isinstance(raw, datetime):
            return raw.strftime("%Y-%m-%d")
        return datetime.fromisoformat(str(raw)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


class FollowerTracker:
    """Persistent daily follower snapshot manager."""

    def __init__(self, tracker_path: Path | None = None) -> None:
        if tracker_path is None:
            settings = get_settings()
            self._path = settings.data_dir / "follower_snapshots.json"
        else:
            self._path = tracker_path
        self._snapshots: list[FollowerSnapshot] = []
        self._load()

    def _load(self) -> None:
        from .filelock import file_lock

        if self._path.exists():
            try:
                with file_lock(self._path):
                    data = json.loads(self._path.read_text(encoding="utf-8"))
                self._snapshots = [FollowerSnapshot.from_dict(s) for s in data]
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupted follower snapshots: %s", self._path)
                self._snapshots = []

    def _save(self) -> None:
        from .filelock import file_lock

        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            [s.to_dict() for s in self._snapshots], ensure_ascii=False, indent=2
        )
        with file_lock(self._path):
            fd, tmp_path = tempfile.mkstemp(
                dir=self._path.parent, suffix=".tmp", prefix="followers_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_path, self._path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    def record_snapshot(
        self,
        date: str,
        followers_count: int,
        delta: int,
        top_post_text: str = "",
        top_post_views: int = 0,
        top_post_hooks: list[str] | None = None,
    ) -> FollowerSnapshot:
        """Record or update a daily follower snapshot."""
        # Update existing snapshot for same date, or create new
        existing = self._find_by_date(date)
        if existing:
            existing.followers_count = followers_count
            existing.delta = delta
            if top_post_text:
                existing.top_post_text = top_post_text
                existing.top_post_views = top_post_views
                existing.top_post_hooks = top_post_hooks or []
        else:
            snapshot = FollowerSnapshot(
                date=date,
                followers_count=followers_count,
                delta=delta,
                top_post_text=top_post_text,
                top_post_views=top_post_views,
                top_post_hooks=top_post_hooks or [],
            )
            self._snapshots.append(snapshot)
            existing = snapshot

        self._save()
        return existing

    def _find_by_date(self, date: str) -> FollowerSnapshot | None:
        for s in self._snapshots:
            if s.date == date:
                return s
        return None

    def get_recent(self, days: int = 30) -> list[FollowerSnapshot]:
        """Get snapshots from the last N days, sorted by date."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        recent = [s for s in self._snapshots if s.date >= cutoff]
        recent.sort(key=lambda s: s.date)
        return recent

    def get_all(self) -> list[FollowerSnapshot]:
        return list(self._snapshots)

    def analyze_correlation(
        self, views_threshold: int = 3000
    ) -> FollowerCorrelation | None:
        """Analyze correlation between views and follower growth.

        Splits days into above/below threshold and compares avg delta.
        """
        with_attribution = [
            s for s in self._snapshots if s.top_post_views > 0
        ]
        if len(with_attribution) < 5:
            return None

        above = [s for s in with_attribution if s.top_post_views >= views_threshold]
        below = [s for s in with_attribution if s.top_post_views < views_threshold]

        if not above or not below:
            return None

        return FollowerCorrelation(
            views_threshold=views_threshold,
            avg_delta_above=sum(s.delta for s in above) / len(above),
            avg_delta_below=sum(s.delta for s in below) / len(below),
            days_above=len(above),
            days_below=len(below),
        )

    def analyze_post_attribution(
        self,
        post_records: list,
    ) -> dict[str, dict[str, float]]:
        """Analyze which hooks and post_types correlate with follower growth.

        For each day with follower growth (delta > 0), finds posts published
        that day and the day before, then ranks hooks and post_types by
        average follower delta.

        Args:
            post_records: List of PostRecord objects (or dicts with
                          text, scheduled_at, post_type fields).

        Returns:
            Dict with two keys:
              - "hook_attribution": {hook_name: avg_follower_delta}
              - "type_attribution": {post_type: avg_follower_delta}
            Sorted by avg_follower_delta descending within each sub-dict.
        """
        from .csv_analyzer import _detect_hooks

        growth_days = [s for s in self._snapshots if s.delta > 0]
        if not growth_days:
            return {"hook_attribution": {}, "type_attribution": {}}

        # Build date→posts index (keyed by YYYY-MM-DD)
        date_posts: dict[str, list] = {}
        for rec in post_records:
            scheduled = _get_scheduled_date(rec)
            if scheduled:
                date_posts.setdefault(scheduled, []).append(rec)

        # Accumulate deltas per hook and per post_type
        hook_deltas: dict[str, list[int]] = {}
        type_deltas: dict[str, list[int]] = {}

        for snap in growth_days:
            # Find posts from this day and the day before
            try:
                snap_date = datetime.strptime(snap.date, "%Y-%m-%d")
            except ValueError:
                continue
            prev_date = (snap_date - timedelta(days=1)).strftime("%Y-%m-%d")
            relevant_posts = date_posts.get(snap.date, []) + date_posts.get(prev_date, [])

            if not relevant_posts:
                continue

            for rec in relevant_posts:
                text = rec.get("text", "") if isinstance(rec, dict) else getattr(rec, "text", "")
                post_type = rec.get("post_type", "reach") if isinstance(rec, dict) else getattr(rec, "post_type", "reach")

                # Hook attribution
                hooks = _detect_hooks(text) if text else []
                for hook in hooks:
                    hook_deltas.setdefault(hook, []).append(snap.delta)

                # Post type attribution
                type_deltas.setdefault(post_type, []).append(snap.delta)

        # Average and sort
        hook_attr = {
            hook: round(sum(deltas) / len(deltas), 2)
            for hook, deltas in hook_deltas.items()
            if deltas
        }
        type_attr = {
            ptype: round(sum(deltas) / len(deltas), 2)
            for ptype, deltas in type_deltas.items()
            if deltas
        }

        # Sort descending
        hook_attr = dict(sorted(hook_attr.items(), key=lambda x: x[1], reverse=True))
        type_attr = dict(sorted(type_attr.items(), key=lambda x: x[1], reverse=True))

        return {
            "hook_attribution": hook_attr,
            "type_attribution": type_attr,
        }

    @property
    def count(self) -> int:
        return len(self._snapshots)
