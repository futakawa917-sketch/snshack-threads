"""CLI entry point for snshack-threads (Metricool-based)."""

from __future__ import annotations

from datetime import datetime, timedelta

import typer
from rich.console import Console
from rich.table import Table

from . import __version__

app = typer.Typer(
    name="snshack",
    help="Threads automation & analytics via Metricool",
    no_args_is_help=True,
)
console = Console()

_DOW_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


def _get_client():
    from .api import MetricoolClient

    return MetricoolClient()


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
):
    """Schedule a single Threads post via Metricool."""
    from .content_guard import append_cta, check_ng
    from .models import PostDraft

    # NG check
    violations = check_ng(text)
    if violations:
        console.print(f"[red]NG detected:[/red] {', '.join(violations)}")
        console.print("外部リンク/LINE/固定投稿への誘導はリーチが激減するため禁止です。")
        raise typer.Exit(1)

    if add_cta:
        text = append_cta(text)

    publish_at = datetime.strptime(at, "%Y-%m-%d %H:%M")
    draft = PostDraft(text=text)

    with _get_client() as client:
        result = client.schedule_post(draft, publish_at)

    console.print(f"[green]Scheduled![/green]  at {publish_at}")
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

    target = datetime.strptime(date, "%Y-%m-%d")
    dow_name = _DOW_NAMES[target.weekday()]

    # Collect drafts from --text args or --file
    all_texts: list[str] = list(texts)
    if file:
        from pathlib import Path

        lines = Path(file).read_text().strip().splitlines()
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

    target = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now()
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
    from .config import get_settings
    from .csv_analyzer import analyze_optimal_times
    from .research import research_genre
    from .scheduler import _resolve_csv_path
    from .threads_api import ThreadsAPIError, ThreadsGraphClient

    settings = get_settings()
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

@app.command()
def collect(
    min_age: int = typer.Option(24, help="Minimum hours since scheduled (default 24)"),
):
    """Collect performance data (views, likes) for published posts.

    Fetches metrics from Metricool API for posts that were scheduled
    at least --min-age hours ago. Run this daily to build up view data.

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

    with _get_client() as client:
        updated = collect_performance(history, client, min_age_hours=min_age)

    if updated:
        console.print(f"[green]Updated {len(updated)} posts with performance data:[/green]")
        for r in updated:
            console.print(f"  {r.views:>6,} views | {r.likes:>3} likes | {r.text[:40]}...")
    else:
        console.print("[yellow]No matching posts found in Metricool.[/yellow]")
        console.print("Posts may not have been published yet, or text didn't match.")


@app.command()
def review(
    days: int = typer.Option(30, help="Number of days to look back"),
    sort_by: str = typer.Option("date", help="Sort by: date, views, likes, engagement"),
):
    """Review post performance with views and engagement.

    Shows all tracked posts with their performance metrics.
    Use 'snshack collect' first to fetch latest metrics.

    Example:
      snshack review                # last 30 days, sorted by date
      snshack review --sort-by views  # sort by views
    """
    from .post_history import PostHistory

    history = PostHistory()
    records = history.get_recent(days=days)

    if not records:
        console.print("[yellow]No posts found.[/yellow]")
        if history.count == 0:
            console.print("Schedule posts with 'snshack schedule-day' to start tracking.")
        return

    # Sort
    if sort_by == "views":
        records.sort(key=lambda r: r.views, reverse=True)
    elif sort_by == "likes":
        records.sort(key=lambda r: r.likes, reverse=True)
    elif sort_by == "engagement":
        records.sort(key=lambda r: r.engagement, reverse=True)
    else:
        records.sort(key=lambda r: r.scheduled_at, reverse=True)

    table = Table(title=f"Post Performance (last {days} days)")
    table.add_column("Date", style="dim")
    table.add_column("Text", max_width=40)
    table.add_column("Views", justify="right")
    table.add_column("Likes", justify="right")
    table.add_column("Replies", justify="right")
    table.add_column("Eng.%", justify="right")
    table.add_column("Status", justify="center")

    total_views = 0
    total_likes = 0
    collected_count = 0

    for r in records:
        try:
            dt = datetime.fromisoformat(r.scheduled_at)
            date_str = dt.strftime("%m/%d %H:%M")
        except ValueError:
            date_str = "-"

        status_style = "green" if r.has_metrics else "yellow"
        status_text = "collected" if r.has_metrics else "pending"

        eng_str = f"{r.engagement * 100:.1f}%" if r.engagement else "-"

        table.add_row(
            date_str,
            r.text[:40],
            f"{r.views:,}" if r.has_metrics else "-",
            f"{r.likes:,}" if r.has_metrics else "-",
            f"{r.replies:,}" if r.has_metrics else "-",
            eng_str,
            f"[{status_style}]{status_text}[/{status_style}]",
        )

        if r.has_metrics:
            total_views += r.views
            total_likes += r.likes
            collected_count += 1

    console.print(table)

    # Summary
    if collected_count > 0:
        console.print()
        console.print(f"[bold]Summary:[/bold] {collected_count}/{len(records)} posts collected")
        console.print(f"  Total views: {total_views:,} | Avg: {total_views // collected_count:,}/post")
        console.print(f"  Total likes: {total_likes:,} | Avg: {total_likes / collected_count:.1f}/post")

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


if __name__ == "__main__":
    app()
