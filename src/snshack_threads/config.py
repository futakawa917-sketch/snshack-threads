"""Configuration management with multi-profile support.

Each profile stores its own credentials, CSV, and data files under:
  ~/.snshack-threads/profiles/{profile_name}/
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

METRICOOL_API_BASE = "https://app.metricool.com/api"
BASE_DIR = Path.home() / ".snshack-threads"
PROFILES_DIR = BASE_DIR / "profiles"
ACTIVE_PROFILE_FILE = BASE_DIR / "active_profile"


class Settings(BaseModel):
    """Application settings loaded from profile config or environment variables."""

    profile_name: str = "default"
    user_token: str = ""
    user_id: str = ""
    blog_id: str = ""
    timezone: str = "Asia/Tokyo"
    api_base: str = METRICOOL_API_BASE
    data_dir: Path = Field(default_factory=lambda: BASE_DIR)
    posts_per_day: int = 5
    csv_path: str = ""

    # Threads Graph API
    threads_access_token: str = ""
    research_keywords: str = ""

    # Anthropic API
    anthropic_api_key: str = ""

    # Hook patterns (業種別カスタマイズ用)
    custom_hooks: dict[str, str] = Field(default_factory=dict)

    def validate_credentials(self) -> bool:
        return bool(self.user_token and self.user_id and self.blog_id)

    def get_research_keywords(self) -> list[str]:
        if not self.research_keywords:
            return []
        return [kw.strip() for kw in self.research_keywords.split(",") if kw.strip()]


def _profile_dir(name: str) -> Path:
    return PROFILES_DIR / name


def _profile_config_path(name: str) -> Path:
    return _profile_dir(name) / "config.json"


# Runtime override for --profile CLI option (not persisted)
_runtime_profile_override: str | None = None


def set_runtime_profile(name: str | None) -> None:
    """Set a runtime profile override (used by CLI --profile flag)."""
    global _runtime_profile_override
    _runtime_profile_override = name


def _read_active_profile() -> str:
    """Read the active profile name, defaulting to 'default'."""
    if _runtime_profile_override:
        return _runtime_profile_override
    if ACTIVE_PROFILE_FILE.exists():
        name = ACTIVE_PROFILE_FILE.read_text(encoding="utf-8").strip()
        if name:
            return name
    return "default"


def _write_active_profile(name: str) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_PROFILE_FILE.write_text(name, encoding="utf-8")


def list_profiles() -> list[str]:
    """Return sorted list of profile names."""
    if not PROFILES_DIR.exists():
        return []
    return sorted(
        d.name for d in PROFILES_DIR.iterdir()
        if d.is_dir() and (d / "config.json").exists()
    )


def create_profile(
    name: str,
    *,
    user_token: str = "",
    user_id: str = "",
    blog_id: str = "",
    timezone: str = "Asia/Tokyo",
    csv_path: str = "",
    threads_access_token: str = "",
    research_keywords: str = "",
) -> Path:
    """Create a new profile directory with config.json."""
    pdir = _profile_dir(name)
    if pdir.exists() and _profile_config_path(name).exists():
        raise FileExistsError(f"Profile '{name}' already exists")

    pdir.mkdir(parents=True, exist_ok=True)
    config = {
        "profile_name": name,
        "user_token": user_token,
        "user_id": user_id,
        "blog_id": blog_id,
        "timezone": timezone,
        "csv_path": csv_path,
        "threads_access_token": threads_access_token,
        "research_keywords": research_keywords,
    }
    _profile_config_path(name).write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return pdir


def rename_profile(old_name: str, new_name: str) -> Path:
    """Rename a profile directory."""
    old_dir = _profile_dir(old_name)
    if not old_dir.exists():
        raise FileNotFoundError(f"Profile '{old_name}' not found")
    new_dir = _profile_dir(new_name)
    if new_dir.exists():
        raise FileExistsError(f"Profile '{new_name}' already exists")

    old_dir.rename(new_dir)

    # Update profile_name in config.json
    config_path = new_dir / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["profile_name"] = new_name
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Update active profile if it was the renamed one
    if _read_active_profile() == old_name:
        _write_active_profile(new_name)

    return new_dir


def delete_profile(name: str) -> None:
    """Delete a profile directory."""
    import shutil

    pdir = _profile_dir(name)
    if not pdir.exists():
        raise FileNotFoundError(f"Profile '{name}' not found")
    shutil.rmtree(pdir)

    # If deleted profile was active, reset to default
    if _read_active_profile() == name:
        _write_active_profile("default")


def switch_profile(name: str) -> None:
    """Set the active profile."""
    if not _profile_dir(name).exists():
        raise FileNotFoundError(f"Profile '{name}' not found")
    _write_active_profile(name)


def get_settings(profile: str | None = None) -> Settings:
    """Return settings for the given profile (or the active profile).

    Priority:
    1. Profile config.json (if profile exists)
    2. Environment variables (fallback, used for 'default' auto-migration)
    """
    name = profile or _read_active_profile()
    config_path = _profile_config_path(name)

    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        data["data_dir"] = str(_profile_dir(name))
        data["profile_name"] = name
        return Settings(**data)

    # Fallback: load from env vars (backward compatibility / default profile)
    return Settings(
        profile_name=name,
        user_token=os.getenv("METRICOOL_USER_TOKEN", ""),
        user_id=os.getenv("METRICOOL_USER_ID", ""),
        blog_id=os.getenv("METRICOOL_BLOG_ID", ""),
        timezone=os.getenv("METRICOOL_TIMEZONE", "Asia/Tokyo"),
        data_dir=_profile_dir(name),
        csv_path=os.getenv("THREADS_CSV_PATH", ""),
        threads_access_token=os.getenv("THREADS_ACCESS_TOKEN", ""),
        research_keywords=os.getenv("RESEARCH_KEYWORDS", ""),
    )


def migrate_env_to_profile(name: str = "default") -> Path | None:
    """Migrate existing .env settings to a named profile.

    Returns the profile dir if migration was performed, None if skipped.
    """
    if _profile_config_path(name).exists():
        return None  # Already migrated

    token = os.getenv("METRICOOL_USER_TOKEN", "")
    if not token:
        return None  # No env vars to migrate

    pdir = create_profile(
        name,
        user_token=token,
        user_id=os.getenv("METRICOOL_USER_ID", ""),
        blog_id=os.getenv("METRICOOL_BLOG_ID", ""),
        timezone=os.getenv("METRICOOL_TIMEZONE", "Asia/Tokyo"),
        csv_path=os.getenv("THREADS_CSV_PATH", ""),
        threads_access_token=os.getenv("THREADS_ACCESS_TOKEN", ""),
        research_keywords=os.getenv("RESEARCH_KEYWORDS", ""),
    )
    _write_active_profile(name)
    return pdir
