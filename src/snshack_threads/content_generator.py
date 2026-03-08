"""AI-powered content generation using Claude API.

Generates post drafts based on proven viral patterns from real data:
- 超短文 (≤50字) = 平均15,524 views — 最強
- 質問CTA (〜いますか？) = 平均60,091 views — リスト獲得最強
- 速報/限定 = 平均25,657 views
- 具体金額 = 平均9,246 views

Post types:
- reach: バズ狙い（超短文パンチライン）
- list: リスト獲得（質問CTA、DM誘導）
"""

from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Proven viral examples (from 150-post analysis) ──────────

# Top reach posts (sorted by views)
REACH_EXAMPLES = [
    "今、最も通りやすいのは『新事業創出補助金』\n補助額は最大9000万円。",  # 148,244 views
    "AI導入補助金熱いねぇ。\nAI導入予定の人はやるべきよね。",  # 54,678 views
    "AI導入補助金がクソ○ミ過ぎる。",  # 54,155 views
    "会社経営してるのに補助金、助成金使ってない人損してますよ！\n従業員ゼロでも個人事業主でも誰でも受けられます！",  # 43,841 views
    "会社を潰す社長は、まず「銀行」へ行く。\n会社を伸ばす社長は、まず「助成金」を調べる。\n\nこの差が、1年後の現預金に「2,000万」の差を生む。",  # 32,481 views
    "うちの会社の正社員が1人だった時代に貰えた助成金と補助金の合計額2300万。無知は罪。",  # 24,431 views
    "補助金は受給出来るのにしてない会社が99%",  # 4,935 views
    "税金は1円単位で払うのに、補助金は1円も受け取らない。\n経営者として「ドM」すぎます。",  # 5,232 views
    "はっきり言います。\n税金払ってるのに補助金助成金の申請すらしない人はドMです。",  # 9,633 views
    "補助金、助成金って99%の会社が貰えるのに貰ってる会社は5%レベル。94%の会社は損してるで。",  # 9,251 views
]

# Top list acquisition posts (drive DMs/comments)
LIST_EXAMPLES = [
    "小規模事業者持続化補助金\n遂に来ましたねぇ\n申請したい方いますか？",  # 134,502 views, 27 replies
    "3/6から小規模事業者持続化補助金が開始しますね！\n申請したい人いますか？？",  # 100,838 views, 33 replies
    "小規模事業者持続化補助金がもうそろそろですね。\nかなり人気の補助金！\n興味ある人はDMで連絡してきてください！",  # 49,094 views, 24 replies
    "小規模持続化補助金もうすぐ締め切りです\n申請依頼はお早めに！",  # 5,825 views
    "5月に激アツ助成金が到来します。\n条件を満たせば100〜200万近く貰えます。",  # 632 views, 1 reply
]

# Medium-length proven posts (100-200 chars, high engagement)
MEDIUM_EXAMPLES = [
    "社長であるあなたが現場に出て、汗水垂らして稼ぐ利益100万円。\nデスクに座って、専門家と打ち合わせて書類で得る補助金1,000万円。\n\nどっちが「時給」いいですかね？\n現場主義も結構ですが、経営者の仕事は「最も効率よくキャッシュを増やすこと」だとお忘れなく。",  # 35,473 views, 130 replies
    "300万のシステムを買って\n国が3分の2（200万）を補助して\n補助金業者が3分の2（200万）をキックバック。\n受給者と業者は100万ずつ儲かる。\nこれ、ただの「補助金詐欺」です。",  # 52,086 views
    "100万円稼ぐのは大変だけど、 100万円の補助金をもらうのは、書類数枚。\nこのチート級の攻略法を使わずに「ビジネスは厳しい」とか言ってるのは、縛りプレイを楽しんでる変態としか思えない。",  # 3,867 views
]


@dataclass
class GeneratedPost:
    """A generated post draft."""

    text: str
    hook_type: str
    topic: str
    source: str = ""  # "reach", "list", "template", "recycle", "freeform"
    post_type: str = "reach"  # "reach" or "list"
    reasoning: str = ""


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


def _load_reference_posts(profile: str | None = None) -> list[dict]:
    """Load reference posts from profile data."""
    try:
        from .config import get_settings
        settings = get_settings(profile=profile)
        ref_path = Path(settings.data_dir) / "reference_posts.json"
        if ref_path.exists():
            return json.loads(ref_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def generate_reach_post(
    topic: str,
    hook_type: str = "",
    news_context: list[str] | None = None,
    style_guide: str = "",
    additional_instructions: str = "",
) -> GeneratedPost:
    """Generate a reach/viral post (超短文パンチライン).

    Based on data: 超短文(≤50字) = 平均15,524 views
    Key patterns: 具体金額, 挑発的, 断言, 対比
    """
    client = _get_claude_client()

    examples = random.sample(REACH_EXAMPLES, min(5, len(REACH_EXAMPLES)))

    system = """あなたはThreadsでバズる投稿を書くプロです。

【絶対ルール】
- 50文字以内の超短文を1つだけ書く
- 投稿文のみを出力（説明不要）
- 外部リンク、URL、LINE誘導は絶対に含めない
- ハッシュタグは使わない

【バズる投稿の法則（実データ150投稿の分析結果）】
- 超短文(50字以内)は平均15,524 views（中長文の4〜8倍）
- 具体的な金額を入れると平均9,246 views
- 挑発的な表現は平均8,668 views
- 「〜です。」「〜ません。」の断言が効く

【トーン】
- カジュアルと専門家の間。堅すぎず軽すぎず
- 「です・ます」と「だよね」「だから」を自然に混ぜる
- 読者は中小企業の経営者・個人事業主"""

    if style_guide:
        system += f"\n\n【追加スタイル指示】\n{style_guide}"

    prompt_parts = [f"テーマ: {topic}"]

    if hook_type:
        prompt_parts.append(f"フックパターン: {hook_type}")

    prompt_parts.append("\n【参考：実際にバズった投稿（これらのスタイル・構造を参考に）】")
    for i, ex in enumerate(examples, 1):
        prompt_parts.append(f"参考{i}: {ex}")

    if news_context:
        news_text = "\n".join(f"- {h}" for h in news_context[:3])
        prompt_parts.append(f"\n【最新ニュース（これを踏まえて書く）】\n{news_text}")

    if additional_instructions:
        prompt_parts.append(f"\n追加指示: {additional_instructions}")

    prompt_parts.append("\n50文字以内のパンチラインを1つ書いてください。")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        system=system,
        messages=[{"role": "user", "content": "\n".join(prompt_parts)}],
    )

    text = message.content[0].text.strip()
    # Remove quotes if AI wrapped in 「」
    if text.startswith("「") and text.endswith("」"):
        text = text[1:-1]

    return GeneratedPost(
        text=text,
        hook_type=hook_type or "reach",
        topic=topic,
        source="reach",
        post_type="reach",
    )


def generate_list_post(
    topic: str,
    news_context: list[str] | None = None,
    style_guide: str = "",
    additional_instructions: str = "",
) -> GeneratedPost:
    """Generate a list-acquisition post (質問CTA / DM誘導).

    Based on data: 質問CTA(〜いますか？) = 平均60,091 views, 15.5 replies
    This is the #1 pattern for driving engagement and list signups.
    """
    client = _get_claude_client()

    examples = LIST_EXAMPLES.copy()

    system = """あなたはThreadsでリスト獲得（コメント・DM誘導）を狙う投稿を書くプロです。

【絶対ルール】
- 60文字以内で書く
- 投稿文のみを出力（説明不要）
- 外部リンク、URL、LINE誘導は絶対に含めない
- ハッシュタグは使わない

【リスト獲得の法則（実データ分析結果）】
- 「〜したい人いますか？」で終わる投稿 → 平均60,091 views, 15.5 replies
- 「DMで連絡してきてください」→ 平均16,898 views
- 補助金名 + 速報感 + 質問 = 最強コンボ

【投稿の型】
パターンA: 「〇〇補助金がもうすぐです。申請したい人いますか？」
パターンB: 「〇〇が始まります。興味ある人はDMで連絡してきてください！」
パターンC: 「〇〇って知ってた？ 気になる人はコメントで教えて！」

【トーン】
- カジュアルで親しみやすい
- 「〜ねぇ」「〜よね」「〜いますか？」など
- 読者は中小企業の経営者・個人事業主"""

    if style_guide:
        system += f"\n\n【追加スタイル指示】\n{style_guide}"

    prompt_parts = [f"テーマ: {topic}"]

    prompt_parts.append("\n【参考：実際にリスト獲得に成功した投稿】")
    for i, ex in enumerate(examples, 1):
        prompt_parts.append(f"参考{i}: {ex}")

    if news_context:
        news_text = "\n".join(f"- {h}" for h in news_context[:3])
        prompt_parts.append(f"\n【最新ニュース（これをネタにして書く）】\n{news_text}")

    if additional_instructions:
        prompt_parts.append(f"\n追加指示: {additional_instructions}")

    prompt_parts.append("\n読者が思わずコメントやDMしたくなるような投稿を1つ書いてください。")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        system=system,
        messages=[{"role": "user", "content": "\n".join(prompt_parts)}],
    )

    text = message.content[0].text.strip()
    if text.startswith("「") and text.endswith("」"):
        text = text[1:-1]

    return GeneratedPost(
        text=text,
        hook_type="質問CTA",
        topic=topic,
        source="list",
        post_type="list",
    )


def generate_medium_post(
    topic: str,
    hook_type: str = "",
    news_context: list[str] | None = None,
    style_guide: str = "",
    additional_instructions: str = "",
) -> GeneratedPost:
    """Generate a medium-length post (100-200 chars, story/comparison).

    Based on data: 中文 = 平均3,792 views, but specific patterns can reach 30K+
    Key: 対比, ストーリー, 具体的な数字比較
    """
    client = _get_claude_client()

    examples = random.sample(MEDIUM_EXAMPLES, min(3, len(MEDIUM_EXAMPLES)))

    # Also pull from reference posts
    refs = _load_reference_posts()
    ref_medium = [r for r in refs if 100 <= len(r.get("text", "")) <= 250 and r.get("views", 0) > 5000]
    if ref_medium:
        ref_medium.sort(key=lambda x: x["views"], reverse=True)
        for r in ref_medium[:2]:
            examples.append(r["text"][:250])

    system = """あなたはThreadsで中程度の長さの投稿を書くプロです。

【絶対ルール】
- 100〜200文字で書く
- 投稿文のみを出力（説明不要）
- 外部リンク、URL、LINE誘導は絶対に含めない
- ハッシュタグは使わない

【中文でバズるパターン】
- 対比型: 「〜する社長」vs「〜する社長」→ 読者が自分事として考える
- ストーリー型: 具体的なシナリオで「あるある」を突く
- 暴露型: 業界の裏側や不都合な真実
- 数字比較: 「100万 vs 1,000万」のインパクト

【トーン】
- 専門家として断言する
- 「です・ます」ベースだが、ところどころ砕ける
- 読者は中小企業の経営者・個人事業主"""

    if style_guide:
        system += f"\n\n【追加スタイル指示】\n{style_guide}"

    prompt_parts = [f"テーマ: {topic}"]

    if hook_type:
        prompt_parts.append(f"フックパターン: {hook_type}")

    prompt_parts.append("\n【参考：バズった中文投稿】")
    for i, ex in enumerate(examples, 1):
        prompt_parts.append(f"参考{i}: {ex[:250]}")

    if news_context:
        news_text = "\n".join(f"- {h}" for h in news_context[:3])
        prompt_parts.append(f"\n【最新ニュース】\n{news_text}")

    if additional_instructions:
        prompt_parts.append(f"\n追加指示: {additional_instructions}")

    prompt_parts.append("\n100〜200文字の投稿を1つ書いてください。")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": "\n".join(prompt_parts)}],
    )

    text = message.content[0].text.strip()
    if text.startswith("「") and text.endswith("」"):
        text = text[1:-1]

    return GeneratedPost(
        text=text,
        hook_type=hook_type or "medium",
        topic=topic,
        source="medium",
        post_type="reach",
    )


# ── Legacy API (backward compatible) ─────────────────────

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
    post_type: str = "reach",
) -> GeneratedPost:
    """Generate a post using the appropriate strategy.

    Routes to specialized generators based on post_type and short flag.
    """
    if post_type == "list":
        return generate_list_post(
            topic=topic,
            news_context=news_context,
            style_guide=style_guide,
            additional_instructions=additional_instructions,
        )

    if short:
        return generate_reach_post(
            topic=topic,
            hook_type=hook_type,
            news_context=news_context,
            style_guide=style_guide,
            additional_instructions=additional_instructions,
        )

    return generate_medium_post(
        topic=topic,
        hook_type=hook_type,
        news_context=news_context,
        style_guide=style_guide,
        additional_instructions=additional_instructions,
    )


def generate_from_template(
    topic: str,
    hook_type: str,
    example_posts: list[str],
    best_length: str = "medium",
) -> GeneratedPost:
    """Generate a post based on a winning template."""
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
    """Generate a recycled version of a high-performing post."""
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
    """Generate two post variants for A/B testing."""
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
    """Generate multiple posts at once."""
    posts = []
    for i, topic in enumerate(topics):
        hook = ""
        if hook_types:
            hook = hook_types[i % len(hook_types)]
        post = generate_post(topic=topic, hook_type=hook, tone=tone)
        posts.append(post)
    return posts
