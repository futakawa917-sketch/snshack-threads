"""AI-powered content generation using Claude API.

Generates post drafts based on analysis results, templates, and recycle suggestions.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class GeneratedPost:
    """A generated post draft."""

    text: str
    hook_type: str
    topic: str
    source: str = ""  # "template", "recycle", "ab_test", "freeform"
    reasoning: str = ""  # Why this hook/angle was chosen


def _get_claude_client():
    """Get Anthropic client. Raises ImportError if not installed."""
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "anthropic package not installed. Run: pip install anthropic"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        # Try loading from profile config
        try:
            from .config import get_settings
            settings = get_settings()
            api_key = getattr(settings, "anthropic_api_key", "")
        except Exception:
            pass
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. Get one from https://console.anthropic.com/"
        )
    return anthropic.Anthropic(api_key=api_key)


def generate_post(
    topic: str,
    hook_type: str = "",
    tone: str = "",
    examples: list[str] | None = None,
    max_length: int = 500,
    additional_instructions: str = "",
    news_context: list[str] | None = None,
    reference_posts: list[str] | None = None,
    style_guide: str = "",
    short: bool = False,
) -> GeneratedPost:
    """Generate a single post draft using Claude.

    Args:
        topic: What the post is about.
        hook_type: Hook pattern to use (e.g. "数字訴求", "疑問形").
        tone: Tone of voice (e.g. "professional", "casual").
        examples: Example posts to use as reference.
        max_length: Maximum character count for the post.
        additional_instructions: Extra instructions for generation.
        news_context: Recent news headlines to reference.
        reference_posts: Example viral posts for style reference.
        style_guide: Freeform style instructions.
        short: If True, generate 1-2 line post (80 chars max).
    """
    client = _get_claude_client()

    if short:
        max_length = 80

    system_parts = [
        "あなたはSNS投稿のプロフェッショナルライターです。",
        "Threads (Meta) 向けの投稿文を生成してください。",
        "ルール:",
        "- 1行目にフック（注目を引く文）を入れる",
        "- 外部リンクやURL、LINE誘導は絶対に含めない",
        "- 自然な日本語で書く",
        "- ハッシュタグは使わない",
        f"- {max_length}文字以内",
        "- 投稿文のみを出力する（説明や前置きは不要）",
    ]

    if style_guide:
        system_parts.append(f"\nスタイルガイド:\n{style_guide}")

    system = "\n".join(system_parts)

    prompt_parts = [f"テーマ: {topic}"]

    if short:
        prompt_parts.append(
            "1〜2行（80文字以内）で端的にインパクトのある投稿を書いてください。"
        )

    if hook_type:
        prompt_parts.append(f"フックパターン: {hook_type}")

    if tone:
        prompt_parts.append(f"トーン: {tone}")

    if news_context:
        news_text = "\n".join(f"- {h}" for h in news_context[:5])
        prompt_parts.append(
            f"以下の最新ニュースを踏まえて投稿を書いてください:\n{news_text}"
        )

    if reference_posts:
        prompt_parts.append("以下のバズ投稿のスタイルを参考にしてください:")
        for i, rp in enumerate(reference_posts[:3], 1):
            prompt_parts.append(f"  参考{i}: {rp[:200]}")

    if examples:
        prompt_parts.append("参考投稿（同じスタイルで）:")
        for i, ex in enumerate(examples[:3], 1):
            prompt_parts.append(f"  例{i}: {ex[:200]}")

    if additional_instructions:
        prompt_parts.append(f"追加指示: {additional_instructions}")

    prompt_parts.append("\n上記の条件で投稿文を1つ生成してください。")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": "\n".join(prompt_parts)}],
    )

    text = message.content[0].text.strip()

    return GeneratedPost(
        text=text,
        hook_type=hook_type,
        topic=topic,
        source="freeform",
    )


def generate_from_template(
    topic: str,
    hook_type: str,
    example_posts: list[str],
    best_length: str = "medium",
) -> GeneratedPost:
    """Generate a post based on a winning template.

    Uses top-performing posts as style reference.
    """
    length_guide = {
        "short": "100文字以内の短い投稿",
        "medium": "100〜300文字の中程度の投稿",
        "long": "300文字以上のしっかりした投稿",
    }

    result = generate_post(
        topic=topic,
        hook_type=hook_type,
        examples=example_posts,
        additional_instructions=(
            f"文字数: {length_guide.get(best_length, '中程度の投稿')}\n"
            f"参考投稿の構造と文体を踏襲しつつ、テーマに合わせてアレンジしてください。"
        ),
    )
    result.source = "template"
    return result


def generate_recycle(
    original_text: str,
    new_hook_type: str,
    original_views: int = 0,
) -> GeneratedPost:
    """Generate a recycled version of a high-performing post with a different hook.

    Args:
        original_text: The original post text.
        new_hook_type: The new hook pattern to apply.
        original_views: Original view count (for context).
    """
    result = generate_post(
        topic="",
        hook_type=new_hook_type,
        additional_instructions=(
            f"以下の過去投稿を「{new_hook_type}」フックで書き直してください。\n"
            f"内容のエッセンスは保ちつつ、まったく別の投稿に見えるようにしてください。\n"
            f"元の投稿（{original_views:,}views）:\n{original_text}"
        ),
    )
    result.source = "recycle"
    return result


def generate_ab_variants(
    topic: str,
    hook_a: str,
    hook_b: str,
) -> tuple[GeneratedPost, GeneratedPost]:
    """Generate two post variants for A/B testing.

    Args:
        topic: Shared topic for both variants.
        hook_a: Hook pattern for variant A.
        hook_b: Hook pattern for variant B.
    """
    a = generate_post(
        topic=topic,
        hook_type=hook_a,
        additional_instructions="A/Bテストのバリアントです。このフックの特徴を最大限活かしてください。",
    )
    a.source = "ab_test"

    b = generate_post(
        topic=topic,
        hook_type=hook_b,
        additional_instructions="A/Bテストのバリアントです。このフックの特徴を最大限活かしてください。",
    )
    b.source = "ab_test"

    return a, b


def generate_batch(
    topics: list[str],
    hook_types: list[str] | None = None,
    tone: str = "",
) -> list[GeneratedPost]:
    """Generate multiple posts at once.

    If hook_types is provided, cycles through them for each topic.
    """
    posts = []
    for i, topic in enumerate(topics):
        hook = ""
        if hook_types:
            hook = hook_types[i % len(hook_types)]
        post = generate_post(topic=topic, hook_type=hook, tone=tone)
        posts.append(post)
    return posts
