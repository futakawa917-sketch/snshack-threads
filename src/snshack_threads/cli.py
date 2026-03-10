"""CLI entry point for snshack-threads (Metricool-based)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__

# Global state for active profile (set via --profile callback)
_active_profile: str | None = None


def _profile_callback(value: Optional[str]) -> Optional[str]:
    global _active_profile
    if value:
        _active_profile = value
        from .config import set_runtime_profile
        set_runtime_profile(value)
    return value


app = typer.Typer(
    name="snshack",
    help="Threads automation & analytics via Metricool",
    no_args_is_help=True,
)
console = Console()

_DOW_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


@app.callback()
def main(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Profile name to use",
        callback=_profile_callback, is_eager=True,
    ),
):
    """Threads automation & analytics via Metricool."""


def _get_client():
    from .api import MetricoolClient

    return MetricoolClient(profile=_active_profile)


def _get_settings():
    from .config import get_settings

    return get_settings(profile=_active_profile)


# ── info ─────────────────────────────────────────────────────

@app.command()
def version():
    """Show version."""
    console.print(f"snshack-threads v{__version__}")


@app.command()
def brands():
    """List brands in your Metricool account."""
    with _get_client() as client:
        items = client.get_brands()

    for b in items:
        networks = ", ".join(n.get("network", "") for n in (b.networks or []))
        console.print(f"  [bold]{b.label}[/bold]  (blogId: {b.id})  [{networks}]")


# ── posts (analytics) ───────────────────────────────────────

@app.command()
def posts(
    days: int = typer.Option(30, help="Number of days to look back"),
):
    """List recent Threads posts with metrics."""
    end = datetime.now()
    start = end - timedelta(days=days)

    with _get_client() as client:
        items = client.get_threads_posts(
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )

    if not items:
        console.print("No posts found.")
        return

    table = Table(title=f"Threads Posts (last {days} days)")
    table.add_column("Date", style="dim")
    table.add_column("Text", max_width=50)
    table.add_column("Views", justify="right")
    table.add_column("Likes", justify="right")
    table.add_column("Replies", justify="right")
    table.add_column("Eng.", justify="right")

    for p in items:
        date_str = p.date.strftime("%m/%d %H:%M") if p.date else "-"
        text = (p.text or "")[:50]
        table.add_row(
            date_str, text,
            f"{p.views:,}", f"{p.likes:,}", f"{p.replies:,}",
            p.engagement_rate_pct,
        )

    console.print(table)


# ── scheduling ───────────────────────────────────────────────

@app.command()
def schedule(
    text: str = typer.Argument(help="Post text (max 500 chars)"),
    at: str = typer.Option(help="Publish time (YYYY-MM-DD HH:MM)"),
    add_cta: bool = typer.Option(False, "--cta", help="Append engagement CTA"),
    first_comment: str = typer.Option("", "--comment", help="First comment for engagement velocity"),
    post_type: str = typer.Option("reach", "--type", help="Post type: reach (バズ) or list (リスト獲得)"),
):
    """Schedule a single Threads post via Metricool."""
    from .content_guard import append_cta, check_ng
    from .models import PostDraft
    from .post_history import PostHistory

    # NG check
    violations = check_ng(text)
    if violations:
        console.print(f"[red]NG detected:[/red] {', '.join(violations)}")
        console.print("外部リンク/LINE/固定投稿への誘導はリーチが激減するため禁止です。")
        raise typer.Exit(1)

    if add_cta:
        text = append_cta(text)

    try:
        publish_at = datetime.strptime(at, "%Y-%m-%d %H:%M")
    except ValueError:
        console.print(f"[red]Invalid datetime format:[/red] {at}")
        console.print("Expected format: YYYY-MM-DD HH:MM (例: 2026-03-10 09:00)")
        raise typer.Exit(1)

    draft = PostDraft(text=text, first_comment=first_comment)

    with _get_client() as client:
        result = client.schedule_post(draft, publish_at)

    # Record in history for performance tracking
    PostHistory().record_scheduled(text=text, publish_at=publish_at, metricool_response=result, post_type=post_type)

    type_label = "reach" if post_type == "reach" else "list"
    console.print(f"[green]Scheduled![/green]  at {publish_at} [{type_label}]")
    console.print(f"  Response: {result}")


@app.command()
def schedule_day(
    date: str = typer.Argument(help="Target date (YYYY-MM-DD)"),
    texts: list[str] = typer.Option([], "--text", "-t", help="Post texts (up to 5)"),
    file: str | None = typer.Option(None, "--file", "-f", help="File with one post text per line"),
    add_cta: bool = typer.Option(False, "--cta", help="Append engagement CTA to each post"),
):
    """Schedule up to 5 posts at data-driven optimal times for the day.

    Time slots use day-of-week specific patterns from CSV analysis.
    All posts are validated against NG rules (no external links/LINE/etc).

    Examples:
      snshack schedule-day 2026-03-08 -t "朝の投稿" -t "昼の投稿" --cta
      snshack schedule-day 2026-03-08 -f posts.txt
    """
    from .content_guard import append_cta as _append_cta
    from .content_guard import check_ng
    from .models import PostDraft
    from .scheduler import ContentNGError, schedule_posts_for_day

    try:
        target = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        console.print(f"[red]Invalid date format:[/red] {date}")
        console.print("Expected format: YYYY-MM-DD (例: 2026-03-10)")
        raise typer.Exit(1)

    dow_name = _DOW_NAMES[target.weekday()]

    # Collect drafts from --text args or --file
    all_texts: list[str] = list(texts)
    if file:
        from pathlib import Path

        p = Path(file)
        if not p.exists():
            console.print(f"[red]File not found:[/red] {file}")
            raise typer.Exit(1)
        lines = p.read_text().strip().splitlines()
        all_texts.extend(line.strip() for line in lines if line.strip())

    if not all_texts:
        console.print("[red]No texts provided.[/red] Use --text or --file.")
        raise typer.Exit(1)

    if len(all_texts) > 5:
        console.print(f"[yellow]Warning:[/yellow] {len(all_texts)} texts provided, only first 5 will be scheduled.")
        all_texts = all_texts[:5]

    # NG check before anything else
    for i, t in enumerate(all_texts):
        violations = check_ng(t)
        if violations:
            console.print(f"[red]Post {i + 1} NG:[/red] {', '.join(violations)}")
            console.print(f"  Text: {t[:80]}...")
            console.print("外部リンク/LINE/固定投稿への誘導はリーチが激減するため禁止です。")
            raise typer.Exit(1)

    if add_cta:
        all_texts = [_append_cta(t) for t in all_texts]

    drafts = [PostDraft(text=t) for t in all_texts]

    try:
        with _get_client() as client:
            results = schedule_posts_for_day(client, drafts, target)
    except ContentNGError as e:
        console.print(f"[red]NG detected:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]Scheduled {len(results)} posts for {date} ({dow_name})![/green]")
    from .scheduler import get_optimal_schedule

    schedule = get_optimal_schedule(day_of_week=target.weekday())
    for i, _result in enumerate(results):
        slot = schedule.slots[i]
        console.print(f"  {slot.hour:02d}:{slot.minute:02d} - {all_texts[i][:60]}")


@app.command()
def schedule_thread(
    file: str = typer.Option(..., "--file", "-f", help="File with thread posts (one per line, --- separator)"),
    at: str = typer.Option(..., help="Publish time (YYYY-MM-DD HH:MM)"),
    first_comment: str = typer.Option("", "--comment", help="First comment for engagement boost"),
    add_cta: bool = typer.Option(False, "--cta", help="Append engagement CTA to last post"),
):
    """Schedule a thread chain (connected posts).

    Each post in the chain gets its own algorithmic distribution.
    Great for step-by-step guides and listicles.

    File format: one post per section, separated by '---':
      補助金申請の5ステップ
      ---
      ステップ1: 対象確認
      ---
      ステップ2: 書類準備

    Example:
      snshack schedule-thread -f thread.txt --at "2026-03-10 09:00" --comment "どのステップが一番大変でしたか？"
    """
    from pathlib import Path

    from .content_guard import append_cta, check_ng
    from .models import PostDraft, ThreadDraft
    from .post_history import PostHistory

    p = Path(file)
    if not p.exists():
        console.print(f"[red]File not found:[/red] {file}")
        raise typer.Exit(1)
    raw = p.read_text(encoding="utf-8")
    sections = [s.strip() for s in raw.split("---") if s.strip()]

    if len(sections) < 2:
        console.print("[red]Thread requires at least 2 posts.[/red] Separate with '---'")
        raise typer.Exit(1)

    if len(sections) > 10:
        console.print("[yellow]Warning:[/yellow] Max 10 posts per thread, truncating.")
        sections = sections[:10]

    # NG check all sections
    for i, text in enumerate(sections):
        violations = check_ng(text)
        if violations:
            console.print(f"[red]Post {i + 1} NG:[/red] {', '.join(violations)}")
            raise typer.Exit(1)

    if add_cta:
        sections[-1] = append_cta(sections[-1])

    drafts = [PostDraft(text=t) for t in sections]
    thread = ThreadDraft(posts=drafts, first_comment=first_comment)

    try:
        publish_at = datetime.strptime(at, "%Y-%m-%d %H:%M")
    except ValueError:
        console.print(f"[red]Invalid datetime format:[/red] {at}")
        console.print("Expected format: YYYY-MM-DD HH:MM (例: 2026-03-10 09:00)")
        raise typer.Exit(1)

    with _get_client() as client:
        result = client.schedule_thread(thread, publish_at)

    PostHistory().record_scheduled(
        text=f"[THREAD {len(sections)}posts] {sections[0][:80]}",
        publish_at=publish_at,
        metricool_response=result,
    )

    console.print(f"[green]Thread scheduled![/green] {len(sections)} posts at {publish_at}")
    for i, text in enumerate(sections):
        label = "Main" if i == 0 else f"  +{i}"
        console.print(f"  {label}: {text[:60]}")
    if first_comment:
        console.print(f"  [dim]First comment: {first_comment[:60]}[/dim]")


@app.command()
def recycle(
    top: int = typer.Option(5, help="Number of top posts to show"),
    min_age: int = typer.Option(30, help="Minimum age in days"),
):
    """Find top-performing posts eligible for recycling.

    Shows your best posts from 30+ days ago with suggested
    new hook patterns for repurposing.

    Example:
      snshack recycle            # show top 5 recyclable posts
      snshack recycle --top 10   # show top 10
    """
    from .content_recycler import find_recyclable_posts, suggest_recycle
    from .post_history import PostHistory

    history = PostHistory()
    candidates = find_recyclable_posts(history, min_age_days=min_age, top_n=top)

    if not candidates:
        console.print("[yellow]No recyclable posts found.[/yellow]")
        if history.count == 0:
            console.print("Schedule and collect post metrics first.")
        else:
            console.print(f"No collected posts older than {min_age} days.")
        return

    table = Table(title=f"Recyclable Posts (>{min_age} days old)")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Text", max_width=40)
    table.add_column("Views", justify="right")
    table.add_column("Likes", justify="right")
    table.add_column("Hooks Used")
    table.add_column("Suggested Hooks")

    for i, record in enumerate(candidates, 1):
        info = suggest_recycle(record)
        table.add_row(
            str(i),
            record.text[:40],
            f"{record.views:,}",
            f"{record.likes:,}",
            ", ".join(info["original_hooks"]) if info["original_hooks"] else "-",
            ", ".join(info["suggested_hooks"]),
        )

    console.print(table)
    console.print()
    console.print("[dim]Tip: Rewrite top posts with a different hook pattern for fresh reach.[/dim]")


@app.command()
def queue(
    days: int = typer.Option(7, help="Number of days ahead to check"),
):
    """Show scheduled (pending) posts in Metricool."""
    start = datetime.now()
    end = start + timedelta(days=days)

    with _get_client() as client:
        items = client.get_scheduled_posts(
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )

    if not items:
        console.print("No scheduled posts.")
        return

    table = Table(title="Scheduled Posts")
    table.add_column("Date", style="dim")
    table.add_column("Text", max_width=50)
    table.add_column("Networks")

    for item in items:
        pub_date = item.get("publicationDate", {})
        dt_str = pub_date.get("dateTime", "-")
        text = (item.get("text", "") or "")[:50]
        providers = ", ".join(
            p.get("network", "") for p in item.get("providers", [])
        )
        table.add_row(dt_str, text, providers)

    console.print(table)


@app.command()
def slots(
    date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD), defaults to today"
    ),
):
    """Show available time slots for a day (day-of-week optimized)."""
    from .scheduler import get_next_available_slots

    try:
        target = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now()
    except ValueError:
        console.print(f"[red]Invalid date format:[/red] {date}")
        console.print("Expected format: YYYY-MM-DD (例: 2026-03-10)")
        raise typer.Exit(1)

    dow_name = _DOW_NAMES[target.weekday()]

    with _get_client() as client:
        available = get_next_available_slots(client, target)

    if not available:
        console.print(f"No available slots for {target.strftime('%Y-%m-%d')} ({dow_name}).")
        return

    console.print(f"[bold]Available slots for {target.strftime('%Y-%m-%d')} ({dow_name}):[/bold]")
    for dt in available:
        console.print(f"  {dt.strftime('%H:%M')}")


@app.command()
def check_text(
    text: str = typer.Argument(help="Post text to validate"),
):
    """Check if post text passes NG rules."""
    from .content_guard import check_ng, suggest_cta

    violations = check_ng(text)
    if violations:
        console.print(f"[red]NG:[/red] {', '.join(violations)}")
        console.print("外部リンク/LINE/固定投稿への誘導はリーチが激減するため禁止です。")
    else:
        console.print("[green]OK[/green] - NG項目なし")

    console.print()
    console.print(f"[dim]CTA候補: {suggest_cta()}[/dim]")


@app.command()
def best_times(
    csv_file: str = typer.Option(None, "--csv", "-c", help="Path to CSV file"),
    top: int = typer.Option(10, help="Number of top hours to show"),
    day: str = typer.Option(None, "--day", "-d", help="Day of week (月火水木金土日)"),
):
    """Analyze CSV data and show optimal posting times."""
    from .csv_analyzer import _DOW_NAMES as dow_names
    from .csv_analyzer import analyze_optimal_times
    from .scheduler import _resolve_csv_path

    resolved = _resolve_csv_path(csv_file)
    if resolved is None:
        console.print("[red]CSV file not found.[/red] Set THREADS_CSV_PATH or use --csv.")
        raise typer.Exit(1)

    result = analyze_optimal_times(resolved)

    if result.total_posts == 0:
        console.print("[red]No data found in CSV.[/red]")
        return

    console.print(f"[bold]Optimal Posting Times[/bold] ({result.total_posts} posts analyzed)")
    console.print()

    # ── Hour-by-Hour table ──
    table = Table(title="Hour-by-Hour Performance")
    table.add_column("Hour", justify="center")
    table.add_column("Posts", justify="right")
    table.add_column("Avg Views", justify="right")
    table.add_column("Avg Likes", justify="right")
    table.add_column("Avg Eng.%", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Reliable", justify="center")

    active = [hs for hs in result.hour_stats.values() if hs.post_count > 0]
    active.sort(key=lambda h: h.score, reverse=True)

    for rank, hs in enumerate(active[:top], 1):
        style = "bold green" if rank <= 5 else ""
        reliable_mark = "o" if hs.reliable else "x"
        table.add_row(
            f"{hs.hour:02d}:00",
            str(hs.post_count),
            f"{hs.avg_views:,.0f}",
            f"{hs.avg_likes:,.1f}",
            f"{hs.avg_engagement:.2f}%",
            f"{hs.score:,.0f}",
            reliable_mark,
            style=style,
        )

    console.print(table)

    # ── Day-of-week breakdown ──
    if day:
        dow_idx = dow_names.index(day) if day in dow_names else None
        if dow_idx is not None:
            _show_day_breakdown(result, dow_idx, day)
    else:
        console.print()
        console.print("[bold]Day-of-Week Best Slots:[/bold]")
        for d in range(7):
            slots = result.get_optimal_slots_for_day(d, n=3)
            times = ", ".join(f"{h:02d}:{m:02d}" for h, m in slots)
            console.print(f"  {dow_names[d]}: {times}")

    # ── Recommended overall schedule ──
    optimal = result.get_optimal_slots(n=5)
    console.print()
    console.print("[bold]Recommended daily schedule (top 5):[/bold]")
    for h, m in optimal:
        console.print(f"  {h:02d}:{m:02d}")

    # ── Content insights ──
    _show_content_insights(result)


def _show_day_breakdown(result, dow_idx: int, day_name: str):
    """Show detailed hour breakdown for a specific day."""
    console.print()
    table = Table(title=f"{day_name}曜日 Hour Breakdown")
    table.add_column("Hour", justify="center")
    table.add_column("Posts", justify="right")
    table.add_column("Avg Views", justify="right")
    table.add_column("Score", justify="right")

    day_stats = [
        dh for (d, h), dh in result.day_hour_stats.items()
        if d == dow_idx and dh.post_count > 0
    ]
    day_stats.sort(key=lambda dh: dh.score, reverse=True)

    for dh in day_stats[:10]:
        table.add_row(
            f"{dh.hour:02d}:00",
            str(dh.post_count),
            f"{dh.avg_views:,.0f}",
            f"{dh.score:,.0f}",
        )

    console.print(table)


def _show_content_insights(result):
    """Show content pattern analysis."""
    cp = result.content
    console.print()
    console.print("[bold]Content Insights[/bold]")

    # Length buckets
    console.print()
    console.print("  [bold]Text Length vs Performance:[/bold]")
    for bucket_name, label in [("short", "<100字"), ("medium", "100-300字"), ("long", "300字+")]:
        bhs = cp.length_buckets.get(bucket_name)
        if bhs and bhs.post_count > 0:
            console.print(f"    {label}: {bhs.post_count}posts, avg {bhs.avg_views:,.0f} views, avg {bhs.avg_likes:,.1f} likes")

    # Hook patterns
    if cp.hook_patterns:
        console.print()
        console.print("  [bold]Hook Pattern Performance:[/bold]")
        sorted_hooks = sorted(cp.hook_patterns.items(), key=lambda x: x[1].avg_views, reverse=True)
        for hook_name, hhs in sorted_hooks:
            console.print(f"    {hook_name}: {hhs.post_count}posts, avg {hhs.avg_views:,.0f} views")

    # Link penalty
    if cp.link_post_avg_views > 0 and cp.no_link_post_avg_views > 0:
        penalty_pct = (1 - cp.link_post_avg_views / cp.no_link_post_avg_views) * 100
        console.print()
        console.print(f"  [bold]Link Penalty:[/bold] avg views {cp.link_post_avg_views:,.0f} (with link) vs {cp.no_link_post_avg_views:,.0f} (without)")
        if penalty_pct > 0:
            console.print(f"    [red]External links reduce reach by ~{penalty_pct:.0f}%[/red]")


# ── competitor research ───────────────────────────────────────

@app.command()
def research(
    keywords: list[str] = typer.Option([], "--keyword", "-k", help="Keywords to search (e.g. 補助金)"),
    max_results: int = typer.Option(50, help="Max posts per keyword"),
    top: int = typer.Option(5, help="Number of top posts to show"),
):
    """Research competitor posts by keyword via Threads API.

    Searches public Threads posts, analyzes content patterns,
    and compares with your own data. Fully automated.

    Uses RESEARCH_KEYWORDS from .env if no --keyword provided.

    Examples:
      snshack research -k 補助金 -k 助成金
      snshack research  # uses RESEARCH_KEYWORDS from .env
    """
    from .csv_analyzer import analyze_optimal_times
    from .research import research_genre
    from .scheduler import _resolve_csv_path
    from .threads_api import ThreadsAPIError, ThreadsGraphClient

    settings = _get_settings()
    all_keywords = list(keywords) or settings.get_research_keywords()

    if not all_keywords:
        console.print("[red]No keywords provided.[/red] Use --keyword or set RESEARCH_KEYWORDS in .env")
        raise typer.Exit(1)

    # Get own hook patterns for gap analysis
    own_hooks: set[str] = set()
    csv_path = _resolve_csv_path()
    if csv_path:
        own_data = analyze_optimal_times(csv_path)
        own_hooks = set(own_data.content.hook_patterns.keys())

    try:
        with ThreadsGraphClient() as client:
            reports = research_genre(
                client, all_keywords,
                max_per_keyword=max_results,
                own_hooks=own_hooks,
            )
    except ThreadsAPIError as e:
        console.print(f"[red]Threads API error:[/red] {e}")
        if "access_token" in str(e).lower() or e.status_code == 190:
            console.print("THREADS_ACCESS_TOKEN that's expired or missing. Check .env")
        raise typer.Exit(1)

    for report in reports:
        console.print()
        console.print(f"[bold]Keyword: {report.keyword}[/bold] ({report.total_posts_found} posts found)")

        if not report.posts:
            console.print("  No posts found for this keyword.")
            continue

        console.print(f"  Avg likes: {report.avg_likes:,.1f} | Avg replies: {report.avg_replies:,.1f}")
        console.print()

        # Top posts
        table = Table(title=f"Top {min(top, len(report.top_posts))} Posts")
        table.add_column("Text", max_width=50)
        table.add_column("Likes", justify="right")
        table.add_column("Replies", justify="right")
        table.add_column("Hooks")

        for p in report.top_posts[:top]:
            table.add_row(
                p.text[:50],
                f"{p.likes:,}",
                f"{p.replies:,}",
                ", ".join(p.hooks) if p.hooks else "-",
            )

        console.print(table)

        # Hook patterns from competitors
        if report.top_hooks:
            console.print()
            console.print("  [bold]Competitor Hook Patterns:[/bold]")
            for hook_name, count, avg_likes in report.top_hooks:
                console.print(f"    {hook_name}: {count} posts, avg {avg_likes:,.1f} likes")

        # Length performance
        if report.length_performance:
            console.print()
            console.print("  [bold]Text Length vs Likes (competitors):[/bold]")
            for bucket, label in [("short", "<100字"), ("medium", "100-300字"), ("long", "300字+")]:
                avg = report.length_performance.get(bucket, 0)
                if avg > 0:
                    console.print(f"    {label}: avg {avg:,.1f} likes")

        # Hook gaps
        if report.hook_gaps:
            console.print()
            console.print("  [bold red]Hook Gaps (competitors use, you don't):[/bold red]")
            for gap in report.hook_gaps:
                console.print(f"    - {gap}")


# ── post performance tracking ─────────────────────────────────

@app.command("collect-threads")
def collect_threads(
    min_age: int = typer.Option(24, help="Minimum hours since scheduled (default 24)"),
):
    """Collect metrics directly from Threads Graph API (no Metricool needed).

    Matches published posts with history records and pulls insights.
    Run after autopilot to gather performance data for learning loop.

    Example:
      snshack collect-threads           # collect for posts 24h+ old
      snshack collect-threads --min-age 6  # collect early metrics
    """
    from .post_history import PostHistory, collect_threads_metrics

    history = PostHistory()

    if history.count == 0:
        console.print("[yellow]投稿履歴がありません。[/yellow]")
        return

    pending = history.get_pending_collection(min_age_hours=min_age)
    if not pending:
        console.print("[green]全投稿のメトリクス収集済み。[/green]")
        return

    console.print(f"[dim]Threads APIからメトリクス収集中... ({len(pending)}件)[/dim]")
    updated = collect_threads_metrics(history, min_age_hours=min_age)

    if updated:
        console.print(f"[green]{len(updated)}件のメトリクスを取得:[/green]")
        for r in updated:
            console.print(f"  {r.views:>6,} views | {r.likes:>3} likes | {r.text[:40]}...")
    else:
        console.print("[yellow]マッチする投稿が見つかりませんでした。[/yellow]")

    # Show remaining uncollected
    still_pending = history.get_pending_collection(min_age_hours=min_age)
    if still_pending:
        console.print(f"[dim]未収集: {len(still_pending)}件[/dim]")


@app.command()
def performance():
    """Show performance summary and learning insights.

    Analyzes all collected post data to show:
    - Top-performing hooks
    - Best posting times
    - Optimal post lengths
    - Milestone progress (toward 100 posts)
    """
    from .post_history import PostHistory, get_performance_summary

    history = PostHistory()
    perf = get_performance_summary(history)

    if perf.get("collected", 0) == 0:
        console.print("[yellow]パフォーマンスデータがまだありません。[/yellow]")
        console.print("[dim]投稿してから24時間後に `snshack collect-threads` を実行してください。[/dim]")
        return

    # Milestone progress
    total = perf["total_posts"]
    collected = perf["collected"]
    progress_bar = "█" * min(total, 100) + "░" * max(0, 100 - total)
    console.print(f"\n[bold]📊 マイルストーン進捗: {total}/100投稿[/bold]")
    console.print(f"  [{progress_bar[:50]}] {total}%")
    console.print(f"  平均views: {perf['avg_views']:,.0f} | 平均likes: {perf['avg_likes']:.1f} | 最高views: {perf['max_views']:,}")

    # Top hooks
    if perf.get("top_hooks"):
        console.print(f"\n[bold]🎣 トップフック (効果が高い順):[/bold]")
        table = Table()
        table.add_column("Hook", style="bold")
        table.add_column("平均Views", justify="right")
        table.add_column("平均Likes", justify="right")
        table.add_column("使用回数", justify="right")
        for h in perf["top_hooks"][:7]:
            table.add_row(h["hook"], f"{h['avg_views']:,.0f}", f"{h['avg_likes']:.1f}", str(h["count"]))
        console.print(table)

    # Best times
    if perf.get("best_times"):
        console.print(f"\n[bold]⏰ ベスト投稿時間:[/bold]")
        for t in perf["best_times"][:5]:
            bar = "█" * min(20, int(t["avg_views"] / max(perf["avg_views"], 1) * 10))
            console.print(f"  {t['hour']:02d}:00 — {t['avg_views']:,.0f} views ({t['count']}回) {bar}")

    # Length performance
    if perf.get("length_performance"):
        console.print(f"\n[bold]📏 投稿長さ別パフォーマンス:[/bold]")
        labels = {"short": "短文 (<100文字)", "medium": "中文 (100-300文字)", "long": "長文 (300文字+)"}
        for bucket, stats in perf["length_performance"].items():
            console.print(f"  {labels.get(bucket, bucket)}: {stats['avg_views']:,.0f} views, {stats['avg_likes']:.1f} likes ({stats['count']}回)")


@app.command()
def collect(
    min_age: int = typer.Option(24, help="Minimum hours since scheduled (default 24)"),
):
    """Collect performance data (views, likes) for published posts.

    Fetches metrics from Metricool API first, then falls back to
    Threads Graph API insights for any remaining uncollected posts.

    Example:
      snshack collect           # collect for posts 24h+ old
      snshack collect --min-age 48  # only posts 48h+ old
    """
    from .post_history import PostHistory, collect_performance

    history = PostHistory()

    if history.count == 0:
        console.print("[yellow]No posts in history yet.[/yellow] Schedule posts first.")
        return

    pending = history.get_pending_collection(min_age_hours=min_age)
    if not pending:
        console.print("[green]All posts already collected.[/green] No pending posts.")
        return

    console.print(f"Collecting metrics for {len(pending)} posts...")

    # Primary: Metricool API
    with _get_client() as client:
        updated = collect_performance(history, client, min_age_hours=min_age)

    if updated:
        console.print(f"[green]Updated {len(updated)} posts via Metricool:[/green]")
        for r in updated:
            console.print(f"  {r.views:>6,} views | {r.likes:>3} likes | {r.text[:40]}...")

    # Fallback: Threads Graph API insights for remaining uncollected
    still_pending = history.get_pending_collection(min_age_hours=min_age)
    if still_pending:
        settings = _get_settings()
        if settings.threads_access_token:
            console.print(f"[dim]Trying Threads API for {len(still_pending)} remaining posts...[/dim]")
            try:
                from .threads_api import ThreadsGraphClient
                with ThreadsGraphClient() as threads_client:
                    my_posts = threads_client.get_my_posts(limit=50)
                    fallback_count = 0
                    for record in still_pending:
                        for tp in my_posts:
                            tp_text = (tp.get("text") or "").strip()
                            if tp_text and record.text.strip() == tp_text:
                                # Get detailed insights
                                post_id = tp.get("id", "")
                                if post_id:
                                    try:
                                        insights = threads_client.get_post_insights(post_id)
                                        history.update_metrics(
                                            record,
                                            views=insights.get("views", 0),
                                            likes=insights.get("likes", tp.get("like_count", 0)),
                                            replies=insights.get("replies", tp.get("reply_count", 0)),
                                            reposts=insights.get("reposts", tp.get("repost_count", 0)),
                                            quotes=insights.get("quotes", tp.get("quote_count", 0)),
                                            engagement=0.0,
                                        )
                                        fallback_count += 1
                                    except Exception:
                                        pass
                                break
                    if fallback_count:
                        console.print(f"[green]Updated {fallback_count} posts via Threads API.[/green]")
            except Exception as e:
                console.print(f"[dim]Threads API fallback skipped: {e}[/dim]")
        else:
            console.print(f"[yellow]{len(still_pending)} posts unmatched.[/yellow] Set THREADS_ACCESS_TOKEN for fallback.")

    final_pending = history.get_pending_collection(min_age_hours=min_age)
    if final_pending:
        console.print(f"[yellow]{len(final_pending)} posts still uncollected.[/yellow]")


@app.command()
def review(
    days: int = typer.Option(30, help="Number of days to look back"),
    sort_by: str = typer.Option("date", help="Sort by: date, views, likes, replies, engagement"),
    post_type: str = typer.Option(None, "--type", help="Filter by type: reach or list"),
):
    """Review post performance with type-based KPIs.

    Shows all tracked posts with performance metrics.
    reach posts are evaluated by views/saves, list posts by replies/DMs.
    Use 'snshack collect' first to fetch latest metrics.

    Example:
      snshack review                    # all posts, last 30 days
      snshack review --type reach       # only reach (バズ) posts
      snshack review --type list        # only list (リスト獲得) posts
      snshack review --sort-by replies  # sort by replies (list KPI)
    """
    from .post_history import PostHistory

    history = PostHistory()
    if post_type:
        records = history.get_by_type(post_type, days=days)
    else:
        records = history.get_recent(days=days)

    if not records:
        console.print("[yellow]No posts found.[/yellow]")
        if history.count == 0:
            console.print("Schedule posts with 'snshack schedule-day' to start tracking.")
        return

    # Sort
    sort_keys = {
        "views": lambda r: r.views,
        "likes": lambda r: r.likes,
        "replies": lambda r: r.replies,
        "engagement": lambda r: r.engagement,
    }
    records.sort(
        key=sort_keys.get(sort_by, lambda r: r.scheduled_at),
        reverse=True,
    )

    type_label = f" [{post_type}]" if post_type else ""
    table = Table(title=f"Post Performance (last {days} days){type_label}")
    table.add_column("Date", style="dim")
    table.add_column("Type", justify="center")
    table.add_column("Chars", justify="right")
    table.add_column("Text", max_width=35)
    table.add_column("Views", justify="right")
    table.add_column("Likes", justify="right")
    table.add_column("Replies", justify="right")
    table.add_column("Eng.%", justify="right")

    total_views = 0
    total_likes = 0
    total_replies = 0
    collected_count = 0
    reach_collected: list = []
    list_collected: list = []

    for r in records:
        try:
            dt = datetime.fromisoformat(r.scheduled_at)
            date_str = dt.strftime("%m/%d %H:%M")
        except ValueError:
            date_str = "-"

        type_icon = "[cyan]R[/cyan]" if r.post_type == "reach" else "[magenta]L[/magenta]"
        eng_str = f"{r.engagement * 100:.1f}%" if r.engagement else "-"

        table.add_row(
            date_str,
            type_icon,
            str(r.char_count),
            r.text[:35],
            f"{r.views:,}" if r.has_metrics else "-",
            f"{r.likes:,}" if r.has_metrics else "-",
            f"{r.replies:,}" if r.has_metrics else "-",
            eng_str,
        )

        if r.has_metrics:
            total_views += r.views
            total_likes += r.likes
            total_replies += r.replies
            collected_count += 1
            if r.post_type == "reach":
                reach_collected.append(r)
            else:
                list_collected.append(r)

    console.print(table)

    # Type-based KPI summary
    if collected_count > 0:
        console.print()
        console.print(f"[bold]Summary:[/bold] {collected_count}/{len(records)} posts collected")
        console.print(f"  Total views: {total_views:,} | Avg: {total_views // collected_count:,}/post")

        if reach_collected:
            r_views = sum(r.views for r in reach_collected)
            r_avg = r_views // len(reach_collected)
            console.print(
                f"  [cyan]Reach ({len(reach_collected)} posts):[/cyan] "
                f"avg {r_avg:,} views/post — "
                f"KPI: views (目標: 6,000+/post)"
            )

        if list_collected:
            l_replies = sum(r.replies for r in list_collected)
            l_avg = l_replies / len(list_collected)
            console.print(
                f"  [magenta]List ({len(list_collected)} posts):[/magenta] "
                f"avg {l_avg:.1f} replies/post — "
                f"KPI: replies/DM (目標: 5+/post)"
            )

    uncollected = len(records) - collected_count
    if uncollected > 0:
        console.print()
        console.print(f"[yellow]{uncollected} posts still pending.[/yellow] Run 'snshack collect' to fetch metrics.")


@app.command()
def refresh_csv(
    csv_file: str = typer.Option(None, "--csv", "-c", help="Path to new CSV file"),
):
    """Refresh analytics by importing updated CSV data.

    Re-analyzes your Threads CSV export to update optimal posting times.
    Export your latest CSV from Threads analytics and provide the path.

    Steps:
      1. Go to Threads > Professional Dashboard > Export Data
      2. Download the CSV file
      3. Run: snshack refresh-csv --csv /path/to/new-export.csv
    """
    from .csv_analyzer import analyze_optimal_times
    from .scheduler import _resolve_csv_path

    resolved = _resolve_csv_path(csv_file)
    if resolved is None:
        console.print("[red]CSV file not found.[/red]")
        console.print("Export your latest data from Threads and provide the path:")
        console.print("  snshack refresh-csv --csv /path/to/スレッズ.csv")
        raise typer.Exit(1)

    result = analyze_optimal_times(resolved)

    console.print(f"[green]CSV loaded:[/green] {result.total_posts} posts analyzed from {resolved.name}")
    console.print()

    # Show updated optimal times
    optimal = result.get_optimal_slots(n=5)
    console.print("[bold]Updated optimal posting times:[/bold]")
    for h, m in optimal:
        hs = result.hour_stats[h]
        console.print(f"  {h:02d}:{m:02d}  ({hs.post_count} posts, avg {hs.avg_views:,.0f} views)")

    # Show day-of-week breakdown
    console.print()
    console.print("[bold]Day-of-week best slots:[/bold]")
    for d in range(7):
        slots = result.get_optimal_slots_for_day(d, n=3)
        times = ", ".join(f"{h:02d}:{m:02d}" for h, m in slots)
        console.print(f"  {_DOW_NAMES[d]}: {times}")

    # Content insights summary
    cp = result.content
    if cp.no_link_post_avg_views > 0:
        console.print()
        console.print(f"[bold]Avg views:[/bold] {cp.no_link_post_avg_views:,.0f} (without links)")
        if cp.link_post_avg_views > 0:
            penalty = (1 - cp.link_post_avg_views / cp.no_link_post_avg_views) * 100
            if penalty > 0:
                console.print(f"  [red]Link penalty: -{penalty:.0f}% views[/red]")


# ── analytics ────────────────────────────────────────────────

@app.command()
def analytics(
    days: int = typer.Option(30, help="Number of days to analyse"),
    top: int = typer.Option(5, help="Number of top posts to show"),
):
    """Show analytics report for Threads posts."""
    from .analytics import generate_report

    end = datetime.now()
    start = end - timedelta(days=days)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    with _get_client() as client:
        report = generate_report(client, start_str, end_str, top_n=top)

    if report.total_posts == 0:
        console.print("No posts found.")
        return

    console.print("[bold]Threads Analytics Report[/bold]")
    console.print(f"  Period:         {report.period_start} ~ {report.period_end}")
    console.print(f"  Posts:          {report.total_posts}")
    console.print(f"  Total views:    {report.total_views:,}")
    console.print(f"  Total likes:    {report.total_likes:,}")
    console.print(f"  Total replies:  {report.total_replies:,}")
    console.print(f"  Total reposts:  {report.total_reposts:,}")
    console.print(f"  Total quotes:   {report.total_quotes:,}")
    console.print(f"  Avg engagement: {report.avg_engagement_rate_pct}")
    if report.followers_count:
        delta = f"+{report.delta_followers}" if report.delta_followers >= 0 else str(report.delta_followers)
        console.print(f"  Followers:      {report.followers_count:,} ({delta})")
    console.print()

    # Virality metrics
    console.print("[bold]Virality Metrics[/bold]")
    console.print(f"  Virality rate:      {report.virality_rate * 100:.2f}% (reposts+quotes / views)")
    console.print(f"  Discussion rate:    {report.discussion_rate:.2f} (replies / likes)")
    console.print(f"  Amplification rate: {report.amplification_rate:.2f} (reposts / likes)")
    if report.virality_rate > 0.02:
        console.print("  [green]Virality rate > 2% — content is spreading well[/green]")
    elif report.virality_rate > 0:
        console.print("  [yellow]Virality rate < 2% — focus on share-worthy content[/yellow]")
    console.print()

    if report.top_posts:
        table = Table(title=f"Top {len(report.top_posts)} Posts by Interactions")
        table.add_column("Date", style="dim")
        table.add_column("Text", max_width=40)
        table.add_column("Views", justify="right")
        table.add_column("Likes", justify="right")
        table.add_column("Replies", justify="right")
        table.add_column("Eng.", justify="right")

        for p in report.top_posts:
            date_str = p.date.strftime("%m/%d") if p.date else "-"
            text = (p.text or "")[:40]
            table.add_row(
                date_str, text,
                f"{p.views:,}", f"{p.likes:,}", f"{p.replies:,}",
                p.engagement_rate_pct,
            )

        console.print(table)


# ── early velocity tracking ──────────────────────────────────


@app.command()
def collect_early(
    max_age: int = typer.Option(6, help="Max hours since scheduled (default 6)"),
):
    """Collect early velocity metrics (1-6h after posting).

    Captures snapshots of views/likes/replies shortly after posting.
    Run 1h and 3h after posting to track initial velocity.

    Example:
      snshack collect-early
    """
    from .post_history import PostHistory, collect_performance

    history = PostHistory()
    if history.count == 0:
        console.print("[yellow]No posts in history yet.[/yellow]")
        return

    eligible = history.get_early_collection(max_age_hours=max_age)
    if not eligible:
        console.print("[green]No posts eligible for early collection (1-6h old).[/green]")
        return

    console.print(f"Collecting early metrics for {len(eligible)} posts...")

    with _get_client() as client:
        collect_performance(history, client, min_age_hours=1)

    snapshots_added = 0
    for record in eligible:
        if record.views > 0 or record.likes > 0:
            history.add_snapshot(record, views=record.views, likes=record.likes, replies=record.replies)
            snapshots_added += 1

    if snapshots_added > 0:
        console.print(f"[green]Added {snapshots_added} velocity snapshots:[/green]")
        for r in eligible:
            if r.snapshots:
                s = r.snapshots[-1]
                console.print(f"  {s.elapsed_hours}h: {s.views:,} views, {s.likes} likes | {r.text[:40]}...")
    else:
        console.print("[yellow]No metrics available yet.[/yellow] Posts may be too new.")


@app.command()
def velocity(
    days: int = typer.Option(30, help="Number of days to look back"),
):
    """Show early velocity data for tracked posts.

    Displays 1h/3h/final metrics to identify which posts
    gained traction fastest.
    """
    from .post_history import PostHistory

    history = PostHistory()
    records = [r for r in history.get_recent(days=days) if r.snapshots and r.has_metrics]

    if not records:
        console.print("[yellow]No posts with velocity data.[/yellow]")
        console.print("Run 'snshack collect-early' 1-3h after posting.")
        return

    table = Table(title="Post Velocity (early -> final)")
    table.add_column("Text", max_width=30)
    table.add_column("Type", justify="center")
    table.add_column("Chars", justify="right")
    table.add_column("1h Views", justify="right")
    table.add_column("3h Views", justify="right")
    table.add_column("Final", justify="right")
    table.add_column("Speed", justify="center")

    for r in sorted(records, key=lambda x: x.views, reverse=True):
        snap_1h = next((s for s in r.snapshots if s.elapsed_hours <= 1), None)
        snap_3h = next((s for s in r.snapshots if 2 <= s.elapsed_hours <= 4), None)
        v1 = f"{snap_1h.views:,}" if snap_1h else "-"
        v3 = f"{snap_3h.views:,}" if snap_3h else "-"
        type_icon = "[cyan]R[/cyan]" if r.post_type == "reach" else "[magenta]L[/magenta]"

        if snap_1h and r.views > 0:
            ratio = snap_1h.views / r.views
            speed = "[green]fast[/green]" if ratio > 0.3 else ("[yellow]mid[/yellow]" if ratio > 0.15 else "[red]slow[/red]")
        else:
            speed = "-"

        table.add_row(r.text[:30], type_icon, str(r.char_count), v1, v3, f"{r.views:,}", speed)

    console.print(table)


# ── follower tracking ────────────────────────────────────────


@app.command()
def track_followers():
    """Record today's follower count with post attribution.

    Run daily to build follower growth data for correlation analysis.
    """
    from .csv_analyzer import _detect_hooks
    from .follower_tracker import FollowerTracker
    from .post_history import PostHistory

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    with _get_client() as client:
        account = client.get_threads_account_metrics(yesterday, today)

    history = PostHistory()
    today_posts = [r for r in history.get_all() if r.has_metrics and r.scheduled_at.startswith(today)]
    top_text, top_views, top_hooks = "", 0, []
    if today_posts:
        best = max(today_posts, key=lambda r: r.views)
        top_text, top_views = best.text[:80], best.views
        top_hooks = _detect_hooks(best.text)

    tracker = FollowerTracker()
    snapshot = tracker.record_snapshot(
        date=today, followers_count=account.followers_count, delta=account.delta_followers,
        top_post_text=top_text, top_post_views=top_views, top_post_hooks=top_hooks,
    )

    delta_str = f"+{snapshot.delta}" if snapshot.delta >= 0 else str(snapshot.delta)
    console.print(f"[bold]Followers:[/bold] {snapshot.followers_count:,} ({delta_str})")
    if top_text:
        console.print(f"  Top post: {top_views:,} views | {top_text[:50]}...")
    console.print(f"[green]Recorded for {today}[/green]")


@app.command()
def follower_report(
    days: int = typer.Option(30, help="Number of days to look back"),
    threshold: int = typer.Option(3000, help="Views threshold for correlation"),
):
    """Show follower growth report with post attribution."""
    from .follower_tracker import FollowerTracker

    tracker = FollowerTracker()
    snapshots = tracker.get_recent(days=days)

    if not snapshots:
        console.print("[yellow]No follower data.[/yellow] Run 'snshack track-followers' daily.")
        return

    table = Table(title=f"Follower Growth (last {days} days)")
    table.add_column("Date", style="dim")
    table.add_column("Followers", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Top Views", justify="right")
    table.add_column("Hook")
    table.add_column("Text", max_width=30)

    for s in snapshots:
        delta_style = "green" if s.delta > 0 else ("red" if s.delta < 0 else "dim")
        delta_str = f"+{s.delta}" if s.delta >= 0 else str(s.delta)
        hooks = ", ".join(s.top_post_hooks) if s.top_post_hooks else "-"
        table.add_row(
            s.date, f"{s.followers_count:,}",
            f"[{delta_style}]{delta_str}[/{delta_style}]",
            f"{s.top_post_views:,}" if s.top_post_views else "-",
            hooks, s.top_post_text[:30] if s.top_post_text else "-",
        )

    console.print(table)

    correlation = tracker.analyze_correlation(views_threshold=threshold)
    if correlation:
        console.print()
        console.print(f"[bold]Views <-> Follower Correlation (threshold: {threshold:,})[/bold]")
        console.print(f"  >= {threshold:,} views: avg +{correlation.avg_delta_above:.1f}/day ({correlation.days_above} days)")
        console.print(f"  <  {threshold:,} views: avg +{correlation.avg_delta_below:.1f}/day ({correlation.days_below} days)")
        if correlation.lift > 1:
            console.print(f"  [green]Viral posts drive {correlation.lift:.1f}x more followers[/green]")


# ── profile audit ────────────────────────────────────────────


@app.command()
def audit_profile():
    """Audit your Threads profile for list acquisition optimization."""
    from .post_history import PostHistory
    from .profile_audit import audit_profile as _audit
    from .threads_api import ThreadsAPIError, ThreadsGraphClient

    try:
        with ThreadsGraphClient() as client:
            profile = client.get_user_profile("me")
    except ThreadsAPIError as e:
        console.print(f"[red]Failed to fetch profile:[/red] {e}")
        raise typer.Exit(1)

    result = _audit(profile, history=PostHistory())

    console.print(f"[bold]Profile Audit: {result.score}/100[/bold]")
    console.print()
    for check in result.checks:
        icon = "[green]o[/green]" if check.passed else "[red]x[/red]"
        console.print(f"  {icon} {check.name}: {check.detail}")
        if not check.passed and check.recommendation:
            console.print(f"    [dim]-> {check.recommendation}[/dim]")


# ── templates ────────────────────────────────────────────────


@app.command()
def templates(
    top: int = typer.Option(5, help="Number of templates to show"),
):
    """Show winning content templates derived from your data."""
    from .post_history import PostHistory
    from .templates import generate_templates

    tmpls = generate_templates(PostHistory(), top_examples=3)

    if not tmpls:
        console.print("[yellow]Not enough data.[/yellow] Run 'snshack collect' first.")
        return

    length_labels = {"short": "<100字", "medium": "100-300字", "long": "300字+"}
    console.print(f"[bold]Winning Content Templates[/bold] ({len(tmpls)} hook types)")
    console.print()
    for i, t in enumerate(tmpls[:top], 1):
        console.print(
            f"  [bold]{i}. {t.hook_type}[/bold] "
            f"({t.post_count} posts, avg {t.avg_views:,.0f} views, "
            f"best: {length_labels.get(t.best_length_bucket, t.best_length_bucket)})"
        )
        console.print(f"    [dim]{t.structure_hint}[/dim]")
        if t.example_posts:
            console.print(f"    [dim]Ex: {t.example_posts[0][:60]}...[/dim]")
        console.print()


@app.command()
def draft(
    hook: str = typer.Argument(help="Hook type (数字訴求, 疑問形, 危機感, etc.)"),
    topic: str = typer.Argument(help="Topic for the post"),
):
    """Generate a draft outline using a winning template.

    Example:
      snshack draft 数字訴求 "2026年の補助金"
    """
    from .templates import generate_draft_outline

    outline = generate_draft_outline(hook, topic)
    console.print(f"[bold]Draft: {hook} x {topic}[/bold]")
    console.print()
    console.print(outline)
    console.print()
    console.print(f"[dim]{len(outline)} chars[/dim]")


# ── A/B testing ──────────────────────────────────────────────


@app.command()
def ab_create(
    theme: str = typer.Option(..., help="Test theme"),
    a: str = typer.Option(..., help="Variant A text"),
    b: str = typer.Option(..., help="Variant B text"),
    at_a: str = typer.Option("", "--at-a", help="Schedule A (YYYY-MM-DD HH:MM)"),
    at_b: str = typer.Option("", "--at-b", help="Schedule B (YYYY-MM-DD HH:MM)"),
):
    """Create an A/B test with two post variants.

    Example:
      snshack ab-create --theme "補助金" \\
        --a "知らないと損する補助金3選" \\
        --b "補助金申請したい人いますか？" \\
        --at-a "2026-03-10 09:00" --at-b "2026-03-17 09:00"
    """
    from .ab_test import ABTestManager
    from .content_guard import check_ng
    from .csv_analyzer import _detect_hooks
    from .models import PostDraft
    from .post_history import PostHistory

    for label, text in [("A", a), ("B", b)]:
        violations = check_ng(text)
        if violations:
            console.print(f"[red]Variant {label} NG:[/red] {', '.join(violations)}")
            raise typer.Exit(1)

    manager = ABTestManager()
    scheduled_a, scheduled_b = "", ""

    if at_a and at_b:
        try:
            pub_a = datetime.strptime(at_a, "%Y-%m-%d %H:%M")
            pub_b = datetime.strptime(at_b, "%Y-%m-%d %H:%M")
        except ValueError:
            console.print("[red]Invalid datetime.[/red] Use YYYY-MM-DD HH:MM")
            raise typer.Exit(1)

        with _get_client() as client:
            client.schedule_post(PostDraft(text=a), pub_a)
            client.schedule_post(PostDraft(text=b), pub_b)

        history = PostHistory()
        history.record_scheduled(text=a, publish_at=pub_a, post_type="reach")
        history.record_scheduled(text=b, publish_at=pub_b, post_type="reach")
        scheduled_a, scheduled_b = pub_a.isoformat(), pub_b.isoformat()

    test = manager.create_test(
        theme=theme, variant_a_text=a, variant_b_text=b,
        variant_a_scheduled_at=scheduled_a, variant_b_scheduled_at=scheduled_b,
    )

    console.print(f"[green]A/B Test created![/green]  ID: {test.test_id}")
    console.print(f"  A ({', '.join(_detect_hooks(a)) or '-'}): {a[:50]}")
    console.print(f"  B ({', '.join(_detect_hooks(b)) or '-'}): {b[:50]}")


@app.command()
def ab_result(
    test_id: str = typer.Argument(default=None, help="Test ID (omit for all)"),
):
    """Show A/B test results. Run 'snshack collect' first."""
    from .ab_test import ABTestManager, determine_winner
    from .csv_analyzer import _detect_hooks
    from .post_history import PostHistory

    manager = ABTestManager()
    if manager.count == 0:
        console.print("[yellow]No A/B tests.[/yellow] Create one with 'snshack ab-create'")
        return

    tests = [manager.get_test(test_id)] if test_id else manager.get_all()
    tests = [t for t in tests if t is not None]

    if not tests:
        console.print(f"[red]Test '{test_id}' not found.[/red]")
        return

    history = PostHistory()
    all_records = history.get_all()

    for test in tests:
        if test.status != "completed":
            for record in all_records:
                if record.has_metrics and record.text.strip() == test.variant_a_text.strip():
                    test.a_views, test.a_likes, test.a_replies = record.views, record.likes, record.replies
                    test.a_engagement = record.engagement
                elif record.has_metrics and record.text.strip() == test.variant_b_text.strip():
                    test.b_views, test.b_likes, test.b_replies = record.views, record.likes, record.replies
                    test.b_engagement = record.engagement
            if test.a_views > 0 or test.b_views > 0:
                determine_winner(test)
                manager._save()

        console.print()
        console.print(f"[bold]{test.test_id}[/bold] ({test.theme}) [{test.status}]")

        table = Table()
        table.add_column("", justify="center")
        table.add_column("Text", max_width=35)
        table.add_column("Chars", justify="right")
        table.add_column("Hook")
        table.add_column("Views", justify="right")
        table.add_column("Replies", justify="right")

        for label, text, views, replies in [
            ("A", test.variant_a_text, test.a_views, test.a_replies),
            ("B", test.variant_b_text, test.b_views, test.b_replies),
        ]:
            style = "bold green" if test.winner == label else ""
            hooks = ", ".join(_detect_hooks(text)) or "-"
            table.add_row(label, text[:35], str(len(text)), hooks, f"{views:,}", f"{replies:,}", style=style)

        console.print(table)

        if test.winner == "draw":
            console.print("  [yellow]Draw[/yellow]")
        elif test.winner:
            console.print(f"  [green]Winner: {test.winner}[/green] ({test.confidence})")


# ── Threads direct publishing (Meta API) ──────────────────

threads_app = typer.Typer(help="Direct Threads publishing via Meta Graph API")
app.add_typer(threads_app, name="threads")


@threads_app.command("publish")
def threads_publish(
    text: str = typer.Argument(help="Post text"),
    image_url: str = typer.Option("", "--image", help="Public image URL (optional)"),
    reply_control: str = typer.Option("everyone", "--reply", help="Reply control: everyone, accounts_you_follow, mentioned_only"),
    cta: bool = typer.Option(False, "--cta", help="Auto-append CTA"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without publishing"),
):
    """Publish a post directly via Threads API (not Metricool)."""
    from .content_guard import append_cta, check_ng
    from .threads_api import ThreadsGraphClient

    # NG check
    issues = check_ng(text)
    if issues:
        console.print("[red]NG detected:[/red]")
        for issue in issues:
            console.print(f"  - {issue}")
        raise typer.Exit(1)

    if cta:
        text = append_cta(text)

    if dry_run:
        console.print("[bold]Preview:[/bold]")
        console.print(text)
        console.print(f"\n[dim]{len(text)}chars | reply: {reply_control}[/dim]")
        return

    with ThreadsGraphClient() as client:
        if image_url:
            post_id = client.create_image_post(text, image_url, reply_control)
        else:
            post_id = client.create_text_post(text, reply_control)

    console.print(f"[green]Published![/green]  Post ID: {post_id}")


@threads_app.command("publish-file")
def threads_publish_file(
    file_path: str = typer.Argument(help="File with post texts (one per section, separated by ---)"),
    interval: int = typer.Option(5, "--interval", help="Seconds between posts"),
    cta: bool = typer.Option(False, "--cta", help="Auto-append CTA"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without publishing"),
):
    """Publish multiple posts from a file via Threads API."""
    import time as time_mod

    from .content_guard import append_cta, check_ng
    from .threads_api import ThreadsGraphClient

    content = Path(file_path).read_text(encoding="utf-8")
    posts = [p.strip() for p in content.split("---") if p.strip()]

    if not posts:
        console.print("[red]No posts found in file.[/red]")
        raise typer.Exit(1)

    console.print(f"Found {len(posts)} post(s)")

    for i, text in enumerate(posts, 1):
        issues = check_ng(text)
        if issues:
            console.print(f"[red]Post {i}: NG detected - {', '.join(issues)}[/red]")
            continue

        if cta:
            text = append_cta(text)

        if dry_run:
            console.print(f"\n[bold]--- Post {i} ---[/bold]")
            console.print(text)
            console.print(f"[dim]{len(text)}chars[/dim]")
            continue

        with ThreadsGraphClient() as client:
            post_id = client.create_text_post(text)
            console.print(f"[green]Post {i} published:[/green] {post_id}")

        if i < len(posts):
            time_mod.sleep(interval)


@threads_app.command("my-posts")
def threads_my_posts(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of posts to show"),
):
    """List your recent Threads posts (via Meta API)."""
    from .threads_api import ThreadsGraphClient

    with ThreadsGraphClient() as client:
        posts = client.get_my_posts(limit=limit)

    if not posts:
        console.print("[yellow]No posts found.[/yellow]")
        return

    table = Table(title="Your Recent Threads Posts")
    table.add_column("Date")
    table.add_column("Text", max_width=50)
    table.add_column("Likes", justify="right")
    table.add_column("Replies", justify="right")

    for p in posts:
        ts = p.get("timestamp", "")[:10]
        text = (p.get("text", "") or "")[:50]
        table.add_row(
            ts,
            text,
            str(p.get("like_count", 0)),
            str(p.get("reply_count", 0)),
        )
    console.print(table)


@threads_app.command("import-posts")
def threads_import_posts(
    limit: int = typer.Option(100, "--limit", "-n", help="Max posts to import"),
):
    """Import existing Threads posts into history with metrics.

    Pulls all your published posts from Threads API, adds them to
    post_history with full insights data. This bootstraps the learning
    loop with real performance data.

    Example:
      snshack threads import-posts        # import up to 100 posts
      snshack threads import-posts -n 50   # import up to 50 posts
    """
    from .post_history import PostHistory
    from .threads_api import ThreadsGraphClient

    history = PostHistory()
    existing_texts = {r.text.strip() for r in history.get_all() if r.text.strip()}

    console.print(f"[dim]Threads APIから投稿をインポート中...[/dim]")

    imported = 0
    skipped = 0

    with ThreadsGraphClient() as client:
        posts = client.get_my_posts(limit=limit)
        console.print(f"  取得: {len(posts)}件")

        for p in posts:
            text = (p.get("text") or "").strip()
            if not text:
                continue

            # Skip if already in history
            if text in existing_texts:
                skipped += 1
                continue

            # Get timestamp
            ts = p.get("timestamp", "")
            if ts:
                try:
                    pub_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    pub_time = datetime.now()
            else:
                pub_time = datetime.now()

            # Record in history
            record = history.record_scheduled(text=text, publish_at=pub_time)

            # Get insights
            post_id = p.get("id", "")
            if post_id:
                try:
                    insights = client.get_post_insights(post_id)
                    views = insights.get("views", 0)
                    likes = insights.get("likes", p.get("like_count", 0))
                    replies = insights.get("replies", p.get("reply_count", 0))
                    reposts = insights.get("reposts", p.get("repost_count", 0))
                    quotes = insights.get("quotes", p.get("quote_count", 0))
                    total = likes + replies + reposts + quotes
                    engagement = (total / views * 100) if views > 0 else 0.0

                    history.update_metrics(
                        record, views=views, likes=likes,
                        replies=replies, reposts=reposts,
                        quotes=quotes, engagement=engagement,
                    )
                except Exception as e:
                    console.print(f"  [dim]insights取得失敗: {e}[/dim]")

            existing_texts.add(text)
            imported += 1

    console.print(f"[green]インポート完了: {imported}件[/green]")
    if skipped:
        console.print(f"  [dim]スキップ（重複）: {skipped}件[/dim]")

    # Show performance summary after import
    if imported > 0:
        console.print()
        from .post_history import get_performance_summary
        perf = get_performance_summary(history)
        if perf.get("collected", 0) > 0:
            console.print(f"  総投稿数: {perf['total_posts']} | 収集済み: {perf['collected']}")
            console.print(f"  平均views: {perf['avg_views']:,.0f} | 最高views: {perf['max_views']:,}")


@threads_app.command("insights")
def threads_insights(
    post_id: str = typer.Argument(help="Post ID to get insights for"),
):
    """Get detailed insights for a specific post."""
    from .threads_api import ThreadsGraphClient

    with ThreadsGraphClient() as client:
        insights = client.get_post_insights(post_id)

    if not insights:
        console.print("[yellow]No insights available.[/yellow]")
        return

    console.print(f"[bold]Post: {post_id}[/bold]")
    for metric, value in insights.items():
        console.print(f"  {metric}: {value:,}")


@threads_app.command("me")
def threads_me():
    """Show your Threads profile info."""
    from .threads_api import ThreadsGraphClient

    with ThreadsGraphClient() as client:
        me = client.get_me()

    console.print(f"[bold]@{me.get('username', '?')}[/bold]  {me.get('name', '')}")
    console.print(f"  ID: {me.get('id', '?')}")
    bio = me.get("threads_biography", "")
    if bio:
        console.print(f"  Bio: {bio}")


# ── rate limit status ─────────────────────────────────────

@app.command("rate-limit")
def rate_limit_status():
    """Show Threads API rate limit usage."""
    from .threads_api import RateLimiter

    limiter = RateLimiter()
    usage = limiter.get_usage_summary()

    console.print("[bold]Threads API Rate Limits[/bold]")

    for name, data in usage.items():
        used = data["used"]
        limit = data["limit"]
        remaining = data["remaining"]
        pct = (used / limit * 100) if limit else 0

        color = "green" if pct < 50 else "yellow" if pct < 80 else "red"
        window = "7 days" if name == "search" else "24 hours"

        console.print(
            f"  {name}: [{color}]{used}/{limit}[/{color}] "
            f"(remaining: {remaining}, window: {window})"
        )


# ── competitor watch ───────────────────────────────────────

competitor_app = typer.Typer(help="Competitor account watching & research")
app.add_typer(competitor_app, name="competitor")


@competitor_app.command("add")
def competitor_add(
    username: str = typer.Argument(help="Threads username (without @)"),
    display_name: str = typer.Option("", "--name", help="Display name"),
    notes: str = typer.Option("", "--notes", help="Notes (e.g. 'direct competitor')"),
):
    """Add a competitor account to watch."""
    from .research_store import ResearchStore

    store = ResearchStore()
    try:
        account = store.add_competitor(username, display_name, notes)
        console.print(f"[green]Added competitor: @{account.username}[/green]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@competitor_app.command("remove")
def competitor_remove(
    username: str = typer.Argument(help="Username to remove"),
):
    """Remove a competitor from watch list."""
    from .research_store import ResearchStore

    store = ResearchStore()
    if store.remove_competitor(username):
        console.print(f"[green]Removed @{username}[/green]")
    else:
        console.print(f"[red]@{username} not found.[/red]")


@competitor_app.command("list")
def competitor_list_cmd():
    """List watched competitor accounts."""
    from .research_store import ResearchStore

    store = ResearchStore()
    competitors = store.list_competitors()

    if not competitors:
        console.print("[yellow]No competitors registered.[/yellow] Use 'snshack competitor add'")
        return

    table = Table(title="Watched Competitors")
    table.add_column("Username")
    table.add_column("Name")
    table.add_column("Notes")
    table.add_column("Added")

    for c in competitors:
        table.add_row(
            f"@{c.username}",
            c.display_name or "-",
            c.notes or "-",
            c.added_at[:10] if c.added_at else "-",
        )
    console.print(table)


@competitor_app.command("scrape")
def competitor_scrape(
    username: str = typer.Argument(default=None, help="Username (omit for all watched)"),
    max_posts: int = typer.Option(20, "--max-posts", help="Max posts to scrape"),
    no_headless: bool = typer.Option(False, "--no-headless", help="Show browser window"),
):
    """Scrape competitor profiles via browser (Playwright)."""
    from .browser_scraper import scrape_profile
    from .csv_analyzer import _detect_hooks
    from .research_store import CompetitorSnapshot, ResearchStore

    store = ResearchStore()

    if username:
        usernames = [username]
    else:
        competitors = store.list_competitors()
        if not competitors:
            console.print("[yellow]No competitors registered.[/yellow]")
            return
        usernames = [c.username for c in competitors]

    for uname in usernames:
        console.print(f"[dim]Scraping @{uname}...[/dim]")
        try:
            profile = scrape_profile(uname, max_posts=max_posts, headless=not no_headless)
        except ImportError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        console.print(f"  [bold]@{profile.username}[/bold]  {profile.display_name}")
        console.print(f"  {profile.followers_text}")
        console.print(f"  Posts found: {len(profile.posts)}")

        if profile.posts:
            # Analyze hooks
            all_hooks: list[str] = []
            for p in profile.posts:
                all_hooks.extend(_detect_hooks(p.text))

            avg_likes = sum(p.likes for p in profile.posts) / len(profile.posts)
            avg_replies = sum(p.replies for p in profile.posts) / len(profile.posts)

            # Save snapshot
            snapshot = CompetitorSnapshot(
                username=uname,
                timestamp=profile.scraped_at,
                posts=[
                    {"text": p.text[:200], "likes": p.likes, "replies": p.replies}
                    for p in profile.posts[:10]
                ],
                post_count=len(profile.posts),
                avg_likes=avg_likes,
                avg_replies=avg_replies,
                top_hooks=list(set(all_hooks)),
            )
            store.save_competitor_snapshot(snapshot)

            # Show top posts
            top = sorted(profile.posts, key=lambda p: p.likes, reverse=True)[:5]
            table = Table(title=f"@{uname} Top Posts")
            table.add_column("Text", max_width=50)
            table.add_column("Likes", justify="right")
            table.add_column("Replies", justify="right")
            table.add_column("Hooks")

            for p in top:
                hooks = ", ".join(_detect_hooks(p.text)) or "-"
                table.add_row(p.text[:50], str(p.likes), str(p.replies), hooks)

            console.print(table)
        console.print()


@competitor_app.command("history")
def competitor_history(
    username: str = typer.Argument(help="Username to show history for"),
    days: int = typer.Option(90, "--days", help="Days to look back"),
):
    """Show competitor trend over time."""
    from .research_store import ResearchStore

    store = ResearchStore()
    snapshots = store.get_competitor_history(username, days=days)

    if not snapshots:
        console.print(f"[yellow]No data for @{username}.[/yellow] Run 'snshack competitor scrape' first.")
        return

    table = Table(title=f"@{username} Trend ({len(snapshots)} snapshots)")
    table.add_column("Date")
    table.add_column("Posts", justify="right")
    table.add_column("Avg Likes", justify="right")
    table.add_column("Avg Replies", justify="right")
    table.add_column("Hooks")

    for s in snapshots:
        table.add_row(
            s.timestamp[:10],
            str(s.post_count),
            f"{s.avg_likes:.1f}",
            f"{s.avg_replies:.1f}",
            ", ".join(s.top_hooks[:3]) or "-",
        )
    console.print(table)


# ── research with persistence ─────────────────────────────

@app.command("research-save")
def research_save(
    keywords: list[str] = typer.Option([], "--keyword", "-k", help="Keywords to search"),
    max_results: int = typer.Option(50, "--max", help="Max results per keyword"),
):
    """Run keyword research and save results for trend tracking."""
    from .csv_analyzer import analyze_optimal_times
    from .research import search_and_analyze
    from .research_store import ResearchSnapshot, ResearchStore
    from .scheduler import _resolve_csv_path
    from .threads_api import ThreadsAPIError, ThreadsGraphClient

    settings = _get_settings()
    all_keywords = list(keywords) or settings.get_research_keywords()

    if not all_keywords:
        console.print("[red]No keywords.[/red] Use --keyword or set RESEARCH_KEYWORDS")
        raise typer.Exit(1)

    # Get own hooks for gap analysis
    own_hooks: set[str] = set()
    csv_path = _resolve_csv_path(settings)
    if csv_path:
        try:
            result = analyze_optimal_times(csv_path)
            own_hooks = set(result.content.hook_patterns.keys())
        except Exception:
            pass

    store = ResearchStore()

    try:
        with ThreadsGraphClient() as client:
            for kw in all_keywords:
                console.print(f"[dim]Searching: {kw}...[/dim]")
                report = search_and_analyze(client, kw, max_results=max_results, own_hooks=own_hooks)

                snapshot = ResearchSnapshot(
                    keyword=kw,
                    timestamp=datetime.now().isoformat(),
                    total_posts=report.total_posts_found,
                    avg_likes=report.avg_likes,
                    avg_replies=report.avg_replies,
                    avg_engagement=report.avg_engagement,
                    top_hooks=[
                        {"name": name, "count": count, "avg_likes": avg}
                        for name, count, avg in report.top_hooks[:5]
                    ],
                    top_posts=[
                        {"text": p.text[:200], "likes": p.likes, "replies": p.replies}
                        for p in report.top_posts[:5]
                    ],
                    hook_gaps=report.hook_gaps,
                )
                store.save_research_snapshot(snapshot)

                console.print(f"  Posts: {report.total_posts_found}  Avg likes: {report.avg_likes:.1f}")
                if report.hook_gaps:
                    console.print(f"  [yellow]Hook gaps:[/yellow] {', '.join(report.hook_gaps)}")

    except ThreadsAPIError as e:
        console.print(f"[red]API error: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[green]Saved {len(all_keywords)} keyword snapshot(s).[/green]")


@app.command("research-trend")
def research_trend(
    keyword: str = typer.Argument(help="Keyword to show trend for"),
    days: int = typer.Option(90, "--days", help="Days to look back"),
):
    """Show keyword research trend over time."""
    from .research_store import ResearchStore

    store = ResearchStore()
    trend = store.get_keyword_trend(keyword, days=days)

    if trend.get("snapshots", 0) == 0:
        console.print(f"[yellow]No data for '{keyword}'.[/yellow] Run 'snshack research-save' first.")
        return

    console.print(f"[bold]Keyword: {keyword}[/bold]  ({trend['snapshots']} snapshots)")
    console.print(f"  Latest avg likes: {trend['latest_avg_likes']:.1f}")
    console.print(f"  Latest avg engagement: {trend['latest_avg_engagement']:.1f}")

    if trend.get("trend_likes"):
        table = Table(title="Likes Trend")
        table.add_column("Date")
        table.add_column("Avg Likes", justify="right")

        for entry in trend["trend_likes"]:
            table.add_row(entry["date"], f"{entry['avg_likes']:.1f}")
        console.print(table)

    if trend.get("trending_hooks"):
        console.print("\n[bold]Trending hooks:[/bold]")
        for h in trend["trending_hooks"]:
            console.print(f"  {h.get('name', '?')} (count: {h.get('count', 0)}, avg likes: {h.get('avg_likes', 0):.1f})")


# ── autopilot (daily auto-generation + scheduling) ────────

@app.command("autopilot")
def autopilot_cmd(
    topics: list[str] = typer.Option([], "--topic", "-t", help="Topics to generate about"),
    date: str = typer.Option("", "--date", help="Target date YYYY-MM-DD (default: today)"),
    method: str = typer.Option("threads", "--method", "-m", help="Publish method: threads or metricool"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Generate plan without publishing"),
    posts_per_day: int = typer.Option(5, "--count", "-n", help="Posts per day"),
):
    """Auto-generate and schedule today's 5 posts (fully automated).

    Determines strategy based on data maturity:
      - bootstrap (<70 posts): Rotate all hooks evenly
      - learning (70-150): Focus top hooks, explore others
      - optimized (150+): Template + recycle, concentrate on winners

    Examples:
      snshack autopilot -t "業界テーマ1" -t "テーマ2"
      snshack autopilot --dry-run
      snshack autopilot --method metricool --date 2026-03-10
    """
    from .autopilot import execute_plan, generate_daily_plan

    # Load profile hooks
    _load_profile_hooks()

    target = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now()

    if not topics:
        settings = _get_settings()
        topics = settings.get_research_keywords()
        if not topics:
            console.print("[red]No topics specified.[/red] Use --topic or set RESEARCH_KEYWORDS")
            raise typer.Exit(1)

    console.print(f"[dim]Generating plan for {target.strftime('%Y-%m-%d')}...[/dim]")

    plan = generate_daily_plan(
        topics=topics,
        profile=_active_profile,
        target_date=target,
        posts_per_day=posts_per_day,
    )

    # Show plan
    phase_labels = {"bootstrap": "立ち上げ期", "learning": "学習期", "optimized": "最適化期"}
    console.print(f"\n[bold]Phase: {phase_labels.get(plan.phase, plan.phase)}[/bold] ({plan.total_posts} collected posts)")

    if not plan.posts:
        console.print("[red]No posts generated.[/red]")
        if plan.skipped:
            for skip in plan.skipped:
                console.print(f"  [yellow]Skip: {skip}[/yellow]")
        raise typer.Exit(1)

    table = Table(title=f"Daily Plan: {plan.date} ({len(plan.posts)} posts)")
    table.add_column("#", justify="right")
    table.add_column("Hook")
    table.add_column("Source")
    table.add_column("Text", max_width=50)
    table.add_column("Chars", justify="right")

    for i, post in enumerate(plan.posts, 1):
        table.add_row(
            str(i),
            post["hook"],
            post["source"],
            post["text"][:50],
            str(len(post["text"])),
        )
    console.print(table)

    if plan.skipped:
        console.print(f"\n[yellow]Skipped: {len(plan.skipped)}[/yellow]")
        for skip in plan.skipped:
            console.print(f"  - {skip}")

    if dry_run:
        console.print("\n[dim]Dry run — nothing published.[/dim]")
        # Show full texts
        for i, post in enumerate(plan.posts, 1):
            console.print(f"\n[bold]--- Post {i} ({post['hook']}) ---[/bold]")
            console.print(post["text"])
        return

    # Execute
    console.print(f"\n[dim]Publishing via {method}...[/dim]")
    results = execute_plan(plan, publish_method=method, profile=_active_profile)

    for r in results:
        if "Failed" in r:
            console.print(f"  [red]{r}[/red]")
        else:
            console.print(f"  [green]{r}[/green]")

    console.print(f"\n[green]Autopilot complete: {len(plan.posts)} posts.[/green]")


# ── hooks (industry customization) ────────────────────────

hooks_app = typer.Typer(help="Hook pattern management (industry customization)")
app.add_typer(hooks_app, name="hooks")


@hooks_app.command("industries")
def hooks_industries():
    """List available industry presets."""
    from .csv_analyzer import INDUSTRY_HOOK_PRESETS, list_industries

    for industry in list_industries():
        patterns = INDUSTRY_HOOK_PRESETS[industry]
        names = ", ".join(name for name, _ in patterns)
        console.print(f"  [bold]{industry}[/bold]: {names}")


@hooks_app.command("set")
def hooks_set(
    industry: str = typer.Option("", "--industry", "-i", help="Industry preset name"),
):
    """Set industry-specific hook patterns for the active profile."""
    import json

    from .config import _profile_config_path, _read_active_profile
    from .csv_analyzer import INDUSTRY_HOOK_PRESETS

    if industry and industry not in INDUSTRY_HOOK_PRESETS:
        console.print(f"[red]Unknown industry: {industry}[/red]")
        console.print("Available: " + ", ".join(INDUSTRY_HOOK_PRESETS.keys()))
        raise typer.Exit(1)

    target = _active_profile or _read_active_profile()
    config_path = _profile_config_path(target)

    if not config_path.exists():
        console.print(f"[red]Profile '{target}' not found.[/red]")
        raise typer.Exit(1)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["industry"] = industry
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    console.print(f"[green]Profile '{target}' industry set to '{industry}'[/green]")
    patterns = INDUSTRY_HOOK_PRESETS.get(industry, [])
    for name, regex in patterns:
        console.print(f"  + {name}: {regex}")


@hooks_app.command("show")
def hooks_show():
    """Show currently active hook patterns."""
    from .csv_analyzer import get_active_hooks

    # Load profile industry if set
    _load_profile_hooks()

    patterns = get_active_hooks()
    console.print(f"[bold]Active hook patterns ({len(patterns)}):[/bold]")
    for name, pat in patterns:
        console.print(f"  {name}: {pat.pattern}")


def _load_profile_hooks() -> None:
    """Load industry hooks from the active profile config."""
    import json

    from .config import _profile_config_path, _read_active_profile
    from .csv_analyzer import load_custom_hooks

    target = _active_profile or _read_active_profile()
    config_path = _profile_config_path(target)

    if not config_path.exists():
        return

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    industry = config.get("industry", "")
    custom_hooks = config.get("custom_hooks", {})

    if industry or custom_hooks:
        load_custom_hooks(industry=industry or None, custom_hooks=custom_hooks or None)


# ── Auto Research ──────────────────────────────────────────

@app.command("auto-research")
def auto_research_cmd(
    max_competitors: int = typer.Option(10, help="Max competitors to auto-register"),
):
    """Run automated competitor research (keyword search + auto-register)."""
    from .auto_research import run_auto_research

    console.print("[bold]Running auto-research...[/bold]")
    report = run_auto_research(profile=_active_profile, max_competitors=max_competitors)

    # If keyword search failed, fall back to self-analysis
    if report.errors and report.total_posts_found == 0:
        console.print("[yellow]キーワード検索が利用不可 — 自己分析モードで実行[/yellow]")
        from .auto_research import run_self_analysis
        report = run_self_analysis(profile=_active_profile)
        if report.trending_hooks:
            console.print(f"  自己分析: {report.total_posts_found}投稿を分析")

    console.print(f"  Keywords searched: {len(report.keywords_searched)}")
    console.print(f"  Posts found: {report.total_posts_found}")
    console.print(f"  Accounts discovered: {len(report.discovered_accounts)}")

    if report.auto_registered:
        console.print(f"  [green]Auto-registered: {', '.join('@' + u for u in report.auto_registered)}[/green]")

    if report.trending_hooks:
        console.print("\n  [bold]Trending hooks:[/bold]")
        for h in report.trending_hooks[:5]:
            console.print(f"    {h['hook']}: avg {h['avg_likes']:.0f} likes ({h['count']} posts)")

    if report.errors:
        for e in report.errors:
            console.print(f"  [yellow]{e}[/yellow]")


# ── CSV sync ───────────────────────────────────────────────

@app.command("sync-csv")
def sync_csv_cmd(
    days: int = typer.Option(90, help="Number of days to sync"),
    output: str = typer.Option("", "--output", "-o", help="Output CSV path (default: auto)"),
):
    """Fetch posts from Metricool and generate CSV (replaces manual export)."""
    from .csv_sync import sync_csv

    out_path = output or None
    with _get_client() as client:
        result = sync_csv(client, days=days, output_path=out_path, profile=_active_profile)

    console.print(f"[green]CSV synced:[/green] {result}")


# ── AI content generation ──────────────────────────────────

ai_app = typer.Typer(help="AI-powered content generation (Claude)")
app.add_typer(ai_app, name="ai")


@ai_app.command("generate")
def ai_generate(
    topic: str = typer.Argument(help="Topic for the post"),
    hook: str = typer.Option("", "--hook", "-h", help="Hook pattern (e.g. 数字訴求, 疑問形)"),
    tone: str = typer.Option("", "--tone", help="Tone of voice"),
    count: int = typer.Option(1, "--count", "-n", help="Number of posts to generate"),
):
    """Generate post drafts using AI."""
    from .content_generator import generate_post

    for i in range(count):
        post = generate_post(topic=topic, hook_type=hook, tone=tone)
        if count > 1:
            console.print(f"\n[bold]--- Draft {i + 1} ---[/bold]")
        console.print()
        console.print(post.text)
        console.print(f"\n[dim]Hook: {post.hook_type or 'auto'} | {len(post.text)}chars[/dim]")


@ai_app.command("from-template")
def ai_from_template(
    topic: str = typer.Argument(help="Topic for the post"),
    hook: str = typer.Option("", "--hook", "-h", help="Hook pattern to use"),
    min_views: int = typer.Option(1000, "--min-views", help="Min views for template examples"),
):
    """Generate a post based on your best-performing templates."""
    from .content_generator import generate_from_template
    from .post_history import PostHistory
    from .templates import generate_templates

    history = PostHistory()
    templates = generate_templates(history, min_views=min_views)

    if not templates:
        console.print("[yellow]No templates available.[/yellow] Need more posts with collected metrics.")
        return

    # Find matching template or use best one
    target = None
    if hook:
        target = next((t for t in templates if t.hook_type == hook), None)
    if target is None:
        target = templates[0]
        console.print(f"[dim]Using best template: {target.hook_type} (avg {target.avg_views:,.0f} views)[/dim]")

    post = generate_from_template(
        topic=topic,
        hook_type=target.hook_type,
        example_posts=target.example_posts,
        best_length=target.best_length_bucket,
    )

    console.print()
    console.print(post.text)
    console.print(f"\n[dim]Hook: {post.hook_type} | Template: {target.post_count} posts avg {target.avg_views:,.0f} views[/dim]")


@ai_app.command("recycle")
def ai_recycle(
    new_hook: str = typer.Option("", "--hook", "-h", help="New hook pattern"),
    min_views: int = typer.Option(1000, "--min-views", help="Min views for recyclable posts"),
    index: int = typer.Option(0, "--index", "-i", help="Which recyclable post to use (0=best)"),
):
    """Rewrite a high-performing post with a different hook."""
    from .content_generator import generate_recycle
    from .content_recycler import find_recyclable_posts
    from .post_history import PostHistory

    history = PostHistory()
    recyclable = find_recyclable_posts(history, min_views=min_views)

    if not recyclable:
        console.print("[yellow]No recyclable posts found.[/yellow]")
        return

    if index >= len(recyclable):
        index = 0

    original = recyclable[index]
    console.print(f"[dim]Recycling: {original.text[:60]}... ({original.views:,} views)[/dim]")

    post = generate_recycle(
        original_text=original.text,
        new_hook_type=new_hook,
        original_views=original.views,
    )

    console.print()
    console.print(post.text)
    console.print(f"\n[dim]New hook: {post.hook_type or 'auto'} | {len(post.text)}chars[/dim]")


@ai_app.command("ab")
def ai_ab(
    topic: str = typer.Argument(help="Shared topic"),
    hook_a: str = typer.Option("数字訴求", "--hook-a", help="Hook for variant A"),
    hook_b: str = typer.Option("疑問形", "--hook-b", help="Hook for variant B"),
):
    """Generate two A/B test variants with different hooks."""
    from .content_generator import generate_ab_variants

    a, b = generate_ab_variants(topic, hook_a, hook_b)

    console.print("\n[bold]Variant A[/bold]  [dim]({hook_a})[/dim]")
    console.print(a.text)

    console.print(f"\n[bold]Variant B[/bold]  [dim]({hook_b})[/dim]")
    console.print(b.text)

    console.print(f"\n[dim]A: {len(a.text)}chars | B: {len(b.text)}chars[/dim]")


@ai_app.command("batch")
def ai_batch(
    topics: list[str] = typer.Option([], "--topic", "-t", help="Topics (one per post)"),
    hooks: list[str] = typer.Option([], "--hook", "-h", help="Hook patterns to cycle through"),
    tone: str = typer.Option("", "--tone", help="Tone of voice"),
):
    """Generate multiple posts at once."""
    from .content_generator import generate_batch

    if not topics:
        console.print("[red]At least one --topic is required.[/red]")
        raise typer.Exit(1)

    posts = generate_batch(topics, hook_types=hooks or None, tone=tone)

    for i, post in enumerate(posts, 1):
        console.print(f"\n[bold]--- {i}. {post.topic} ({post.hook_type or 'auto'}) ---[/bold]")
        console.print(post.text)
        console.print(f"[dim]{len(post.text)}chars[/dim]")


# ── profile management ─────────────────────────────────────

profile_app = typer.Typer(help="Manage client profiles")
app.add_typer(profile_app, name="profile")


@profile_app.command("list")
def profile_list():
    """List all profiles."""
    from .config import _read_active_profile, list_profiles

    profiles = list_profiles()
    active = _read_active_profile()

    if not profiles:
        console.print("[yellow]No profiles yet.[/yellow] Create one with 'snshack profile create'")
        return

    for name in profiles:
        marker = " [green]*[/green]" if name == active else ""
        console.print(f"  {name}{marker}")


@profile_app.command("create")
def profile_create(
    name: str = typer.Argument(help="Profile name (e.g. client-a)"),
    user_token: str = typer.Option("", "--token", help="Metricool API token"),
    user_id: str = typer.Option("", "--user-id", help="Metricool user ID"),
    blog_id: str = typer.Option("", "--blog-id", help="Metricool blog ID"),
    timezone: str = typer.Option("Asia/Tokyo", "--tz", help="Timezone"),
    csv_path: str = typer.Option("", "--csv", help="Path to Threads CSV"),
    threads_token: str = typer.Option("", "--threads-token", help="Threads Graph API token"),
    keywords: str = typer.Option("", "--keywords", help="Research keywords (comma-separated)"),
):
    """Create a new client profile."""
    from .config import create_profile

    try:
        pdir = create_profile(
            name,
            user_token=user_token,
            user_id=user_id,
            blog_id=blog_id,
            timezone=timezone,
            csv_path=csv_path,
            threads_access_token=threads_token,
            research_keywords=keywords,
        )
        console.print(f"[green]Profile '{name}' created.[/green]  {pdir}")
    except FileExistsError:
        console.print(f"[red]Profile '{name}' already exists.[/red]")
        raise typer.Exit(1)


@profile_app.command("switch")
def profile_switch(
    name: str = typer.Argument(help="Profile name to activate"),
):
    """Switch active profile."""
    from .config import switch_profile

    try:
        switch_profile(name)
        console.print(f"[green]Switched to '{name}'[/green]")
    except FileNotFoundError:
        console.print(f"[red]Profile '{name}' not found.[/red]")
        raise typer.Exit(1)


@profile_app.command("show")
def profile_show(
    name: str = typer.Argument(default=None, help="Profile name (default: active)"),
):
    """Show profile settings."""
    from .config import _read_active_profile, get_settings

    target = name or _active_profile or _read_active_profile()
    settings = get_settings(profile=target)

    console.print(f"[bold]Profile: {settings.profile_name}[/bold]")
    console.print(f"  Data dir : {settings.data_dir}")
    console.print(f"  Token    : {'***' + settings.user_token[-4:] if len(settings.user_token) > 4 else '(not set)'}")
    console.print(f"  User ID  : {settings.user_id or '(not set)'}")
    console.print(f"  Blog ID  : {settings.blog_id or '(not set)'}")
    console.print(f"  Timezone : {settings.timezone}")
    console.print(f"  CSV      : {settings.csv_path or '(not set)'}")
    console.print(f"  Threads  : {'set' if settings.threads_access_token else '(not set)'}")
    console.print(f"  Keywords : {settings.research_keywords or '(not set)'}")


@profile_app.command("delete")
def profile_delete(
    name: str = typer.Argument(help="Profile name to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a profile and all its data."""
    from .config import delete_profile

    if not force:
        confirm = typer.confirm(f"Delete profile '{name}' and all its data?")
        if not confirm:
            raise typer.Abort()

    try:
        delete_profile(name)
        console.print(f"[green]Profile '{name}' deleted.[/green]")
    except FileNotFoundError:
        console.print(f"[red]Profile '{name}' not found.[/red]")
        raise typer.Exit(1)


@profile_app.command("migrate")
def profile_migrate(
    name: str = typer.Option("default", "--name", "-n", help="Profile name for migrated settings"),
):
    """Migrate .env settings to a profile."""
    from .config import migrate_env_to_profile

    pdir = migrate_env_to_profile(name)
    if pdir:
        console.print(f"[green]Migrated .env to profile '{name}'[/green]  {pdir}")
    else:
        console.print("[yellow]Nothing to migrate[/yellow] (profile exists or no .env credentials)")


@profile_app.command("edit")
def profile_edit(
    name: str = typer.Argument(default=None, help="Profile name (default: active)"),
    token: str = typer.Option(None, "--token", help="Metricool API token"),
    user_id: str = typer.Option(None, "--user-id", help="Metricool user ID"),
    blog_id: str = typer.Option(None, "--blog-id", help="Metricool blog ID"),
    timezone: str = typer.Option(None, "--tz", help="Timezone"),
    csv_path: str = typer.Option(None, "--csv", help="Path to Threads CSV"),
    threads_token: str = typer.Option(None, "--threads-token", help="Threads Graph API token"),
    keywords: str = typer.Option(None, "--keywords", help="Research keywords"),
):
    """Update profile settings."""
    import json

    from .config import _profile_config_path, _read_active_profile

    target = name or _active_profile or _read_active_profile()
    config_path = _profile_config_path(target)

    if not config_path.exists():
        console.print(f"[red]Profile '{target}' not found.[/red]")
        raise typer.Exit(1)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    updates = {
        "user_token": token,
        "user_id": user_id,
        "blog_id": blog_id,
        "timezone": timezone,
        "csv_path": csv_path,
        "threads_access_token": threads_token,
        "research_keywords": keywords,
    }
    changed = 0
    for key, value in updates.items():
        if value is not None:
            config[key] = value
            changed += 1

    if changed == 0:
        console.print("[yellow]No changes specified.[/yellow]")
        return

    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]Profile '{target}' updated ({changed} field(s)).[/green]")


# ── cron automation ────────────────────────────────────────

cron_app = typer.Typer(help="Manage scheduled automation (cron)")
app.add_typer(cron_app, name="cron")

_CRON_TAG = "snshack-threads"


def _find_project_dir() -> str:
    """Find the project directory (where scripts/ lives)."""
    import importlib.resources
    # Walk up from this file to find the project root
    this_file = Path(__file__).resolve()
    for parent in this_file.parents:
        if (parent / "scripts" / "auto-collect.sh").exists():
            return str(parent)
    return str(Path.cwd())


@cron_app.command("setup")
def cron_setup(
    interval: str = typer.Option("hourly", help="Interval: hourly, daily, or cron expression like '0 */2 * * *'"),
):
    """Register auto-collect in crontab."""
    import subprocess

    project_dir = _find_project_dir()
    script = f"{project_dir}/scripts/auto-collect.sh"

    if not Path(script).exists():
        console.print(f"[red]Script not found: {script}[/red]")
        raise typer.Exit(1)

    # Build cron expression
    if interval == "hourly":
        cron_expr = "0 * * * *"
    elif interval == "daily":
        cron_expr = "0 9 * * *"
    else:
        cron_expr = interval

    cron_line = f'{cron_expr} {script} # {_CRON_TAG}'

    # Read current crontab
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        existing = result.stdout if result.returncode == 0 else ""
    except FileNotFoundError:
        existing = ""

    # Remove old snshack entries
    lines = [l for l in existing.splitlines() if _CRON_TAG not in l]
    lines.append(cron_line)

    # Write new crontab
    new_crontab = "\n".join(lines) + "\n"
    proc = subprocess.run(["crontab", "-"], input=new_crontab, capture_output=True, text=True)
    if proc.returncode != 0:
        console.print(f"[red]Failed to set crontab: {proc.stderr}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Cron registered:[/green] {cron_line}")
    console.print(f"  Logs: {project_dir}/logs/")


@cron_app.command("status")
def cron_status():
    """Show current cron entries for snshack."""
    import subprocess

    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode != 0:
            console.print("[yellow]No crontab configured.[/yellow]")
            return
    except FileNotFoundError:
        console.print("[red]crontab not available.[/red]")
        return

    entries = [l for l in result.stdout.splitlines() if _CRON_TAG in l]
    if not entries:
        console.print("[yellow]No snshack cron entries found.[/yellow] Run 'snshack cron setup'")
        return

    for entry in entries:
        console.print(f"  {entry}")


@cron_app.command("remove")
def cron_remove():
    """Remove snshack entries from crontab."""
    import subprocess

    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode != 0:
            console.print("[yellow]No crontab configured.[/yellow]")
            return
    except FileNotFoundError:
        console.print("[red]crontab not available.[/red]")
        return

    lines = [l for l in result.stdout.splitlines() if _CRON_TAG not in l]
    removed = len(result.stdout.splitlines()) - len(lines)

    if removed == 0:
        console.print("[yellow]No snshack entries found.[/yellow]")
        return

    new_crontab = "\n".join(lines) + "\n" if lines else ""
    proc = subprocess.run(["crontab", "-"], input=new_crontab, capture_output=True, text=True)
    if proc.returncode != 0:
        console.print(f"[red]Failed to update crontab: {proc.stderr}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Removed {removed} snshack cron entry(s).[/green]")


@cron_app.command("logs")
def cron_logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    profile_name: str = typer.Option("", "--profile-name", help="Filter by profile"),
):
    """Show recent auto-collect logs."""
    project_dir = Path(_find_project_dir())
    log_dir = project_dir / "logs"

    if not log_dir.exists():
        console.print("[yellow]No logs yet.[/yellow]")
        return

    # Find log files, newest first
    pattern = f"collect_{profile_name}*" if profile_name else "collect_*"
    log_files = sorted(log_dir.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)

    if not log_files:
        console.print("[yellow]No log files found.[/yellow]")
        return

    shown = 0
    for lf in log_files:
        if shown >= lines:
            break
        content = lf.read_text(encoding="utf-8", errors="replace").strip()
        if content:
            console.print(f"[dim]--- {lf.name} ---[/dim]")
            for line in content.splitlines():
                console.print(f"  {line}")
                shown += 1
                if shown >= lines:
                    break


# ── Data Update (shared intelligence & hook-theme matrix) ──


@app.command("update-intelligence")
def update_intelligence_cmd():
    """全プロファイルのCSVデータからクロスジャンル分析を更新する。

    universal_insights.json を最新のCSVデータで再生成します。
    3層データ解決のuniversal tierで使用されます。
    """
    from .config import list_profiles, get_settings
    from .shared_intelligence import analyze_cross_genre, update_shared_data

    csv_configs = []
    for name in list_profiles():
        try:
            settings = get_settings(profile=name)
            csv_path = None
            if settings.csv_path:
                csv_path = Path(settings.csv_path)
            elif settings.reference_csv_path.exists():
                csv_path = settings.reference_csv_path
            if csv_path and csv_path.exists():
                csv_configs.append({
                    "path": str(csv_path),
                    "genre": settings.genre or "不明",
                    "account": name,
                })
        except Exception:
            continue

    if not csv_configs:
        console.print("[red]CSVデータが見つかりません。sync-csvを先に実行してください。[/red]")
        raise typer.Exit(1)

    console.print(f"[dim]{len(csv_configs)}プロファイルのCSVを分析中...[/dim]")
    for cfg in csv_configs:
        console.print(f"  - {cfg['account']} ({cfg['genre']})")

    insights = analyze_cross_genre(csv_configs)
    out_dir = update_shared_data(insights)

    console.print(f"\n[green]共有インサイト更新完了[/green]")
    console.print(f"  総投稿数: {insights.total_posts}")
    console.print(f"  ジャンル: {', '.join(insights.genres)}")
    console.print(f"  保存先: {out_dir}")


@app.command("update-matrix")
def update_matrix_cmd():
    """プロファイルのHook×Themeマトリクスを更新する。

    hook_theme_matrix.json を最新のCSVデータで再生成します。
    AI投稿生成のシステムプロンプトで使用されます。
    """
    from .config import list_profiles, get_settings
    from .hook_theme_matrix import build_matrix, save_matrix, export_matrix_report

    target = _active_profile
    profiles_to_update = [target] if target else list_profiles()

    for name in profiles_to_update:
        try:
            settings = get_settings(profile=name)
            csv_path = None
            if settings.csv_path:
                csv_path = Path(settings.csv_path)
            elif settings.reference_csv_path.exists():
                csv_path = settings.reference_csv_path
            if not csv_path or not csv_path.exists():
                console.print(f"  [yellow]{name}: CSVなし — スキップ[/yellow]")
                continue

            csv_configs = [{
                "csv_path": str(csv_path),
                "genre": settings.genre or None,
                "profile": name,
            }]
            matrix = build_matrix(csv_configs)
            out_path = save_matrix(matrix, settings.profile_dir)

            console.print(f"  [green]{name}[/green]: {matrix.total_posts}投稿 → {matrix.classified_posts}分類済み → {out_path}")
        except Exception as e:
            console.print(f"  [red]{name}: エラー — {e}[/red]")

    console.print("\n[green]マトリクス更新完了[/green]")


@app.command("update-data")
def update_data_cmd():
    """共有インサイトとマトリクスを一括更新する。

    update-intelligence + update-matrix を順番に実行します。
    """
    console.print("[bold]1/2: 共有インサイト更新[/bold]")
    try:
        update_intelligence_cmd()
    except SystemExit:
        console.print("[yellow]共有インサイト更新をスキップ[/yellow]")

    console.print(f"\n[bold]2/2: マトリクス更新[/bold]")
    try:
        update_matrix_cmd()
    except SystemExit:
        console.print("[yellow]マトリクス更新をスキップ[/yellow]")

    console.print("\n[green]全データ更新完了[/green]")


# ── Dashboard ──────────────────────────────────────────────

@app.command("dashboard")
def dashboard_cmd(
    port: int = typer.Option(8501, "--port", help="Port number"),
):
    """Launch the Streamlit web dashboard."""
    import subprocess
    import sys

    dashboard_module = Path(__file__).parent / "dashboard.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(dashboard_module), "--server.port", str(port)]
    console.print(f"[green]Starting dashboard on port {port}...[/green]")
    console.print("[dim]Stop with Ctrl+C[/dim]")
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        console.print("[red]streamlit not found. Install: pip install 'snshack-threads[dashboard]'[/red]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped.[/yellow]")


# ── Token refresh ─────────────────────────────────────────

@threads_app.command("refresh-token")
def threads_refresh_token(
    force: bool = typer.Option(False, "--force", help="Force refresh even if not expiring soon"),
):
    """Refresh Threads API long-lived token (60-day tokens)."""
    from .threads_api import ThreadsGraphClient

    settings = _get_settings()
    if not settings.threads_access_token:
        console.print("[red]No Threads access token configured.[/red]")
        return

    # Show current token info
    console.print("[bold]Current token status:[/bold]")
    try:
        with ThreadsGraphClient() as client:
            info = client.get_token_info()
            expires = info.get("expires_at")
            if expires:
                exp_dt = datetime.fromtimestamp(expires)
                days_left = (exp_dt - datetime.now()).days
                color = "green" if days_left > 14 else "yellow" if days_left > 7 else "red"
                console.print(f"  Expires: {exp_dt:%Y-%m-%d} ([{color}]{days_left} days left[/{color}])")

                if days_left > 14 and not force:
                    console.print("[green]Token is still valid. Use --force to refresh anyway.[/green]")
                    return
            else:
                console.print("  [yellow]Could not determine expiry[/yellow]")

            # Refresh
            console.print("\n[bold]Refreshing token...[/bold]")
            result = client.refresh_long_lived_token()
            if result.get("access_token"):
                console.print("[green]Token refreshed successfully![/green]")
                expires_in = result.get("expires_in", 0)
                if expires_in:
                    console.print(f"  Valid for {expires_in // 86400} days")
            else:
                console.print("[red]Token refresh failed.[/red]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


# ── Notifications ─────────────────────────────────────────

notify_app = typer.Typer(help="Notification management (Slack / Email)")
app.add_typer(notify_app, name="notify")


@notify_app.command("test")
def notify_test(
    message: str = typer.Option("Test notification from SNShack Threads", "--message", "-m"),
):
    """Send a test notification to all configured channels."""
    from .notifier import NotifyConfig, send_email, send_slack

    config = NotifyConfig.from_profile(_active_profile)

    sent = False
    if config.has_slack:
        ok = send_slack(config, message, title="SNShack Test")
        console.print(f"  Slack: {'[green]sent[/green]' if ok else '[red]failed[/red]'}")
        sent = sent or ok
    else:
        console.print("  Slack: [dim]not configured[/dim]")

    if config.has_email:
        ok = send_email(config, subject="SNShack Test", body=message)
        console.print(f"  Email: {'[green]sent[/green]' if ok else '[red]failed[/red]'}")
        sent = sent or ok
    else:
        console.print("  Email: [dim]not configured[/dim]")

    if not sent:
        console.print("\n[yellow]No notification channels configured.[/yellow]")
        console.print("Set via env vars or profile config:")
        console.print("  Slack: SNSHACK_SLACK_WEBHOOK")
        console.print("  Email: SNSHACK_SMTP_HOST, SNSHACK_EMAIL_FROM, SNSHACK_EMAIL_TO")


@notify_app.command("check")
def notify_check():
    """Run all alert checks (engagement drop, rate limits) and notify if issues found."""
    from .notifier import run_all_checks

    console.print("[bold]Running alert checks...[/bold]")
    alerts = run_all_checks(profile=_active_profile)

    if alerts:
        console.print(f"\n[red]{len(alerts)} alert(s) triggered:[/red]")
        for a in alerts:
            console.print(f"  {a}")
    else:
        console.print("[green]All checks passed. No alerts.[/green]")


@notify_app.command("status")
def notify_status():
    """Show notification configuration status."""
    from .notifier import NotifyConfig

    config = NotifyConfig.from_profile(_active_profile)

    console.print("[bold]Notification Configuration:[/bold]")
    console.print(f"  Slack webhook: {'[green]configured[/green]' if config.has_slack else '[dim]not set[/dim]'}")
    if config.slack_channel:
        console.print(f"  Slack channel: {config.slack_channel}")
    console.print(f"  Email: {'[green]configured[/green]' if config.has_email else '[dim]not set[/dim]'}")
    if config.has_email:
        console.print(f"    From: {config.email_from}")
        console.print(f"    To: {config.email_to}")
        console.print(f"    SMTP: {config.email_smtp_host}:{config.email_smtp_port}")


if __name__ == "__main__":
    app()
