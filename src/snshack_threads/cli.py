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


if __name__ == "__main__":
    app()
