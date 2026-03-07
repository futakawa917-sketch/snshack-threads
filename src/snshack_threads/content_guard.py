"""Content validation and engagement CTA injection.

Rules:
- BLOCK external link promotion (URLs, LINE, profile link mentions)
- These kill reach on Threads algorithm
- Instead, use engagement CTAs (comments, saves, follows) that boost algorithm signals
- Save CTAs are prioritized (saves are 3-5x algorithm weight of likes)
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

# Categorized CTAs for targeted engagement optimization
_SAVE_CTAS = [
    "参考になったら保存しておいてね",
    "保存して後で見返してね",
    "申請する時に見返せるよう保存推奨",
    "保存しておけばいつでも確認できるよ",
]

_COMMENT_CTAS = [
    "知らなかった人はコメントで「初耳」と教えて",
    "当てはまる人はコメントで教えて",
    "これ知らなかった人いる？コメントで教えて",
    "もっと詳しく知りたい人はコメントで「詳しく」って送って",
    "あなたの会社ではどうですか？コメントで教えて",
]

_FOLLOW_CTAS = [
    "気になる人はフォローしておいてね",
    "明日も有益な情報を届けるのでフォローしておいてね",
]

_SHARE_CTAS = [
    "周りにも教えたい人はリポストしてね",
    "役に立ったらいいねで教えてね",
]

# Combined list for backward compatibility
_ENGAGEMENT_CTAS = _SAVE_CTAS + _COMMENT_CTAS + _FOLLOW_CTAS + _SHARE_CTAS

# Patterns that indicate save-worthy content (reference material)
_SAVE_WORTHY_PATTERNS = re.compile(
    r"一覧|チェックリスト|まとめ|ステップ|手順|比較|締切|期限|変更点|"
    r"\d+選|TOP|ランキング|必要書類|申請方法|対象|条件"
)


def detect_save_worthy(text: str) -> bool:
    """Check if the content has save-worthy patterns (reference material)."""
    return bool(_SAVE_WORTHY_PATTERNS.search(text))


def suggest_cta(priority: str = "auto") -> str:
    """Return an engagement CTA.

    Args:
        priority: "save", "comment", "follow", "share", or "auto".
                  "auto" uses save CTA for save-worthy content.
    """
    pools = {
        "save": _SAVE_CTAS,
        "comment": _COMMENT_CTAS,
        "follow": _FOLLOW_CTAS,
        "share": _SHARE_CTAS,
    }
    pool = pools.get(priority, _ENGAGEMENT_CTAS)
    return random.choice(pool)


def append_cta(text: str, cta: str | None = None) -> str:
    """Append an engagement CTA to post text if it doesn't already have one.

    Auto-detects save-worthy content and uses save CTA when appropriate.
    Skips if text already ends with a CTA-like pattern.
    Max 500 chars for Threads.
    """
    if cta is None:
        # Prioritize save CTAs for reference-type content
        if detect_save_worthy(text):
            cta = suggest_cta("save")
        else:
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
