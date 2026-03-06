"""Configuration management."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

THREADS_API_BASE = "https://graph.threads.net/v1.0"


class Settings(BaseModel):
    """Application settings loaded from environment variables."""

    access_token: str = Field(default_factory=lambda: os.getenv("THREADS_ACCESS_TOKEN", ""))
    user_id: str = Field(default_factory=lambda: os.getenv("THREADS_USER_ID", ""))
    api_base: str = THREADS_API_BASE
    data_dir: Path = Field(default_factory=lambda: Path.home() / ".snshack-threads")

    def validate_credentials(self) -> bool:
        """Check that required credentials are set."""
        return bool(self.access_token and self.user_id)


def get_settings() -> Settings:
    """Return application settings."""
    return Settings()
