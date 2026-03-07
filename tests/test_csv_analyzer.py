"""Tests for CSV analyzer module."""

import csv
import tempfile
from pathlib import Path

import pytest

from snshack_threads.csv_analyzer import (
    CSVAnalysisResult,
    HourStats,
    analyze_optimal_times,
    parse_csv,
)


def _write_csv(rows: list[dict], path: Path) -> Path:
    """Helper to write test CSV data."""
    fieldnames = ["Image", "PostLink", "Content", "Type", "Date", "Views", "Likes", "Replies", "Reposts", "Quotes", "Shares", "Engagement"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample CSV with known data."""
    rows = [
        {"Date": "2026-03-01 09:00", "Views": "5000", "Likes": "20", "Replies": "5", "Engagement": "0.50"},
        {"Date": "2026-03-01 09:30", "Views": "4000", "Likes": "15", "Replies": "3", "Engagement": "0.45"},
        {"Date": "2026-03-02 09:00", "Views": "6000", "Likes": "25", "Replies": "8", "Engagement": "0.55"},
        {"Date": "2026-03-01 19:00", "Views": "3000", "Likes": "10", "Replies": "2", "Engagement": "0.33"},
        {"Date": "2026-03-02 19:00", "Views": "4500", "Likes": "18", "Replies": "4", "Engagement": "0.49"},
        {"Date": "2026-03-01 12:00", "Views": "2000", "Likes": "5", "Replies": "1", "Engagement": "0.25"},
        {"Date": "2026-03-03 15:00", "Views": "3500", "Likes": "12", "Replies": "3", "Engagement": "0.43"},
        {"Date": "2026-03-03 21:00", "Views": "1000", "Likes": "3", "Replies": "0", "Engagement": "0.30"},
    ]
    return _write_csv(rows, tmp_path / "test.csv")


class TestHourStats:
    def test_score_no_posts(self):
        hs = HourStats(hour=10)
        assert hs.score == 0.0

    def test_score_with_posts(self):
        hs = HourStats(hour=9, post_count=2, total_views=10000, total_likes=40, total_engagement=1.0)
        assert hs.avg_views == 5000
        assert hs.avg_likes == 20
        assert hs.avg_engagement == 0.5
        assert hs.score > 0


class TestAnalyzeOptimalTimes:
    def test_basic_analysis(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        assert result.total_posts == 8
        assert result.hour_stats[9].post_count == 3
        assert result.hour_stats[19].post_count == 2
        assert result.hour_stats[12].post_count == 1

    def test_hour_9_is_top(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        assert result.top_hours[0] == 9  # 9:00 has best stats

    def test_optimal_slots_spacing(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        slots = result.get_optimal_slots(n=5)
        hours = [h for h, m in slots]
        # All hours should be at least 2 apart
        for i in range(len(hours) - 1):
            assert hours[i + 1] - hours[i] >= 2

    def test_optimal_slots_sorted(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        slots = result.get_optimal_slots(n=5)
        hours = [h for h, m in slots]
        assert hours == sorted(hours)

    def test_empty_csv(self, tmp_path):
        path = _write_csv([], tmp_path / "empty.csv")
        result = analyze_optimal_times(path)
        assert result.total_posts == 0
        assert result.get_optimal_slots() == []


class TestParseCSV:
    def test_parse_basic(self, sample_csv):
        rows = parse_csv(sample_csv)
        assert len(rows) == 8

    def test_multiline_content(self, tmp_path):
        """Verify multi-line Content fields are parsed correctly."""
        path = tmp_path / "multiline.csv"
        with path.open("w", encoding="utf-8") as f:
            f.write("Image,PostLink,Content,Type,Date,Views,Likes,Replies,Reposts,Quotes,Shares,Engagement\n")
            f.write(',,"Line1\nLine2\nLine3","TEXT_POST",2026-03-01 09:00,1000,5,1,0,0,0,"0.50"\n')
        rows = parse_csv(path)
        assert len(rows) == 1
        assert "Line1" in rows[0]["Content"]
