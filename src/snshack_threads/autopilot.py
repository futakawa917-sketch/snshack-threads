"""Autopilot — fully automated daily content planning and scheduling.

Strategy adapts based on data maturity:
  - bootstrap (<100 posts): Rotate all hooks evenly for data collection
  - learning (100-200): Focus on top hooks, explore others, A/B test
  - optimized (200+): Template-based + recycle, concentrate on proven winners

Integrates: early velocity, follower correlation, AB test winners, shared intelligence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class DailyPlan:
    """A generated daily content plan."""

    date: str
    phase: str  # bootstrap | learning | optimized
    total_posts: int  # Total collected posts in history
    posts: list[dict] = field(default_factory=list)  # {text, hook, source}
    skipped: list[str] = field(default_factory=list)  # Reasons for skipped posts


def _auto_refresh_data(profile: str | None = None) -> None:
    """Auto-refresh shared intelligence and matrix if data is stale.

    Refresh interval adapts to phase: 3 days during bootstrap, 7 days otherwise.
    """
    from pathlib import Path

    # Determine refresh interval based on phase
    try:
        from .config import get_settings as _gs_phase
        from .post_history import PostHistory
        _s = _gs_phase(profile=profile)
        _h = PostHistory(history_path=_s.profile_dir / "post_history.json")
        _collected = sum(1 for r in _h.get_all() if r.has_metrics)
        refresh_days = 3 if _collected < 100 else 7
    except Exception:
        refresh_days = 7

    try:
        # Check shared intelligence staleness
        shared_path = Path.home() / ".snshack-threads" / "shared" / "universal_insights.json"
        _refresh_shared = not shared_path.exists()
        if shared_path.exists():
            import json
            data = json.loads(shared_path.read_text(encoding="utf-8"))
            analyzed_at = data.get("metadata", {}).get("analyzed_at", "")
            if analyzed_at:
                last = datetime.strptime(analyzed_at, "%Y-%m-%d %H:%M:%S")
                if (datetime.now() - last).days >= refresh_days:
                    _refresh_shared = True

        if _refresh_shared:
            logger.info("Shared intelligence is stale, refreshing...")
            from .config import list_profiles, get_settings as _gs
            from .shared_intelligence import analyze_cross_genre, update_shared_data

            csv_configs = []
            for name in list_profiles():
                try:
                    s = _gs(profile=name)
                    csv_path = None
                    if s.csv_path:
                        csv_path = Path(s.csv_path)
                    elif s.reference_csv_path.exists():
                        csv_path = s.reference_csv_path
                    if csv_path and csv_path.exists():
                        csv_configs.append({
                            "path": str(csv_path),
                            "genre": s.genre or "不明",
                            "account": name,
                        })
                except Exception:
                    continue
            if csv_configs:
                insights = analyze_cross_genre(csv_configs)
                update_shared_data(insights)
                logger.info("Shared intelligence updated (%d posts)", insights.total_posts)

        # Check matrix staleness for this profile
        from .config import get_settings as _gs2
        settings = _gs2(profile=profile)
        matrix_path = settings.profile_dir / "hook_theme_matrix.json"
        _refresh_matrix = not matrix_path.exists()
        if matrix_path.exists():
            import os
            mtime = datetime.fromtimestamp(os.path.getmtime(matrix_path))
            if (datetime.now() - mtime).days >= refresh_days:
                _refresh_matrix = True

        if _refresh_matrix:
            csv_path = None
            if settings.csv_path:
                csv_path = Path(settings.csv_path)
            elif settings.reference_csv_path.exists():
                csv_path = settings.reference_csv_path
            if csv_path and csv_path.exists():
                logger.info("Hook-theme matrix is stale, refreshing...")
                from .hook_theme_matrix import build_matrix, save_matrix
                matrix = build_matrix([{
                    "csv_path": str(csv_path),
                    "genre": settings.genre or None,
                    "profile": profile or "default",
                }])
                save_matrix(matrix, settings.profile_dir)
                logger.info("Matrix updated (%d posts, %d classified)", matrix.total_posts, matrix.classified_posts)
    except Exception as e:
        logger.debug("Auto-refresh failed (non-critical): %s", e)


def _determine_phase(collected_count: int) -> str:
    """Determine the autopilot phase based on data maturity."""
    if collected_count < 100:
        return "bootstrap"
    elif collected_count < 200:
        return "learning"
    return "optimized"


def generate_daily_plan(
    topics: list[str],
    profile: str | None = None,
    target_date: datetime | None = None,
    posts_per_day: int = 5,
) -> DailyPlan:
    """Generate a daily content plan based on data maturity.

    Uses 3-tier data resolution (account → genre → universal) to inform
    hook selection and strategy even when the account has little data.

    Args:
        topics: List of topics/themes to generate about.
        profile: Profile name to use.
        target_date: Target date (default: today).
        posts_per_day: Number of posts to plan.

    Returns:
        DailyPlan with generated posts.
    """
    from .config import get_settings
    from .content_generator import generate_post
    from .csv_analyzer import _detect_hooks, get_active_hooks
    from .data_resolver import DataTier, resolve_hooks, resolve_phase
    from .post_history import PostHistory, get_performance_summary
    from .templates import generate_templates

    target = target_date or datetime.now()
    date_str = target.strftime("%Y-%m-%d")

    # Auto-refresh shared intelligence & matrix if stale (>7 days)
    _auto_refresh_data(profile)

    settings = get_settings(profile=profile)
    history = PostHistory(history_path=settings.profile_dir / "post_history.json")
    collected = [r for r in history.get_all() if r.has_metrics]
    phase, data_tier = resolve_phase(profile)

    plan = DailyPlan(
        date=date_str,
        phase=phase,
        total_posts=len(collected),
    )

    # Enrich topics with trending news if we have few topics
    if len(topics) < posts_per_day:
        try:
            from .web_research import search_news_for_keywords
            keywords = settings.get_research_keywords()
            if keywords:
                headlines = search_news_for_keywords(keywords, max_per_keyword=3)
                existing = set(topics)
                for headline in headlines:
                    short = headline[:50]
                    if short not in existing:
                        topics.append(short)
                        existing.add(short)
                        if len(topics) >= posts_per_day * 2:
                            break
                logger.info("Topics enriched with news headlines → %d topics", len(topics))
        except Exception as e:
            logger.debug("News enrichment failed: %s", e)

    all_hooks = [name for name, _ in get_active_hooks()]
    # Deduplicate
    seen = set()
    unique_hooks = []
    for h in all_hooks:
        if h not in seen:
            seen.add(h)
            unique_hooks.append(h)

    # Resolve hook intelligence from best available tier
    resolved = resolve_hooks(profile)
    logger.info(
        "Autopilot phase=%s, data_tier=%s, hooks_tier=%s",
        phase, data_tier.value, resolved.tier.value,
    )

    # Boost hooks that showed high early velocity recently
    velocity_data: dict = {}
    try:
        from .early_velocity import feed_velocity_to_learning
        velocity_data = feed_velocity_to_learning(history)
        boost_hooks = velocity_data.get("boost_hooks", [])
        if boost_hooks:
            logger.info("Velocity boost hooks: %s", boost_hooks)
        # Log velocity insights for scheduling optimization
        if velocity_data.get("best_hours"):
            best_h = velocity_data["best_hours"][0]
            logger.info("Velocity best hour: %d:00 (%s views/h)", best_h["hour"], best_h["avg_vph"])
    except Exception:
        boost_hooks = []

    # Resolve follower correlation for post type mix
    follower_correlation = None
    try:
        from .data_resolver import resolve_follower_insights
        fi = resolve_follower_insights(profile)
        follower_correlation = fi.correlation
        if follower_correlation:
            logger.info("Follower correlation: lift=%.1f (threshold=%d views)",
                        follower_correlation.lift, follower_correlation.views_threshold)
    except Exception:
        pass

    # Content-engagement factor analysis
    try:
        from .content_analyzer import analyze_content_factors, summarize_top_factors
        content_insights = analyze_content_factors(collected)
        if content_insights:
            summary_text = summarize_top_factors(content_insights, top_n=3)
            logger.info("Content factor analysis:\n%s", summary_text)
    except Exception as e:
        logger.debug("Content factor analysis failed (non-critical): %s", e)

    # Follower post attribution analysis
    try:
        from .follower_tracker import FollowerTracker
        ft = FollowerTracker()
        attribution = ft.analyze_post_attribution(collected)
        hook_attr = attribution.get("hook_attribution", {})
        type_attr = attribution.get("type_attribution", {})
        if hook_attr:
            top_hook = next(iter(hook_attr))
            logger.info("Follower attribution: top hook=%s (avg delta=%.1f)",
                        top_hook, hook_attr[top_hook])
        if type_attr:
            logger.info("Follower attribution by type: %s", type_attr)
    except Exception as e:
        logger.debug("Follower attribution failed (non-critical): %s", e)

    # Get AB test winners to inform hook selection
    ab_winning_hooks: list[str] = []
    try:
        from .ab_test import ABTestManager
        ab_manager = ABTestManager()
        for test in ab_manager.get_all():
            if test.status == "completed" and test.winner in ("A", "B") and test.confidence in ("high", "medium"):
                winning_text = test.variant_a_text if test.winner == "A" else test.variant_b_text
                from .csv_analyzer import _detect_hooks
                winning_hooks = _detect_hooks(winning_text)
                ab_winning_hooks.extend(winning_hooks)
        if ab_winning_hooks:
            logger.info("AB test winning hooks: %s", list(set(ab_winning_hooks)))
    except Exception:
        pass

    # Determine post types with follower + velocity intelligence
    velocity_preferred = velocity_data.get("preferred_type", "reach") if velocity_data else "reach"
    post_types = _determine_post_types(
        posts_per_day, phase,
        follower_correlation=follower_correlation,
        velocity_preferred_type=velocity_preferred,
    )

    # Determine post lengths (short vs medium) based on config
    short_ratio = settings.short_post_ratio if hasattr(settings, "short_post_ratio") else 0.5
    post_lengths = _determine_post_lengths(posts_per_day, short_ratio=short_ratio)

    try:
        if phase == "bootstrap":
            if resolved.hooks and resolved.tier != DataTier.ACCOUNT:
                plan = _plan_bootstrap_guided(
                    plan, topics, unique_hooks, resolved.hooks, posts_per_day,
                    boost_hooks=boost_hooks, post_types=post_types, post_lengths=post_lengths,
                )
            else:
                plan = _plan_bootstrap(plan, topics, unique_hooks, posts_per_day, post_types=post_types, post_lengths=post_lengths)
        elif phase == "learning":
            plan = _plan_learning(
                plan, topics, unique_hooks, history, posts_per_day,
                fallback_hooks=resolved.hooks if resolved.tier != DataTier.ACCOUNT else None,
                boost_hooks=boost_hooks, post_types=post_types, post_lengths=post_lengths,
                ab_winning_hooks=ab_winning_hooks,
            )
        else:
            plan = _plan_optimized(plan, topics, unique_hooks, history, posts_per_day, post_types=post_types, post_lengths=post_lengths)
    except Exception as e:
        logger.error("Plan generation failed: %s", e)
        plan.skipped.append(f"Generation error: {e}")

    return plan


def _determine_post_lengths(count: int, short_ratio: float = 0.5) -> list[str]:
    """Determine the length mix for a daily plan.

    Args:
        count: Number of posts.
        short_ratio: Fraction of posts that should be short (1-2 lines).

    Returns:
        List of "short" or "medium" strings.
    """
    import random

    n_short = max(0, round(count * short_ratio))
    n_medium = count - n_short
    lengths = ["short"] * n_short + ["medium"] * n_medium
    random.shuffle(lengths)
    return lengths


def _determine_post_types(
    count: int,
    phase: str,
    follower_correlation: object | None = None,
    velocity_preferred_type: str = "reach",
) -> list[str]:
    """Determine the post_type mix for a daily plan.

    - bootstrap: mostly reach, but incorporate list early if velocity data suggests it
    - learning: 80% reach, 20% list (adjusted by follower correlation)
    - optimized: 60% reach, 40% list (adjusted by follower correlation)

    Args:
        follower_correlation: FollowerCorrelation object with .lift attribute.
        velocity_preferred_type: "reach" or "list" from early velocity analysis.
    """
    if phase == "bootstrap":
        # Allow some list posts if velocity data shows list performs well
        if velocity_preferred_type == "list":
            n_list = max(1, int(count * 0.2))
        else:
            return ["reach"] * count
    elif phase == "learning":
        base_list_ratio = 0.2
        # Boost list ratio if follower correlation shows high lift from views
        if follower_correlation and hasattr(follower_correlation, "lift"):
            if follower_correlation.lift > 2.0:
                base_list_ratio = 0.3
            elif follower_correlation.lift > 1.5:
                base_list_ratio = 0.25
        n_list = max(1, int(count * base_list_ratio))
    else:
        base_list_ratio = 0.4
        if follower_correlation and hasattr(follower_correlation, "lift"):
            if follower_correlation.lift > 2.0:
                base_list_ratio = 0.5
        n_list = max(1, int(count * base_list_ratio))
    n_reach = count - n_list
    return ["reach"] * n_reach + ["list"] * n_list


def _try_ab_test_pair(
    plan: DailyPlan,
    topics: list[str],
    hooks: list[str],
    topic_index: int,
    post_types: list[str] | None = None,
) -> tuple[DailyPlan, int]:
    """Try to add an A/B test pair to the plan."""
    from .ab_test import ABTestManager
    from .content_generator import generate_ab_variants

    if len(hooks) < 2:
        return plan, 0

    topic = topics[topic_index % len(topics)]
    hook_a, hook_b = hooks[0], hooks[1]
    if hook_a == hook_b and len(hooks) > 2:
        hook_b = hooks[2]

    try:
        variant_a, variant_b = generate_ab_variants(topic, hook_a, hook_b)
        ptype = "reach"
        plan.posts.append({
            "text": variant_a.text, "hook": hook_a,
            "source": "ab_test_A", "post_type": ptype,
        })
        plan.posts.append({
            "text": variant_b.text, "hook": hook_b,
            "source": "ab_test_B", "post_type": ptype,
        })
        try:
            manager = ABTestManager()
            manager.create_test(
                theme=topic,
                variant_a_text=variant_a.text,
                variant_b_text=variant_b.text,
            )
        except Exception as e:
            logger.debug("Failed to record A/B test: %s", e)
        return plan, 2
    except Exception as e:
        logger.warning("A/B test generation failed: %s", e)
        return plan, 0


def _plan_bootstrap(
    plan: DailyPlan,
    topics: list[str],
    hooks: list[str],
    count: int,
    post_types: list[str] | None = None,
    post_lengths: list[str] | None = None,
) -> DailyPlan:
    """Bootstrap phase: rotate hooks evenly for data collection."""
    from .content_generator import generate_post

    for i in range(count):
        topic = topics[i % len(topics)]
        hook = hooks[i % len(hooks)]
        ptype = post_types[i] if post_types and i < len(post_types) else "reach"
        plength = post_lengths[i] if post_lengths and i < len(post_lengths) else "medium"
        try:
            post = generate_post(topic=topic, hook_type=hook, length=plength)
            plan.posts.append({
                "text": post.text, "hook": hook,
                "source": "bootstrap_generate", "post_type": ptype,
            })
        except Exception as e:
            plan.skipped.append(f"Failed: {hook} x {topic}: {e}")

    return plan


def _plan_bootstrap_guided(
    plan: DailyPlan,
    topics: list[str],
    all_hooks: list[str],
    resolved_hooks: list[dict],
    count: int,
    boost_hooks: list[str] | None = None,
    post_types: list[str] | None = None,
    post_lengths: list[str] | None = None,
) -> DailyPlan:
    """Bootstrap with genre/universal intelligence guiding hook selection.

    Uses resolved hook rankings to prioritize promising hooks (60%) while
    still exploring others (40%) for this account's own data collection.
    """
    from .content_generator import generate_post

    # Top hooks from genre/universal data
    top_hook_names = [h["hook"] for h in resolved_hooks[:5]]
    guided_hooks = [h for h in top_hook_names if h in all_hooks]
    explore_hooks = [h for h in all_hooks if h not in guided_hooks]

    # Boost velocity hooks to front of guided list
    if boost_hooks:
        for bh in boost_hooks:
            if bh in all_hooks and bh not in guided_hooks:
                guided_hooks.insert(0, bh)

    n_guided = max(1, int(count * 0.6))
    n_explore = count - n_guided

    for i in range(n_guided):
        topic = topics[i % len(topics)]
        hook = guided_hooks[i % len(guided_hooks)] if guided_hooks else all_hooks[i % len(all_hooks)]
        ptype = post_types[i] if post_types and i < len(post_types) else "reach"
        plength = post_lengths[i] if post_lengths and i < len(post_lengths) else "medium"
        try:
            post = generate_post(topic=topic, hook_type=hook, length=plength)
            plan.posts.append({
                "text": post.text, "hook": hook,
                "source": "bootstrap_guided", "post_type": ptype,
            })
        except Exception as e:
            plan.skipped.append(f"Failed: {hook} x {topic}: {e}")

    for i in range(n_explore):
        idx = n_guided + i
        topic = topics[idx % len(topics)]
        hook = explore_hooks[i % len(explore_hooks)] if explore_hooks else all_hooks[i % len(all_hooks)]
        ptype = post_types[idx] if post_types and idx < len(post_types) else "reach"
        plength = post_lengths[idx] if post_lengths and idx < len(post_lengths) else "medium"
        try:
            post = generate_post(topic=topic, hook_type=hook, length=plength)
            plan.posts.append({
                "text": post.text, "hook": hook,
                "source": "bootstrap_explore", "post_type": ptype,
            })
        except Exception as e:
            plan.skipped.append(f"Failed: {hook} x {topic}: {e}")

    return plan


def _plan_learning(
    plan: DailyPlan,
    topics: list[str],
    hooks: list[str],
    history,
    count: int,
    fallback_hooks: list[dict] | None = None,
    boost_hooks: list[str] | None = None,
    post_types: list[str] | None = None,
    post_lengths: list[str] | None = None,
    ab_winning_hooks: list[str] | None = None,
) -> DailyPlan:
    """Learning phase: focus on top hooks, explore others, A/B test one pair."""
    from .content_generator import generate_post
    from .post_history import get_performance_summary

    summary = get_performance_summary(history)
    top_hooks_data = summary.get("top_hooks", [])
    top_hook_names = [h["hook"] for h in top_hooks_data[:5]]

    # If account has no top hooks yet, use fallback from genre/universal
    if not top_hook_names and fallback_hooks:
        top_hook_names = [h["hook"] for h in fallback_hooks[:5]]
        logger.info("Learning phase using fallback hooks: %s", top_hook_names)

    # Prioritize AB test winners at the front of top hooks
    if ab_winning_hooks:
        for wh in reversed(ab_winning_hooks):
            if wh in hooks:
                if wh in top_hook_names:
                    top_hook_names.remove(wh)
                top_hook_names.insert(0, wh)
        logger.info("AB winners prioritized: %s", top_hook_names[:3])

    # Merge velocity-boosted hooks into top selection
    if boost_hooks:
        for bh in boost_hooks:
            if bh not in top_hook_names and bh in hooks:
                top_hook_names.append(bh)
                if len(top_hook_names) >= 7:
                    break

    explore_hooks = [h for h in hooks if h not in top_hook_names]

    # Allocate: A/B pair (2) + top hooks + explore
    n_ab = 2 if count >= 4 and len(explore_hooks) >= 1 else 0
    remaining = count - n_ab
    n_top = max(1, int(remaining * 0.7))
    n_explore = remaining - n_top

    for i in range(n_top):
        topic = topics[i % len(topics)]
        hook = top_hook_names[i % len(top_hook_names)] if top_hook_names else hooks[i % len(hooks)]
        ptype = post_types[i] if post_types and i < len(post_types) else "reach"
        plength = post_lengths[i] if post_lengths and i < len(post_lengths) else "medium"
        tone = "フォロワー獲得重視。保存したくなるリスト形式・チェックリスト・ランキング・実用ノウハウを使い、「このアカウントをフォローすると得する」と思わせる構成にする" if ptype == "list" else ""
        try:
            post = generate_post(topic=topic, hook_type=hook, tone=tone, length=plength)
            plan.posts.append({
                "text": post.text, "hook": hook,
                "source": "learning_top", "post_type": ptype,
            })
        except Exception as e:
            plan.skipped.append(f"Failed: {hook} x {topic}: {e}")

    for i in range(n_explore):
        idx = n_top + i
        topic = topics[idx % len(topics)]
        hook = explore_hooks[i % len(explore_hooks)] if explore_hooks else hooks[idx % len(hooks)]
        ptype = post_types[idx] if post_types and idx < len(post_types) else "reach"
        plength = post_lengths[idx] if post_lengths and idx < len(post_lengths) else "medium"
        tone = "フォロワー獲得重視。保存したくなるリスト形式・チェックリスト・ランキング・実用ノウハウを使い、「このアカウントをフォローすると得する」と思わせる構成にする" if ptype == "list" else ""
        try:
            post = generate_post(topic=topic, hook_type=hook, tone=tone, length=plength)
            plan.posts.append({
                "text": post.text, "hook": hook,
                "source": "learning_explore", "post_type": ptype,
            })
        except Exception as e:
            plan.skipped.append(f"Failed: {hook} x {topic}: {e}")

    # A/B test pair
    if n_ab > 0:
        ab_hooks = []
        if top_hook_names:
            ab_hooks.append(top_hook_names[0])
        if explore_hooks:
            ab_hooks.append(explore_hooks[0])
        elif len(top_hook_names) > 1:
            ab_hooks.append(top_hook_names[1])
        if len(ab_hooks) >= 2:
            plan, added = _try_ab_test_pair(plan, topics, ab_hooks, count - 2, post_types)
            if added == 0:
                # Fallback: regular posts if A/B failed
                for i in range(n_ab):
                    idx = n_top + n_explore + i
                    topic = topics[idx % len(topics)]
                    hook = hooks[idx % len(hooks)]
                    try:
                        post = generate_post(topic=topic, hook_type=hook)
                        plan.posts.append({
                            "text": post.text, "hook": hook,
                            "source": "learning_explore", "post_type": "reach",
                        })
                    except Exception:
                        pass

    return plan


def _plan_optimized(
    plan: DailyPlan,
    topics: list[str],
    hooks: list[str],
    history,
    count: int,
    post_types: list[str] | None = None,
    post_lengths: list[str] | None = None,
) -> DailyPlan:
    """Optimized phase: template-based + recycle for proven winners."""
    from .content_generator import generate_from_template, generate_recycle
    from .content_recycler import find_recyclable_posts
    from .templates import generate_templates

    if post_types is None:
        post_types = ["reach"] * count

    templates = generate_templates(history, min_views=500)
    recyclable = find_recyclable_posts(history, min_views=1000, min_age_days=30)

    posts_added = 0

    # 1 recycled post if available
    if recyclable and posts_added < count:
        original = recyclable[0]
        try:
            post = generate_recycle(
                original_text=original.text,
                original_views=original.views,
            )
            plan.posts.append({
                "text": post.text,
                "hook": post.hook_type or "recycle",
                "source": "recycle",
                "post_type": post_types[posts_added] if posts_added < len(post_types) else "reach",
            })
            posts_added += 1
        except Exception as e:
            plan.skipped.append(f"Recycle failed: {e}")

    # Template-based posts
    for tmpl in templates:
        if posts_added >= count:
            break
        topic = topics[posts_added % len(topics)]
        pt = post_types[posts_added] if posts_added < len(post_types) else "reach"
        plength = post_lengths[posts_added] if post_lengths and posts_added < len(post_lengths) else None
        try:
            tone = "フォロワー獲得重視。保存したくなるリスト形式・チェックリスト・ランキング・実用ノウハウを使い、「このアカウントをフォローすると得する」と思わせる構成にする" if pt == "list" else ""
            # post_lengths指定があればそれを優先、なければテンプレートのbest_lengthを使用
            length_for_tmpl = plength if plength else tmpl.best_length_bucket
            post = generate_from_template(
                topic=topic,
                hook_type=tmpl.hook_type,
                example_posts=tmpl.example_posts,
                best_length=length_for_tmpl,
            )
            plan.posts.append({
                "text": post.text,
                "hook": tmpl.hook_type,
                "source": "template",
                "post_type": pt,
            })
            posts_added += 1
        except Exception as e:
            plan.skipped.append(f"Template {tmpl.hook_type} failed: {e}")

    # Fill remaining with regular generation
    from .content_generator import generate_post

    while posts_added < count:
        topic = topics[posts_added % len(topics)]
        hook = hooks[posts_added % len(hooks)]
        pt = post_types[posts_added] if posts_added < len(post_types) else "reach"
        plength = post_lengths[posts_added] if post_lengths and posts_added < len(post_lengths) else "medium"
        try:
            tone = "フォロワー獲得重視。保存したくなるリスト形式・チェックリスト・ランキング・実用ノウハウを使い、「このアカウントをフォローすると得する」と思わせる構成にする" if pt == "list" else ""
            post = generate_post(topic=topic, hook_type=hook, tone=tone, length=plength)
            plan.posts.append({
                "text": post.text,
                "hook": hook,
                "source": "optimized_generate",
                "post_type": pt,
            })
            posts_added += 1
        except Exception as e:
            plan.skipped.append(f"Generate failed: {e}")
            break

    return plan


def execute_plan(
    plan: DailyPlan,
    publish_method: str = "threads",
    profile: str | None = None,
) -> list[str]:
    """Execute a daily plan by scheduling all posts.

    Args:
        plan: The daily plan to execute.
        publish_method: "threads" (direct API) or "metricool" (via Metricool).
        profile: Profile name.

    Returns:
        List of result messages.
    """
    from datetime import datetime as dt

    from .models import PostDraft
    from .scheduler import get_optimal_schedule, schedule_posts_for_day

    results: list[str] = []
    target = dt.strptime(plan.date, "%Y-%m-%d")

    drafts = [PostDraft(text=p["text"]) for p in plan.posts]

    if not drafts:
        return ["No posts to schedule"]

    if publish_method == "metricool":
        try:
            from .api import MetricoolClient

            with MetricoolClient(profile=profile) as client:
                schedule = get_optimal_schedule(day_of_week=target.weekday(), profile=profile)
                responses = schedule_posts_for_day(client, drafts, target, schedule=schedule)
                for i, resp in enumerate(responses):
                    results.append(f"Scheduled #{i + 1}: {plan.posts[i]['hook']}")
        except Exception as e:
            results.append(f"Failed (Metricool): {e}")
    else:
        # Threads direct API — save plan for timed publishing
        try:
            from .timed_publisher import publish_due_posts, save_pending_plan

            schedule = get_optimal_schedule(day_of_week=target.weekday(), profile=profile)
            slots = [(s.hour, s.minute) for s in schedule.slots]

            save_pending_plan(plan.posts, slots, profile=profile or "default")

            for i, post in enumerate(plan.posts):
                if i < len(slots):
                    h, m = slots[i]
                    results.append(f"Queued #{i + 1}: {post['hook']} → {h:02d}:{m:02d}")
        except Exception as e:
            results.append(f"Failed (Threads): {e}")

    return results
