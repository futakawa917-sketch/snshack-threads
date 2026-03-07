"""A/B testing for Threads posts.

Schedule two variants of the same theme with different hooks,
then compare performance to identify winning patterns.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .config import get_settings
from .csv_analyzer import _detect_hooks, _text_length_bucket

logger = logging.getLogger(__name__)


@dataclass
class ABTest:
    """An A/B test comparing two post variants."""

    test_id: str
    theme: str
    variant_a_text: str
    variant_b_text: str
    variant_a_scheduled_at: str = ""  # ISO datetime
    variant_b_scheduled_at: str = ""
    created_at: str = ""
    status: str = "created"  # created | running | completed

    # Results (filled after collection)
    a_views: int = 0
    a_likes: int = 0
    a_replies: int = 0
    a_engagement: float = 0.0
    b_views: int = 0
    b_likes: int = 0
    b_replies: int = 0
    b_engagement: float = 0.0

    winner: str = ""  # "A" | "B" | "draw"
    confidence: str = ""  # "high" | "medium" | "low"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ABTest:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def a_hooks(self) -> list[str]:
        return _detect_hooks(self.variant_a_text)

    @property
    def b_hooks(self) -> list[str]:
        return _detect_hooks(self.variant_b_text)

    @property
    def a_total_engagement(self) -> int:
        return self.a_likes + self.a_replies

    @property
    def b_total_engagement(self) -> int:
        return self.b_likes + self.b_replies


def determine_winner(test: ABTest) -> ABTest:
    """Determine the winner based on views and engagement.

    Uses views as primary metric, engagement as tiebreaker.
    """
    if test.a_views == 0 and test.b_views == 0:
        test.winner = "draw"
        test.confidence = "low"
        return test

    # Primary: views ratio
    if test.a_views > 0 and test.b_views > 0:
        ratio = max(test.a_views, test.b_views) / min(test.a_views, test.b_views)
    elif test.a_views > 0:
        ratio = float("inf")
    else:
        ratio = float("inf")

    if ratio >= 2.0:
        test.confidence = "high"
    elif ratio >= 1.3:
        test.confidence = "medium"
    else:
        test.confidence = "low"

    # Determine winner
    a_score = test.a_views * 0.7 + test.a_total_engagement * 100
    b_score = test.b_views * 0.7 + test.b_total_engagement * 100

    if abs(a_score - b_score) < a_score * 0.1:  # Within 10%
        test.winner = "draw"
        test.confidence = "low"
    elif a_score > b_score:
        test.winner = "A"
    else:
        test.winner = "B"

    test.status = "completed"
    return test


class ABTestManager:
    """Persistent A/B test manager."""

    def __init__(self, test_path: Path | None = None) -> None:
        if test_path is None:
            settings = get_settings()
            self._path = settings.data_dir / "ab_tests.json"
        else:
            self._path = test_path
        self._tests: list[ABTest] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._tests = [ABTest.from_dict(t) for t in data]
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupted AB test file: %s", self._path)
                self._tests = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            [t.to_dict() for t in self._tests], ensure_ascii=False, indent=2
        )
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, suffix=".tmp", prefix="abtests_"
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

    def create_test(
        self,
        theme: str,
        variant_a_text: str,
        variant_b_text: str,
        variant_a_scheduled_at: str = "",
        variant_b_scheduled_at: str = "",
    ) -> ABTest:
        """Create a new A/B test."""
        test = ABTest(
            test_id=uuid.uuid4().hex[:8],
            theme=theme,
            variant_a_text=variant_a_text,
            variant_b_text=variant_b_text,
            variant_a_scheduled_at=variant_a_scheduled_at,
            variant_b_scheduled_at=variant_b_scheduled_at,
            created_at=datetime.now().isoformat(),
            status="running",
        )
        self._tests.append(test)
        self._save()
        return test

    def get_test(self, test_id: str) -> ABTest | None:
        for t in self._tests:
            if t.test_id == test_id:
                return t
        return None

    def get_all(self) -> list[ABTest]:
        return list(self._tests)

    def get_running(self) -> list[ABTest]:
        return [t for t in self._tests if t.status == "running"]

    def update_results(
        self,
        test_id: str,
        a_views: int, a_likes: int, a_replies: int, a_engagement: float,
        b_views: int, b_likes: int, b_replies: int, b_engagement: float,
    ) -> ABTest | None:
        """Update test with collected metrics and determine winner."""
        test = self.get_test(test_id)
        if test is None:
            return None

        test.a_views = a_views
        test.a_likes = a_likes
        test.a_replies = a_replies
        test.a_engagement = a_engagement
        test.b_views = b_views
        test.b_likes = b_likes
        test.b_replies = b_replies
        test.b_engagement = b_engagement

        determine_winner(test)
        self._save()
        return test

    @property
    def count(self) -> int:
        return len(self._tests)
