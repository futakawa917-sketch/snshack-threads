"""Configuration management."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

METRICOOL_API_BASE = "https://app.metricool.com/api"


class Settings(BaseModel):
    """Application settings loaded from environment variables."""

    user_token: str = Field(default_factory=lambda: os.getenv("METRICOOL_USER_TOKEN", ""))
    user_id: str = Field(default_factory=lambda: os.getenv("METRICOOL_USER_ID", ""))
    blog_id: str = Field(default_factory=lambda: os.getenv("METRICOOL_BLOG_ID", ""))
    timezone: str = Field(default_factory=lambda: os.getenv("METRICOOL_TIMEZONE", "Asia/Tokyo"))
    api_base: str = METRICOOL_API_BASE
    data_dir: Path = Field(default_factory=lambda: Path.home() / ".snshack-threads")
    posts_per_day: int = Field(default=5)
    csv_path: str = Field(default_factory=lambda: os.getenv("THREADS_CSV_PATH", ""))

    # Threads Graph API (for keyword search / competitor research)
    threads_access_token: str = Field(default_factory=lambda: os.getenv("THREADS_ACCESS_TOKEN", ""))
    research_keywords: str = Field(default_factory=lambda: os.getenv("RESEARCH_KEYWORDS", ""))

    def validate_credentials(self) -> bool:
        """Check that required credentials are set."""
        return bool(self.user_token and self.user_id and self.blog_id)

    def get_research_keywords(self) -> list[str]:
        """Parse comma-separated research keywords."""
        if not self.research_keywords:
            return []
        return [kw.strip() for kw in self.research_keywords.split(",") if kw.strip()]


def get_settings() -> Settings:
    """Return application settings."""
    return Settings()
