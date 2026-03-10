"""Profile optimization audit for Threads.

Checks whether the account profile is optimized for the
buzz → follow → list acquisition funnel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .post_history import PostHistory


@dataclass
class AuditCheck:
    """A single audit check result."""

    name: str
    passed: bool
    detail: str
    recommendation: str = ""


@dataclass
class ProfileAuditResult:
    """Complete profile audit result."""

    checks: list[AuditCheck] = field(default_factory=list)

    @property
    def score(self) -> int:
        """Score out of 100."""
        if not self.checks:
            return 0
        return int(sum(1 for c in self.checks if c.passed) / len(self.checks) * 100)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_checks(self) -> list[AuditCheck]:
        return [c for c in self.checks if not c.passed]


def audit_profile(
    profile: dict,
    history: PostHistory | None = None,
) -> ProfileAuditResult:
    """Run profile audit checks.

    Args:
        profile: Dict from ThreadsGraphClient.get_user_profile().
        history: PostHistory for frequency analysis.
    """
    result = ProfileAuditResult()
    bio = profile.get("threads_biography", "") or ""
    username = profile.get("username", "") or ""
    name = profile.get("name", "") or ""

    # Check 1: Username is set
    result.checks.append(AuditCheck(
        name="ユーザーネーム設定",
        passed=bool(username),
        detail=f"@{username}" if username else "未設定",
        recommendation="わかりやすいユーザーネームを設定しましょう",
    ))

    # Check 2: Display name has identity/title
    has_identity = bool(name) and len(name) >= 2
    result.checks.append(AuditCheck(
        name="表示名に肩書き/実績",
        passed=has_identity,
        detail=name if name else "未設定",
        recommendation="「〇〇専門家｜実績XXX」のように肩書きと実績を含めましょう",
    ))

    # Check 3: Bio has follow benefit
    benefit_keywords = ["発信", "教える", "解説", "毎日", "情報", "ノウハウ", "コツ", "方法", "まとめ"]
    has_benefit = any(kw in bio for kw in benefit_keywords)
    result.checks.append(AuditCheck(
        name="プロフにフォローメリット",
        passed=has_benefit,
        detail=bio[:60] if bio else "プロフ文なし",
        recommendation="「〇〇について毎日発信」のようにフォローする理由を明記しましょう",
    ))

    # Check 4: Bio length is sufficient
    bio_ok = len(bio) >= 30
    result.checks.append(AuditCheck(
        name="プロフ文の充実度",
        passed=bio_ok,
        detail=f"{len(bio)}文字",
        recommendation="30文字以上で「何者か」「何を発信するか」「フォローするメリット」を書きましょう",
    ))

    # Check 5: Profile picture is set
    has_pic = bool(profile.get("threads_profile_picture_url", ""))
    result.checks.append(AuditCheck(
        name="プロフィール画像",
        passed=has_pic,
        detail="設定済み" if has_pic else "未設定",
        recommendation="顔写真またはブランドロゴを設定しましょう",
    ))

    # Check 6: Posting frequency (from history)
    if history is not None:
        recent = history.get_recent(days=14)
        freq = len(recent) / 2  # posts per week
        freq_ok = freq >= 3
        result.checks.append(AuditCheck(
            name="投稿頻度（週3回以上）",
            passed=freq_ok,
            detail=f"週{freq:.1f}回（直近2週間）",
            recommendation="アルゴリズム評価を維持するには週3回以上の投稿が必要です",
        ))

        # Check 7: Has high-performing posts
        collected = [r for r in history.get_all() if r.has_metrics]
        if collected:
            max_views = max(r.views for r in collected)
            has_viral = max_views >= 1000
            result.checks.append(AuditCheck(
                name="バズ投稿の実績",
                passed=has_viral,
                detail=f"最高{max_views:,}views",
                recommendation="1,000views超えの投稿を作ることを目標にしましょう",
            ))

    return result
