"""Post element analysis — break down posts into structural elements.

Analyzes individual post components (hook, body structure, CTA, length,
emoji usage, line breaks) and correlates each with performance metrics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .csv_analyzer import _detect_hooks, _text_length_bucket
from .post_history import PostHistory, PostRecord


@dataclass
class ElementBreakdown:
    """Structural breakdown of a single post."""

    text: str
    hook_line: str = ""
    hooks: list[str] = field(default_factory=list)
    body_lines: int = 0
    has_cta: bool = False
    cta_type: str = ""
    length_bucket: str = ""
    char_count: int = 0
    line_count: int = 0
    emoji_count: int = 0
    has_numbers: bool = False
    has_question: bool = False


@dataclass
class ElementStats:
    """Aggregated stats for a specific element pattern."""

    pattern: str
    post_count: int = 0
    avg_views: float = 0.0
    avg_likes: float = 0.0
    avg_engagement: float = 0.0


_CTA_PATTERNS = {
    "save": re.compile(r"保存|見返"),
    "comment": re.compile(r"コメント|教えて"),
    "follow": re.compile(r"フォロー"),
    "share": re.compile(r"リポスト|いいね"),
    "dm": re.compile(r"DM|メッセージ"),
}

_EMOJI_PATTERN = re.compile(
    r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FA6F]"
)


def analyze_element(text: str) -> ElementBreakdown:
    """Break down a post into its structural elements."""
    lines = text.strip().split("\n")
    hook_line = lines[0] if lines else ""

    # Detect CTA
    cta_type = ""
    has_cta = False
    last_lines = "\n".join(lines[-3:]) if len(lines) >= 3 else text
    for cta_name, pattern in _CTA_PATTERNS.items():
        if pattern.search(last_lines):
            has_cta = True
            cta_type = cta_name
            break

    emoji_count = len(_EMOJI_PATTERN.findall(text))

    return ElementBreakdown(
        text=text,
        hook_line=hook_line,
        hooks=_detect_hooks(text),
        body_lines=max(0, len(lines) - 2),  # exclude hook + CTA
        has_cta=has_cta,
        cta_type=cta_type,
        length_bucket=_text_length_bucket(text),
        char_count=len(text),
        line_count=len(lines),
        emoji_count=emoji_count,
        has_numbers=bool(re.search(r"\d", hook_line)),
        has_question=bool(re.search(r"[？?]", text)),
    )


def analyze_elements_batch(history: PostHistory, days: int = 90) -> list[ElementStats]:
    """Analyze element patterns across post history and rank by performance.

    Returns:
        List of ElementStats sorted by avg_views descending.
    """
    collected = [r for r in history.get_recent(days=days) if r.has_metrics]
    if not collected:
        return []

    # Track patterns
    pattern_data: dict[str, list[PostRecord]] = {}

    for record in collected:
        breakdown = analyze_element(record.text)

        # Length bucket
        key = f"length:{breakdown.length_bucket}"
        pattern_data.setdefault(key, []).append(record)

        # CTA type
        if breakdown.has_cta:
            key = f"cta:{breakdown.cta_type}"
            pattern_data.setdefault(key, []).append(record)
        else:
            pattern_data.setdefault("cta:none", []).append(record)

        # Emoji usage
        emoji_key = "emoji:yes" if breakdown.emoji_count > 0 else "emoji:no"
        pattern_data.setdefault(emoji_key, []).append(record)

        # Question
        q_key = "question:yes" if breakdown.has_question else "question:no"
        pattern_data.setdefault(q_key, []).append(record)

        # Number hook
        n_key = "numbers:yes" if breakdown.has_numbers else "numbers:no"
        pattern_data.setdefault(n_key, []).append(record)

    # Build stats
    stats = []
    for pattern, records in pattern_data.items():
        if not records:
            continue
        stats.append(ElementStats(
            pattern=pattern,
            post_count=len(records),
            avg_views=sum(r.views for r in records) / len(records),
            avg_likes=sum(r.likes for r in records) / len(records),
            avg_engagement=sum(r.engagement for r in records) / len(records),
        ))

    stats.sort(key=lambda s: s.avg_views, reverse=True)
    return stats
