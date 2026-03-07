"""Winning pattern templates — derive post structures from top-performing content.

Analyzes post history to extract templates for each hook type,
enabling data-driven content creation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .csv_analyzer import _HOOK_PATTERNS, _detect_hooks, _text_length_bucket
from .post_history import PostHistory, PostRecord


@dataclass
class PostTemplate:
    """A winning content template derived from top-performing posts."""

    hook_type: str
    avg_views: float
    avg_likes: float
    post_count: int
    best_length_bucket: str
    example_posts: list[str] = field(default_factory=list)  # top 3 texts

    @property
    def structure_hint(self) -> str:
        """Generate a structural hint based on the hook type."""
        hints = {
            "数字訴求": "1行目: 具体的な数字+結論 → 本文: 根拠を3つ → CTA",
            "疑問形": "1行目: 読者が気になる問い → 本文: 答え+解説 → CTA",
            "危機感": "1行目: リスク/損失を提示 → 本文: 具体例+回避策 → CTA",
            "限定感": "1行目: 緊急性/希少性 → 本文: 詳細+行動喚起 → CTA",
            "断言": "1行目: 強い主張 → 本文: 根拠を列挙 → CTA",
            "呼びかけ": "1行目: ターゲットに呼びかけ → 本文: 共感+提案 → CTA",
            "共感": "1行目: あるある/共感ポイント → 本文: 深掘り → CTA",
            "対比": "1行目: 意外な事実/逆説 → 本文: 理由+具体例 → CTA",
            "裏技": "1行目: プロのコツ/秘訣 → 本文: ステップ解説 → CTA",
            "実体験": "1行目: 体験の結果/学び → 本文: 経緯+気づき → CTA",
            "ランキング": "1行目: TOP○/ベスト○ → 本文: ランク順に解説 → CTA",
            "議論喚起": "1行目: 意見を問う → 本文: 両面を提示 → CTA",
        }
        return hints.get(self.hook_type, "1行目: フック → 本文: 解説 → CTA")


def generate_templates(
    history: PostHistory,
    min_views: int = 0,
    top_examples: int = 3,
) -> list[PostTemplate]:
    """Generate templates from post history, ranked by hook performance.

    Args:
        history: Post history with collected metrics.
        min_views: Minimum views to qualify for template generation.
        top_examples: Number of example posts per template.

    Returns:
        List of PostTemplates sorted by avg_views descending.
    """
    collected = [
        r for r in history.get_all()
        if r.has_metrics and r.views >= min_views
    ]

    if not collected:
        return []

    # Group by hook type
    hook_posts: dict[str, list[PostRecord]] = {}
    for record in collected:
        hooks = _detect_hooks(record.text)
        for hook in hooks:
            hook_posts.setdefault(hook, []).append(record)

    templates: list[PostTemplate] = []
    for hook_type, posts in hook_posts.items():
        if not posts:
            continue

        avg_views = sum(r.views for r in posts) / len(posts)
        avg_likes = sum(r.likes for r in posts) / len(posts)

        # Find best length bucket
        bucket_views: dict[str, list[int]] = {}
        for r in posts:
            bucket = _text_length_bucket(r.text)
            bucket_views.setdefault(bucket, []).append(r.views)

        best_bucket = max(
            bucket_views.items(),
            key=lambda x: sum(x[1]) / len(x[1]),
        )[0]

        # Top examples by views
        top = sorted(posts, key=lambda r: r.views, reverse=True)
        examples = [r.text for r in top[:top_examples]]

        templates.append(PostTemplate(
            hook_type=hook_type,
            avg_views=avg_views,
            avg_likes=avg_likes,
            post_count=len(posts),
            best_length_bucket=best_bucket,
            example_posts=examples,
        ))

    templates.sort(key=lambda t: t.avg_views, reverse=True)
    return templates


def generate_draft_outline(hook_type: str, topic: str) -> str:
    """Generate a draft outline for a given hook type and topic.

    Returns a text skeleton the user can fill in.
    """
    outlines = {
        "数字訴求": (
            f"【数字】{topic}に関する衝撃の事実\n"
            f"\n"
            f"① [具体例1 + 数字]\n"
            f"② [具体例2 + 数字]\n"
            f"③ [具体例3 + 数字]\n"
            f"\n"
            f"保存して見返してね"
        ),
        "疑問形": (
            f"なぜ{topic}で失敗する人が多いのか？\n"
            f"\n"
            f"答えはシンプルで…\n"
            f"\n"
            f"[答え + 解説]\n"
            f"\n"
            f"参考になったら保存してね"
        ),
        "危機感": (
            f"知らないと損する{topic}の落とし穴\n"
            f"\n"
            f"実は[リスクの説明]\n"
            f"\n"
            f"回避するには：\n"
            f"・[対策1]\n"
            f"・[対策2]\n"
            f"\n"
            f"後で見返せるように保存推奨"
        ),
        "限定感": (
            f"今すぐチェック！{topic}の最新情報\n"
            f"\n"
            f"[期限/限定の説明]\n"
            f"\n"
            f"詳しくは：\n"
            f"・[ポイント1]\n"
            f"・[ポイント2]\n"
            f"\n"
            f"保存して忘れないようにしてね"
        ),
        "実体験": (
            f"{topic}を実際にやってみた結果\n"
            f"\n"
            f"[結果の要約]\n"
            f"\n"
            f"学んだこと：\n"
            f"・[気づき1]\n"
            f"・[気づき2]\n"
            f"\n"
            f"参考になったら保存してね"
        ),
    }

    default = (
        f"{topic}について\n"
        f"\n"
        f"[1行目: フックで注目を集める]\n"
        f"\n"
        f"[本文: 3つのポイントで解説]\n"
        f"① [ポイント1]\n"
        f"② [ポイント2]\n"
        f"③ [ポイント3]\n"
        f"\n"
        f"保存して見返してね"
    )

    return outlines.get(hook_type, default)
