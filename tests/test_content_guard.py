"""Tests for content guard (NG detection + CTA)."""

import pytest

from snshack_threads.content_guard import append_cta, check_ng, suggest_cta


class TestCheckNG:
    def test_url_blocked(self):
        assert "URL" in check_ng("詳しくはhttps://example.com")

    def test_line_blocked(self):
        assert "LINE誘導" in check_ng("LINE公式に登録してください")

    def test_line_at_blocked(self):
        assert "LINE誘導" in check_ng("LINE@で配信中")

    def test_profile_link_blocked(self):
        assert "プロフリンク誘導" in check_ng("プロフのリンクを見てね")

    def test_fixed_post_blocked(self):
        assert "固定投稿誘導" in check_ng("固定投稿をチェックしてね")

    def test_dm_blocked(self):
        assert "DM誘導" in check_ng("DMください！")

    def test_clean_text_passes(self):
        assert check_ng("補助金を知らない社長は損してます") == []

    def test_engagement_cta_passes(self):
        assert check_ng("参考になったらコメントで教えてね") == []

    def test_multiple_violations(self):
        violations = check_ng("LINE公式はhttps://example.comから登録！固定投稿も見てね")
        assert len(violations) >= 2


class TestSuggestCTA:
    def test_returns_string(self):
        cta = suggest_cta()
        assert isinstance(cta, str)
        assert len(cta) > 0

    def test_contains_engagement_word(self):
        # Run multiple times to check variety
        engagement_words = ["コメント", "保存", "フォロー", "いいね", "リポスト", "教えて"]
        for _ in range(20):
            cta = suggest_cta()
            assert any(w in cta for w in engagement_words)


class TestAppendCTA:
    def test_appends_cta(self):
        text = "補助金の話"
        result = append_cta(text, "コメントで教えてね")
        assert "コメントで教えてね" in result
        assert result.startswith("補助金の話")

    def test_skips_if_already_has_cta(self):
        text = "補助金の話\n\nコメントで教えてください"
        result = append_cta(text)
        assert result == text  # Should not add another CTA

    def test_skips_save_cta(self):
        text = "大事な情報\n\n保存しておいてね"
        result = append_cta(text)
        assert result == text

    def test_respects_500_char_limit(self):
        text = "x" * 490
        result = append_cta(text, "コメントで教えてね")
        # Should not append because it would exceed 500
        assert result == text

    def test_within_limit_appends(self):
        text = "x" * 100
        result = append_cta(text, "保存してね")
        assert "保存してね" in result
