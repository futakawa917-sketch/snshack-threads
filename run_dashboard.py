"""Streamlit dashboard launcher.

Usage:
    streamlit run run_dashboard.py
"""

import sys
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent / "src"))

from snshack_threads.dashboard import main

main()
