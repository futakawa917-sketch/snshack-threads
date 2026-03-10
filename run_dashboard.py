"""Streamlit dashboard launcher.

Usage:
    streamlit run run_dashboard.py
"""

import json
import os
import sys
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Auto-create profile from Streamlit Cloud secrets or env vars
def _ensure_profile():
    """Create default profile from secrets if it doesn't exist."""
    try:
        import streamlit as st
        secrets = dict(st.secrets) if hasattr(st, "secrets") and len(st.secrets) > 0 else {}
    except Exception:
        secrets = {}

    # Fallback to env vars
    token = secrets.get("THREADS_ACCESS_TOKEN", os.environ.get("THREADS_ACCESS_TOKEN", ""))
    api_key = secrets.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
    genre = secrets.get("PROFILE_GENRE", os.environ.get("PROFILE_GENRE", ""))
    keywords = secrets.get("RESEARCH_KEYWORDS", os.environ.get("RESEARCH_KEYWORDS", ""))

    if not token:
        return

    profile_dir = Path.home() / ".snshack-threads" / "profiles" / "default"
    config_path = profile_dir / "config.json"

    # Don't overwrite if exists (preserves post_history etc.)
    if config_path.exists():
        # Update secrets only
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        existing["threads_access_token"] = token
        if api_key:
            existing["anthropic_api_key"] = api_key
        config_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    profile_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "profile_name": "default",
        "threads_access_token": token,
        "anthropic_api_key": api_key,
        "genre": genre,
        "research_keywords": keywords,
        "timezone": "Asia/Tokyo",
        "posts_per_day": 5,
        "short_post_ratio": 0.5,
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


_ensure_profile()

from snshack_threads.dashboard import main

main()
