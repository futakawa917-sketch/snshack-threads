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
):
    """Schedule a single Threads post via Metricool."""
    from .models import PostDraft

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
):
    """Schedule up to 5 posts for a day at optimal time slots (8, 11, 14, 18, 21).

    Examples:
      snshack schedule-day 2026-03-08 -t "朝の投稿" -t "昼の投稿" -t "午後" -t "夕方" -t "夜"
      snshack schedule-day 2026-03-08 -f posts.txt
    """
    from .models import PostDraft
    from .scheduler import schedule_posts_for_day

    target = datetime.strptime(date, "%Y-%m-%d")

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

    drafts = [PostDraft(text=t) for t in all_texts]

    with _get_client() as client:
        results = schedule_posts_for_day(client, drafts, target)

    console.print(f"[green]Scheduled {len(results)} posts for {date}![/green]")
    from .models import DailySchedule

    slots = DailySchedule().slots
    for i, _result in enumerate(results):
        slot = slots[i]
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
    """Show available time slots for a day."""
    from .scheduler import get_next_available_slots

    target = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now()

    with _get_client() as client:
        available = get_next_available_slots(client, target)

    if not available:
        console.print(f"No available slots for {target.strftime('%Y-%m-%d')}.")
        return

    console.print(f"[bold]Available slots for {target.strftime('%Y-%m-%d')}:[/bold]")
    for dt in available:
        console.print(f"  {dt.strftime('%H:%M')}")


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
