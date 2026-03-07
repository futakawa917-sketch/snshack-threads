"""Streamlit web dashboard for non-technical users.

Run: streamlit run src/snshack_threads/dashboard.py
Or:  snshack dashboard
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path


def main():
    import streamlit as st

    from .config import (
        _read_active_profile,
        get_settings,
        list_profiles,
    )

    st.set_page_config(page_title="SNShack Threads", layout="wide")
    st.title("SNShack Threads Dashboard")

    # Profile selector
    profiles = list_profiles()
    if not profiles:
        st.warning("No profiles found. Create one with `snshack profile create`")
        return

    active = _read_active_profile()
    selected_profile = st.sidebar.selectbox(
        "Profile", profiles, index=profiles.index(active) if active in profiles else 0
    )
    settings = get_settings(profile=selected_profile)

    st.sidebar.markdown(f"**Data dir:** `{settings.data_dir}`")
    st.sidebar.markdown(f"**Timezone:** {settings.timezone}")

    # Tabs
    tab_overview, tab_posts, tab_followers, tab_competitors, tab_schedule = st.tabs(
        ["Overview", "Posts", "Followers", "Competitors", "Schedule"]
    )

    # ── Overview ──────────────────────────────────────────
    with tab_overview:
        _render_overview(settings)

    # ── Posts ──────────────────────────────────────────────
    with tab_posts:
        _render_posts(settings)

    # ── Followers ─────────────────────────────────────────
    with tab_followers:
        _render_followers(settings)

    # ── Competitors ───────────────────────────────────────
    with tab_competitors:
        _render_competitors(settings)

    # ── Schedule ──────────────────────────────────────────
    with tab_schedule:
        _render_schedule(settings)


def _load_json(path: Path) -> list | dict:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _render_overview(settings):
    import streamlit as st

    data_dir = Path(settings.data_dir)
    history = _load_json(data_dir / "post_history.json")
    followers = _load_json(data_dir / "follower_snapshots.json")

    # Key metrics
    col1, col2, col3, col4 = st.columns(4)

    total_posts = len(history)
    collected = [r for r in history if r.get("status") == "collected"]
    total_views = sum(r.get("views", 0) for r in collected)
    total_likes = sum(r.get("likes", 0) for r in collected)
    avg_engagement = (
        sum(r.get("engagement", 0) for r in collected) / len(collected)
        if collected else 0
    )

    col1.metric("Total Posts", total_posts)
    col2.metric("Total Views", f"{total_views:,}")
    col3.metric("Total Likes", f"{total_likes:,}")
    col4.metric("Avg Engagement", f"{avg_engagement * 100:.2f}%")

    # Phase indicator
    if total_posts < 70:
        phase = "立ち上げ期 (Bootstrap)"
        phase_color = "orange"
    elif total_posts < 150:
        phase = "学習期 (Learning)"
        phase_color = "blue"
    else:
        phase = "最適化期 (Optimized)"
        phase_color = "green"

    st.markdown(f"**Autopilot Phase:** :{phase_color}[{phase}] — {len(collected)} collected posts")

    # Recent performance
    if collected:
        st.subheader("Recent Performance (Last 7 days)")
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        recent = [r for r in collected if r.get("scheduled_at", "") > cutoff]
        if recent:
            chart_data = {
                "Date": [r.get("scheduled_at", "")[:10] for r in recent],
                "Views": [r.get("views", 0) for r in recent],
                "Likes": [r.get("likes", 0) for r in recent],
            }
            st.bar_chart(chart_data, x="Date", y=["Views", "Likes"])
        else:
            st.info("No data in the last 7 days")

    # Top hooks
    if collected:
        st.subheader("Hook Performance")
        from .csv_analyzer import _detect_hooks

        hook_data: dict[str, list[int]] = {}
        for r in collected:
            hooks = _detect_hooks(r.get("text", ""))
            for h in hooks:
                hook_data.setdefault(h, []).append(r.get("views", 0))

        if hook_data:
            hook_table = []
            for name, views in sorted(hook_data.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True):
                hook_table.append({
                    "Hook": name,
                    "Posts": len(views),
                    "Avg Views": f"{sum(views) / len(views):,.0f}",
                    "Total Views": f"{sum(views):,}",
                })
            st.table(hook_table)


def _render_posts(settings):
    import streamlit as st

    data_dir = Path(settings.data_dir)
    history = _load_json(data_dir / "post_history.json")

    if not history:
        st.info("No posts yet")
        return

    days = st.slider("Days to show", 7, 90, 30)
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    recent = [r for r in history if r.get("scheduled_at", "") > cutoff]
    recent.sort(key=lambda r: r.get("views", 0), reverse=True)

    st.subheader(f"Posts ({len(recent)} in last {days} days)")

    for r in recent:
        status = r.get("status", "scheduled")
        views = r.get("views", 0)
        likes = r.get("likes", 0)
        text = r.get("text", "")[:100]
        date = r.get("scheduled_at", "")[:16]

        if status == "collected":
            st.markdown(f"**{date}** — {views:,} views / {likes} likes")
        else:
            st.markdown(f"**{date}** — _{status}_")
        st.text(text)
        st.divider()


def _render_followers(settings):
    import streamlit as st

    data_dir = Path(settings.data_dir)
    snapshots = _load_json(data_dir / "follower_snapshots.json")

    if not snapshots:
        st.info("No follower data. Run `snshack track-followers` first.")
        return

    st.subheader("Follower Growth")

    chart_data = {
        "Date": [s.get("date", "") for s in snapshots],
        "Followers": [s.get("followers_count", 0) for s in snapshots],
        "Delta": [s.get("delta", 0) for s in snapshots],
    }
    st.line_chart(chart_data, x="Date", y="Followers")

    st.subheader("Daily Change")
    st.bar_chart(chart_data, x="Date", y="Delta")


def _render_competitors(settings):
    import streamlit as st

    data_dir = Path(settings.data_dir)
    research_dir = data_dir / "research"

    competitors = _load_json(research_dir / "competitors.json")
    snapshots = _load_json(research_dir / "competitor_snapshots.json")

    if not competitors:
        st.info("No competitors registered. Use `snshack competitor add`")
        return

    st.subheader(f"Watching {len(competitors)} Competitors")

    for comp in competitors:
        username = comp.get("username", "")
        name = comp.get("display_name", "") or username
        notes = comp.get("notes", "")

        with st.expander(f"@{username} — {name} ({notes})"):
            comp_snaps = [s for s in snapshots if s.get("username") == username]
            if comp_snaps:
                latest = comp_snaps[-1]
                st.metric("Posts scraped", latest.get("post_count", 0))
                st.metric("Avg Likes", f"{latest.get('avg_likes', 0):.1f}")
                st.metric("Avg Replies", f"{latest.get('avg_replies', 0):.1f}")

                hooks = latest.get("top_hooks", [])
                if hooks:
                    st.markdown(f"**Hooks:** {', '.join(hooks[:5])}")

                top = latest.get("posts", [])[:5]
                for p in top:
                    st.text(f"{p.get('likes', 0)} likes — {p.get('text', '')[:80]}")
            else:
                st.info("No data yet. Run `snshack competitor scrape`")


def _render_schedule(settings):
    import streamlit as st

    data_dir = Path(settings.data_dir)
    history = _load_json(data_dir / "post_history.json")

    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    today_posts = [r for r in history if r.get("scheduled_at", "")[:10] == today]
    tomorrow_posts = [r for r in history if r.get("scheduled_at", "")[:10] == tomorrow]

    st.subheader(f"Today ({today})")
    if today_posts:
        for r in today_posts:
            time = r.get("scheduled_at", "")[11:16]
            status = r.get("status", "scheduled")
            text = r.get("text", "")[:80]
            icon = "✅" if status == "collected" else "⏳" if status == "scheduled" else "📤"
            st.markdown(f"{icon} **{time}** — {text}")
    else:
        st.info("No posts scheduled for today")

    st.subheader(f"Tomorrow ({tomorrow})")
    if tomorrow_posts:
        for r in tomorrow_posts:
            time = r.get("scheduled_at", "")[11:16]
            text = r.get("text", "")[:80]
            st.markdown(f"⏳ **{time}** — {text}")
    else:
        st.info("No posts scheduled for tomorrow. Run `snshack autopilot`")


if __name__ == "__main__":
    main()
