"""CLI entry point."""

from __future__ import annotations

from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from . import __version__

app = typer.Typer(
    name="snshack",
    help="Threads (Meta) automation & analytics CLI",
    no_args_is_help=True,
)
console = Console()


def _get_client():
    from .api import ThreadsClient

    return ThreadsClient()


# ── info ─────────────────────────────────────────────────────

@app.command()
def version():
    """Show version."""
    console.print(f"snshack-threads v{__version__}")


@app.command()
def profile():
    """Show your Threads profile."""
    with _get_client() as client:
        p = client.get_profile()
        console.print(f"[bold]{p.username}[/bold]  (ID: {p.id})")
        if p.threads_biography:
            console.print(f"  Bio: {p.threads_biography}")


# ── posts ────────────────────────────────────────────────────

@app.command()
def posts(limit: int = typer.Option(10, help="Number of posts to fetch")):
    """List your recent Threads posts."""
    with _get_client() as client:
        items = client.get_posts(limit=limit)

    table = Table(title="Recent Posts")
    table.add_column("ID", style="dim")
    table.add_column("Text", max_width=60)
    table.add_column("Type")
    table.add_column("Time")

    for p in items:
        ts = p.timestamp.strftime("%Y-%m-%d %H:%M") if p.timestamp else "-"
        text = (p.text or "")[:60]
        table.add_row(p.id, text, p.media_type.value, ts)

    console.print(table)


@app.command()
def post(
    text: str = typer.Argument(help="Post text (max 500 chars)"),
    image_url: str | None = typer.Option(None, help="Image URL to attach"),
):
    """Publish a new Threads post."""
    from .models import MediaType, PostDraft

    media_type = MediaType.IMAGE if image_url else MediaType.TEXT
    draft = PostDraft(text=text, media_type=media_type, image_url=image_url)

    with _get_client() as client:
        result = client.create_post(draft)

    console.print(f"[green]Published![/green]  Post ID: {result.id}")
    if result.permalink:
        console.print(f"  Link: {result.permalink}")


# ── scheduling ───────────────────────────────────────────────

@app.command()
def schedule(
    text: str = typer.Argument(help="Post text"),
    at: str = typer.Option(help="Scheduled time (YYYY-MM-DD HH:MM)"),
):
    """Schedule a post for later."""
    from .models import PostDraft
    from .scheduler import PostQueue

    scheduled_at = datetime.strptime(at, "%Y-%m-%d %H:%M")
    draft = PostDraft(text=text)
    queue = PostQueue()
    entry = queue.add(draft, scheduled_at)
    console.print(f"[green]Scheduled![/green]  ID: {entry.id}  at {scheduled_at}")


@app.command()
def queue():
    """Show scheduled (pending) posts."""
    from .scheduler import PostQueue

    q = PostQueue()
    pending = q.list_pending()

    if not pending:
        console.print("No pending posts.")
        return

    table = Table(title="Scheduled Posts")
    table.add_column("ID", style="dim")
    table.add_column("Text", max_width=50)
    table.add_column("Scheduled At")

    for entry in pending:
        table.add_row(
            entry.id,
            entry.draft.text[:50],
            entry.scheduled_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@app.command()
def publish_due():
    """Publish all posts whose scheduled time has passed."""
    from .scheduler import publish_due_posts

    ids = publish_due_posts()
    if ids:
        console.print(f"[green]Published {len(ids)} post(s):[/green]")
        for pid in ids:
            console.print(f"  - {pid}")
    else:
        console.print("No due posts to publish.")


# ── analytics ────────────────────────────────────────────────

@app.command()
def analytics(
    limit: int = typer.Option(25, help="Number of posts to analyse"),
    top: int = typer.Option(5, help="Number of top posts to show"),
):
    """Show analytics report for recent posts."""
    from .analytics import generate_report

    with _get_client() as client:
        report = generate_report(client, limit=limit, top_n=top)

    if report.total_posts == 0:
        console.print("No posts found.")
        return

    console.print("[bold]Analytics Report[/bold]")
    if report.period_start and report.period_end:
        console.print(
            f"  Period: {report.period_start:%Y-%m-%d} ~ {report.period_end:%Y-%m-%d}"
        )
    console.print(f"  Posts analysed: {report.total_posts}")
    console.print(f"  Total views:    {report.total_views:,}")
    console.print(f"  Total likes:    {report.total_likes:,}")
    console.print(f"  Total replies:  {report.total_replies:,}")
    console.print(f"  Total reposts:  {report.total_reposts:,}")
    console.print(f"  Total quotes:   {report.total_quotes:,}")
    console.print(f"  Avg engagement: {report.avg_engagement_rate_pct}")
    console.print()

    if report.top_posts:
        table = Table(title=f"Top {len(report.top_posts)} Posts by Interactions")
        table.add_column("ID", style="dim")
        table.add_column("Text", max_width=40)
        table.add_column("Views", justify="right")
        table.add_column("Likes", justify="right")
        table.add_column("Replies", justify="right")
        table.add_column("Eng. Rate", justify="right")

        for s in report.top_posts:
            text = (s.post.text or "")[:40]
            table.add_row(
                s.post.id,
                text,
                f"{s.metrics.views:,}",
                f"{s.metrics.likes:,}",
                f"{s.metrics.replies:,}",
                s.engagement_rate_pct,
            )

        console.print(table)


@app.command()
def metrics(post_id: str = typer.Argument(help="Post ID to check")):
    """Show metrics for a specific post."""
    with _get_client() as client:
        m = client.get_post_metrics(post_id)

    console.print(f"[bold]Metrics for {post_id}[/bold]")
    console.print(f"  Views:    {m.views:,}")
    console.print(f"  Likes:    {m.likes:,}")
    console.print(f"  Replies:  {m.replies:,}")
    console.print(f"  Reposts:  {m.reposts:,}")
    console.print(f"  Quotes:   {m.quotes:,}")
    console.print(f"  Eng rate: {m.engagement_rate * 100:.2f}%")


if __name__ == "__main__":
    app()
