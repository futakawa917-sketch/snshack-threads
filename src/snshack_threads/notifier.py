"""Notification system for alerts and monitoring (Slack / Email)."""

from __future__ import annotations

import json
import logging
import os
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger(__name__)


@dataclass
class NotifyConfig:
    """Notification configuration for Slack and Email."""

    slack_webhook: str = ""
    slack_channel: str = ""
    email_from: str = ""
    email_to: str = ""
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_user: str = ""
    email_smtp_password: str = ""

    @property
    def has_slack(self) -> bool:
        return bool(self.slack_webhook)

    @property
    def has_email(self) -> bool:
        return bool(self.email_smtp_host and self.email_from and self.email_to)

    @classmethod
    def from_profile(cls, profile: str | None = None) -> NotifyConfig:
        """Load notification config from profile or environment variables."""
        try:
            from .config import get_settings

            settings = get_settings(profile=profile)
            config_path = settings.profile_dir / "config.json"
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                notify_data = data.get("notify", {})
                if notify_data:
                    return cls(
                        slack_webhook=notify_data.get("slack_webhook", ""),
                        slack_channel=notify_data.get("slack_channel", ""),
                        email_from=notify_data.get("email_from", ""),
                        email_to=notify_data.get("email_to", ""),
                        email_smtp_host=notify_data.get("email_smtp_host", ""),
                        email_smtp_port=notify_data.get("email_smtp_port", 587),
                        email_smtp_user=notify_data.get("email_smtp_user", ""),
                        email_smtp_password=notify_data.get("email_smtp_password", ""),
                    )
        except Exception:
            pass

        return cls(
            slack_webhook=os.getenv("SNSHACK_SLACK_WEBHOOK", ""),
            slack_channel=os.getenv("SNSHACK_SLACK_CHANNEL", ""),
            email_from=os.getenv("SNSHACK_EMAIL_FROM", ""),
            email_to=os.getenv("SNSHACK_EMAIL_TO", ""),
            email_smtp_host=os.getenv("SNSHACK_SMTP_HOST", ""),
            email_smtp_port=int(os.getenv("SNSHACK_SMTP_PORT", "587")),
            email_smtp_user=os.getenv("SNSHACK_SMTP_USER", ""),
            email_smtp_password=os.getenv("SNSHACK_SMTP_PASSWORD", ""),
        )


def send_slack(config: NotifyConfig, message: str, title: str = "") -> bool:
    """Post a message to Slack via webhook. Returns True if successful."""
    if not config.has_slack:
        return False

    payload: dict = {}
    if title:
        payload["text"] = f"*{title}*\n{message}"
    else:
        payload["text"] = message

    if config.slack_channel:
        payload["channel"] = config.slack_channel

    try:
        resp = httpx.post(config.slack_webhook, json=payload, timeout=10.0)
        return resp.status_code == 200
    except Exception as e:
        logger.error("Slack notification failed: %s", e)
        return False


def send_email(config: NotifyConfig, subject: str, body: str) -> bool:
    """Send an email via SMTP. Returns True if successful."""
    if not config.has_email:
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = config.email_from
    msg["To"] = config.email_to

    try:
        with smtplib.SMTP(config.email_smtp_host, config.email_smtp_port) as server:
            server.starttls()
            if config.email_smtp_user and config.email_smtp_password:
                server.login(config.email_smtp_user, config.email_smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error("Email notification failed: %s", e)
        return False


def run_all_checks(profile: str | None = None) -> list[str]:
    """Run monitoring checks and send notifications for any issues.

    Checks:
    - Engagement drop (>30% below 30-day average)
    - Unmatched posts (scheduled but metrics not collected)

    Returns list of alert messages (empty = all clear).
    """
    alerts: list[str] = []

    try:
        from .post_history import PostHistory

        history = PostHistory()
        recent = history.get_recent(days=7)
        collected = [r for r in recent if r.has_metrics]

        if len(collected) >= 3:
            avg_views = sum(r.views for r in collected) / len(collected)
            older = history.get_recent(days=30)
            older_collected = [r for r in older if r.has_metrics]

            if len(older_collected) >= 5:
                overall_avg = sum(r.views for r in older_collected) / len(older_collected)
                if overall_avg > 0 and avg_views < overall_avg * 0.7:
                    drop_pct = (1 - avg_views / overall_avg) * 100
                    alerts.append(
                        f"Engagement drop: 直近7日の平均閲覧数 {avg_views:.0f} "
                        f"(30日平均 {overall_avg:.0f} から {drop_pct:.0f}% 低下)"
                    )

        uncollected = history.get_uncollected()
        old_uncollected = [r for r in uncollected if r.status == "scheduled"]
        if len(old_uncollected) > 5:
            alerts.append(
                f"Unmatched posts: {len(old_uncollected)} 件の投稿のメトリクスが未収集"
            )

    except Exception as e:
        logger.warning("Check failed: %s", e)

    if alerts:
        config = NotifyConfig.from_profile(profile)
        message = "\n".join(f"- {a}" for a in alerts)

        if config.has_slack:
            send_slack(config, message, title="SNShack Alert")
        if config.has_email:
            send_email(config, subject="SNShack Alert", body=message)

    return alerts
