"""Streamlit web dashboard for non-technical users.

Run: streamlit run src/snshack_threads/dashboard.py
Or:  snshack dashboard

Full operation UI — no CLI needed.
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

    # ── Authentication ────────────────────────────────────
    if not _check_auth(st):
        return

    st.title("SNShack Threads Dashboard")

    # Profile selector
    profiles = list_profiles()
    if not profiles:
        st.warning("プロファイルがありません。右の「設定」タブから作成してください。")
        _render_settings_only(st)
        return

    active = _read_active_profile()
    selected_profile = st.sidebar.selectbox(
        "クライアント (Profile)",
        profiles,
        index=profiles.index(active) if active in profiles else 0,
    )
    settings = get_settings(profile=selected_profile)

    st.sidebar.markdown(f"**Data dir:** `{settings.data_dir}`")
    st.sidebar.markdown(f"**Timezone:** {settings.timezone}")

    # Tabs
    tabs = st.tabs([
        "Overview",
        "Posts",
        "Autopilot",
        "Competitors",
        "Followers",
        "Notifications",
        "Settings",
    ])

    with tabs[0]:
        _render_overview(settings)
    with tabs[1]:
        _render_posts(settings)
    with tabs[2]:
        _render_autopilot(st, settings, selected_profile)
    with tabs[3]:
        _render_competitors(st, settings, selected_profile)
    with tabs[4]:
        _render_followers(settings)
    with tabs[5]:
        _render_notifications(st, selected_profile)
    with tabs[6]:
        _render_settings(st, selected_profile, settings)


def _check_auth(st) -> bool:
    """Simple password authentication."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    # If no password configured, skip auth
    try:
        password = st.secrets["password"]
    except (FileNotFoundError, KeyError):
        return True

    if st.session_state.authenticated:
        return True

    st.title("SNShack Threads")
    pw = st.text_input("Password", type="password")
    if st.button("Login"):
        if pw == password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")
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
    st.subheader("New Profile")
    _profile_create_form(st)


# ── Overview ──────────────────────────────────────────────


def _render_overview(settings):
    import streamlit as st

    data_dir = Path(settings.data_dir)
    history = _load_json(data_dir / "post_history.json")

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

    col1.metric("Total Posts", total_posts)
    col2.metric("Total Views", f"{total_views:,}")
    col3.metric("Total Likes", f"{total_likes:,}")
    col4.metric("Avg Engagement", f"{avg_engagement * 100:.2f}%")

    # Phase indicator
    if total_posts < 70:
        phase = "Bootstrap (< 70 posts)"
        phase_color = "orange"
    elif total_posts < 150:
        phase = "Learning (70-150 posts)"
        phase_color = "blue"
    else:
        phase = "Optimized (150+ posts)"
        phase_color = "green"

    st.markdown(
        f"**Autopilot Phase:** :{phase_color}[{phase}] --- {len(collected)} collected"
    )

    # Recent performance chart
    if collected:
        st.subheader("Recent Performance (7 days)")
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

    # Hook performance
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
            for name, views in sorted(
                hook_data.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True
            ):
                hook_table.append(
                    {
                        "Hook": name,
                        "Posts": len(views),
                        "Avg Views": f"{sum(views) / len(views):,.0f}",
                        "Total Views": f"{sum(views):,}",
                    }
                )
            st.table(hook_table)


# ── Posts ─────────────────────────────────────────────────


def _render_posts(settings):
    import streamlit as st

    data_dir = Path(settings.data_dir)
    history = _load_json(data_dir / "post_history.json")

    if not history:
        st.info("No posts yet")
        return

    # Manual post
    st.subheader("Manual Post")
    with st.form("manual_post"):
        post_text = st.text_area("Post text", height=150, max_chars=500)
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            submitted = st.form_submit_button("Publish Now (Threads API)")
        with col_btn2:
            check_only = st.form_submit_button("NG Check Only")

    if check_only and post_text:
        from .content_guard import check_ng

        issues = check_ng(post_text)
        if issues:
            st.error(f"NG detected: {', '.join(issues)}")
        else:
            st.success("OK - No issues found")
            st.text(f"Length: {len(post_text)} chars")

    if submitted and post_text:
        from .content_guard import check_ng

        issues = check_ng(post_text)
        if issues:
            st.error(f"NG detected: {', '.join(issues)}")
        else:
            try:
                from .threads_api import ThreadsGraphClient

                with ThreadsGraphClient() as client:
                    post_id = client.create_text_post(post_text)
                st.success(f"Published! Post ID: {post_id}")
            except Exception as e:
                st.error(f"Failed: {e}")

    # Post list
    st.divider()
    st.subheader("Post History")
    days = st.slider("Days to show", 7, 90, 30)
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    recent = [r for r in history if r.get("scheduled_at", "") > cutoff]
    recent.sort(key=lambda r: r.get("views", 0), reverse=True)

    st.caption(f"{len(recent)} posts in last {days} days")

    for r in recent:
        status = r.get("status", "scheduled")
        views = r.get("views", 0)
        likes = r.get("likes", 0)
        text = r.get("text", "")[:100]
        date = r.get("scheduled_at", "")[:16]

        if status == "collected":
            st.markdown(f"**{date}** --- {views:,} views / {likes} likes")
        else:
            st.markdown(f"**{date}** --- _{status}_")
        st.text(text)
        st.divider()


# ── Autopilot ────────────────────────────────────────────


def _render_autopilot(st, settings, profile):
    st.subheader("Autopilot")

    st.markdown(
        "Autopilot generates and publishes posts automatically. "
        "Normally runs via cron, but you can trigger it manually here."
    )

    # Schedule display
    data_dir = Path(settings.data_dir)
    history = _load_json(data_dir / "post_history.json")

    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Today ({today})**")
        today_posts = [r for r in history if r.get("scheduled_at", "")[:10] == today]
        if today_posts:
            for r in today_posts:
                time = r.get("scheduled_at", "")[11:16]
                status = r.get("status", "scheduled")
                text = r.get("text", "")[:60]
                icon = {"collected": "OK", "scheduled": "WAIT", "published": "SENT"}.get(
                    status, "?"
                )
                st.markdown(f"`{icon}` **{time}** --- {text}")
        else:
            st.info("No posts scheduled for today")

    with col2:
        st.markdown(f"**Tomorrow ({tomorrow})**")
        tomorrow_posts = [
            r for r in history if r.get("scheduled_at", "")[:10] == tomorrow
        ]
        if tomorrow_posts:
            for r in tomorrow_posts:
                time = r.get("scheduled_at", "")[11:16]
                text = r.get("text", "")[:60]
                st.markdown(f"`WAIT` **{time}** --- {text}")
        else:
            st.info("No posts for tomorrow")

    # Manual autopilot trigger
    st.divider()
    st.subheader("Run Autopilot")

    with st.form("autopilot_form"):
        topics_input = st.text_area(
            "Topics (one per line)",
            value="\n".join(settings.get_research_keywords() or ["general topic"]),
            height=100,
        )
        posts_per_day = st.number_input("Posts per day", min_value=1, max_value=10, value=5)
        publish_method = st.selectbox("Publish method", ["threads", "metricool"])
        dry_run = st.checkbox("Dry run (preview only, don't publish)", value=True)
        run_btn = st.form_submit_button("Generate & Schedule")

    if run_btn:
        topics = [t.strip() for t in topics_input.strip().splitlines() if t.strip()]
        if not topics:
            st.error("Please enter at least one topic")
            return

        with st.spinner("Generating posts..."):
            try:
                from .autopilot import execute_plan, generate_daily_plan

                plan = generate_daily_plan(
                    topics=topics,
                    profile=profile,
                    posts_per_day=posts_per_day,
                )

                st.success(
                    f"Generated {len(plan.posts)} posts (Phase: {plan.phase})"
                )

                for i, p in enumerate(plan.posts):
                    st.markdown(f"**Post {i + 1}** (hook: {p['hook']})")
                    st.text(p["text"])
                    st.divider()

                if plan.skipped:
                    st.warning(f"Skipped: {len(plan.skipped)}")
                    for s in plan.skipped:
                        st.text(f"  - {s}")

                if not dry_run:
                    results = execute_plan(plan, publish_method=publish_method, profile=profile)
                    for r in results:
                        st.text(r)
                    st.success("Autopilot execution complete!")
                else:
                    st.info("Dry run mode --- posts were NOT published")

            except Exception as e:
                st.error(f"Autopilot error: {e}")


# ── Competitors ──────────────────────────────────────────


def _render_competitors(st, settings, profile):
    data_dir = Path(settings.data_dir)
    research_dir = data_dir / "research"

    competitors = _load_json(research_dir / "competitors.json")
    snapshots = _load_json(research_dir / "competitor_snapshots.json")

    # Add competitor form
    st.subheader("Add Competitor")
    with st.form("add_competitor"):
        comp_username = st.text_input("Username (without @)")
        comp_display = st.text_input("Display name (optional)")
        comp_notes = st.text_input("Notes (e.g. industry, relationship)")
        add_btn = st.form_submit_button("Add")

    if add_btn and comp_username:
        comp_username = comp_username.strip().lstrip("@")
        existing = [c.get("username") for c in competitors] if isinstance(competitors, list) else []
        if comp_username in existing:
            st.warning(f"@{comp_username} is already registered")
        else:
            if not isinstance(competitors, list):
                competitors = []
            competitors.append({
                "username": comp_username,
                "display_name": comp_display,
                "notes": comp_notes,
                "added_at": datetime.now().isoformat(),
            })
            _save_json(research_dir / "competitors.json", competitors)
            st.success(f"Added @{comp_username}")
            st.rerun()

    # Scrape button
    if competitors:
        st.divider()
        col_scrape, col_remove = st.columns(2)
        with col_scrape:
            if st.button("Scrape All Competitors"):
                try:
                    from .browser_scraper import scrape_profile
                    from .research_store import ResearchStore

                    store = ResearchStore(profile=profile)
                    progress = st.progress(0.0)
                    for i, comp in enumerate(competitors):
                        username = comp.get("username", "")
                        st.text(f"Scraping @{username}...")
                        try:
                            result = scrape_profile(username)
                            store.save_competitor_snapshot(username, {
                                "post_count": len(result.posts),
                                "avg_likes": sum(p.likes for p in result.posts) / len(result.posts) if result.posts else 0,
                                "avg_replies": sum(p.replies for p in result.posts) / len(result.posts) if result.posts else 0,
                            })
                        except Exception as e:
                            st.warning(f"Failed @{username}: {e}")
                        progress.progress((i + 1) / len(competitors))
                    st.success("Scraping complete!")
                except ImportError:
                    st.error("playwright not installed. Run: pip install 'snshack-threads[scraper]'")

    # List competitors
    if competitors:
        st.divider()
        st.subheader(f"Watching {len(competitors)} Competitors")

        for comp in competitors:
            username = comp.get("username", "")
            name = comp.get("display_name", "") or username
            notes = comp.get("notes", "")

            with st.expander(f"@{username} --- {name} ({notes})"):
                comp_snaps = [
                    s for s in snapshots if s.get("username") == username
                ]
                if comp_snaps:
                    latest = comp_snaps[-1]
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Posts scraped", latest.get("post_count", 0))
                    c2.metric("Avg Likes", f"{latest.get('avg_likes', 0):.1f}")
                    c3.metric("Avg Replies", f"{latest.get('avg_replies', 0):.1f}")

                    hooks = latest.get("top_hooks", [])
                    if hooks:
                        st.markdown(f"**Hooks:** {', '.join(hooks[:5])}")

                    top = latest.get("posts", [])[:5]
                    for p in top:
                        st.text(
                            f"{p.get('likes', 0)} likes --- {p.get('text', '')[:80]}"
                        )
                else:
                    st.info("No data yet. Click 'Scrape All Competitors'")

                # Remove button
                if st.button(f"Remove @{username}", key=f"rm_{username}"):
                    competitors = [
                        c for c in competitors if c.get("username") != username
                    ]
                    _save_json(research_dir / "competitors.json", competitors)
                    st.success(f"Removed @{username}")
                    st.rerun()
    else:
        st.info("No competitors registered yet")


# ── Followers ────────────────────────────────────────────


def _render_followers(settings):
    import streamlit as st

    data_dir = Path(settings.data_dir)
    snapshots = _load_json(data_dir / "follower_snapshots.json")

    if not snapshots:
        st.info("No follower data yet. Follower tracking runs automatically via cron.")
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


# ── Notifications ────────────────────────────────────────


def _render_notifications(st, profile):
    st.subheader("Notification Settings")

    from .notifier import NotifyConfig

    config = NotifyConfig.from_profile(profile)

    # Status display
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Slack**")
        if config.has_slack:
            st.success("Configured")
            if config.slack_channel:
                st.text(f"Channel: {config.slack_channel}")
        else:
            st.warning("Not configured")

    with col2:
        st.markdown("**Email**")
        if config.has_email:
            st.success("Configured")
            st.text(f"To: {config.email_to}")
        else:
            st.warning("Not configured")

    # Slack setup
    st.divider()
    st.subheader("Setup Slack")
    with st.form("slack_setup"):
        webhook_url = st.text_input(
            "Slack Webhook URL",
            value=config.slack_webhook_url,
            type="password",
        )
        slack_channel = st.text_input(
            "Slack Channel (optional)",
            value=config.slack_channel,
        )
        slack_save = st.form_submit_button("Save Slack Settings")

    if slack_save:
        _update_profile_notify(profile, {
            "slack_webhook_url": webhook_url,
            "slack_channel": slack_channel,
        })
        st.success("Slack settings saved!")
        st.rerun()

    # Test notification
    st.divider()
    if st.button("Send Test Notification"):
        from .notifier import send_email, send_slack

        config = NotifyConfig.from_profile(profile)
        results = []
        if config.has_slack:
            ok = send_slack(config, "Test from SNShack Dashboard", title="Test")
            results.append(f"Slack: {'OK' if ok else 'Failed'}")
        if config.has_email:
            ok = send_email(config, "SNShack Test", "Test from dashboard")
            results.append(f"Email: {'OK' if ok else 'Failed'}")
        if results:
            st.info(" / ".join(results))
        else:
            st.warning("No notification channels configured")

    # Alert check
    st.divider()
    st.subheader("Alert Check")
    if st.button("Run Alert Checks Now"):
        from .notifier import run_all_checks

        alerts = run_all_checks(profile=profile)
        if alerts:
            st.error(f"{len(alerts)} alert(s):")
            for a in alerts:
                st.text(a)
        else:
            st.success("All checks passed. No alerts.")


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
    st.subheader("Profile Settings")

    # Current profile info
    st.markdown(f"**Active profile:** `{profile}`")

    # Edit profile
    from .config import _profile_config_path

    config_path = _profile_config_path(profile)
    if config_path.exists():
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config_data = {}

    with st.form("edit_profile"):
        st.markdown("**API Credentials**")
        user_token = st.text_input(
            "Metricool User Token",
            value=config_data.get("user_token", ""),
            type="password",
        )
        user_id = st.text_input(
            "Metricool User ID", value=config_data.get("user_id", "")
        )
        blog_id = st.text_input(
            "Metricool Blog ID", value=config_data.get("blog_id", "")
        )
        threads_token = st.text_input(
            "Threads Access Token",
            value=config_data.get("threads_access_token", ""),
            type="password",
        )

        st.markdown("**General**")
        timezone = st.text_input(
            "Timezone", value=config_data.get("timezone", "Asia/Tokyo")
        )
        research_kw = st.text_input(
            "Research Keywords (comma-separated)",
            value=config_data.get("research_keywords", ""),
        )

        save_btn = st.form_submit_button("Save Settings")

    if save_btn:
        config_data.update({
            "user_token": user_token,
            "user_id": user_id,
            "blog_id": blog_id,
            "threads_access_token": threads_token,
            "timezone": timezone,
            "research_keywords": research_kw,
        })
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(config_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        st.success("Settings saved!")

    # Token status
    st.divider()
    st.subheader("Threads Token Status")
    if settings.threads_access_token:
        if st.button("Check Token"):
            try:
                from .threads_api import get_token_info

                info = get_token_info(settings)
                if info:
                    expires = info.get("expires_at")
                    if expires:
                        exp_dt = datetime.fromtimestamp(expires)
                        days_left = (exp_dt - datetime.now()).days
                        if days_left > 14:
                            st.success(f"Token valid --- expires {exp_dt:%Y-%m-%d} ({days_left} days)")
                        elif days_left > 7:
                            st.warning(f"Token expiring soon --- {exp_dt:%Y-%m-%d} ({days_left} days)")
                        else:
                            st.error(f"Token expiring! --- {exp_dt:%Y-%m-%d} ({days_left} days)")
                    else:
                        st.info("Could not determine expiry")
                else:
                    st.error("Could not fetch token info")
            except Exception as e:
                st.error(f"Error: {e}")

        if st.button("Refresh Token Now"):
            try:
                from .threads_api import refresh_long_lived_token

                new_token = refresh_long_lived_token(settings)
                if new_token:
                    st.success("Token refreshed!")
                else:
                    st.error("Token refresh failed")
            except Exception as e:
                st.error(f"Error: {e}")
    else:
        st.info("No Threads token configured")

    # Profile management
    st.divider()
    st.subheader("Profile Management")

    profiles = list_profiles()
    st.markdown(f"**Profiles:** {', '.join(profiles) if profiles else 'none'}")

    # Create new profile
    _profile_create_form(st)


def _profile_create_form(st):
    """Render profile creation form."""
    from .config import create_profile

    with st.form("create_profile"):
        st.markdown("**Create New Profile**")
        new_name = st.text_input("Profile name (e.g. client-restaurant-tokyo)")
        new_token = st.text_input("Metricool User Token", type="password", key="new_token")
        new_user_id = st.text_input("Metricool User ID", key="new_uid")
        new_blog_id = st.text_input("Metricool Blog ID", key="new_bid")
        new_threads = st.text_input(
            "Threads Access Token (optional)", type="password", key="new_threads"
        )
        new_keywords = st.text_input(
            "Research Keywords (optional, comma-separated)", key="new_kw"
        )
        create_btn = st.form_submit_button("Create Profile")

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
            st.success(f"Profile '{new_name}' created!")
            st.rerun()
        except FileExistsError:
            st.error(f"Profile '{new_name}' already exists")
        except Exception as e:
            st.error(f"Error: {e}")


def list_profiles():
    from .config import list_profiles as _list_profiles

    return _list_profiles()


if __name__ == "__main__":
    main()
