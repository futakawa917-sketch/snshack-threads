"""Analyze Threads CSV export data for optimal posting strategy.

Covers:
- Time-of-day analysis with minimum sample size filtering
- Day-of-week × hour 2D analysis
- Content pattern analysis (hooks, length, themes)
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Minimum posts in a time bucket to consider it statistically reliable
MIN_SAMPLE_SIZE = 5

# Day-of-week names (Monday=0)
_DOW_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


# ── Time Stats ───────────────────────────────────────────────


@dataclass
class HourStats:
    """Aggregated stats for a specific hour of day."""

    hour: int
    post_count: int = 0
    total_views: int = 0
    total_likes: int = 0
    total_replies: int = 0
    total_engagement: float = 0.0

    @property
    def avg_views(self) -> float:
        return self.total_views / self.post_count if self.post_count else 0

    @property
    def avg_likes(self) -> float:
        return self.total_likes / self.post_count if self.post_count else 0

    @property
    def avg_engagement(self) -> float:
        return self.total_engagement / self.post_count if self.post_count else 0

    @property
    def reliable(self) -> bool:
        """Has enough data points to be statistically meaningful."""
        return self.post_count >= MIN_SAMPLE_SIZE

    @property
    def score(self) -> float:
        """Composite score: weighted combination of views, likes, and engagement rate.

        Penalizes low-sample-size buckets to avoid noise dominating.
        """
        if not self.post_count:
            return 0.0
        raw = (self.avg_views * 0.3) + (self.avg_likes * 50) + (self.avg_engagement * 1000)
        if not self.reliable:
            # Discount proportionally: 1 post = 20% weight, 4 posts = 80%
            raw *= self.post_count / MIN_SAMPLE_SIZE
        return raw


@dataclass
class DayHourStats:
    """Stats for a specific day-of-week + hour combination."""

    day: int  # 0=Monday, 6=Sunday
    hour: int
    post_count: int = 0
    total_views: int = 0
    total_likes: int = 0
    total_replies: int = 0
    total_engagement: float = 0.0

    @property
    def day_name(self) -> str:
        return _DOW_NAMES[self.day]

    @property
    def avg_views(self) -> float:
        return self.total_views / self.post_count if self.post_count else 0

    @property
    def avg_likes(self) -> float:
        return self.total_likes / self.post_count if self.post_count else 0

    @property
    def avg_engagement(self) -> float:
        return self.total_engagement / self.post_count if self.post_count else 0

    @property
    def score(self) -> float:
        if not self.post_count:
            return 0.0
        raw = (self.avg_views * 0.3) + (self.avg_likes * 50) + (self.avg_engagement * 1000)
        if self.post_count < 3:
            raw *= self.post_count / 3
        return raw


# ── Content Pattern Stats ────────────────────────────────────


@dataclass
class ContentPattern:
    """Analysis of what content patterns drive performance."""

    # Length buckets: short (<100), medium (100-300), long (300+)
    length_buckets: dict[str, HourStats] = field(default_factory=dict)
    # Hook patterns (first line patterns)
    hook_patterns: dict[str, HourStats] = field(default_factory=dict)
    # Top performing posts for reference
    top_posts: list[dict] = field(default_factory=list)
    # Posts with external links (for tracking penalty)
    link_post_avg_views: float = 0.0
    no_link_post_avg_views: float = 0.0


# ── Main Result ──────────────────────────────────────────────


@dataclass
class CSVAnalysisResult:
    """Result of CSV-based comprehensive analysis."""

    total_posts: int = 0
    hour_stats: dict[int, HourStats] = field(default_factory=dict)
    day_hour_stats: dict[tuple[int, int], DayHourStats] = field(default_factory=dict)
    top_hours: list[int] = field(default_factory=list)
    content: ContentPattern = field(default_factory=ContentPattern)

    def get_optimal_slots(self, n: int = 5) -> list[tuple[int, int]]:
        """Return top N (hour, minute) tuples sorted by score.

        Only considers statistically reliable hours (>= MIN_SAMPLE_SIZE).
        Falls back to unreliable hours if not enough reliable ones exist.
        Ensures at least 2 hours gap between slots for better spread.
        """
        reliable = sorted(
            [h for h in self.hour_stats.values() if h.reliable],
            key=lambda h: h.score,
            reverse=True,
        )
        unreliable = sorted(
            [h for h in self.hour_stats.values() if h.post_count > 0 and not h.reliable],
            key=lambda h: h.score,
            reverse=True,
        )
        ranked = reliable + unreliable

        selected: list[int] = []
        for hs in ranked:
            if len(selected) >= n:
                break
            if all(_hour_distance(hs.hour, s) >= 2 for s in selected):
                selected.append(hs.hour)

        selected.sort()
        return [(h, 0) for h in selected]

    def get_optimal_slots_for_day(self, day_of_week: int, n: int = 5) -> list[tuple[int, int]]:
        """Return top N slots for a specific day of week.

        Falls back to overall hour analysis if not enough day-specific data.
        """
        day_stats = [
            dh for (d, h), dh in self.day_hour_stats.items()
            if d == day_of_week and dh.post_count > 0
        ]
        day_stats.sort(key=lambda dh: dh.score, reverse=True)

        selected: list[int] = []
        for dh in day_stats:
            if len(selected) >= n:
                break
            if all(_hour_distance(dh.hour, s) >= 2 for s in selected):
                selected.append(dh.hour)

        # If not enough day-specific slots, fill from overall
        if len(selected) < n:
            overall = self.get_optimal_slots(n=n)
            for h, m in overall:
                if len(selected) >= n:
                    break
                if all(_hour_distance(h, s) >= 2 for s in selected):
                    selected.append(h)

        selected.sort()
        return [(h, 0) for h in selected]


def _hour_distance(a: int, b: int) -> int:
    """Circular distance between two hours (0-23), wrapping around midnight."""
    return min(abs(a - b), 24 - abs(a - b))


# ── CSV Parsing ──────────────────────────────────────────────


def parse_csv(csv_path: str | Path) -> list[dict]:
    """Parse the Threads CSV export file.

    Handles UTF-8, UTF-8 BOM, and Shift-JIS (Japanese Windows exports).
    """
    path = Path(csv_path)
    # Try UTF-8 BOM first (handles both BOM and plain UTF-8)
    for encoding in ("utf-8-sig", "cp932"):
        try:
            with path.open(encoding=encoding) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            return rows
        except UnicodeDecodeError:
            continue
    # Final fallback with error replacement
    with path.open(encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ── Hook Pattern Detection ───────────────────────────────────

_DEFAULT_HOOK_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Original 6 patterns
    ("数字訴求", re.compile(r"[\d,]+[万億円%]|最大|[0-9]{2,}")),
    ("疑問形", re.compile(r"[？\?]|知ってた|ですか")),
    ("危機感", re.compile(r"やばい|ヤバい|危険|損|知らない|放置|倒産|リスク")),
    ("限定感", re.compile(r"今だけ|いよいよ|緊急|速報|激アツ|到来")),
    ("断言", re.compile(r"です[。\n]|ください|べき|一択|必見")),
    ("呼びかけ", re.compile(r"必見|オーナー|社長|経営者|あなた")),
    # Added patterns for broader content diversity
    ("共感", re.compile(r"あるある|わかる|共感|それな|ほんと")),
    ("対比", re.compile(r"実は|逆に|一方で|でも実際|思いきや")),
    ("裏技", re.compile(r"コツ|秘訣|裏技|裏ワザ|知る人ぞ知る|プロが")),
    ("実体験", re.compile(r"やってみた|結果|実際に|体験|経験談")),
    ("ランキング", re.compile(r"TOP|ランキング|第[0-9]位|ベスト|ワースト|選$")),
    ("議論喚起", re.compile(r"どう思う|賛否|議論|意見|反論|正直")),
]

# Industry-specific hook presets
INDUSTRY_HOOK_PRESETS: dict[str, list[tuple[str, str]]] = {
    "飲食": [
        ("季節感", r"春|夏|秋|冬|旬|季節|限定メニュー"),
        ("食欲訴求", r"とろける|濃厚|ジューシー|ふわふわ|サクサク|もちもち"),
        ("コスパ", r"コスパ|お得|ワンコイン|食べ放題|半額"),
        ("新メニュー", r"新作|新メニュー|初登場|リニューアル"),
    ],
    "美容": [
        ("ビフォーアフター", r"ビフォー|アフター|変化|変身|激変"),
        ("悩み共感", r"悩み|困って|コンプレックス|気になる"),
        ("トレンド", r"トレンド|流行|最新|話題|バズ"),
        ("即効性", r"たった|だけで|すぐに|即|速攻"),
    ],
    "アパレル": [
        ("トレンド", r"トレンド|流行|最旬|今季|新作"),
        ("着回し", r"着回し|コーデ|合わせ方|万能|使える"),
        ("プチプラ", r"プチプラ|コスパ|安い|お手頃|ユニクロ|GU"),
        ("季節感", r"春|夏|秋|冬|季節|シーズン"),
    ],
    "不動産": [
        ("数字訴求", r"[\d,]+万|坪|利回り|㎡|LDK"),
        ("エリア", r"駅|徒歩|分|エリア|立地"),
        ("比較", r"比較|vs|違い|どっち|選び方"),
        ("注意喚起", r"注意|失敗|後悔|騙さ|落とし穴|やめて"),
    ],
    "教育": [
        ("成果", r"合格|成績|点|偏差値|UP|上がった"),
        ("悩み共感", r"悩み|困って|苦手|嫌い|挫折"),
        ("メソッド", r"方法|やり方|コツ|勉強法|テクニック"),
        ("保護者向け", r"お母さん|お父さん|保護者|親|子ども"),
    ],
    "EC・物販": [
        ("セール", r"セール|SALE|割引|クーポン|ポイント|還元"),
        ("レビュー", r"レビュー|口コミ|使ってみた|買ってみた|正直"),
        ("比較", r"比較|vs|どっち|おすすめ|ランキング"),
        ("新商品", r"新商品|新作|入荷|再販|予約"),
    ],
    "士業": [
        ("法改正", r"改正|施行|義務化|新制度|法律"),
        ("期限", r"締切|期限|期日|まで|急いで"),
        ("事例", r"事例|ケース|実例|判例|相談"),
        ("注意喚起", r"注意|罰則|違反|リスク|知らない"),
    ],
}

# Active custom hooks (set via load_custom_hooks)
_active_hook_patterns: list[tuple[str, re.Pattern]] | None = None

# Keep reference for backward compatibility
_HOOK_PATTERNS = _DEFAULT_HOOK_PATTERNS


def load_custom_hooks(
    industry: str | None = None,
    custom_hooks: dict[str, str] | None = None,
) -> None:
    """Load industry-specific or custom hook patterns.

    Args:
        industry: Industry preset name (e.g. "飲食", "美容").
        custom_hooks: Dict of {pattern_name: regex_string}.
    """
    global _active_hook_patterns, _HOOK_PATTERNS

    patterns = list(_DEFAULT_HOOK_PATTERNS)

    if industry and industry in INDUSTRY_HOOK_PRESETS:
        for name, regex in INDUSTRY_HOOK_PRESETS[industry]:
            patterns.append((name, re.compile(regex)))

    if custom_hooks:
        for name, regex in custom_hooks.items():
            patterns.append((name, re.compile(regex)))

    _active_hook_patterns = patterns
    _HOOK_PATTERNS = patterns


def reset_hooks() -> None:
    """Reset to default hook patterns."""
    global _active_hook_patterns, _HOOK_PATTERNS
    _active_hook_patterns = None
    _HOOK_PATTERNS = _DEFAULT_HOOK_PATTERNS


def get_active_hooks() -> list[tuple[str, re.Pattern]]:
    """Return the currently active hook patterns."""
    return _active_hook_patterns if _active_hook_patterns is not None else _DEFAULT_HOOK_PATTERNS


def get_hooks_for_profile(profile: str | None = None) -> list[tuple[str, re.Pattern]]:
    """Return hook patterns resolved for a specific profile (no global mutation).

    This is safe to call from multi-profile contexts like data_resolver.
    """
    from .config import get_settings

    settings = get_settings(profile=profile)
    patterns = list(_DEFAULT_HOOK_PATTERNS)

    industry = getattr(settings, "industry", None) or ""
    if industry and industry in INDUSTRY_HOOK_PRESETS:
        for name, regex in INDUSTRY_HOOK_PRESETS[industry]:
            patterns.append((name, re.compile(regex)))

    custom = getattr(settings, "custom_hooks", None) or {}
    if custom:
        for name, regex in custom.items():
            patterns.append((name, re.compile(regex)))

    return patterns


def list_industries() -> list[str]:
    """Return available industry preset names."""
    return list(INDUSTRY_HOOK_PRESETS.keys())


def _detect_hooks(text: str) -> list[str]:
    """Detect hook patterns in the first line of a post."""
    first_line = text.split("\n")[0] if text else ""
    patterns = get_active_hooks()
    return [name for name, pat in patterns if pat.search(first_line)]


def detect_hooks_with_patterns(text: str, patterns: list[tuple[str, re.Pattern]]) -> list[str]:
    """Detect hook patterns using explicit pattern list (no global state)."""
    first_line = text.split("\n")[0] if text else ""
    return [name for name, pat in patterns if pat.search(first_line)]


def _text_length_bucket(text: str) -> str:
    length = len(text)
    if length < 100:
        return "short"
    elif length <= 300:
        return "medium"
    else:
        return "long"


# ── External Link Detection ──────────────────────────────────

_LINK_PATTERN = re.compile(r"https?://|LINE|ライン|リンク.*(プロフ|概要)|固定.*(見て|チェック)|プロフ.*(リンク|URL)")


def has_external_promotion(text: str) -> bool:
    """Detect external link/LINE/profile link promotion in text."""
    return bool(_LINK_PATTERN.search(text))


# ── CSV Analysis Cache ────────────────────────────────────────

_csv_analysis_cache: dict[str, tuple[float, "CSVAnalysisResult"]] = {}  # path -> (mtime, result)


def _get_cached_analysis(csv_path: Path) -> "CSVAnalysisResult | None":
    """Return cached analysis if the file hasn't been modified."""
    key = str(csv_path)
    if key in _csv_analysis_cache:
        cached_mtime, cached_result = _csv_analysis_cache[key]
        try:
            current_mtime = csv_path.stat().st_mtime
            if current_mtime == cached_mtime:
                return cached_result
        except OSError:
            pass
    return None


def _set_cached_analysis(csv_path: Path, result: "CSVAnalysisResult") -> None:
    """Store analysis result in cache."""
    try:
        mtime = csv_path.stat().st_mtime
        _csv_analysis_cache[str(csv_path)] = (mtime, result)
    except OSError:
        pass


# ── Main Analysis Functions ──────────────────────────────────


def analyze_optimal_times(csv_path: str | Path) -> CSVAnalysisResult:
    """Comprehensive analysis: time, day-of-week, and content patterns."""
    csv_path = Path(csv_path)
    cached = _get_cached_analysis(csv_path)
    if cached is not None:
        return cached

    rows = parse_csv(csv_path)
    result = CSVAnalysisResult()

    # Initialize all hours and day×hour combos
    for h in range(24):
        result.hour_stats[h] = HourStats(hour=h)
    for d in range(7):
        for h in range(24):
            result.day_hour_stats[(d, h)] = DayHourStats(day=d, hour=h)

    # Content analysis accumulators
    length_buckets: dict[str, list[dict]] = {"short": [], "medium": [], "long": []}
    hook_accum: dict[str, list[dict]] = {}
    link_views: list[int] = []
    no_link_views: list[int] = []
    all_posts_with_metrics: list[dict] = []

    for row in rows:
        date_str = row.get("Date", "").strip()
        if not date_str:
            continue

        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        except ValueError:
            continue

        hour = dt.hour
        dow = dt.weekday()
        views = _safe_int(row.get("Views", "0"))
        likes = _safe_int(row.get("Likes", "0"))
        replies = _safe_int(row.get("Replies", "0"))
        engagement = _safe_float(row.get("Engagement", "0"))
        content = row.get("Content", "")

        # Hour stats
        hs = result.hour_stats[hour]
        hs.post_count += 1
        hs.total_views += views
        hs.total_likes += likes
        hs.total_replies += replies
        hs.total_engagement += engagement

        # Day × Hour stats
        dh = result.day_hour_stats[(dow, hour)]
        dh.post_count += 1
        dh.total_views += views
        dh.total_likes += likes
        dh.total_replies += replies
        dh.total_engagement += engagement

        result.total_posts += 1

        # Content analysis
        post_data = {
            "date": date_str, "content": content, "views": views,
            "likes": likes, "replies": replies, "engagement": engagement,
        }
        all_posts_with_metrics.append(post_data)

        bucket = _text_length_bucket(content)
        length_buckets[bucket].append(post_data)

        hooks = _detect_hooks(content)
        for hook_name in hooks:
            hook_accum.setdefault(hook_name, []).append(post_data)

        if has_external_promotion(content):
            link_views.append(views)
        else:
            no_link_views.append(views)

    # Rank hours by score (reliable first)
    active_hours = [hs for hs in result.hour_stats.values() if hs.post_count > 0]
    active_hours.sort(key=lambda h: h.score, reverse=True)
    result.top_hours = [hs.hour for hs in active_hours]

    # Build content patterns
    for bucket_name, posts in length_buckets.items():
        bhs = HourStats(hour=0, post_count=len(posts))
        bhs.total_views = sum(p["views"] for p in posts)
        bhs.total_likes = sum(p["likes"] for p in posts)
        bhs.total_replies = sum(p["replies"] for p in posts)
        bhs.total_engagement = sum(p["engagement"] for p in posts)
        result.content.length_buckets[bucket_name] = bhs

    for hook_name, posts in hook_accum.items():
        hhs = HourStats(hour=0, post_count=len(posts))
        hhs.total_views = sum(p["views"] for p in posts)
        hhs.total_likes = sum(p["likes"] for p in posts)
        hhs.total_replies = sum(p["replies"] for p in posts)
        hhs.total_engagement = sum(p["engagement"] for p in posts)
        result.content.hook_patterns[hook_name] = hhs

    result.content.top_posts = sorted(
        all_posts_with_metrics, key=lambda p: p["views"], reverse=True
    )[:10]

    result.content.link_post_avg_views = (
        sum(link_views) / len(link_views) if link_views else 0
    )
    result.content.no_link_post_avg_views = (
        sum(no_link_views) / len(no_link_views) if no_link_views else 0
    )

    _set_cached_analysis(csv_path, result)
    return result


def _safe_int(val: str) -> int:
    try:
        return int(val.strip().strip('"').replace(",", ""))
    except (ValueError, AttributeError):
        return 0


def _safe_float(val: str) -> float:
    try:
        return float(val.strip().strip('"').replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0
