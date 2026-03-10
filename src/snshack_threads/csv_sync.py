"""Auto-sync: fetch posts from Metricool API and write to CSV format.

Eliminates the need for manual CSV export from Threads.
The generated CSV has the same columns as the Threads export,
so csv_analyzer.py can consume it directly.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta
from pathlib import Path

from .api import MetricoolClient
from .config import get_settings

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "Image", "PostLink", "Content", "Type", "Date",
    "Views", "Likes", "Replies", "Reposts", "Quotes",
    "Shares", "Engagement",
]


def sync_csv(
    client: MetricoolClient,
    days: int = 90,
    output_path: Path | str | None = None,
    profile: str | None = None,
) -> Path:
    """Fetch posts from Metricool and write a CSV compatible with csv_analyzer.

    Args:
        client: Metricool API client.
        days: Number of days to look back.
        output_path: Where to write the CSV. Defaults to profile data_dir/threads_synced.csv.
        profile: Profile name (for resolving default output path).

    Returns:
        Path to the written CSV file.
    """
    settings = get_settings(profile=profile)

    if output_path is None:
        output_path = Path(settings.data_dir) / "threads_synced.csv"
    else:
        output_path = Path(output_path)

    end = datetime.now()
    start = end - timedelta(days=days)

    posts = client.get_threads_posts(
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for post in posts:
            date_str = ""
            if post.date:
                date_str = post.date.strftime("%Y-%m-%d %H:%M")

            writer.writerow({
                "Image": getattr(post, "image_url", "") or "",
                "PostLink": getattr(post, "post_link", "") or "",
                "Content": post.text or "",
                "Type": "TEXT_POST",
                "Date": date_str,
                "Views": post.views,
                "Likes": post.likes,
                "Replies": post.replies,
                "Reposts": post.reposts,
                "Quotes": post.quotes,
                "Shares": 0,
                "Engagement": f"{post.engagement:.4f}" if post.engagement else "0",
            })

    logger.info("Synced %d posts to %s", len(posts), output_path)
    return output_path
