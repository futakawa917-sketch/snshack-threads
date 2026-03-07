"""Streamlit web dashboard for non-technical users.

Run: streamlit run src/snshack_threads/dashboard.py
Or:  snshack dashboard

Full operation UI — no CLI needed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path


# ── Custom CSS ────────────────────────────────────────────

_CUSTOM_CSS = """
<style>
/* Card-like containers */
div[data-testid="stMetric"] {
    background: linear-gradient(135deg, #667eea10, #764ba210);
    border: 1px solid #e0e0e0;
    border-radius: 12px;
    padding: 16px;
}
div[data-testid="stMetric"] label {
    font-size: 0.85rem;
    color: #666;
}
div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
    font-size: 1.8rem;
    font-weight: 700;
}

/* Phase badge */
.phase-badge {
    display: inline-block;
    padding: 6px 16px;
    border-radius: 20px;
    font-weight: 600;
    font-size: 0.9rem;
    margin: 8px 0;
}
.phase-bootstrap { background: #fff3cd; color: #856404; }
.phase-learning { background: #cce5ff; color: #004085; }
.phase-optimized { background: #d4edda; color: #155724; }

/* Status pills */
.status-pill {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 10px;
    font-size: 0.8rem;
    font-weight: 600;
}
.status-ok { background: #d4edda; color: #155724; }
.status-wait { background: #fff3cd; color: #856404; }
.status-sent { background: #cce5ff; color: #004085; }
.status-ng { background: #f8d7da; color: #721c24; }

/* Section header */
.section-header {
    font-size: 1.1rem;
    font-weight: 600;
    color: #333;
    border-bottom: 2px solid #667eea;
    padding-bottom: 6px;
    margin: 16px 0 12px 0;
}

/* Help text */
.help-box {
    background: #f8f9fa;
    border-left: 4px solid #667eea;
    padding: 12px 16px;
    border-radius: 0 8px 8px 0;
    margin: 8px 0;
    font-size: 0.9rem;
    color: #555;
}
</style>
"""


def main():
    import streamlit as st

    from .config import (
        _read_active_profile,
        get_settings,
        list_profiles,
    )

    st.set_page_config(
        page_title="SNShack Threads",
        page_icon="https://threads.net/favicon.ico",
        layout="wide",
    )
    st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)

    # ── Authentication ────────────────────────────────────
    if not _check_auth(st):
        return

    # ── Header ────────────────────────────────────────────
    st.markdown("## SNShack Threads")
    st.caption("Threads SNS運用管理ダッシュボード")

    # Profile selector
    profiles = list_profiles()
    if not profiles:
        st.warning("クライアントが登録されていません。下のフォームから作成してください。")
        _render_settings_only(st)
        return

    active = _read_active_profile()

    # Sidebar
    st.sidebar.markdown("### クライアント選択")
    selected_profile = st.sidebar.selectbox(
        "クライアント",
        profiles,
        index=profiles.index(active) if active in profiles else 0,
        label_visibility="collapsed",
    )
    settings = get_settings(profile=selected_profile)
    st.sidebar.markdown(f"**TZ:** {settings.timezone}")
    st.sidebar.divider()
    st.sidebar.markdown("### メニュー")
    st.sidebar.markdown(
        "上のタブから各機能にアクセスできます。\n\n"
        "- **概要** — 全体の成績\n"
        "- **投稿** — 手動投稿・履歴\n"
        "- **自動投稿** — AI自動生成\n"
        "- **リサーチ** — 自動競合分析の結果\n"
        "- **競合分析** — 競合ウォッチ\n"
        "- **フォロワー** — 推移グラフ\n"
        "- **通知** — Slack/Email設定\n"
        "- **設定** — API・プロファイル管理"
    )

    # Tabs
    tabs = st.tabs([
        "概要",
        "投稿",
        "自動投稿",
        "リサーチ",
        "競合分析",
        "フォロワー",
        "通知",
        "設定",
    ])

    with tabs[0]:
        _render_overview(settings)
    with tabs[1]:
        _render_posts(settings)
    with tabs[2]:
        _render_autopilot(st, settings, selected_profile)
    with tabs[3]:
        _render_research(st, settings, selected_profile)
    with tabs[4]:
        _render_competitors(st, settings, selected_profile)
    with tabs[5]:
        _render_followers(settings)
    with tabs[6]:
        _render_notifications(st, selected_profile)
    with tabs[7]:
        _render_settings(st, selected_profile, settings)


def _check_auth(st) -> bool:
    """Simple password authentication."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    try:
        password = st.secrets["password"]
    except (FileNotFoundError, KeyError):
        return True

    if st.session_state.authenticated:
        return True

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("## SNShack Threads")
        st.caption("ログインしてください")
        pw = st.text_input("パスワード", type="password")
        if st.button("ログイン", use_container_width=True):
            if pw == password:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("パスワードが正しくありません")
    return False


def _load_json(path: Path) -> list | dict:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_settings_only(st):
    """Minimal settings view when no profiles exist."""
    st.markdown('<div class="section-header">新規クライアント登録</div>', unsafe_allow_html=True)
    _profile_create_form(st)


# ── Overview ──────────────────────────────────────────────


def _render_overview(settings):
    import streamlit as st

    data_dir = Path(settings.data_dir)
    history = _load_json(data_dir / "post_history.json")

    # Key metrics
    st.markdown('<div class="section-header">主要指標</div>', unsafe_allow_html=True)

    col1, col2, col3, col4 = st.columns(4)

    total_posts = len(history)
    collected = [r for r in history if r.get("status") == "collected"]
    total_views = sum(r.get("views", 0) for r in collected)
    total_likes = sum(r.get("likes", 0) for r in collected)
    avg_engagement = (
        sum(r.get("engagement", 0) for r in collected) / len(collected)
        if collected
        else 0
    )

    col1.metric("総投稿数", total_posts)
    col2.metric("総閲覧数", f"{total_views:,}")
    col3.metric("総いいね数", f"{total_likes:,}")
    col4.metric("平均エンゲージメント", f"{avg_engagement * 100:.2f}%")

    # Phase indicator
    if total_posts < 70:
        phase_name = "立ち上げ期"
        phase_desc = "まだデータ収集中です。いろいろなフック（書き出し）パターンを試しています。"
        phase_class = "phase-bootstrap"
        phase_progress = total_posts / 70
    elif total_posts < 150:
        phase_name = "学習期"
        phase_desc = "効果の高いフックが見えてきました。上位パターンを重点的に使いつつ、新パターンも試しています。"
        phase_class = "phase-learning"
        phase_progress = (total_posts - 70) / 80
    else:
        phase_name = "最適化期"
        phase_desc = "十分なデータが集まりました。トップパフォーマンスのフック＆テンプレートで最適運用中です。"
        phase_class = "phase-optimized"
        phase_progress = 1.0

    st.markdown(
        f'<span class="phase-badge {phase_class}">{phase_name}</span>'
        f' &nbsp; データ収集済み: {len(collected)}件',
        unsafe_allow_html=True,
    )
    st.progress(min(phase_progress, 1.0))
    st.markdown(
        f'<div class="help-box">{phase_desc}</div>',
        unsafe_allow_html=True,
    )

    # Recent performance chart
    if collected:
        st.markdown('<div class="section-header">直近7日間のパフォーマンス</div>', unsafe_allow_html=True)
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        recent = [r for r in collected if r.get("scheduled_at", "") > cutoff]
        if recent:
            chart_data = {
                "日付": [r.get("scheduled_at", "")[:10] for r in recent],
                "閲覧数": [r.get("views", 0) for r in recent],
                "いいね": [r.get("likes", 0) for r in recent],
            }
            st.bar_chart(chart_data, x="日付", y=["閲覧数", "いいね"])
        else:
            st.info("直近7日間のデータはまだありません")

    # Hook performance
    if collected:
        st.markdown('<div class="section-header">フック（書き出し）別パフォーマンス</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="help-box">「フック」とは投稿の書き出しパターンのこと。'
            "どのフックが一番読まれているかを確認できます。</div>",
            unsafe_allow_html=True,
        )
        from .csv_analyzer import _detect_hooks

        hook_data: dict[str, list[int]] = {}
        for r in collected:
            hooks = _detect_hooks(r.get("text", ""))
            for h in hooks:
                hook_data.setdefault(h, []).append(r.get("views", 0))

        if hook_data:
            hook_table = []
            for name, views in sorted(
                hook_data.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True
            ):
                hook_table.append(
                    {
                        "フック": name,
                        "投稿数": len(views),
                        "平均閲覧数": f"{sum(views) / len(views):,.0f}",
                        "合計閲覧数": f"{sum(views):,}",
                    }
                )
            st.table(hook_table)


# ── Posts ─────────────────────────────────────────────────


def _render_posts(settings):
    import streamlit as st

    data_dir = Path(settings.data_dir)
    history = _load_json(data_dir / "post_history.json")

    # Manual post — always show even if no history
    st.markdown('<div class="section-header">手動投稿</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="help-box">投稿文を入力して、すぐにThreadsに投稿できます。'
        "NGチェックで禁止ワードがないか確認もできます。</div>",
        unsafe_allow_html=True,
    )

    with st.form("manual_post"):
        post_text = st.text_area(
            "投稿文（最大500文字）",
            height=150,
            max_chars=500,
            placeholder="ここに投稿文を入力...",
        )
        st.caption(f"現在 {len(post_text) if post_text else 0} / 500 文字")
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            submitted = st.form_submit_button(
                "Threadsに投稿する", use_container_width=True
            )
        with col_btn2:
            check_only = st.form_submit_button(
                "NGチェックのみ", use_container_width=True
            )

    if check_only and post_text:
        from .content_guard import check_ng

        issues = check_ng(post_text)
        if issues:
            st.error(f"NG検出: {', '.join(issues)}")
        else:
            st.success("問題なし！ 投稿できます。")

    if submitted and post_text:
        from .content_guard import check_ng

        issues = check_ng(post_text)
        if issues:
            st.error(f"NG検出: {', '.join(issues)}")
        else:
            try:
                from .threads_api import ThreadsGraphClient

                with ThreadsGraphClient() as client:
                    post_id = client.create_text_post(post_text)
                st.success(f"投稿完了！ (ID: {post_id})")
            except Exception as e:
                st.error(f"投稿失敗: {e}")

    # Post list
    if not history:
        st.info("まだ投稿がありません。自動投稿を設定するか、上から手動で投稿してください。")
        return

    st.markdown('<div class="section-header">投稿履歴</div>', unsafe_allow_html=True)
    days = st.slider("表示期間（日）", 7, 90, 30)
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    recent = [r for r in history if r.get("scheduled_at", "") > cutoff]
    recent.sort(key=lambda r: r.get("views", 0), reverse=True)

    st.caption(f"直近{days}日間: {len(recent)}件")

    for r in recent:
        status = r.get("status", "scheduled")
        views = r.get("views", 0)
        likes = r.get("likes", 0)
        text = r.get("text", "")[:120]
        date = r.get("scheduled_at", "")[:16]

        if status == "collected":
            st.markdown(
                f'**{date}** &nbsp; '
                f'<span class="status-pill status-ok">収集済</span> &nbsp; '
                f"{views:,} 閲覧 / {likes} いいね",
                unsafe_allow_html=True,
            )
        elif status == "published":
            st.markdown(
                f'**{date}** &nbsp; '
                f'<span class="status-pill status-sent">投稿済</span>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'**{date}** &nbsp; '
                f'<span class="status-pill status-wait">予約中</span>',
                unsafe_allow_html=True,
            )
        st.text(text)
        st.divider()


# ── Autopilot ────────────────────────────────────────────


def _render_autopilot(st, settings, profile):
    st.markdown('<div class="section-header">自動投稿（Autopilot）</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="help-box">'
        "毎日自動でAIが投稿を5件生成してスケジュールします。"
        "通常はcron（定時実行）で自動で動きますが、ここから手動でも実行できます。"
        "</div>",
        unsafe_allow_html=True,
    )

    # Schedule display
    data_dir = Path(settings.data_dir)
    history = _load_json(data_dir / "post_history.json")

    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**今日のスケジュール** ({today})")
        today_posts = [r for r in history if r.get("scheduled_at", "")[:10] == today]
        if today_posts:
            for r in today_posts:
                time = r.get("scheduled_at", "")[11:16]
                status = r.get("status", "scheduled")
                text = r.get("text", "")[:50]
                status_map = {
                    "collected": ("収集済", "status-ok"),
                    "published": ("投稿済", "status-sent"),
                    "scheduled": ("予約中", "status-wait"),
                }
                label, css = status_map.get(status, ("不明", "status-wait"))
                st.markdown(
                    f'<span class="status-pill {css}">{label}</span> '
                    f"**{time}** — {text}",
                    unsafe_allow_html=True,
                )
        else:
            st.info("今日の投稿はまだスケジュールされていません")

    with col2:
        st.markdown(f"**明日のスケジュール** ({tomorrow})")
        tomorrow_posts = [
            r for r in history if r.get("scheduled_at", "")[:10] == tomorrow
        ]
        if tomorrow_posts:
            for r in tomorrow_posts:
                time = r.get("scheduled_at", "")[11:16]
                text = r.get("text", "")[:50]
                st.markdown(
                    f'<span class="status-pill status-wait">予約中</span> '
                    f"**{time}** — {text}",
                    unsafe_allow_html=True,
                )
        else:
            st.info("明日の投稿はまだありません")

    # Manual autopilot trigger
    st.divider()
    st.markdown('<div class="section-header">手動でAutopilotを実行</div>', unsafe_allow_html=True)

    with st.form("autopilot_form"):
        topics_input = st.text_area(
            "トピック（1行に1つ）",
            value="\n".join(settings.get_research_keywords() or ["一般的な話題"]),
            height=100,
            help="AIがこのトピックに沿った投稿を生成します",
        )
        col_a, col_b = st.columns(2)
        with col_a:
            posts_per_day = st.number_input(
                "1日の投稿数", min_value=1, max_value=10, value=5
            )
        with col_b:
            publish_method = st.selectbox(
                "投稿方法",
                ["threads", "metricool"],
                format_func=lambda x: "Threads API（直接投稿）"
                if x == "threads"
                else "Metricool（予約投稿）",
            )
        dry_run = st.checkbox("プレビューのみ（実際には投稿しない）", value=True)
        run_btn = st.form_submit_button(
            "投稿を生成する", use_container_width=True
        )

    if run_btn:
        topics = [t.strip() for t in topics_input.strip().splitlines() if t.strip()]
        if not topics:
            st.error("トピックを1つ以上入力してください")
            return

        with st.spinner("AIが投稿を生成中..."):
            try:
                from .autopilot import execute_plan, generate_daily_plan

                plan = generate_daily_plan(
                    topics=topics,
                    profile=profile,
                    posts_per_day=posts_per_day,
                )

                st.success(f"{len(plan.posts)}件の投稿を生成しました（フェーズ: {plan.phase}）")

                for i, p in enumerate(plan.posts):
                    with st.expander(f"投稿 {i + 1} — フック: {p['hook']}", expanded=True):
                        st.text(p["text"])

                if plan.skipped:
                    st.warning(f"{len(plan.skipped)}件スキップ:")
                    for s in plan.skipped:
                        st.caption(f"  - {s}")

                if not dry_run:
                    results = execute_plan(
                        plan, publish_method=publish_method, profile=profile
                    )
                    for r in results:
                        st.text(r)
                    st.success("自動投稿完了！")
                else:
                    st.info("プレビューモード — 実際には投稿されていません")

            except Exception as e:
                st.error(f"エラー: {e}")


# ── Research ──────────────────────────────────────────────


def _render_research(st, settings, profile):
    st.markdown('<div class="section-header">自動リサーチ結果</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="help-box">'
        "毎日自動で設定キーワードを検索し、競合アカウントの発見・フックパターンの分析を行っています。"
        "結果はAutopilotにも自動反映されます。"
        "</div>",
        unsafe_allow_html=True,
    )

    # Show configured keywords
    keywords = settings.get_research_keywords()
    if keywords:
        st.markdown(f"**検索キーワード:** {', '.join(keywords)}")
    else:
        st.warning(
            "リサーチキーワードが未設定です。「設定」タブの「リサーチキーワード」に入力してください。"
        )

    # Load latest report
    from .auto_research import get_latest_report

    report = get_latest_report(profile=profile)

    if not report:
        st.info("まだリサーチが実行されていません。cronで毎日自動実行されます。")

        if st.button("今すぐリサーチを実行", use_container_width=True):
            if not keywords:
                st.error("先にリサーチキーワードを設定してください")
            else:
                with st.spinner("リサーチ中...（APIでキーワード検索しています）"):
                    try:
                        from .auto_research import run_auto_research

                        result = run_auto_research(profile=profile)
                        st.success(
                            f"完了！ {result.total_posts_found}件の投稿を分析、"
                            f"{len(result.discovered_accounts)}アカウント発見、"
                            f"{len(result.auto_registered)}件自動登録"
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"エラー: {e}")
        return

    # Report summary
    st.markdown(f"**最終実行日:** {report.get('date', 'N/A')}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("検索キーワード数", len(report.get("keywords_searched", [])))
    col2.metric("発見した投稿数", report.get("total_posts_found", 0))
    col3.metric("発見アカウント数", len(report.get("discovered_accounts", [])))
    col4.metric("自動登録数", len(report.get("auto_registered", [])))

    # Auto-registered
    auto_reg = report.get("auto_registered", [])
    if auto_reg:
        st.markdown(
            f'<div class="help-box">'
            f"今回新たに競合として自動登録: "
            f"{'、'.join('@' + u for u in auto_reg)}"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Trending hooks
    trending = report.get("trending_hooks", [])
    if trending:
        st.markdown('<div class="section-header">トレンドフック（競合で伸びてるパターン）</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="help-box">'
            "キーワード検索で見つかった投稿から、エンゲージメントの高い書き出しパターンを分析しています。"
            "これらのパターンはAutopilotの投稿生成にも自動で反映されます。"
            "</div>",
            unsafe_allow_html=True,
        )

        hook_table = []
        for h in trending[:10]:
            hook_table.append({
                "フック": h.get("hook", ""),
                "使用数": h.get("count", 0),
                "平均いいね": f"{h.get('avg_likes', 0):.1f}",
                "合計いいね": h.get("total_likes", 0),
            })
        st.table(hook_table)

    # Discovered accounts
    discovered = report.get("discovered_accounts", [])
    if discovered:
        st.markdown('<div class="section-header">発見したアカウント（上位）</div>', unsafe_allow_html=True)

        for acc in discovered[:10]:
            username = acc.get("username", "")
            avg_likes = acc.get("avg_likes", 0)
            posts_found = acc.get("total_posts_found", 0)
            top_hooks = acc.get("top_hooks", [])
            via = acc.get("discovered_via", [])

            with st.expander(
                f"@{username} — 平均{avg_likes:.0f}いいね / {posts_found}投稿"
            ):
                col1, col2 = st.columns(2)
                col1.metric("平均いいね", f"{avg_likes:.1f}")
                col2.metric("発見投稿数", posts_found)

                if top_hooks:
                    st.markdown(f"**使用フック:** {', '.join(top_hooks)}")
                if via:
                    st.caption(f"発見キーワード: {', '.join(via)}")

                samples = acc.get("sample_posts", [])
                if samples:
                    st.caption("投稿サンプル:")
                    for p in samples[:3]:
                        st.text(f"  {p.get('likes', 0)} いいね — {p.get('text', '')[:80]}")

    # Errors
    errors = report.get("errors", [])
    if errors:
        st.divider()
        st.warning("実行時のエラー:")
        for e in errors:
            st.caption(e)

    # Manual trigger
    st.divider()
    if st.button("リサーチを再実行", use_container_width=True):
        with st.spinner("リサーチ中..."):
            try:
                from .auto_research import run_auto_research

                result = run_auto_research(profile=profile)
                st.success(f"完了！ {result.total_posts_found}件分析")
                st.rerun()
            except Exception as e:
                st.error(f"エラー: {e}")


# ── Competitors ──────────────────────────────────────────


def _render_competitors(st, settings, profile):
    data_dir = Path(settings.data_dir)
    research_dir = data_dir / "research"

    competitors = _load_json(research_dir / "competitors.json")
    snapshots = _load_json(research_dir / "competitor_snapshots.json")

    st.markdown('<div class="section-header">競合アカウント追加</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="help-box">'
        "競合のThreadsアカウントを登録すると、投稿内容やエンゲージメントを定期的にチェックできます。"
        "</div>",
        unsafe_allow_html=True,
    )

    with st.form("add_competitor"):
        col1, col2, col3 = st.columns([2, 2, 3])
        with col1:
            comp_username = st.text_input("ユーザー名（@なし）", placeholder="example_user")
        with col2:
            comp_display = st.text_input("表示名（任意）", placeholder="競合A社")
        with col3:
            comp_notes = st.text_input(
                "メモ", placeholder="同業種・直接競合 など"
            )
        add_btn = st.form_submit_button("追加する", use_container_width=True)

    if add_btn and comp_username:
        comp_username = comp_username.strip().lstrip("@")
        existing = (
            [c.get("username") for c in competitors]
            if isinstance(competitors, list)
            else []
        )
        if comp_username in existing:
            st.warning(f"@{comp_username} は既に登録されています")
        else:
            if not isinstance(competitors, list):
                competitors = []
            competitors.append(
                {
                    "username": comp_username,
                    "display_name": comp_display,
                    "notes": comp_notes,
                    "added_at": datetime.now().isoformat(),
                }
            )
            _save_json(research_dir / "competitors.json", competitors)
            st.success(f"@{comp_username} を追加しました")
            st.rerun()

    # Scrape button
    if competitors:
        st.divider()
        if st.button("全競合の最新データを取得する", use_container_width=True):
            try:
                from .browser_scraper import scrape_profile
                from .research_store import ResearchStore

                store = ResearchStore(profile=profile)
                progress = st.progress(0.0)
                for i, comp in enumerate(competitors):
                    username = comp.get("username", "")
                    st.text(f"@{username} を取得中...")
                    try:
                        result = scrape_profile(username)
                        store.save_competitor_snapshot(
                            username,
                            {
                                "post_count": len(result.posts),
                                "avg_likes": sum(p.likes for p in result.posts)
                                / len(result.posts)
                                if result.posts
                                else 0,
                                "avg_replies": sum(p.replies for p in result.posts)
                                / len(result.posts)
                                if result.posts
                                else 0,
                            },
                        )
                    except Exception as e:
                        st.warning(f"@{username} の取得に失敗: {e}")
                    progress.progress((i + 1) / len(competitors))
                st.success("取得完了！")
            except ImportError:
                st.error(
                    "playwright がインストールされていません。"
                    "管理者に `pip install 'snshack-threads[scraper]'` を依頼してください。"
                )

    # List competitors
    if competitors:
        st.markdown(
            f'<div class="section-header">競合一覧（{len(competitors)}件）</div>',
            unsafe_allow_html=True,
        )

        for comp in competitors:
            username = comp.get("username", "")
            name = comp.get("display_name", "") or username
            notes = comp.get("notes", "")

            with st.expander(f"@{username} — {name}（{notes}）"):
                comp_snaps = [
                    s for s in snapshots if s.get("username") == username
                ]
                if comp_snaps:
                    latest = comp_snaps[-1]
                    c1, c2, c3 = st.columns(3)
                    c1.metric("取得投稿数", latest.get("post_count", 0))
                    c2.metric("平均いいね", f"{latest.get('avg_likes', 0):.1f}")
                    c3.metric("平均リプライ", f"{latest.get('avg_replies', 0):.1f}")

                    hooks = latest.get("top_hooks", [])
                    if hooks:
                        st.markdown(f"**使用フック:** {', '.join(hooks[:5])}")

                    top = latest.get("posts", [])[:5]
                    if top:
                        st.caption("人気の投稿:")
                        for p in top:
                            st.text(
                                f"{p.get('likes', 0)} いいね — {p.get('text', '')[:80]}"
                            )
                else:
                    st.info("まだデータがありません。上の「全競合の最新データを取得する」を実行してください。")

                if st.button(f"@{username} を削除", key=f"rm_{username}"):
                    competitors = [
                        c for c in competitors if c.get("username") != username
                    ]
                    _save_json(research_dir / "competitors.json", competitors)
                    st.success(f"@{username} を削除しました")
                    st.rerun()
    elif not add_btn:
        st.info("まだ競合アカウントが登録されていません。上のフォームから追加してください。")


# ── Followers ────────────────────────────────────────────


def _render_followers(settings):
    import streamlit as st

    data_dir = Path(settings.data_dir)
    snapshots = _load_json(data_dir / "follower_snapshots.json")

    st.markdown('<div class="section-header">フォロワー推移</div>', unsafe_allow_html=True)

    if not snapshots:
        st.info(
            "フォロワーデータはまだありません。\n\n"
            "cron（定時実行）で自動的に記録されます。"
        )
        return

    chart_data = {
        "日付": [s.get("date", "") for s in snapshots],
        "フォロワー数": [s.get("followers_count", 0) for s in snapshots],
        "増減": [s.get("delta", 0) for s in snapshots],
    }
    st.line_chart(chart_data, x="日付", y="フォロワー数")

    st.markdown('<div class="section-header">日別増減</div>', unsafe_allow_html=True)
    st.bar_chart(chart_data, x="日付", y="増減")


# ── Notifications ────────────────────────────────────────


def _render_notifications(st, profile):
    st.markdown('<div class="section-header">通知設定</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="help-box">'
        "エンゲージメントが急落した場合や、APIの利用制限が近づいた場合にSlackやメールで通知を受け取れます。"
        "</div>",
        unsafe_allow_html=True,
    )

    from .notifier import NotifyConfig

    config = NotifyConfig.from_profile(profile)

    # Status display
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Slack**")
        if config.has_slack:
            st.success("設定済み")
            if config.slack_channel:
                st.caption(f"チャンネル: {config.slack_channel}")
        else:
            st.warning("未設定")

    with col2:
        st.markdown("**Email**")
        if config.has_email:
            st.success("設定済み")
            st.caption(f"送信先: {config.email_to}")
        else:
            st.warning("未設定")

    # Slack setup
    st.divider()
    st.markdown('<div class="section-header">Slack設定</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="help-box">'
        "SlackのIncoming Webhook URLを設定すると、アラートがSlackに届きます。"
        "</div>",
        unsafe_allow_html=True,
    )

    with st.form("slack_setup"):
        webhook_url = st.text_input(
            "Slack Webhook URL",
            value=config.slack_webhook_url,
            type="password",
            placeholder="https://hooks.slack.com/services/...",
        )
        slack_channel = st.text_input(
            "Slackチャンネル（任意）",
            value=config.slack_channel,
            placeholder="#sns-alerts",
        )
        slack_save = st.form_submit_button("Slack設定を保存", use_container_width=True)

    if slack_save:
        _update_profile_notify(
            profile,
            {
                "slack_webhook_url": webhook_url,
                "slack_channel": slack_channel,
            },
        )
        st.success("Slack設定を保存しました！")
        st.rerun()

    # Test & Check
    st.divider()
    col1, col2 = st.columns(2)

    with col1:
        st.markdown('<div class="section-header">テスト送信</div>', unsafe_allow_html=True)
        if st.button("テスト通知を送信", use_container_width=True):
            from .notifier import send_email, send_slack

            config = NotifyConfig.from_profile(profile)
            results = []
            if config.has_slack:
                ok = send_slack(config, "SNShack ダッシュボードからのテスト通知です", title="テスト")
                results.append(f"Slack: {'成功' if ok else '失敗'}")
            if config.has_email:
                ok = send_email(config, "SNShack テスト", "ダッシュボードからのテスト通知です")
                results.append(f"Email: {'成功' if ok else '失敗'}")
            if results:
                st.info(" / ".join(results))
            else:
                st.warning("通知先が設定されていません")

    with col2:
        st.markdown('<div class="section-header">アラートチェック</div>', unsafe_allow_html=True)
        if st.button("今すぐアラートを確認", use_container_width=True):
            from .notifier import run_all_checks

            alerts = run_all_checks(profile=profile)
            if alerts:
                st.error(f"{len(alerts)}件のアラート:")
                for a in alerts:
                    st.text(a)
            else:
                st.success("問題なし！ アラートはありません。")


def _update_profile_notify(profile: str, notify_data: dict) -> None:
    """Update notification settings in profile config.json."""
    from .config import _profile_config_path

    config_path = _profile_config_path(profile)
    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        data = {}

    existing_notify = data.get("notify", {})
    existing_notify.update(notify_data)
    data["notify"] = existing_notify
    config_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Settings ─────────────────────────────────────────────


def _render_settings(st, profile, settings):
    st.markdown('<div class="section-header">現在のプロファイル設定</div>', unsafe_allow_html=True)
    st.markdown(f"**選択中のクライアント:** `{profile}`")

    from .config import _profile_config_path

    config_path = _profile_config_path(profile)
    if config_path.exists():
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config_data = {}

    with st.form("edit_profile"):
        st.markdown("**API認証情報**")
        st.markdown(
            '<div class="help-box">'
            "MetricoolとThreads APIの認証情報を設定します。わからない場合は管理者に確認してください。"
            "</div>",
            unsafe_allow_html=True,
        )
        user_token = st.text_input(
            "Metricool ユーザートークン",
            value=config_data.get("user_token", ""),
            type="password",
        )
        col1, col2 = st.columns(2)
        with col1:
            user_id = st.text_input(
                "Metricool ユーザーID",
                value=config_data.get("user_id", ""),
            )
        with col2:
            blog_id = st.text_input(
                "Metricool ブログID",
                value=config_data.get("blog_id", ""),
            )
        threads_token = st.text_input(
            "Threads アクセストークン",
            value=config_data.get("threads_access_token", ""),
            type="password",
        )

        st.markdown("**一般設定**")
        col1, col2 = st.columns(2)
        with col1:
            timezone = st.text_input(
                "タイムゾーン",
                value=config_data.get("timezone", "Asia/Tokyo"),
            )
        with col2:
            research_kw = st.text_input(
                "リサーチキーワード（カンマ区切り）",
                value=config_data.get("research_keywords", ""),
                placeholder="美容,スキンケア,コスメ",
            )

        save_btn = st.form_submit_button("設定を保存", use_container_width=True)

    if save_btn:
        config_data.update(
            {
                "user_token": user_token,
                "user_id": user_id,
                "blog_id": blog_id,
                "threads_access_token": threads_token,
                "timezone": timezone,
                "research_keywords": research_kw,
            }
        )
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(config_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        st.success("設定を保存しました！")

    # Token status
    st.divider()
    st.markdown('<div class="section-header">Threadsトークン管理</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="help-box">'
        "Threads APIのトークンは60日で期限切れになります。"
        "期限が近づいたら「トークンを更新」をクリックしてください。"
        "（通常はcronで自動更新されます）"
        "</div>",
        unsafe_allow_html=True,
    )

    if settings.threads_access_token:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("トークン状態を確認", use_container_width=True):
                try:
                    from .threads_api import get_token_info

                    info = get_token_info(settings)
                    if info:
                        expires = info.get("expires_at")
                        if expires:
                            exp_dt = datetime.fromtimestamp(expires)
                            days_left = (exp_dt - datetime.now()).days
                            if days_left > 14:
                                st.success(
                                    f"有効 — 期限: {exp_dt:%Y-%m-%d}（残り{days_left}日）"
                                )
                            elif days_left > 7:
                                st.warning(
                                    f"まもなく期限切れ — {exp_dt:%Y-%m-%d}（残り{days_left}日）"
                                )
                            else:
                                st.error(
                                    f"期限切れ間近！ — {exp_dt:%Y-%m-%d}（残り{days_left}日）"
                                )
                        else:
                            st.info("期限を取得できませんでした")
                    else:
                        st.error("トークン情報を取得できませんでした")
                except Exception as e:
                    st.error(f"エラー: {e}")

        with col2:
            if st.button("トークンを更新", use_container_width=True):
                try:
                    from .threads_api import refresh_long_lived_token

                    new_token = refresh_long_lived_token(settings)
                    if new_token:
                        st.success("トークンを更新しました！")
                    else:
                        st.error("トークンの更新に失敗しました")
                except Exception as e:
                    st.error(f"エラー: {e}")
    else:
        st.info("Threadsトークンが設定されていません。上のフォームで設定してください。")

    # Profile management
    st.divider()
    st.markdown('<div class="section-header">クライアント管理</div>', unsafe_allow_html=True)

    profiles = list_profiles()
    st.markdown(
        f"**登録済みクライアント:** {', '.join(profiles) if profiles else 'なし'}"
    )

    _profile_create_form(st)


def _profile_create_form(st):
    """Render profile creation form."""
    from .config import create_profile

    with st.form("create_profile"):
        st.markdown("**新規クライアント登録**")
        new_name = st.text_input(
            "プロファイル名",
            placeholder="例: client-restaurant-tokyo",
            help="半角英数とハイフンで入力してください",
        )
        col1, col2 = st.columns(2)
        with col1:
            new_token = st.text_input(
                "Metricool ユーザートークン",
                type="password",
                key="new_token",
            )
            new_user_id = st.text_input("Metricool ユーザーID", key="new_uid")
        with col2:
            new_blog_id = st.text_input("Metricool ブログID", key="new_bid")
            new_threads = st.text_input(
                "Threads アクセストークン（任意）",
                type="password",
                key="new_threads",
            )
        new_keywords = st.text_input(
            "リサーチキーワード（任意・カンマ区切り）",
            key="new_kw",
            placeholder="例: 飲食,レストラン,グルメ",
        )
        create_btn = st.form_submit_button(
            "クライアントを登録", use_container_width=True
        )

    if create_btn and new_name:
        new_name = new_name.strip()
        try:
            create_profile(
                new_name,
                user_token=new_token,
                user_id=new_user_id,
                blog_id=new_blog_id,
                threads_access_token=new_threads,
                research_keywords=new_keywords,
            )
            st.success(f"「{new_name}」を登録しました！")
            st.rerun()
        except FileExistsError:
            st.error(f"「{new_name}」は既に存在します")
        except Exception as e:
            st.error(f"エラー: {e}")


def list_profiles():
    from .config import list_profiles as _list_profiles

    return _list_profiles()


if __name__ == "__main__":
    main()
