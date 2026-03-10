"""Hook × Theme matrix analysis for Threads posts.

Builds a cross-tabulation of Hook Type × Theme/Topic to identify
which hook structure works best for which content theme.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .csv_analyzer import _safe_float, _safe_int, parse_csv

# ── Hook Type Definitions (genre-independent structures) ─────


HOOK_TYPES: list[tuple[str, re.Pattern]] = [
    ("number型", re.compile(
        r"[\d０-９,]+[万億円%％]"  # numbers with units
        r"|[0-9０-９]+選"           # ○選
        r"|[0-9０-９]+つ"           # ○つ
        r"|[0-9０-９]{2,}"          # 2+ digit numbers
    )),
    ("question型", re.compile(
        r"[？\?]"                   # ends with ?
        r"|知ってる|知ってた|ですか|ません\?|だろう|でしょう"
    )),
    ("shocking型", re.compile(
        r"実は|知らない|知らなかった|衝撃|ヤバい|やばい|危険|注意|驚き"
    )),
    ("story型", re.compile(
        r"〜した|した話|だった話|の話$|体験|経験"
        r"|してみた|やってみた|だった$|でした$"
    )),
    ("command型", re.compile(
        r"しろ$|するな$|しなさい|してください|すべき|絶対"
        r"|やめろ|やめて|見ろ|見て$"
    )),
    ("list型", re.compile(
        r"[①②③④⑤⑥⑦⑧⑨⑩]"       # circled numbers
        r"|^・"                      # bullet points
        r"|^[1-9]\."                 # numbered list
        r"|まとめ|一覧|リスト"
    )),
    ("comparison型", re.compile(
        r"比較|vs|VS|ＶＳ|より|違い|どっち|一方|逆に|ビフォー|アフター"
    )),
    ("urgency型", re.compile(
        r"速報|締切|今すぐ|急いで|期限|緊急|残り|ラスト|最終"
    )),
]


# ── Theme Definitions (per genre + generic) ──────────────────


GENRE_THEMES: dict[str, list[tuple[str, re.Pattern]]] = {
    "補助金": [
        ("小規模持続化", re.compile(r"小規模|持続化|持続化補助金")),
        ("IT導入", re.compile(r"IT導入|IT補助金|デジタル")),
        ("事業再構築", re.compile(r"事業再構築|再構築")),
        ("創業", re.compile(r"創業|起業|スタートアップ|開業")),
        ("キャリアアップ", re.compile(r"キャリアアップ|キャリア助成|正社員化")),
        ("申請", re.compile(r"申請|書類|手続き|申込|応募")),
        ("税理士", re.compile(r"税理士|税務|会計|顧問")),
        ("経営", re.compile(r"経営|売上|利益|資金繰り|融資")),
    ],
    "転職": [
        ("面接", re.compile(r"面接|面談|質問|自己紹介")),
        ("履歴書", re.compile(r"履歴書|職務経歴|ES|エントリー")),
        ("年収", re.compile(r"年収|給与|給料|月収|手取り|報酬")),
        ("退職", re.compile(r"退職|辞め|退社|転職理由")),
        ("未経験", re.compile(r"未経験|異業種|ゼロから|初心者")),
        ("スキル", re.compile(r"スキル|資格|能力|経験")),
        ("副業", re.compile(r"副業|複業|兼業|ダブルワーク")),
        ("キャリア", re.compile(r"キャリア|成長|将来|キャリアプラン")),
    ],
    "軽配送": [
        ("配送", re.compile(r"配送|配達|デリバリー|宅配")),
        ("ルート", re.compile(r"ルート|エリア|コース|走行")),
        ("収入", re.compile(r"収入|稼ぎ|稼げ|売上|月収|日当")),
        ("開業", re.compile(r"開業|独立|起業|始め")),
        ("車両", re.compile(r"車両|車|バン|軽バン|ハイエース|リース")),
        ("荷物", re.compile(r"荷物|荷さばき|不在|再配達|置き配")),
        ("個人事業", re.compile(r"個人事業|確定申告|経費|税金|フリーランス")),
    ],
}

GENERIC_THEMES: list[tuple[str, re.Pattern]] = [
    ("失敗談", re.compile(r"失敗|やらかし|ミス|後悔|しくじ")),
    ("成功事例", re.compile(r"成功|うまく|達成|結果|成果|実績")),
    ("ノウハウ", re.compile(r"コツ|ノウハウ|方法|やり方|テクニック|ポイント|秘訣")),
    ("マインドセット", re.compile(r"マインド|考え方|意識|心構え|姿勢|覚悟|メンタル")),
    ("比較", re.compile(r"比較|違い|vs|どっち|メリット|デメリット")),
    ("速報/ニュース", re.compile(r"速報|ニュース|最新|発表|改正|変更")),
]


# ── Data Classes ─────────────────────────────────────────────


@dataclass
class MatrixCell:
    """A single cell in the Hook × Theme matrix."""

    hook_type: str
    theme: str
    count: int = 0
    total_views: int = 0
    total_engagement: float = 0.0
    top_post_text: str = ""
    top_post_views: int = 0

    @property
    def avg_views(self) -> float:
        return self.total_views / self.count if self.count else 0.0

    @property
    def avg_engagement(self) -> float:
        return self.total_engagement / self.count if self.count else 0.0

    def to_dict(self) -> dict:
        return {
            "hook_type": self.hook_type,
            "theme": self.theme,
            "count": self.count,
            "avg_views": round(self.avg_views, 1),
            "avg_engagement": round(self.avg_engagement, 2),
            "top_post": self.top_post_text[:120],
            "top_post_views": self.top_post_views,
        }


@dataclass
class HookThemeMatrix:
    """Complete Hook × Theme matrix for one or more profiles."""

    cells: dict[tuple[str, str], MatrixCell] = field(default_factory=dict)
    genre: str = ""
    profiles: list[str] = field(default_factory=list)
    total_posts: int = 0
    classified_posts: int = 0  # posts that matched at least one hook+theme

    def get_cell(self, hook: str, theme: str) -> MatrixCell | None:
        return self.cells.get((hook, theme))

    @property
    def hook_types(self) -> list[str]:
        return sorted({k[0] for k in self.cells})

    @property
    def themes(self) -> list[str]:
        return sorted({k[1] for k in self.cells})


# ── Hook & Theme Detection ───────────────────────────────────


def _detect_hook_types(text: str) -> list[str]:
    """Detect hook types from the first line of a post."""
    first_line = text.split("\n")[0] if text else ""
    return [name for name, pat in HOOK_TYPES if pat.search(first_line)]


def _detect_themes(text: str, genre: str | None = None) -> list[str]:
    """Detect themes from full post text."""
    themes_found: list[str] = []

    # Genre-specific themes
    if genre and genre in GENRE_THEMES:
        for name, pat in GENRE_THEMES[genre]:
            if pat.search(text):
                themes_found.append(name)

    # Generic themes (always checked)
    for name, pat in GENERIC_THEMES:
        if pat.search(text):
            themes_found.append(name)

    return themes_found


def _detect_genre(profile_name: str) -> str | None:
    """Auto-detect genre from profile name hint."""
    mapping = {
        "ryoooooo256": "補助金",
        "rei_tenshoku1": "転職",
        "yunsongsurezzu": "軽配送",
    }
    for key, genre in mapping.items():
        if key in profile_name:
            return genre
    return None


# ── Matrix Building ──────────────────────────────────────────


def build_matrix(csv_configs: list[dict]) -> HookThemeMatrix:
    """Build a Hook × Theme matrix from CSV data.

    Args:
        csv_configs: list of dicts with keys:
            - csv_path (str|Path): path to CSV file
            - genre (str, optional): genre name for theme detection
            - profile (str, optional): profile name / label

    Returns:
        HookThemeMatrix with all cells populated.
    """
    matrix = HookThemeMatrix()

    for config in csv_configs:
        csv_path = config["csv_path"]
        genre = config.get("genre") or _detect_genre(str(csv_path))
        profile = config.get("profile", Path(csv_path).stem)

        if genre and not matrix.genre:
            matrix.genre = genre
        elif genre and matrix.genre and genre != matrix.genre:
            matrix.genre = "mixed"

        matrix.profiles.append(profile)
        rows = parse_csv(csv_path)

        for row in rows:
            content = row.get("Content", "")
            if not content.strip():
                continue

            views = _safe_int(row.get("Views", "0"))
            engagement = _safe_float(row.get("Engagement", "0"))

            matrix.total_posts += 1

            hooks = _detect_hook_types(content)
            themes = _detect_themes(content, genre)

            if not hooks or not themes:
                continue

            matrix.classified_posts += 1

            for hook in hooks:
                for theme in themes:
                    key = (hook, theme)
                    if key not in matrix.cells:
                        matrix.cells[key] = MatrixCell(hook_type=hook, theme=theme)

                    cell = matrix.cells[key]
                    cell.count += 1
                    cell.total_views += views
                    cell.total_engagement += engagement

                    if views > cell.top_post_views:
                        cell.top_post_views = views
                        cell.top_post_text = content

    return matrix


# ── Query Functions ──────────────────────────────────────────


def get_best_hook_for_theme(
    matrix: HookThemeMatrix, theme: str
) -> list[tuple[str, float]]:
    """Ranked hooks for a given theme, sorted by avg_views descending.

    Returns:
        list of (hook_type, avg_views) tuples.
    """
    candidates = [
        cell for (h, t), cell in matrix.cells.items()
        if t == theme and cell.count > 0
    ]
    candidates.sort(key=lambda c: c.avg_views, reverse=True)
    return [(c.hook_type, round(c.avg_views, 1)) for c in candidates]


def get_best_theme_for_hook(
    matrix: HookThemeMatrix, hook: str
) -> list[tuple[str, float]]:
    """Ranked themes for a given hook type, sorted by avg_views descending.

    Returns:
        list of (theme, avg_views) tuples.
    """
    candidates = [
        cell for (h, t), cell in matrix.cells.items()
        if h == hook and cell.count > 0
    ]
    candidates.sort(key=lambda c: c.avg_views, reverse=True)
    return [(c.theme, round(c.avg_views, 1)) for c in candidates]


def get_top_combinations(
    matrix: HookThemeMatrix, n: int = 10
) -> list[dict]:
    """Top N hook+theme combinations by avg_views.

    Only includes combos with count >= 2 to filter noise.

    Returns:
        list of dicts with hook, theme, count, avg_views, avg_engagement, top_post.
    """
    cells = [c for c in matrix.cells.values() if c.count >= 2]
    cells.sort(key=lambda c: c.avg_views, reverse=True)
    return [c.to_dict() for c in cells[:n]]


# ── Report Generation ────────────────────────────────────────


def export_matrix_report(matrix: HookThemeMatrix) -> str:
    """Generate a human-readable text report of the matrix."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Hook × Theme マトリクス分析レポート")
    lines.append("=" * 70)
    lines.append(f"プロフィール: {', '.join(matrix.profiles)}")
    lines.append(f"ジャンル: {matrix.genre or '不明'}")
    lines.append(f"総投稿数: {matrix.total_posts}")
    lines.append(f"分類済み投稿: {matrix.classified_posts} "
                 f"({matrix.classified_posts / matrix.total_posts * 100:.1f}%)"
                 if matrix.total_posts else "")
    lines.append("")

    # ── Top Combinations ──
    lines.append("-" * 50)
    lines.append("■ トップ組み合わせ (avg_views順)")
    lines.append("-" * 50)
    top = get_top_combinations(matrix, n=15)
    if not top:
        lines.append("  (2件以上の組み合わせが見つかりませんでした)")
    for i, combo in enumerate(top, 1):
        lines.append(
            f"  {i:2d}. [{combo['hook_type']}] × [{combo['theme']}]"
            f"  投稿数={combo['count']}  平均Views={combo['avg_views']:.0f}"
            f"  平均Eng={combo['avg_engagement']:.2f}%"
        )
        if combo["top_post"]:
            lines.append(f"      └ \"{combo['top_post'][:80]}...\"")
    lines.append("")

    # ── Best Hook for Each Theme ──
    lines.append("-" * 50)
    lines.append("■ テーマ別ベストフック")
    lines.append("-" * 50)
    for theme in sorted(matrix.themes):
        ranked = get_best_hook_for_theme(matrix, theme)
        if not ranked:
            continue
        best_hook, best_views = ranked[0]
        total_in_theme = sum(
            c.count for c in matrix.cells.values() if c.theme == theme
        )
        lines.append(f"  {theme} (計{total_in_theme}件)")
        for hook, views in ranked[:3]:
            lines.append(f"    → {hook}: 平均{views:.0f} views")
    lines.append("")

    # ── Best Theme for Each Hook ──
    lines.append("-" * 50)
    lines.append("■ フック別ベストテーマ")
    lines.append("-" * 50)
    for hook in sorted(matrix.hook_types):
        ranked = get_best_theme_for_hook(matrix, hook)
        if not ranked:
            continue
        total_with_hook = sum(
            c.count for c in matrix.cells.values() if c.hook_type == hook
        )
        lines.append(f"  {hook} (計{total_with_hook}件)")
        for theme, views in ranked[:3]:
            lines.append(f"    → {theme}: 平均{views:.0f} views")
    lines.append("")

    # ── Full Matrix Table ──
    lines.append("-" * 50)
    lines.append("■ マトリクス全体 (投稿数 / 平均Views)")
    lines.append("-" * 50)

    themes = sorted(matrix.themes)
    hooks = sorted(matrix.hook_types)

    if themes and hooks:
        # Header
        header = f"{'':>14s}"
        for t in themes:
            header += f" {t:>10s}"
        lines.append(header)

        # Rows
        for hook in hooks:
            row_str = f"{hook:>14s}"
            for theme in themes:
                cell = matrix.get_cell(hook, theme)
                if cell and cell.count > 0:
                    row_str += f" {cell.count:>4d}/{cell.avg_views:>5.0f}"
                else:
                    row_str += f" {'---':>10s}"
            lines.append(row_str)

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


# ── Persistence ──────────────────────────────────────────────


def save_matrix(matrix: HookThemeMatrix, profile_dir: str | Path) -> Path:
    """Save matrix to JSON in the given profile directory.

    Returns:
        Path to the saved JSON file.
    """
    out_dir = Path(profile_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "hook_theme_matrix.json"

    data = {
        "genre": matrix.genre,
        "profiles": matrix.profiles,
        "total_posts": matrix.total_posts,
        "classified_posts": matrix.classified_posts,
        "cells": {
            f"{h}|{t}": {
                "hook_type": cell.hook_type,
                "theme": cell.theme,
                "count": cell.count,
                "total_views": cell.total_views,
                "total_engagement": cell.total_engagement,
                "top_post_text": cell.top_post_text,
                "top_post_views": cell.top_post_views,
            }
            for (h, t), cell in matrix.cells.items()
        },
    }

    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def load_matrix(profile_dir: str | Path) -> HookThemeMatrix:
    """Load matrix from JSON in the given profile directory.

    Raises:
        FileNotFoundError if no saved matrix exists.
    """
    in_path = Path(profile_dir) / "hook_theme_matrix.json"
    data = json.loads(in_path.read_text(encoding="utf-8"))

    matrix = HookThemeMatrix(
        genre=data["genre"],
        profiles=data["profiles"],
        total_posts=data["total_posts"],
        classified_posts=data["classified_posts"],
    )

    for key_str, cell_data in data["cells"].items():
        hook, theme = key_str.split("|", 1)
        cell = MatrixCell(
            hook_type=cell_data["hook_type"],
            theme=cell_data["theme"],
            count=cell_data["count"],
            total_views=cell_data["total_views"],
            total_engagement=cell_data["total_engagement"],
            top_post_text=cell_data["top_post_text"],
            top_post_views=cell_data["top_post_views"],
        )
        matrix.cells[(hook, theme)] = cell

    return matrix


# ── Convenience Runner ───────────────────────────────────────


def run_analysis() -> str:
    """Run the full matrix analysis on the three known CSV files.

    Returns the report as a string.
    """
    configs = [
        {
            "csv_path": "/Users/futakawa/Downloads/threads-posts_2024-12-01_2026-03-03.csv",
            "genre": "補助金",
            "profile": "@ryoooooo256",
        },
        {
            "csv_path": "/Users/futakawa/Downloads/threads-posts_2024-12-01_2026-03-03 (1).csv",
            "genre": "転職",
            "profile": "@rei_tenshoku1",
        },
        {
            "csv_path": "/Users/futakawa/Downloads/threads-posts_2024-12-01_2026-03-03 (2).csv",
            "genre": "軽配送",
            "profile": "@yunsongsurezzu",
        },
    ]

    matrix = build_matrix(configs)
    report = export_matrix_report(matrix)
    return report


if __name__ == "__main__":
    print(run_analysis())
