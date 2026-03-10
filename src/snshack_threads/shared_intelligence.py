"""Cross-genre shared intelligence module.

Analyzes CSV data from multiple genres/accounts to extract UNIVERSAL patterns
that work across all genres on Threads.

Covers:
- Character count vs performance analysis
- Posting time (day-of-week x hour) heatmap
- Post format classification and effectiveness
- CTA pattern detection and ranking
- Hook structure analysis (genre-independent)
- Line break density correlation
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from snshack_threads.csv_analyzer import parse_csv, _safe_int, _safe_float

# ── Constants ────────────────────────────────────────────────

_STORAGE_DIR = Path.home() / ".snshack-threads" / "shared"

_DOW_NAMES = ["月", "火", "水", "木", "金", "土", "日"]

# Character-length buckets
_LENGTH_BUCKETS: list[tuple[str, int, int]] = [
    ("~50", 0, 50),
    ("51-100", 51, 100),
    ("101-200", 101, 200),
    ("201-300", 201, 300),
    ("300+", 301, 999_999),
]

# ── Format Classification ────────────────────────────────────

_FORMAT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("question型", re.compile(r"[？\?]")),
    ("list型", re.compile(
        r"(?:①|②|③|❶|❷|❸|[①-⑩]|[❶-❿]|[1-9][\.．\)]|・.*\n.*・|STEP|ステップ)",
        re.MULTILINE,
    )),
    ("story型", re.compile(
        r"(実は|ある日|去年|先日|以前|昔|当時|あの時|〜した|した[。\n]|だった[。\n]|ました[。\n])",
    )),
    ("comparison型", re.compile(
        r"(vs|VS|ＶＳ|比較|違い|どっち|一方|それに対し|逆に|対して|けど実は)",
    )),
    ("punch-line型", re.compile(
        r"(。\n\n[^\n]{1,30}$|。$)",
        re.MULTILINE,
    )),
]

# ── CTA Detection ────────────────────────────────────────────

_CTA_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("保存CTA", re.compile(r"保存")),
    ("コメントCTA", re.compile(r"コメント")),
    ("フォローCTA", re.compile(r"フォロー")),
    ("いいねCTA", re.compile(r"いいね|👍|❤")),
    ("リポストCTA", re.compile(r"リポスト|シェア|拡散")),
    ("DM_CTA", re.compile(r"DM|ダイレクト")),
    ("質問CTA", re.compile(r"いますか[？\?]|ありますか[？\?]|どうですか[？\?]|ですか[？\?]$")),
]

# ── Hook Structure (genre-independent) ───────────────────────

_HOOK_STRUCTURE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("number型", re.compile(r"[0-9０-９,、]+[万億円%％件人倍年日本]|[0-9]{2,}")),
    ("question型", re.compile(r"[？\?]$|[？\?]\s*$")),
    ("shocking型", re.compile(r"衝撃|実は|知らない|ヤバい|やばい|驚き|まさか|嘘")),
    ("story型", re.compile(r"[〜~]?した|だった|ました|してた|していた|なった")),
    ("command型", re.compile(r"しろ[。！\n]|するな[。！\n]|やめろ|やめて|してください|すべき")),
]


# ── Data Structures ──────────────────────────────────────────


@dataclass
class BucketStats:
    """Aggregated performance stats for a category bucket."""

    label: str
    post_count: int = 0
    total_views: int = 0
    total_likes: int = 0
    total_replies: int = 0
    total_reposts: int = 0
    total_engagement: float = 0.0

    @property
    def avg_views(self) -> float:
        return self.total_views / self.post_count if self.post_count else 0.0

    @property
    def avg_likes(self) -> float:
        return self.total_likes / self.post_count if self.post_count else 0.0

    @property
    def avg_engagement(self) -> float:
        return self.total_engagement / self.post_count if self.post_count else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "post_count": self.post_count,
            "avg_views": round(self.avg_views, 1),
            "avg_likes": round(self.avg_likes, 2),
            "avg_replies": round(
                self.total_replies / self.post_count if self.post_count else 0, 2,
            ),
            "avg_engagement": round(self.avg_engagement, 3),
        }


@dataclass
class GenreData:
    """Parsed post data for a single genre/account."""

    genre: str
    account: str
    posts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SharedInsights:
    """Cross-genre analysis results."""

    # Character length analysis
    length_overall: dict[str, BucketStats] = field(default_factory=dict)
    length_per_genre: dict[str, dict[str, BucketStats]] = field(default_factory=dict)

    # Time analysis: key = "dow:hour" -> BucketStats
    time_heatmap: dict[str, BucketStats] = field(default_factory=dict)

    # Format analysis
    format_effectiveness: dict[str, BucketStats] = field(default_factory=dict)

    # CTA analysis
    cta_effectiveness: dict[str, BucketStats] = field(default_factory=dict)
    no_cta_baseline: BucketStats | None = None

    # Hook structure analysis
    hook_structures: dict[str, BucketStats] = field(default_factory=dict)
    hook_per_genre: dict[str, dict[str, BucketStats]] = field(default_factory=dict)

    # Line break analysis
    linebreak_buckets: dict[str, BucketStats] = field(default_factory=dict)

    # Metadata
    total_posts: int = 0
    genre_count: int = 0
    genres: list[str] = field(default_factory=list)
    analyzed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "metadata": {
                "total_posts": self.total_posts,
                "genre_count": self.genre_count,
                "genres": self.genres,
                "analyzed_at": self.analyzed_at,
            },
            "length_analysis": {
                "overall": {k: v.to_dict() for k, v in self.length_overall.items()},
                "per_genre": {
                    genre: {k: v.to_dict() for k, v in buckets.items()}
                    for genre, buckets in self.length_per_genre.items()
                },
            },
            "time_heatmap": {k: v.to_dict() for k, v in self.time_heatmap.items()},
            "format_analysis": {
                k: v.to_dict() for k, v in self.format_effectiveness.items()
            },
            "cta_analysis": {
                "cta_types": {
                    k: v.to_dict() for k, v in self.cta_effectiveness.items()
                },
                "no_cta_baseline": self.no_cta_baseline.to_dict()
                if self.no_cta_baseline
                else None,
            },
            "hook_structures": {
                "overall": {
                    k: v.to_dict() for k, v in self.hook_structures.items()
                },
                "per_genre": {
                    genre: {k: v.to_dict() for k, v in buckets.items()}
                    for genre, buckets in self.hook_per_genre.items()
                },
            },
            "linebreak_analysis": {
                k: v.to_dict() for k, v in self.linebreak_buckets.items()
            },
        }


# ── Internal Helpers ─────────────────────────────────────────


def _length_bucket(text: str) -> str:
    """Classify text into a character-length bucket."""
    n = len(text)
    for label, lo, hi in _LENGTH_BUCKETS:
        if lo <= n <= hi:
            return label
    return "300+"


def _linebreak_bucket(text: str) -> str:
    """Classify by number of line breaks."""
    breaks = text.count("\n")
    if breaks == 0:
        return "0行"
    if breaks <= 2:
        return "1-2行"
    if breaks <= 5:
        return "3-5行"
    if breaks <= 10:
        return "6-10行"
    return "11+行"


def _classify_formats(content: str) -> list[str]:
    """Classify a post into format types (can match multiple)."""
    matched: list[str] = []
    for name, pat in _FORMAT_PATTERNS:
        if pat.search(content):
            matched.append(name)
    return matched


def _detect_ctas(content: str) -> list[str]:
    """Detect CTA patterns in the last 30% of a post."""
    # Focus on the tail of the post where CTAs typically appear
    tail_start = max(0, len(content) - max(len(content) // 3, 60))
    tail = content[tail_start:]
    return [name for name, pat in _CTA_PATTERNS if pat.search(tail)]


def _detect_hook_structures(content: str) -> list[str]:
    """Detect hook structure type from the first line."""
    first_line = content.split("\n")[0] if content else ""
    return [name for name, pat in _HOOK_STRUCTURE_PATTERNS if pat.search(first_line)]


def _add_to_bucket(bucket: BucketStats, post: dict[str, Any]) -> None:
    """Accumulate a post's metrics into a BucketStats."""
    bucket.post_count += 1
    bucket.total_views += post["views"]
    bucket.total_likes += post["likes"]
    bucket.total_replies += post["replies"]
    bucket.total_reposts += post["reposts"]
    bucket.total_engagement += post["engagement"]


def _parse_posts(csv_path: str | Path) -> list[dict[str, Any]]:
    """Parse CSV rows into standardized post dicts, reusing csv_analyzer.parse_csv."""
    rows = parse_csv(csv_path)
    posts: list[dict[str, Any]] = []

    for row in rows:
        date_str = row.get("Date", "").strip()
        if not date_str:
            continue

        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        except ValueError:
            continue

        content = row.get("Content", "")
        posts.append({
            "content": content,
            "datetime": dt,
            "hour": dt.hour,
            "dow": dt.weekday(),
            "views": _safe_int(row.get("Views", "0")),
            "likes": _safe_int(row.get("Likes", "0")),
            "replies": _safe_int(row.get("Replies", "0")),
            "reposts": _safe_int(row.get("Reposts", "0")),
            "engagement": _safe_float(row.get("Engagement", "0")),
            "char_count": len(content),
            "linebreaks": content.count("\n"),
        })

    return posts


# ── Main Analysis ────────────────────────────────────────────


def analyze_cross_genre(csv_configs: list[dict[str, str]]) -> SharedInsights:
    """Analyze multiple genre CSVs to find universal patterns.

    Args:
        csv_configs: List of dicts with keys:
            - "path": path to CSV file
            - "genre": genre label (e.g. "補助金")
            - "account": account handle (e.g. "@ryoooooo256")

    Returns:
        SharedInsights with all cross-genre analysis results.
    """
    insights = SharedInsights(
        analyzed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # Collect all posts per genre
    genre_datasets: list[GenreData] = []
    all_posts: list[dict[str, Any]] = []

    for cfg in csv_configs:
        posts = _parse_posts(cfg["path"])
        gd = GenreData(genre=cfg["genre"], account=cfg["account"], posts=posts)
        genre_datasets.append(gd)
        all_posts.extend(posts)

    insights.total_posts = len(all_posts)
    insights.genre_count = len(genre_datasets)
    insights.genres = [gd.genre for gd in genre_datasets]

    # ── 1. Character count analysis ──────────────────────────

    # Overall
    for label, _, _ in _LENGTH_BUCKETS:
        insights.length_overall[label] = BucketStats(label=label)

    for post in all_posts:
        bucket_label = _length_bucket(post["content"])
        _add_to_bucket(insights.length_overall[bucket_label], post)

    # Per genre
    for gd in genre_datasets:
        genre_buckets: dict[str, BucketStats] = {}
        for label, _, _ in _LENGTH_BUCKETS:
            genre_buckets[label] = BucketStats(label=label)
        for post in gd.posts:
            bucket_label = _length_bucket(post["content"])
            _add_to_bucket(genre_buckets[bucket_label], post)
        insights.length_per_genre[gd.genre] = genre_buckets

    # ── 2. Posting time heatmap ──────────────────────────────

    for dow in range(7):
        for hour in range(24):
            key = f"{_DOW_NAMES[dow]}:{hour:02d}"
            insights.time_heatmap[key] = BucketStats(label=key)

    for post in all_posts:
        key = f"{_DOW_NAMES[post['dow']]}:{post['hour']:02d}"
        _add_to_bucket(insights.time_heatmap[key], post)

    # ── 3. Format analysis ───────────────────────────────────

    for name, _ in _FORMAT_PATTERNS:
        insights.format_effectiveness[name] = BucketStats(label=name)
    insights.format_effectiveness["unclassified"] = BucketStats(label="unclassified")

    for post in all_posts:
        formats = _classify_formats(post["content"])
        if not formats:
            _add_to_bucket(insights.format_effectiveness["unclassified"], post)
        else:
            for fmt in formats:
                _add_to_bucket(insights.format_effectiveness[fmt], post)

    # ── 4. CTA analysis ─────────────────────────────────────

    for name, _ in _CTA_PATTERNS:
        insights.cta_effectiveness[name] = BucketStats(label=name)
    no_cta = BucketStats(label="no_cta")

    for post in all_posts:
        ctas = _detect_ctas(post["content"])
        if not ctas:
            _add_to_bucket(no_cta, post)
        else:
            for cta in ctas:
                _add_to_bucket(insights.cta_effectiveness[cta], post)

    insights.no_cta_baseline = no_cta

    # ── 5. Hook structure analysis ───────────────────────────

    for name, _ in _HOOK_STRUCTURE_PATTERNS:
        insights.hook_structures[name] = BucketStats(label=name)
    insights.hook_structures["other"] = BucketStats(label="other")

    for post in all_posts:
        hooks = _detect_hook_structures(post["content"])
        if not hooks:
            _add_to_bucket(insights.hook_structures["other"], post)
        else:
            for hook in hooks:
                _add_to_bucket(insights.hook_structures[hook], post)

    # Per genre hooks
    for gd in genre_datasets:
        genre_hooks: dict[str, BucketStats] = {}
        for name, _ in _HOOK_STRUCTURE_PATTERNS:
            genre_hooks[name] = BucketStats(label=name)
        genre_hooks["other"] = BucketStats(label="other")

        for post in gd.posts:
            hooks = _detect_hook_structures(post["content"])
            if not hooks:
                _add_to_bucket(genre_hooks["other"], post)
            else:
                for hook in hooks:
                    _add_to_bucket(genre_hooks[hook], post)

        insights.hook_per_genre[gd.genre] = genre_hooks

    # ── 6. Line break analysis ───────────────────────────────

    lb_labels = ["0行", "1-2行", "3-5行", "6-10行", "11+行"]
    for label in lb_labels:
        insights.linebreak_buckets[label] = BucketStats(label=label)

    for post in all_posts:
        lb = _linebreak_bucket(post["content"])
        _add_to_bucket(insights.linebreak_buckets[lb], post)

    return insights


# ── Persistence ──────────────────────────────────────────────


def update_shared_data(insights: SharedInsights) -> Path:
    """Save analysis results to disk.

    Stores three JSON files under ~/.snshack-threads/shared/:
    - universal_insights.json  (full analysis)
    - hook_structures.json     (hook-specific data)
    - cta_effectiveness.json   (CTA-specific data)

    Returns:
        Path to the storage directory.
    """
    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    full_data = insights.to_dict()

    # 1. Universal insights (everything)
    _write_json(_STORAGE_DIR / "universal_insights.json", full_data)

    # 2. Hook structures (focused extract)
    hook_data = {
        "metadata": full_data["metadata"],
        "hook_structures": full_data["hook_structures"],
    }
    _write_json(_STORAGE_DIR / "hook_structures.json", hook_data)

    # 3. CTA effectiveness (focused extract)
    cta_data = {
        "metadata": full_data["metadata"],
        "cta_analysis": full_data["cta_analysis"],
    }
    _write_json(_STORAGE_DIR / "cta_effectiveness.json", cta_data)

    return _STORAGE_DIR


def load_shared_insights() -> dict[str, Any]:
    """Load previously saved universal insights from disk.

    Returns:
        Dict with all saved analysis data, or empty dict if no data exists.
    """
    path = _STORAGE_DIR / "universal_insights.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def get_universal_recommendations() -> dict[str, Any]:
    """Generate actionable recommendations from saved shared insights.

    Recommendations are genre-agnostic and suitable for new accounts
    entering any niche.

    Returns:
        Dict with categorized recommendations.
    """
    data = load_shared_insights()
    if not data:
        return {"error": "共有インサイトデータが見つかりません。先にanalyze_cross_genreを実行してください。"}

    recs: dict[str, Any] = {
        "optimal_length": _recommend_length(data),
        "best_posting_times": _recommend_times(data),
        "best_formats": _recommend_formats(data),
        "best_ctas": _recommend_ctas(data),
        "best_hooks": _recommend_hooks(data),
        "optimal_linebreaks": _recommend_linebreaks(data),
    }
    return recs


# ── Recommendation Generators ────────────────────────────────


def _recommend_length(data: dict[str, Any]) -> dict[str, Any]:
    """Find the optimal character count range."""
    buckets = data.get("length_analysis", {}).get("overall", {})
    if not buckets:
        return {}

    ranked = sorted(
        buckets.values(),
        key=lambda b: b.get("avg_views", 0),
        reverse=True,
    )
    best = ranked[0] if ranked else {}
    return {
        "recommendation": f"最適な文字数は「{best.get('label', '?')}」文字です（平均閲覧数: {best.get('avg_views', 0):.0f}）",
        "ranking": [
            {"range": b.get("label"), "avg_views": b.get("avg_views", 0), "posts": b.get("post_count", 0)}
            for b in ranked
            if b.get("post_count", 0) >= 5
        ],
    }


def _recommend_times(data: dict[str, Any]) -> dict[str, Any]:
    """Find the best posting time slots across all genres."""
    heatmap = data.get("time_heatmap", {})
    if not heatmap:
        return {}

    # Filter to slots with enough data
    valid = [
        (k, v) for k, v in heatmap.items()
        if v.get("post_count", 0) >= 5
    ]
    ranked = sorted(valid, key=lambda kv: kv[1].get("avg_views", 0), reverse=True)

    top_5 = ranked[:5]
    return {
        "recommendation": "全ジャンル共通で効果的な投稿時間帯",
        "top_slots": [
            {
                "slot": k,
                "avg_views": v.get("avg_views", 0),
                "avg_engagement": v.get("avg_engagement", 0),
                "posts": v.get("post_count", 0),
            }
            for k, v in top_5
        ],
    }


def _recommend_formats(data: dict[str, Any]) -> dict[str, Any]:
    """Rank post formats by effectiveness."""
    formats = data.get("format_analysis", {})
    if not formats:
        return {}

    ranked = sorted(
        formats.values(),
        key=lambda f: f.get("avg_views", 0),
        reverse=True,
    )
    return {
        "recommendation": "効果的な投稿フォーマット",
        "ranking": [
            {
                "format": f.get("label"),
                "avg_views": f.get("avg_views", 0),
                "avg_engagement": f.get("avg_engagement", 0),
                "posts": f.get("post_count", 0),
            }
            for f in ranked
            if f.get("post_count", 0) >= 5
        ],
    }


def _recommend_ctas(data: dict[str, Any]) -> dict[str, Any]:
    """Rank CTA types by engagement lift vs no-CTA baseline."""
    cta_section = data.get("cta_analysis", {})
    cta_types = cta_section.get("cta_types", {})
    baseline = cta_section.get("no_cta_baseline", {})
    baseline_eng = baseline.get("avg_engagement", 0) if baseline else 0

    ranked = sorted(
        cta_types.values(),
        key=lambda c: c.get("avg_engagement", 0),
        reverse=True,
    )
    return {
        "recommendation": "効果的なCTAパターン",
        "baseline_engagement": baseline_eng,
        "ranking": [
            {
                "cta": c.get("label"),
                "avg_engagement": c.get("avg_engagement", 0),
                "lift_vs_baseline": round(
                    c.get("avg_engagement", 0) - baseline_eng, 3,
                ),
                "posts": c.get("post_count", 0),
            }
            for c in ranked
            if c.get("post_count", 0) >= 3
        ],
    }


def _recommend_hooks(data: dict[str, Any]) -> dict[str, Any]:
    """Rank hook structures by view performance."""
    hooks = data.get("hook_structures", {}).get("overall", {})
    if not hooks:
        return {}

    ranked = sorted(
        hooks.values(),
        key=lambda h: h.get("avg_views", 0),
        reverse=True,
    )
    return {
        "recommendation": "効果的なフック構造（1行目パターン）",
        "ranking": [
            {
                "hook": h.get("label"),
                "avg_views": h.get("avg_views", 0),
                "avg_engagement": h.get("avg_engagement", 0),
                "posts": h.get("post_count", 0),
            }
            for h in ranked
            if h.get("post_count", 0) >= 5
        ],
    }


def _recommend_linebreaks(data: dict[str, Any]) -> dict[str, Any]:
    """Find optimal line break density."""
    lb = data.get("linebreak_analysis", {})
    if not lb:
        return {}

    ranked = sorted(
        lb.values(),
        key=lambda b: b.get("avg_views", 0),
        reverse=True,
    )
    best = ranked[0] if ranked else {}
    return {
        "recommendation": f"最適な改行数は「{best.get('label', '?')}」です（平均閲覧数: {best.get('avg_views', 0):.0f}）",
        "ranking": [
            {"breaks": b.get("label"), "avg_views": b.get("avg_views", 0), "posts": b.get("post_count", 0)}
            for b in ranked
            if b.get("post_count", 0) >= 5
        ],
    }


# ── Utilities ────────────────────────────────────────────────


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write dict as pretty-printed JSON."""
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
