"""Streamlit web dashboard for snshack-threads analytics.

Multi-client support: each profile gets its own "room" with full analytics.
Data sources:
1. Post history (post_history.json) — primary, always available
2. CSV mode — upload or use bundled CSV
3. API mode — live data from Metricool
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


def main() -> None:
    """Main entry point for the Streamlit dashboard."""
    try:
        import streamlit as st
    except ImportError:
        print("streamlit not installed. Run: pip install 'snshack-threads[dashboard]'")
        return

    st.set_page_config(
        page_title="SNShack Threads Dashboard",
        page_icon="📊",
        layout="wide",
    )

    # ── Sidebar: Client Selection ──────────────────────────
    st.sidebar.title("📊 SNShack Threads")

    from .config import get_settings, list_profiles

    profiles = list_profiles()

    if not profiles:
        st.sidebar.warning("プロフィールが未設定です")
        st.info("まず `snshack profile create` でプロフィールを作成してください")
        return

    # Profile selector with display names
    profile_labels = {}
    for p in profiles:
        try:
            s = get_settings(profile=p)
            label = s.display_name or p
            profile_labels[p] = f"{label} (@{p})" if s.display_name else p
        except Exception:
            profile_labels[p] = p

    selected_profile = st.sidebar.selectbox(
        "クライアント選択",
        profiles,
        format_func=lambda p: profile_labels.get(p, p),
    )

    settings = get_settings(profile=selected_profile)

    # ── Load Data ──────────────────────────────────────────
    df = _load_profile_data(st, settings, selected_profile)

    if df is None or df.empty:
        st.warning(f"**{profile_labels.get(selected_profile, selected_profile)}** のデータがまだありません")
        st.info("autopilot を実行して投稿データを蓄積してください")
        return

    # ── Client Header ──────────────────────────────────────
    display_name = settings.display_name or selected_profile
    st.title(f"{display_name}")

    # Phase badge
    phase_info = _get_phase_info(settings, selected_profile)
    phase_colors = {"bootstrap": "🟡", "learning": "🟠", "optimized": "🟢"}
    phase_labels = {"bootstrap": "ブートストラップ", "learning": "学習中", "optimized": "最適化済み"}
    phase_icon = phase_colors.get(phase_info["phase"], "⚪")
    phase_label = phase_labels.get(phase_info["phase"], phase_info["phase"])
    st.caption(
        f"{phase_icon} フェーズ: **{phase_label}** | "
        f"収集済み: **{phase_info['collected']}**投稿 | "
        f"ジャンル: **{settings.genre or '未設定'}**"
    )

    # ── Tabs ───────────────────────────────────────────────
    tab_overview, tab_time, tab_hooks, tab_content, tab_posts, tab_status = st.tabs([
        "📈 概要", "⏰ 時間帯分析", "🎣 フック分析", "📝 コンテンツ分析", "📋 投稿一覧", "⚙️ 学習状態",
    ])

    with tab_overview:
        _render_overview(st, df)

    with tab_time:
        _render_time_analysis(st, df)

    with tab_hooks:
        _render_hook_analysis(st, df)

    with tab_content:
        _render_content_analysis(st, df)

    with tab_posts:
        _render_post_list(st, df)

    with tab_status:
        _render_learning_status(st, settings, selected_profile)


# ── Data Loading ─────────────────────────────────────────


def _load_profile_data(st, settings, profile: str):
    """Load data from profile's post_history.json, with CSV/API fallback."""

    # Primary: post_history.json
    history_path = settings.profile_dir / "post_history.json"
    if history_path.exists():
        df = _load_post_history(history_path)
        if df is not None and not df.empty:
            st.sidebar.success(f"📂 post_history.json ({len(df)}件)")

            # Optional: supplement with CSV upload
            st.sidebar.markdown("---")
            uploaded = st.sidebar.file_uploader("CSV追加データ", type=["csv"], key=f"csv_{profile}")
            if uploaded:
                csv_df = _load_csv_upload(uploaded)
                if csv_df is not None:
                    df = pd.concat([df, csv_df], ignore_index=True)
                    df = df.drop_duplicates(subset=["Content"], keep="first")
                    st.sidebar.info(f"CSV統合後: {len(df)}件")
            return df

    # Fallback: CSV
    st.sidebar.info("post_historyなし — CSVモード")
    csv_path = None
    if settings.csv_path:
        csv_path = Path(settings.csv_path)
    elif settings.reference_csv_path.exists():
        csv_path = settings.reference_csv_path

    if csv_path and csv_path.exists():
        return _load_csv_file(csv_path)

    uploaded = st.sidebar.file_uploader("CSVアップロード", type=["csv"], key=f"csv_{profile}")
    if uploaded:
        return _load_csv_upload(uploaded)

    return None


def _load_post_history(path: Path):
    """Load post_history.json into a DataFrame."""
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data:
            return None

        rows = []
        for record in data:
            if record.get("status") != "collected":
                continue
            rows.append({
                "Content": record.get("text", ""),
                "Date": record.get("scheduled_at", ""),
                "Views": record.get("views", 0),
                "Likes": record.get("likes", 0),
                "Replies": record.get("replies", 0),
                "Reposts": record.get("reposts", 0),
                "Quotes": record.get("quotes", 0),
                "Engagement": record.get("engagement", 0.0),
                "PostType": record.get("post_type", "reach"),
            })
        if not rows:
            return None
        df = pd.DataFrame(rows)
        return _normalize_df(df)
    except Exception:
        return None


def _load_csv_file(path: Path):
    """Load a CSV file with encoding detection."""
    for encoding in ("utf-8-sig", "cp932", "utf-8"):
        try:
            df = pd.read_csv(path, encoding=encoding)
            return _normalize_df(df)
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    return None


def _load_csv_upload(uploaded):
    """Load an uploaded CSV file."""
    for encoding in ("utf-8-sig", "cp932", "utf-8"):
        try:
            uploaded.seek(0)
            df = pd.read_csv(uploaded, encoding=encoding)
            return _normalize_df(df)
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    return None


def _normalize_df(df):
    """Normalize column names and parse dates."""
    for col in ["Views", "Likes", "Replies", "Reposts", "Quotes", "Engagement"]:
        if col not in df.columns:
            df[col] = 0

    for col in ["Content", "Date"]:
        if col not in df.columns:
            df[col] = ""

    if "PostType" not in df.columns:
        df["PostType"] = "reach"

    for col in ["Views", "Likes", "Replies", "Reposts", "Quotes", "Engagement"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])

    df["Hour"] = df["Date"].dt.hour
    df["DayOfWeek"] = df["Date"].dt.dayofweek
    df["DayName"] = df["DayOfWeek"].map(
        {0: "月", 1: "火", 2: "水", 3: "木", 4: "金", 5: "土", 6: "日"}
    )
    df["CharCount"] = df["Content"].astype(str).str.len()

    return df


def _get_phase_info(settings, profile: str) -> dict:
    """Get phase and stats for a profile."""
    try:
        from .post_history import PostHistory
        from .data_resolver import resolve_phase

        history = PostHistory(history_path=settings.profile_dir / "post_history.json")
        collected = sum(1 for r in history.get_all() if r.has_metrics)
        phase, tier = resolve_phase(profile=profile)
        return {"phase": phase, "collected": collected, "tier": tier.value, "total": history.count}
    except Exception:
        return {"phase": "bootstrap", "collected": 0, "tier": "universal", "total": 0}


# ── Tab Renderers ─────────────────────────────────────────


def _render_overview(st, df):
    """Render the overview tab."""
    st.header("📈 概要")

    total = len(df)
    total_views = int(df["Views"].sum())
    avg_views = df["Views"].mean()
    avg_likes = df["Likes"].mean()
    avg_engagement = df["Engagement"].mean()
    total_likes = int(df["Likes"].sum())

    col1, col2, col3 = st.columns(3)
    col1.metric("投稿数", f"{total:,}")
    col2.metric("合計閲覧数", f"{total_views:,}")
    col3.metric("合計いいね", f"{total_likes:,}")

    col4, col5, col6 = st.columns(3)
    col4.metric("平均閲覧数", f"{avg_views:,.0f}")
    col5.metric("平均いいね", f"{avg_likes:,.1f}")
    col6.metric("平均エンゲージメント", f"{avg_engagement:.2%}")

    # Views over time
    st.subheader("閲覧数の推移")
    daily = df.set_index("Date").resample("D")["Views"].sum().reset_index()
    daily.columns = ["日付", "閲覧数"]
    st.line_chart(daily, x="日付", y="閲覧数")

    # Post type breakdown
    if "PostType" in df.columns:
        st.subheader("投稿タイプ別パフォーマンス")
        type_stats = df.groupby("PostType")["Views"].agg(["mean", "count"]).reset_index()
        type_stats.columns = ["タイプ", "平均閲覧数", "投稿数"]
        type_stats["タイプ"] = type_stats["タイプ"].map({"reach": "リーチ型", "list": "リスト獲得型"})
        st.dataframe(type_stats, use_container_width=True, hide_index=True)

    # Top 5 posts
    st.subheader("🏆 トップ5投稿")
    top5 = df.nlargest(5, "Views")[["Date", "Content", "Views", "Likes", "Replies", "Engagement"]]
    top5["Content"] = top5["Content"].astype(str).str[:80]
    top5["Date"] = top5["Date"].dt.strftime("%Y-%m-%d %H:%M")
    st.dataframe(top5, use_container_width=True, hide_index=True)


def _render_time_analysis(st, df):
    """Render time-of-day analysis."""
    st.header("⏰ 時間帯別パフォーマンス")

    # Hourly
    st.subheader("時間帯別 平均閲覧数")
    hourly = df.groupby("Hour")["Views"].agg(["mean", "count"]).reset_index()
    hourly.columns = ["時間", "平均閲覧数", "投稿数"]
    hourly = hourly[hourly["投稿数"] > 0]
    st.bar_chart(hourly, x="時間", y="平均閲覧数")
    st.dataframe(hourly, use_container_width=True, hide_index=True)

    # Day of week
    st.subheader("曜日別 平均閲覧数")
    dow_order = ["月", "火", "水", "木", "金", "土", "日"]
    daily = df.groupby("DayName")["Views"].agg(["mean", "count"]).reset_index()
    daily.columns = ["曜日", "平均閲覧数", "投稿数"]
    daily["曜日"] = pd.Categorical(daily["曜日"], categories=dow_order, ordered=True)
    daily = daily.sort_values("曜日")
    st.bar_chart(daily, x="曜日", y="平均閲覧数")

    # Heatmap: day x hour
    st.subheader("曜日×時間帯 ヒートマップ")
    pivot = df.pivot_table(values="Views", index="DayName", columns="Hour", aggfunc="mean")
    pivot = pivot.reindex(dow_order)
    st.dataframe(
        pivot.style.background_gradient(cmap="YlOrRd", axis=None).format("{:.0f}"),
        use_container_width=True,
    )


def _render_hook_analysis(st, df):
    """Render hook pattern analysis."""
    st.header("🎣 フックパターン分析")

    from .csv_analyzer import _detect_hooks

    hook_data = []
    for _, row in df.iterrows():
        content = str(row.get("Content", ""))
        hooks = _detect_hooks(content)
        for hook in hooks:
            hook_data.append({
                "hook": hook,
                "views": row["Views"],
                "likes": row["Likes"],
                "engagement": row["Engagement"],
            })

    if not hook_data:
        st.info("フックパターンが検出されませんでした")
        return

    hdf = pd.DataFrame(hook_data)
    summary = hdf.groupby("hook").agg(
        平均閲覧数=("views", "mean"),
        平均いいね=("likes", "mean"),
        投稿数=("views", "count"),
    ).reset_index()
    summary.columns = ["フック", "平均閲覧数", "平均いいね", "投稿数"]
    summary = summary.sort_values("平均閲覧数", ascending=False)

    st.bar_chart(summary, x="フック", y="平均閲覧数")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    # Hook combinations
    st.subheader("フック組み合わせ")
    combo_data = []
    for _, row in df.iterrows():
        hooks = _detect_hooks(str(row.get("Content", "")))
        if len(hooks) >= 2:
            combo = " + ".join(sorted(hooks[:3]))
            combo_data.append({"combo": combo, "views": row["Views"]})

    if combo_data:
        cdf = pd.DataFrame(combo_data)
        combo_summary = cdf.groupby("combo").agg(
            平均閲覧数=("views", "mean"),
            投稿数=("views", "count"),
        ).reset_index()
        combo_summary.columns = ["組み合わせ", "平均閲覧数", "投稿数"]
        combo_summary = combo_summary[combo_summary["投稿数"] >= 2].sort_values("平均閲覧数", ascending=False)
        if not combo_summary.empty:
            st.dataframe(combo_summary, use_container_width=True, hide_index=True)


def _render_content_analysis(st, df):
    """Render content pattern analysis."""
    st.header("📝 コンテンツ分析")

    # Length analysis
    st.subheader("文字数と閲覧数の関係")

    def length_bucket(n):
        if n < 100:
            return "短い (<100)"
        elif n <= 300:
            return "中程度 (100-300)"
        return "長い (300+)"

    df["LengthBucket"] = df["CharCount"].apply(length_bucket)
    length_stats = df.groupby("LengthBucket")["Views"].agg(["mean", "count"]).reset_index()
    length_stats.columns = ["文字数", "平均閲覧数", "投稿数"]
    st.bar_chart(length_stats, x="文字数", y="平均閲覧数")
    st.dataframe(length_stats, use_container_width=True, hide_index=True)

    # External link penalty
    st.subheader("外部リンクの影響")
    from .csv_analyzer import has_external_promotion

    df["HasLink"] = df["Content"].astype(str).apply(has_external_promotion)
    link_stats = df.groupby("HasLink")["Views"].mean().reset_index()
    link_stats.columns = ["外部リンクあり", "平均閲覧数"]
    link_stats["外部リンクあり"] = link_stats["外部リンクあり"].map({True: "あり", False: "なし"})
    st.bar_chart(link_stats, x="外部リンクあり", y="平均閲覧数")

    # Engagement distribution
    st.subheader("エンゲージメント分布")
    st.scatter_chart(df, x="Views", y="Likes", size="Replies")


def _render_post_list(st, df):
    """Render the full post list with search."""
    st.header("📋 投稿一覧")

    search = st.text_input("🔍 検索")
    col1, col2 = st.columns(2)
    sort_by = col1.selectbox("並び替え", ["Views", "Likes", "Engagement", "Date"])
    ascending = col2.checkbox("昇順", value=False)

    filtered = df.copy()
    if search:
        filtered = filtered[
            filtered["Content"].astype(str).str.contains(search, case=False, na=False)
        ]

    filtered = filtered.sort_values(sort_by, ascending=ascending)

    display = filtered[["Date", "Content", "Views", "Likes", "Replies", "Engagement"]].copy()
    display["Date"] = display["Date"].dt.strftime("%Y-%m-%d %H:%M")
    display["Content"] = display["Content"].astype(str).str[:100]

    st.write(f"**{len(display)}件**の投稿")
    st.dataframe(display, use_container_width=True, hide_index=True)


def _render_learning_status(st, settings, profile: str):
    """Render the learning/automation status tab."""
    st.header("⚙️ 学習状態")

    # Phase progress
    phase_info = _get_phase_info(settings, profile)
    collected = phase_info["collected"]

    st.subheader("フェーズ進捗")
    if collected < 100:
        progress = collected / 100
        next_phase = "学習フェーズ"
        remaining = 100 - collected
    elif collected < 200:
        progress = (collected - 100) / 100
        next_phase = "最適化フェーズ"
        remaining = 200 - collected
    else:
        progress = 1.0
        next_phase = None
        remaining = 0

    st.progress(progress)
    if next_phase:
        st.caption(f"次のフェーズ（{next_phase}）まであと **{remaining}投稿**")
    else:
        st.caption("最適化フェーズに到達済み")

    # Data resolution status
    st.subheader("データソース状況")
    try:
        from .data_resolver import get_resolution_status
        status = get_resolution_status(profile=profile)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("**自分のデータ**")
            acct = status.get("account", {})
            st.write(f"投稿数: {acct.get('total_posts', 0)}")
            st.write(f"収集済み: {acct.get('collected_posts', 0)}")
            st.write(f"使用可能: {'✅' if acct.get('has_data') else '❌'}")

        with col2:
            st.markdown("**同業種データ**")
            genre = status.get("genre", {})
            st.write(f"プロフィール数: {genre.get('profile_count', 0)}")
            st.write(f"使用可能: {'✅' if genre.get('has_data') else '❌'}")

        with col3:
            st.markdown("**全体データ**")
            universal = status.get("universal", {})
            st.write(f"投稿数: {universal.get('total_posts', 0)}")
            st.write(f"使用可能: {'✅' if universal.get('has_data') else '❌'}")

        # Current resolution
        st.subheader("現在のデータ参照元")
        resolution = status.get("current_resolution", {})
        tier_labels = {"account": "自分", "genre": "同業種", "universal": "全体"}
        res_data = {
            "項目": ["フック選択", "投稿時刻", "参考投稿", "フォロワー分析"],
            "参照元": [
                tier_labels.get(resolution.get("hooks_tier", ""), "—"),
                tier_labels.get(resolution.get("times_tier", ""), "—"),
                tier_labels.get(resolution.get("examples_tier", ""), "—"),
                tier_labels.get(resolution.get("follower_tier", ""), "—"),
            ],
        }
        st.dataframe(pd.DataFrame(res_data), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"データソース情報取得失敗: {e}")

    # Follower trend
    st.subheader("フォロワー推移")
    try:
        from .follower_tracker import FollowerTracker
        tracker = FollowerTracker(tracker_path=settings.profile_dir / "follower_snapshots.json")
        snapshots = tracker.get_all()
        if snapshots:
            follower_data = pd.DataFrame([
                {"日付": s.date, "フォロワー数": s.count, "増減": s.delta}
                for s in snapshots
            ])
            follower_data["日付"] = pd.to_datetime(follower_data["日付"])
            st.line_chart(follower_data, x="日付", y="フォロワー数")

            # Recent stats
            recent = tracker.get_recent(days=7)
            if recent:
                avg_delta = sum(s.delta for s in recent) / len(recent)
                total_delta = sum(s.delta for s in recent)
                col1, col2 = st.columns(2)
                col1.metric("直近7日のフォロワー増減", f"{total_delta:+,}")
                col2.metric("1日平均増減", f"{avg_delta:+,.1f}")
        else:
            st.info("フォロワーデータなし（track-followers を実行してください）")
    except Exception:
        st.info("フォロワーデータなし")

    # Config summary
    st.subheader("設定値")
    config_data = {
        "設定": [
            "1日の投稿数",
            "ショート投稿割合",
            "ジャンル",
            "スタイルガイド",
            "カスタムフック",
        ],
        "値": [
            str(settings.posts_per_day),
            f"{settings.short_post_ratio:.0%}",
            settings.genre or "未設定",
            "あり" if settings.load_style_guide() else "なし",
            f"{len(settings.custom_hooks)}件" if settings.custom_hooks else "なし",
        ],
    }
    st.dataframe(pd.DataFrame(config_data), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
