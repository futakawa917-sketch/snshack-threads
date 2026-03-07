"""Notification system for alerts and daily reports.

Supports:
  - Slack (via webhook or slack-sdk)
  - Email (via SMTP)

Triggers:
  - Engagement drop alert (views below threshold)
  - Daily autopilot summary
  - Token expiration warning
  - Rate limit warning
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


@dataclass
class NotifyConfig:
    """Notification configuration."""

    slack_webhook_url: str = ""
    slack_channel: str = ""
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_from: str = ""
    email_to: str = ""
    email_password: str = ""

    @classmethod
    def from_env(cls) -> NotifyConfig:
        return cls(
            slack_webhook_url=os.getenv("SNSHACK_SLACK_WEBHOOK", ""),
            slack_channel=os.getenv("SNSHACK_SLACK_CHANNEL", ""),
            email_smtp_host=os.getenv("SNSHACK_SMTP_HOST", ""),
            email_smtp_port=int(os.getenv("SNSHACK_SMTP_PORT", "587")),
            email_from=os.getenv("SNSHACK_EMAIL_FROM", ""),
            email_to=os.getenv("SNSHACK_EMAIL_TO", ""),
            email_password=os.getenv("SNSHACK_EMAIL_PASSWORD", ""),
        )

    @classmethod
    def from_profile(cls, profile: str | None = None) -> NotifyConfig:
        """Load notification config from profile or env."""
        from .config import get_settings

        settings = get_settings(profile=profile)
        config_path = Path(settings.data_dir) / "config.json"

        config = cls.from_env()

        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                notify = data.get("notify", {})
                if notify.get("slack_webhook_url"):
                    config.slack_webhook_url = notify["slack_webhook_url"]
                if notify.get("slack_channel"):
                    config.slack_channel = notify["slack_channel"]
                if notify.get("email_to"):
                    config.email_to = notify["email_to"]
            except (json.JSONDecodeError, OSError):
                pass

        return config

    @property
    def has_slack(self) -> bool:
        return bool(self.slack_webhook_url)

    @property
    def has_email(self) -> bool:
        return bool(self.email_smtp_host and self.email_from and self.email_to)


def send_slack(config: NotifyConfig, message: str, title: str = "") -> bool:
    """Send a Slack notification via webhook."""
    if not config.has_slack:
        logger.debug("Slack not configured, skipping")
        return False

    payload: dict = {}
    if title:
        payload["blocks"] = [
            {"type": "header", "text": {"type": "plain_text", "text": title}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message}},
        ]
    else:
        payload["text"] = message

    if config.slack_channel:
        payload["channel"] = config.slack_channel

    try:
        resp = httpx.post(config.slack_webhook_url, json=payload, timeout=10.0)
        if resp.status_code == 200:
            logger.info("Slack notification sent")
            return True
        else:
            logger.warning("Slack webhook returned %d: %s", resp.status_code, resp.text)
            return False
    except Exception as e:
        logger.warning("Slack notification failed: %s", e)
        return False


def send_email(config: NotifyConfig, subject: str, body: str) -> bool:
    """Send an email notification via SMTP."""
    if not config.has_email:
        logger.debug("Email not configured, skipping")
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = config.email_from
    msg["To"] = config.email_to

    try:
        with smtplib.SMTP(config.email_smtp_host, config.email_smtp_port) as server:
            server.starttls()
            if config.email_password:
                server.login(config.email_from, config.email_password)
            server.send_message(msg)
        logger.info("Email notification sent to %s", config.email_to)
        return True
    except Exception as e:
        logger.warning("Email notification failed: %s", e)
        return False


def notify(
    message: str,
    title: str = "SNShack Threads",
    profile: str | None = None,
    level: str = "info",
) -> None:
    """Send notification to all configured channels.

    Args:
        message: Notification body.
        title: Notification title/subject.
        profile: Profile name for config lookup.
        level: "info", "warning", or "alert".
    """
    config = NotifyConfig.from_profile(profile)

    prefix = {"info": "ℹ️", "warning": "⚠️", "alert": "🚨"}.get(level, "")
    full_title = f"{prefix} {title}" if prefix else title

    if config.has_slack:
        send_slack(config, message, title=full_title)

    if config.has_email:
        send_email(config, subject=full_title, body=message)


# ── Alert checks ──────────────────────────────────────────


def check_engagement_drop(
    profile: str | None = None,
    threshold_pct: float = 50.0,
) -> str | None:
    """Check if recent engagement dropped significantly.

    Compares last 7 days vs previous 7 days.
    Returns alert message if drop exceeds threshold, None otherwise.
    """
    from .post_history import PostHistory

    history = PostHistory()
    all_records = [r for r in history.get_all() if r.has_metrics]

    if len(all_records) < 10:
        return None

    now_str = __import__("datetime").datetime.now().isoformat()
    week_ago = (__import__("datetime").datetime.now() - __import__("datetime").timedelta(days=7)).isoformat()
    two_weeks_ago = (__import__("datetime").datetime.now() - __import__("datetime").timedelta(days=14)).isoformat()

    recent = [r for r in all_records if r.scheduled_at > week_ago]
    previous = [r for r in all_records if two_weeks_ago < r.scheduled_at <= week_ago]

    if not recent or not previous:
        return None

    recent_avg = sum(r.views for r in recent) / len(recent)
    previous_avg = sum(r.views for r in previous) / len(previous)

    if previous_avg == 0:
        return None

    drop_pct = ((previous_avg - recent_avg) / previous_avg) * 100

    if drop_pct >= threshold_pct:
        return (
            f"Views dropped {drop_pct:.0f}% vs last week!\n"
            f"  Last 7 days avg: {recent_avg:,.0f} views\n"
            f"  Previous 7 days avg: {previous_avg:,.0f} views\n"
            f"  Posts: {len(recent)} recent, {len(previous)} previous"
        )
    return None


def check_rate_limit_warning(threshold_pct: float = 80.0) -> str | None:
    """Check if API rate limits are near exhaustion."""
    from .threads_api import RateLimiter

    limiter = RateLimiter()
    usage = limiter.get_usage_summary()

    warnings = []
    for name, data in usage.items():
        used_pct = (data["used"] / data["limit"] * 100) if data["limit"] else 0
        if used_pct >= threshold_pct:
            warnings.append(
                f"{name}: {data['used']}/{data['limit']} ({used_pct:.0f}% used)"
            )

    if warnings:
        return "Rate limit warning:\n" + "\n".join(f"  - {w}" for w in warnings)
    return None


def run_all_checks(profile: str | None = None) -> list[str]:
    """Run all alert checks and send notifications for any issues.

    Returns list of alert messages that were sent.
    """
    alerts = []

    # Engagement drop
    msg = check_engagement_drop(profile=profile)
    if msg:
        notify(msg, title="Engagement Drop Alert", profile=profile, level="alert")
        alerts.append(msg)

    # Rate limit
    msg = check_rate_limit_warning()
    if msg:
        notify(msg, title="Rate Limit Warning", profile=profile, level="warning")
        alerts.append(msg)

    return alerts
