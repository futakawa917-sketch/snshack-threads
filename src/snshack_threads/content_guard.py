"""Content validation and engagement CTA injection.

Rules:
- BLOCK external link promotion (URLs, LINE, profile link mentions)
- These kill reach on Threads algorithm
- Instead, use engagement CTAs (comments, saves, follows) that boost algorithm signals
"""

from __future__ import annotations

import random
import re

# ── NG Patterns (reach killers) ──────────────────────────────

_NG_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("URL", re.compile(r"https?://\S+")),
    ("LINE誘導", re.compile(r"LINE|ライン公式|ライン登録|LINE@|公式LINE")),
    ("プロフリンク誘導", re.compile(r"(プロフ|概要欄).*(リンク|URL|見て|飛んで|チェック)")),
    ("固定投稿誘導", re.compile(r"固定.*(投稿|ポスト|見て|チェック)")),
    ("外部誘導", re.compile(r"(リンク|URL).*(貼って|載せて|プロフ|概要)")),
    ("DM誘導", re.compile(r"DM.*(ください|してね|送って|待って)")),
]


def check_ng(text: str) -> list[str]:
    """Check text for NG patterns that kill reach.

    Returns list of violation descriptions. Empty = OK.
    """
    violations = []
    for label, pattern in _NG_PATTERNS:
        if pattern.search(text):
            violations.append(label)
    return violations


# ── Engagement CTAs (algorithm boosters) ─────────────────────

_ENGAGEMENT_CTAS = [
    "参考になったら保存しておいてね",
    "知らなかった人はコメントで「初耳」と教えて",
    "当てはまる人はコメントで教えて",
    "気になる人はフォローしておいてね",
    "保存して後で見返してね",
    "これ知らなかった人いる？コメントで教えて",
    "役に立ったらいいねで教えてね",
    "周りにも教えたい人はリポストしてね",
    "もっと詳しく知りたい人はコメントで「詳しく」って送って",
    "明日も有益な情報を届けるのでフォローしておいてね",
]


def suggest_cta() -> str:
    """Return a random engagement-boosting CTA."""
    return random.choice(_ENGAGEMENT_CTAS)


def append_cta(text: str, cta: str | None = None) -> str:
    """Append an engagement CTA to post text if it doesn't already have one.

    Skips if text already ends with a CTA-like pattern.
    Max 500 chars for Threads.
    """
    if cta is None:
        cta = suggest_cta()

    # Don't add if text already has a CTA-like ending
    last_line = text.strip().split("\n")[-1]
    if any(word in last_line for word in ["コメント", "保存", "フォロー", "いいね", "リポスト", "教えて"]):
        return text

    result = f"{text.rstrip()}\n\n{cta}"

    # Respect Threads 500 char limit
    if len(result) > 500:
        return text

    return result
