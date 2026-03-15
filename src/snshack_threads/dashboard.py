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

    # Sidebar: profile selector + management
    st.sidebar.markdown("---")

    # Add new client button
    if st.sidebar.toggle("➕ クライアント追加・設定", value=not profiles):
        _render_client_management(st)
        return

    if not profiles:
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
        st.info("Threads APIトークンが正しければ、次回のデータ収集後に表示されます")
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
    tab_overview, tab_time, tab_hooks, tab_content, tab_posts, tab_ab, tab_tools, tab_research, tab_token, tab_status = st.tabs([
        "📈 概要", "⏰ 時間帯分析", "🎣 フック分析", "📝 コンテンツ分析", "📋 投稿一覧",
        "🧪 A/Bテスト", "🔧 ツール", "🔍 リサーチ", "🔑 トークン管理", "⚙️ 学習状態",
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

    with tab_ab:
        _render_ab_test(st, settings, selected_profile)

    with tab_tools:
        _render_tools(st, settings, selected_profile, df)

    with tab_research:
        _render_research(st, settings, selected_profile)

    with tab_token:
        _render_token_management(st, settings, selected_profile)

    with tab_status:
        _render_learning_status(st, settings, selected_profile)


def _render_ab_test(st, settings, profile: str):
    """Render A/B test management tab."""
    st.header("🧪 A/Bテスト")

    try:
        from .ab_test import ABTestManager
        manager = ABTestManager(store_path=settings.profile_dir / "ab_tests.json")
        tests = manager.get_all()

        if not tests:
            st.info("A/Bテストはまだありません。autopilotのlearningフェーズで自動作成されます。")
            return

        # Summary metrics
        completed = [t for t in tests if t.status == "completed"]
        active = [t for t in tests if t.status == "active"]
        col1, col2, col3 = st.columns(3)
        col1.metric("合計テスト数", len(tests))
        col2.metric("実行中", len(active))
        col3.metric("完了", len(completed))

        # Active tests
        if active:
            st.subheader("実行中のテスト")
            for test in active:
                with st.expander(f"📊 {test.theme}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("**パターンA**")
                        st.text(test.variant_a_text[:200])
                        if hasattr(test, 'variant_a_views'):
                            st.metric("閲覧数", test.variant_a_views)
                    with col2:
                        st.markdown("**パターンB**")
                        st.text(test.variant_b_text[:200])
                        if hasattr(test, 'variant_b_views'):
                            st.metric("閲覧数", test.variant_b_views)

        # Completed tests
        if completed:
            st.subheader("完了したテスト")
            for test in completed:
                winner_label = f"パターン{test.winner}" if test.winner in ("A", "B") else "引き分け"
                confidence_label = {"high": "高", "medium": "中", "low": "低"}.get(test.confidence, "—")
                with st.expander(f"{'✅' if test.winner in ('A', 'B') else '➖'} {test.theme} → {winner_label}（確信度: {confidence_label}）"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown(f"**パターンA** {'🏆' if test.winner == 'A' else ''}")
                        st.text(test.variant_a_text[:200])
                    with col2:
                        st.markdown(f"**パターンB** {'🏆' if test.winner == 'B' else ''}")
                        st.text(test.variant_b_text[:200])
    except Exception as e:
        st.warning(f"A/Bテストデータ取得失敗: {e}")


def _render_tools(st, settings, profile: str, df):
    """Render tools tab with recycling, scheduling, audit, hooks, and AI generation."""
    st.header("🔧 ツール")

    tool_tab1, tool_tab2, tool_tab3, tool_tab4, tool_tab5 = st.tabs([
        "♻️ リサイクル", "📅 予約投稿", "🎯 プロファイル監査", "🪝 フック管理", "🤖 AI生成",
    ])

    # -- Recycle --
    with tool_tab1:
        st.subheader("♻️ コンテンツリサイクル")
        st.caption("30日以上前の高パフォーマンス投稿を新しいフックで再利用")
        try:
            from .content_recycler import find_recyclable_posts
            from .post_history import PostHistory

            history = PostHistory(history_path=settings.profile_dir / "post_history.json")
            min_views = st.number_input("最低閲覧数", min_value=100, value=500, step=100, key="recycle_min")
            min_age = st.number_input("最低経過日数", min_value=7, value=30, step=7, key="recycle_age")

            recyclable = find_recyclable_posts(history, min_views=min_views, min_age_days=min_age)

            if recyclable:
                st.success(f"♻️ {len(recyclable)}件のリサイクル候補")
                for i, post in enumerate(recyclable[:10]):
                    with st.expander(f"{post.views:,} views — {post.text[:60]}..."):
                        st.text(post.text)
                        st.caption(f"閲覧数: {post.views:,} | 投稿日: {post.scheduled_at}")
                        if st.button(f"リサイクル生成", key=f"recycle_{i}"):
                            try:
                                from .content_generator import generate_recycle
                                recycled = generate_recycle(original_text=post.text, original_views=post.views)
                                st.markdown("**生成結果:**")
                                st.text(recycled.text)
                            except Exception as e:
                                st.error(f"生成失敗: {e}")
            else:
                st.info("リサイクル候補なし（投稿数が少ないか、基準を下げてください）")
        except Exception as e:
            st.warning(f"リサイクル機能エラー: {e}")

    # -- Manual Scheduling --
    with tool_tab2:
        st.subheader("📅 予約投稿")
        if not settings.validate_credentials():
            st.warning("Metricool認証情報を設定してください")
        else:
            with st.form("manual_schedule"):
                post_text = st.text_area("投稿テキスト", height=150, placeholder="投稿内容を入力...")
                col1, col2 = st.columns(2)
                schedule_date = col1.date_input("投稿日")
                schedule_time = col2.time_input("投稿時刻")
                submitted = st.form_submit_button("Metricoolで予約", type="primary")

            if submitted and post_text:
                try:
                    from .api import MetricoolClient
                    from .models import PostDraft

                    publish_at = datetime.combine(schedule_date, schedule_time)
                    draft = PostDraft(text=post_text)
                    with MetricoolClient(settings=settings) as client:
                        resp = client.schedule_post(draft, publish_at)
                        st.success(f"✅ {publish_at.strftime('%Y-%m-%d %H:%M')} に予約しました")
                except Exception as e:
                    st.error(f"予約失敗: {e}")

            # Show queue
            st.markdown("---")
            st.subheader("予約キュー")
            if st.button("予約一覧を取得", key="load_queue"):
                try:
                    from .api import MetricoolClient
                    today = datetime.now().strftime("%Y-%m-%d")
                    week_later = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
                    with MetricoolClient(settings=settings) as client:
                        scheduled = client.get_scheduled_posts(today, week_later)
                        if scheduled:
                            st.write(f"**{len(scheduled)}件** の予約投稿")
                            for p in scheduled:
                                pub_date = p.get("publicationDate", {}).get("dateTime", "")
                                text = ""
                                for net in p.get("networks", []):
                                    text = net.get("text", "")[:80]
                                    break
                                st.write(f"📌 {pub_date} — {text}")
                        else:
                            st.info("予約投稿なし")
                except Exception as e:
                    st.error(f"取得失敗: {e}")

    # -- Profile Audit --
    with tool_tab3:
        st.subheader("🎯 プロファイル監査")
        if st.button("監査を実行", key="run_audit"):
            try:
                from .profile_audit import audit_profile
                result = audit_profile(profile=profile)

                score = result.get("score", 0)
                st.metric("総合スコア", f"{score}/100")
                st.progress(score / 100)

                for item in result.get("items", []):
                    status_icon = "✅" if item.get("passed") else "❌"
                    st.write(f"{status_icon} **{item.get('name', '')}** — {item.get('detail', '')}")

                suggestions = result.get("suggestions", [])
                if suggestions:
                    st.subheader("改善提案")
                    for s in suggestions:
                        st.write(f"💡 {s}")
            except Exception as e:
                st.warning(f"監査失敗: {e}")

    # -- Hook Management --
    with tool_tab4:
        st.subheader("🪝 フック管理")
        try:
            from .csv_analyzer import get_active_hooks
            hooks = get_active_hooks()
            if hooks:
                st.write(f"**{len(hooks)}個** のアクティブフック")
                for name, pattern in hooks:
                    st.write(f"• **{name}** — `{pattern}`")
            else:
                st.info("カスタムフックなし（デフォルトを使用中）")

            # Industry presets
            st.markdown("---")
            st.subheader("業界プリセット")
            try:
                from .csv_analyzer import INDUSTRY_HOOKS
                industries = list(INDUSTRY_HOOKS.keys()) if hasattr(INDUSTRY_HOOKS, 'keys') else []
                if industries:
                    selected = st.selectbox("業界を選択", industries, key="hook_industry")
                    if st.button("このプリセットを適用", key="apply_hooks"):
                        import json
                        hooks_path = settings.profile_dir / "hooks.json"
                        hooks_data = {"hooks": INDUSTRY_HOOKS[selected], "genre": selected}
                        hooks_path.write_text(json.dumps(hooks_data, ensure_ascii=False, indent=2), encoding="utf-8")
                        st.success(f"✅ {selected} のフックプリセットを適用しました")
            except Exception:
                pass
        except Exception as e:
            st.warning(f"フック情報取得失敗: {e}")

    # -- AI Generation --
    with tool_tab5:
        st.subheader("🤖 AI投稿生成")
        if not settings.anthropic_api_key:
            st.warning("Anthropic API Keyを設定してください")
        else:
            with st.form("ai_generate"):
                topic = st.text_input("トピック", placeholder="例: 補助金の申請方法")
                col1, col2 = st.columns(2)
                try:
                    from .csv_analyzer import get_active_hooks
                    hook_names = [name for name, _ in get_active_hooks()]
                except Exception:
                    hook_names = ["問いかけ型", "数字訴求", "危機感", "共感型", "意外性"]
                hook_type = col1.selectbox("フックタイプ", hook_names, key="gen_hook")
                length = col2.selectbox("長さ", ["short", "medium"], key="gen_length")
                generate_btn = st.form_submit_button("生成", type="primary")

            if generate_btn and topic:
                try:
                    from .content_generator import generate_post
                    with st.spinner("生成中..."):
                        post = generate_post(topic=topic, hook_type=hook_type, length=length)
                        st.markdown("**生成結果:**")
                        st.text_area("生成テキスト", value=post.text, height=200, key="gen_result")
                        st.caption(f"文字数: {len(post.text)}")
                except Exception as e:
                    st.error(f"生成失敗: {e}")


def _render_research(st, settings, profile: str):
    """Render research tab."""
    st.header("🔍 リサーチ")

    research_tab1, research_tab2 = st.tabs(["🔎 キーワード検索", "📊 トレンド"])

    with research_tab1:
        st.subheader("キーワード検索")
        if not settings.threads_access_token:
            st.warning("Threads Access Tokenを設定してください")
        else:
            keyword = st.text_input("検索キーワード", placeholder="例: 補助金", key="research_kw")
            if st.button("検索", key="research_search") and keyword:
                try:
                    from .threads_api import ThreadsGraphClient
                    with st.spinner("検索中..."):
                        with ThreadsGraphClient(access_token=settings.threads_access_token) as client:
                            results = client.search_posts(keyword, limit=20)
                            if results:
                                st.success(f"**{len(results)}件** の投稿が見つかりました")
                                from .csv_analyzer import _detect_hooks
                                for r in results:
                                    text = r.get("text", "")
                                    hooks = _detect_hooks(text)
                                    hook_str = ", ".join(hooks) if hooks else "—"
                                    likes = r.get("like_count", 0)
                                    with st.expander(f"❤️ {likes} | 🎣 {hook_str} | {text[:60]}..."):
                                        st.text(text)
                                        st.caption(f"ユーザー: @{r.get('username', '?')} | いいね: {likes}")
                            else:
                                st.info("結果なし")
                except Exception as e:
                    st.error(f"検索失敗: {e}")

    with research_tab2:
        st.subheader("リサーチ履歴・トレンド")
        try:
            from .research_store import ResearchStore
            store = ResearchStore(store_path=settings.profile_dir / "keyword_snapshots.json")
            snapshots = store.get_all() if hasattr(store, 'get_all') else []
            if snapshots:
                rows = []
                for s in snapshots:
                    rows.append({
                        "日付": s.get("date", ""),
                        "キーワード": s.get("keyword", ""),
                        "投稿数": s.get("count", 0),
                        "平均いいね": s.get("avg_likes", 0),
                    })
                if rows:
                    rdf = pd.DataFrame(rows)
                    st.dataframe(rdf, use_container_width=True, hide_index=True)
            else:
                st.info("リサーチ履歴なし")
        except Exception as e:
            st.info("リサーチ履歴なし")


def _render_token_management(st, settings, profile: str):
    """Render token and rate limit management tab."""
    st.header("🔑 トークン・API管理")

    # Token status
    st.subheader("Threads APIトークン")
    if settings.threads_access_token:
        if st.button("トークン情報を確認", key="check_token"):
            try:
                from .threads_api import ThreadsGraphClient
                with ThreadsGraphClient(access_token=settings.threads_access_token) as client:
                    info = client.get_token_info()
                    expires_at = info.get("expires_at", 0)
                    if expires_at:
                        import time as _time
                        remaining = expires_at - int(_time.time())
                        days_left = remaining // 86400
                        if days_left > 7:
                            st.success(f"✅ トークン有効期限: あと **{days_left}日**")
                        elif days_left > 0:
                            st.warning(f"⚠️ トークン有効期限: あと **{days_left}日** — 更新をお勧めします")
                        else:
                            st.error("❌ トークンは期限切れです")
                    else:
                        st.info("有効期限情報を取得できませんでした")

                    me = client.get_me()
                    st.write(f"アカウント: **@{me.get('username', '?')}**")
                    st.write(f"名前: {me.get('name', '—')}")
            except Exception as e:
                st.error(f"トークン確認失敗: {e}")

        if st.button("トークンを更新", key="refresh_token"):
            try:
                from .threads_api import ThreadsGraphClient
                with ThreadsGraphClient(access_token=settings.threads_access_token) as client:
                    new_token = client.refresh_long_lived_token()
                    if new_token:
                        # Update config
                        import json
                        config_path = settings.profile_dir / "config.json"
                        if config_path.exists():
                            config = json.loads(config_path.read_text(encoding="utf-8"))
                            config["threads_access_token"] = new_token
                            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
                        st.success("✅ トークンを更新しました（約60日間有効）")
                    else:
                        st.warning("トークン更新に失敗しました")
            except Exception as e:
                st.error(f"トークン更新失敗: {e}")
    else:
        st.warning("Threads Access Tokenが設定されていません")

    # Rate limits
    st.markdown("---")
    st.subheader("レート制限")
    try:
        rate_path = settings.profile_dir / "rate_limits.json"
        if rate_path.exists():
            import json
            data = json.loads(rate_path.read_text(encoding="utf-8"))
            entries = data if isinstance(data, list) else []

            from datetime import datetime as dt, timedelta
            now = dt.now()

            # Count recent API calls
            search_count = sum(1 for e in entries if e.get("endpoint") == "search" and
                             (now - dt.strptime(e.get("timestamp", "2000-01-01"), "%Y-%m-%d %H:%M:%S")).days < 7)
            publish_count = sum(1 for e in entries if e.get("endpoint") == "publish" and
                              (now - dt.strptime(e.get("timestamp", "2000-01-01"), "%Y-%m-%d %H:%M:%S")).days < 1)

            col1, col2 = st.columns(2)
            col1.metric("検索API（7日間）", f"{search_count}/500")
            col2.metric("投稿API（24時間）", f"{publish_count}/250")

            search_pct = min(search_count / 500, 1.0)
            publish_pct = min(publish_count / 250, 1.0)
            col1.progress(search_pct)
            col2.progress(publish_pct)
        else:
            st.info("レート制限データなし")
    except Exception as e:
        st.info(f"レート制限情報取得失敗: {e}")

    # Metricool status
    st.markdown("---")
    st.subheader("Metricool接続状態")
    if settings.validate_credentials():
        st.write(f"User ID: `{settings.user_id}`")
        st.write(f"Blog ID: `{settings.blog_id}`")
        st.write("トークン: ✅ 設定済み")
    else:
        st.warning("Metricool認証情報が未設定です")


# ── Client Management ────────────────────────────────────


def _render_client_management(st):
    """Render the client add/edit management page."""
    import json

    from .config import PROFILES_DIR, get_settings, list_profiles

    st.title("クライアント管理")

    tab_add, tab_edit, tab_list = st.tabs(["➕ 新規追加", "✏️ 設定変更", "📋 一覧"])

    # ── Tab: Add new client ──
    with tab_add:
        st.subheader("新規クライアント追加")

        with st.form("add_client"):
            profile_id = st.text_input(
                "プロファイルID（英数字、スペースなし）",
                placeholder="例: hojokin_client1",
            )
            display_name = st.text_input(
                "表示名",
                placeholder="例: 株式会社〇〇 補助金アカウント",
            )
            genre = st.selectbox(
                "ジャンル",
                ["hojokin", "tenshoku", "beauty", "recruit", "fudosan", "other"],
                help="同ジャンルのデータを共有して学習を加速します",
            )
            threads_token = st.text_input(
                "Threads Access Token",
                type="password",
                help="Meta Developer Consoleから取得",
            )
            anthropic_key = st.text_input(
                "Anthropic API Key",
                type="password",
                help="AI投稿生成に必要",
            )
            research_keywords = st.text_input(
                "リサーチキーワード（カンマ区切り）",
                placeholder="例: 補助金,助成金,資金調達",
            )

            st.markdown("---")
            st.markdown("**Metricool設定（予約投稿用）**")
            metricool_token = st.text_input(
                "Metricool User Token",
                type="password",
                help="Metricoolの設定から取得",
            )
            metricool_user_id = st.text_input(
                "Metricool User ID",
                help="MetricoolのユーザーID",
            )
            metricool_blog_id = st.text_input(
                "Metricool Blog ID",
                help="MetricoolのブログID",
            )

            st.markdown("---")
            st.markdown("**投稿設定**")
            col1, col2 = st.columns(2)
            posts_per_day = col1.number_input("1日の投稿数", min_value=1, max_value=10, value=5)
            short_ratio = col2.slider("ショート投稿割合", 0.0, 1.0, 0.5, 0.1)

            style_guide = st.text_area(
                "スタイルガイド（任意）",
                placeholder="トーン、NG表現、CTA例などを自由記述",
                height=100,
            )

            submitted = st.form_submit_button("追加", type="primary")

        if submitted:
            if not profile_id:
                st.error("プロファイルIDを入力してください")
            elif not profile_id.replace("_", "").replace("-", "").isalnum():
                st.error("プロファイルIDは英数字・ハイフン・アンダースコアのみ使えます")
            elif not threads_token:
                st.error("Threads Access Tokenを入力してください")
            else:
                try:
                    profile_dir = PROFILES_DIR / profile_id
                    profile_dir.mkdir(parents=True, exist_ok=True)
                    config = {
                        "profile_name": profile_id,
                        "display_name": display_name,
                        "threads_access_token": threads_token,
                        "anthropic_api_key": anthropic_key,
                        "genre": genre,
                        "research_keywords": research_keywords,
                        "timezone": "Asia/Tokyo",
                        "posts_per_day": posts_per_day,
                        "short_post_ratio": short_ratio,
                        "user_token": metricool_token,
                        "user_id": metricool_user_id,
                        "blog_id": metricool_blog_id,
                    }
                    config_path = profile_dir / "config.json"
                    config_path.write_text(
                        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    # Create style guide if provided
                    if style_guide:
                        (profile_dir / "style_guide.md").write_text(style_guide, encoding="utf-8")

                    st.success(f"✅ **{display_name or profile_id}** を追加しました！")
                    st.info("サイドバーの「クライアント追加・設定」をオフにすると、ダッシュボードに戻ります")

                    # Test Threads API connection
                    try:
                        from .threads_api import ThreadsGraphClient
                        with ThreadsGraphClient(access_token=threads_token) as client:
                            me = client.get_me()
                            st.success(f"📡 Threads API接続OK: @{me.get('username', '?')}")
                    except Exception as e:
                        st.warning(f"⚠️ Threads API接続テスト失敗: {e}")
                        st.info("トークンを確認してください。ダッシュボードは作成されています。")

                except Exception as e:
                    st.error(f"作成失敗: {e}")

    # ── Tab: Edit existing client ──
    with tab_edit:
        st.subheader("クライアント設定変更")
        profiles = list_profiles()
        if not profiles:
            st.info("まだクライアントがありません")
        else:
            edit_profile = st.selectbox("編集するクライアント", profiles, key="edit_select")
            settings = get_settings(profile=edit_profile)
            config_path = PROFILES_DIR / edit_profile / "config.json"

            try:
                current_config = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                current_config = {}

            with st.form("edit_client"):
                new_display = st.text_input("表示名", value=current_config.get("display_name", ""))
                new_genre = st.text_input("ジャンル", value=current_config.get("genre", ""))
                new_token = st.text_input(
                    "Threads Access Token",
                    value=current_config.get("threads_access_token", ""),
                    type="password",
                )
                new_api_key = st.text_input(
                    "Anthropic API Key",
                    value=current_config.get("anthropic_api_key", ""),
                    type="password",
                )
                new_keywords = st.text_input(
                    "リサーチキーワード",
                    value=current_config.get("research_keywords", ""),
                )

                st.markdown("---")
                st.markdown("**Metricool設定（予約投稿用）**")
                new_metricool_token = st.text_input(
                    "Metricool User Token",
                    value=current_config.get("user_token", ""),
                    type="password",
                )
                new_metricool_user_id = st.text_input(
                    "Metricool User ID",
                    value=current_config.get("user_id", ""),
                )
                new_metricool_blog_id = st.text_input(
                    "Metricool Blog ID",
                    value=current_config.get("blog_id", ""),
                )

                st.markdown("---")
                col1, col2 = st.columns(2)
                new_posts = col1.number_input(
                    "1日の投稿数",
                    min_value=1, max_value=10,
                    value=current_config.get("posts_per_day", 5),
                )
                new_short = col2.slider(
                    "ショート投稿割合",
                    0.0, 1.0,
                    value=float(current_config.get("short_post_ratio", 0.5)),
                    step=0.1,
                )

                # Style guide
                style_path = PROFILES_DIR / edit_profile / "style_guide.md"
                current_style = ""
                if style_path.exists():
                    current_style = style_path.read_text(encoding="utf-8")
                new_style = st.text_area("スタイルガイド", value=current_style, height=100)

                save_btn = st.form_submit_button("保存", type="primary")

            if save_btn:
                current_config.update({
                    "display_name": new_display,
                    "genre": new_genre,
                    "threads_access_token": new_token,
                    "anthropic_api_key": new_api_key,
                    "research_keywords": new_keywords,
                    "posts_per_day": new_posts,
                    "short_post_ratio": new_short,
                    "user_token": new_metricool_token,
                    "user_id": new_metricool_user_id,
                    "blog_id": new_metricool_blog_id,
                })
                config_path.write_text(
                    json.dumps(current_config, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                if new_style:
                    style_path.write_text(new_style, encoding="utf-8")
                st.success(f"✅ **{new_display or edit_profile}** を更新しました！")

                # Test connection
                if new_token:
                    try:
                        from .threads_api import ThreadsGraphClient
                        with ThreadsGraphClient(access_token=new_token) as client:
                            me = client.get_me()
                            st.success(f"📡 Threads API接続OK: @{me.get('username', '?')}")
                    except Exception as e:
                        st.warning(f"⚠️ Threads API接続テスト失敗: {e}")

    # ── Tab: Client list ──
    with tab_list:
        st.subheader("クライアント一覧")
        profiles = list_profiles()
        if not profiles:
            st.info("まだクライアントがありません")
        else:
            for p in profiles:
                try:
                    s = get_settings(profile=p)
                    label = s.display_name or p
                    has_token = "✅" if s.threads_access_token else "❌"
                    has_api = "✅" if s.anthropic_api_key else "❌"

                    has_metricool = "✅" if s.validate_credentials() else "❌"

                    with st.expander(f"**{label}** ({p})"):
                        col1, col2, col3 = st.columns(3)
                        col1.write(f"Threads Token: {has_token}")
                        col2.write(f"Anthropic Key: {has_api}")
                        col3.write(f"ジャンル: {s.genre or '未設定'}")

                        col4, col5, col6 = st.columns(3)
                        col4.write(f"Metricool: {has_metricool}")
                        col5.write(f"投稿数/日: {s.posts_per_day}")
                        col6.write(f"ショート割合: {s.short_post_ratio:.0%}")

                        bcol1, bcol2 = st.columns(2)
                        with bcol1:
                            if s.threads_access_token:
                                if st.button("Threads接続テスト", key=f"test_threads_{p}"):
                                    try:
                                        from .threads_api import ThreadsGraphClient
                                        with ThreadsGraphClient(access_token=s.threads_access_token) as client:
                                            me = client.get_me()
                                            st.success(f"📡 @{me.get('username', '?')} — 接続OK")
                                    except Exception as e:
                                        st.error(f"接続失敗: {e}")
                        with bcol2:
                            if s.validate_credentials():
                                if st.button("Metricool接続テスト", key=f"test_metricool_{p}"):
                                    try:
                                        from .api import MetricoolClient
                                        with MetricoolClient(settings=s) as client:
                                            # 接続テスト: 予約投稿の取得を試行
                                            posts = client.get_scheduled_posts(
                                                datetime.now().strftime("%Y-%m-%d"),
                                                datetime.now().strftime("%Y-%m-%d"),
                                            )
                                            st.success(f"📡 Metricool接続OK（本日の予約: {len(posts)}件）")
                                    except Exception as e:
                                        st.error(f"Metricool接続失敗: {e}")
                except Exception as e:
                    st.error(f"{p}: {e}")


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

    # Fallback 1: Threads API (live data)
    if settings.threads_access_token:
        df = _load_threads_api(st, settings)
        if df is not None and not df.empty:
            return df

    # Fallback 2: CSV
    st.sidebar.info("データなし — CSVアップロードしてください")
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


def _load_threads_api(st, settings):
    """Load data directly from Threads Graph API."""
    try:
        from .threads_api import ThreadsGraphClient

        st.sidebar.info("📡 Threads APIからデータ取得中...")
        with ThreadsGraphClient(access_token=settings.threads_access_token) as client:
            posts = client.get_my_posts(limit=100)

            if not posts:
                return None

            rows = []
            for p in posts:
                # Get insights for each post
                post_id = p.get("id", "")
                try:
                    insights = client.get_post_insights(post_id) if post_id else {}
                except Exception:
                    insights = {}

                views = insights.get("views", 0)
                likes = insights.get("likes", p.get("like_count", 0))
                replies = insights.get("replies", p.get("reply_count", 0))
                reposts = insights.get("reposts", p.get("repost_count", 0))
                quotes = insights.get("quotes", p.get("quote_count", 0))
                total = likes + replies + reposts + quotes
                engagement = total / views if views > 0 else 0.0

                rows.append({
                    "Content": p.get("text", ""),
                    "Date": p.get("timestamp", ""),
                    "Views": views,
                    "Likes": likes,
                    "Replies": replies,
                    "Reposts": reposts,
                    "Quotes": quotes,
                    "Engagement": engagement,
                    "PostType": "reach",
                })

        if not rows:
            return None

        df = pd.DataFrame(rows)
        df = _normalize_df(df)
        st.sidebar.success(f"📡 Threads API ({len(df)}件)")
        return df
    except Exception as e:
        st.sidebar.warning(f"Threads API: {e}")
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
    pivot = pivot.reindex(dow_order).fillna(0)
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
