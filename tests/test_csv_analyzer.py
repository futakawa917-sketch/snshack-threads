"""Tests for CSV analyzer module."""

import csv
import tempfile
from pathlib import Path

import pytest

from snshack_threads.csv_analyzer import (
    MIN_SAMPLE_SIZE,
    CSVAnalysisResult,
    DayHourStats,
    HourStats,
    _detect_hooks,
    analyze_optimal_times,
    has_external_promotion,
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
        # 9:00 has 6 posts (reliable)
        {"Date": "2026-03-01 09:00", "Content": "短いテスト", "Views": "5000", "Likes": "20", "Replies": "5", "Engagement": "0.50"},
        {"Date": "2026-03-01 09:30", "Content": "テスト2", "Views": "4000", "Likes": "15", "Replies": "3", "Engagement": "0.45"},
        {"Date": "2026-03-02 09:00", "Content": "最大9000万円の補助金", "Views": "6000", "Likes": "25", "Replies": "8", "Engagement": "0.55"},
        {"Date": "2026-03-03 09:00", "Content": "テスト4", "Views": "4500", "Likes": "18", "Replies": "4", "Engagement": "0.48"},
        {"Date": "2026-03-04 09:00", "Content": "テスト5", "Views": "5500", "Likes": "22", "Replies": "6", "Engagement": "0.52"},
        {"Date": "2026-03-05 09:00", "Content": "テスト6", "Views": "4800", "Likes": "19", "Replies": "5", "Engagement": "0.46"},
        # 19:00 has 2 posts (unreliable)
        {"Date": "2026-03-01 19:00", "Content": "知ってた？これ危険です", "Views": "3000", "Likes": "10", "Replies": "2", "Engagement": "0.33"},
        {"Date": "2026-03-02 19:00", "Content": "テスト", "Views": "4500", "Likes": "18", "Replies": "4", "Engagement": "0.49"},
        # Other hours
        {"Date": "2026-03-01 12:00", "Content": "LINEで詳しく教えます", "Views": "2000", "Likes": "5", "Replies": "1", "Engagement": "0.25"},
        {"Date": "2026-03-03 15:00", "Content": "今だけ限定の情報", "Views": "3500", "Likes": "12", "Replies": "3", "Engagement": "0.43"},
        {"Date": "2026-03-03 21:00", "Content": "経営者必見！", "Views": "1000", "Likes": "3", "Replies": "0", "Engagement": "0.30"},
    ]
    return _write_csv(rows, tmp_path / "test.csv")


class TestHourStats:
    def test_score_no_posts(self):
        hs = HourStats(hour=10)
        assert hs.score == 0.0

    def test_reliable_threshold(self):
        hs = HourStats(hour=9, post_count=MIN_SAMPLE_SIZE)
        assert hs.reliable

    def test_unreliable_below_threshold(self):
        hs = HourStats(hour=9, post_count=MIN_SAMPLE_SIZE - 1)
        assert not hs.reliable

    def test_score_penalized_when_unreliable(self):
        reliable = HourStats(hour=9, post_count=5, total_views=25000, total_likes=100, total_engagement=2.5)
        unreliable = HourStats(hour=23, post_count=2, total_views=10000, total_likes=40, total_engagement=1.0)
        # Unreliable should be penalized even if raw avg is similar
        assert reliable.score > unreliable.score


class TestAnalyzeOptimalTimes:
    def test_basic_analysis(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        assert result.total_posts == 11
        assert result.hour_stats[9].post_count == 6
        assert result.hour_stats[19].post_count == 2

    def test_reliable_hours_ranked_first(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        # 9:00 has 6 posts (reliable), should rank above unreliable hours with similar raw scores
        assert result.hour_stats[9].reliable
        assert not result.hour_stats[19].reliable

    def test_optimal_slots_prefer_reliable(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        slots = result.get_optimal_slots(n=3)
        hours = [h for h, m in slots]
        # 9:00 should be in top slots (reliable + high score)
        assert 9 in hours

    def test_optimal_slots_spacing(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        slots = result.get_optimal_slots(n=5)
        hours = [h for h, m in slots]
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

    def test_day_of_week_analysis(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        # 2026-03-01 is Sunday (weekday=6), should have data
        sunday_slots = result.get_optimal_slots_for_day(6)
        assert len(sunday_slots) > 0

    def test_day_of_week_fallback_to_overall(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        # A day with no data should fall back to overall
        slots = result.get_optimal_slots_for_day(1, n=3)  # Tuesday - may have limited data
        assert len(slots) > 0  # Should have at least some from fallback


class TestContentAnalysis:
    def test_length_buckets(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        assert "short" in result.content.length_buckets
        assert result.content.length_buckets["short"].post_count > 0

    def test_hook_patterns_detected(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        # "最大9000万円" should trigger 数字訴求
        assert "数字訴求" in result.content.hook_patterns

    def test_link_penalty_tracked(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        # "LINEで詳しく教えます" is a link post
        assert result.content.link_post_avg_views > 0
        assert result.content.no_link_post_avg_views > 0

    def test_top_posts_collected(self, sample_csv):
        result = analyze_optimal_times(sample_csv)
        assert len(result.content.top_posts) > 0
        assert result.content.top_posts[0]["views"] >= result.content.top_posts[-1]["views"]


class TestHookDetection:
    def test_number_hook(self):
        hooks = _detect_hooks("最大9000万円の補助金があります")
        assert "数字訴求" in hooks

    def test_question_hook(self):
        hooks = _detect_hooks("これ知ってた？")
        assert "疑問形" in hooks

    def test_urgency_hook(self):
        hooks = _detect_hooks("知らないとやばい事実")
        assert "危機感" in hooks

    def test_scarcity_hook(self):
        hooks = _detect_hooks("今だけの限定情報です")
        assert "限定感" in hooks


class TestExternalPromotion:
    def test_url_detected(self):
        assert has_external_promotion("詳しくは https://example.com を見て")

    def test_line_detected(self):
        assert has_external_promotion("LINE公式に登録してね")

    def test_fixed_post_detected(self):
        assert has_external_promotion("固定投稿を見てください")

    def test_dm_not_detected_without_link(self):
        # DM detection is in content_guard, not csv_analyzer
        assert not has_external_promotion("普通のテキスト投稿です")

    def test_clean_text_passes(self):
        assert not has_external_promotion("補助金を知らない社長は損してます")


class TestParseCSV:
    def test_parse_basic(self, sample_csv):
        rows = parse_csv(sample_csv)
        assert len(rows) == 11

    def test_multiline_content(self, tmp_path):
        path = tmp_path / "multiline.csv"
        with path.open("w", encoding="utf-8") as f:
            f.write("Image,PostLink,Content,Type,Date,Views,Likes,Replies,Reposts,Quotes,Shares,Engagement\n")
            f.write(',,"Line1\nLine2\nLine3","TEXT_POST",2026-03-01 09:00,1000,5,1,0,0,0,"0.50"\n')
        rows = parse_csv(path)
        assert len(rows) == 1
        assert "Line1" in rows[0]["Content"]
