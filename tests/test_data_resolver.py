"""Tests for the 3-tier data resolver."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from snshack_threads.data_resolver import (
    DataTier,
    resolve_examples,
    resolve_hooks,
    resolve_phase,
    resolve_times,
)


# ── resolve_hooks ─────────────────────────────────────────────


class TestResolveHooks:
    def test_account_tier_preferred(self):
        """Account data should be used when available."""
        account_hooks = [{"hook": "数字訴求", "avg_views": 200, "avg_likes": 5, "count": 15}]
        with patch("snshack_threads.data_resolver._account_hooks", return_value=account_hooks):
            result = resolve_hooks(profile="test")

        assert result.tier == DataTier.ACCOUNT
        assert result.hooks == account_hooks

    def test_genre_tier_when_no_account(self):
        """Genre data used when account has no data."""
        genre_hooks = [
            {"hook": "数字訴求", "avg_views": 500, "avg_likes": 10, "count": 20},
            {"hook": "疑問形", "avg_views": 300, "avg_likes": 8, "count": 15},
        ]
        with (
            patch("snshack_threads.data_resolver._account_hooks", return_value=None),
            patch("snshack_threads.data_resolver._genre_hooks", return_value=genre_hooks),
        ):
            result = resolve_hooks(profile="test")

        assert result.tier == DataTier.GENRE
        assert result.hooks == genre_hooks

    def test_universal_tier_as_last_resort(self):
        """Universal data used when no account or genre data."""
        universal_hooks = [{"hook": "number型", "avg_views": 400, "avg_likes": 7, "count": 50}]
        with (
            patch("snshack_threads.data_resolver._account_hooks", return_value=None),
            patch("snshack_threads.data_resolver._genre_hooks", return_value=None),
            patch("snshack_threads.data_resolver._universal_hooks", return_value=universal_hooks),
        ):
            result = resolve_hooks(profile="test")

        assert result.tier == DataTier.UNIVERSAL
        assert result.hooks == universal_hooks

    def test_empty_when_no_data_anywhere(self):
        """Empty hooks returned when no data at any tier."""
        with (
            patch("snshack_threads.data_resolver._account_hooks", return_value=None),
            patch("snshack_threads.data_resolver._genre_hooks", return_value=None),
            patch("snshack_threads.data_resolver._universal_hooks", return_value=None),
        ):
            result = resolve_hooks(profile="test")

        assert result.tier == DataTier.UNIVERSAL
        assert result.hooks == []

    def test_account_beats_genre(self):
        """Account data preferred even when genre has higher views."""
        account_hooks = [{"hook": "account_hook", "avg_views": 100, "avg_likes": 5, "count": 15}]
        genre_hooks = [{"hook": "genre_hook", "avg_views": 500, "avg_likes": 20, "count": 50}]
        with (
            patch("snshack_threads.data_resolver._account_hooks", return_value=account_hooks),
            patch("snshack_threads.data_resolver._genre_hooks", return_value=genre_hooks),
        ):
            result = resolve_hooks(profile="test")

        assert result.tier == DataTier.ACCOUNT
        assert result.hooks[0]["hook"] == "account_hook"

    def test_genre_beats_universal(self):
        """Genre data preferred over universal."""
        genre_hooks = [{"hook": "genre_hook", "avg_views": 300, "avg_likes": 10, "count": 20}]
        universal_hooks = [{"hook": "universal_hook", "avg_views": 200, "avg_likes": 8, "count": 100}]
        with (
            patch("snshack_threads.data_resolver._account_hooks", return_value=None),
            patch("snshack_threads.data_resolver._genre_hooks", return_value=genre_hooks),
            patch("snshack_threads.data_resolver._universal_hooks", return_value=universal_hooks),
        ):
            result = resolve_hooks(profile="test")

        assert result.tier == DataTier.GENRE
        assert result.hooks[0]["hook"] == "genre_hook"


# ── resolve_times ─────────────────────────────────────────────


class TestResolveTimes:
    def test_account_tier_preferred(self):
        """Account times used when available."""
        account_slots = [(7, 0), (10, 0), (13, 0), (17, 0), (20, 0)]
        with patch("snshack_threads.data_resolver._account_times", return_value=account_slots):
            result = resolve_times(profile="test")

        assert result.tier == DataTier.ACCOUNT
        assert result.slots == account_slots

    def test_genre_tier_fallback(self):
        """Genre times used when account has none."""
        genre_slots = [(9, 0), (12, 0), (15, 0), (19, 0), (22, 0)]
        with (
            patch("snshack_threads.data_resolver._account_times", return_value=None),
            patch("snshack_threads.data_resolver._genre_times", return_value=genre_slots),
        ):
            result = resolve_times(profile="test")

        assert result.tier == DataTier.GENRE
        assert result.slots == genre_slots

    def test_universal_tier_fallback(self):
        """Universal times used when no account or genre data."""
        universal_slots = [(8, 0), (12, 0), (16, 0), (20, 0)]
        with (
            patch("snshack_threads.data_resolver._account_times", return_value=None),
            patch("snshack_threads.data_resolver._genre_times", return_value=None),
            patch("snshack_threads.data_resolver._universal_times", return_value=universal_slots),
        ):
            result = resolve_times(profile="test")

        assert result.tier == DataTier.UNIVERSAL
        assert result.slots == universal_slots

    def test_default_fallback_when_no_data(self):
        """Default schedule when no data at any tier."""
        with (
            patch("snshack_threads.data_resolver._account_times", return_value=None),
            patch("snshack_threads.data_resolver._genre_times", return_value=None),
            patch("snshack_threads.data_resolver._universal_times", return_value=None),
        ):
            result = resolve_times(profile="test")

        assert result.tier == DataTier.UNIVERSAL
        assert len(result.slots) == 5
        assert result.slots == [(8, 0), (11, 0), (14, 0), (18, 0), (21, 0)]

    def test_passes_day_of_week(self):
        """day_of_week parameter is forwarded to tier functions."""
        with (
            patch("snshack_threads.data_resolver._account_times", return_value=None) as mock_account,
            patch("snshack_threads.data_resolver._genre_times", return_value=None) as mock_genre,
            patch("snshack_threads.data_resolver._universal_times", return_value=None) as mock_universal,
        ):
            resolve_times(profile="test", day_of_week=3)

        mock_account.assert_called_once_with("test", 3)
        mock_genre.assert_called_once_with("test", 3)
        mock_universal.assert_called_once_with(3)


# ── resolve_examples ──────────────────────────────────────────


class TestResolveExamples:
    def test_account_examples_preferred(self):
        """Account examples used when available."""
        with patch(
            "snshack_threads.data_resolver._account_examples",
            return_value=["example1", "example2"],
        ):
            result = resolve_examples(profile="test", n=5)

        assert result.tier == DataTier.ACCOUNT
        assert len(result.examples) == 2

    def test_genre_examples_fallback(self):
        """Genre examples used when account has none."""
        with (
            patch("snshack_threads.data_resolver._account_examples", return_value=None),
            patch(
                "snshack_threads.data_resolver._genre_examples",
                return_value=["genre_example"],
            ),
        ):
            result = resolve_examples(profile="test", n=5)

        assert result.tier == DataTier.GENRE
        assert result.examples == ["genre_example"]

    def test_empty_when_no_data(self):
        """Empty list when no examples at any tier."""
        with (
            patch("snshack_threads.data_resolver._account_examples", return_value=None),
            patch("snshack_threads.data_resolver._genre_examples", return_value=None),
        ):
            result = resolve_examples(profile="test", n=5)

        assert result.tier == DataTier.UNIVERSAL
        assert result.examples == []


# ── resolve_phase ─────────────────────────────────────────────


class TestResolvePhase:
    def _mock_history(self, count: int):
        """Create a mock PostHistory with N collected records."""
        from unittest.mock import MagicMock

        history = MagicMock()
        records = []
        for i in range(count):
            r = MagicMock()
            r.has_metrics = True
            records.append(r)
        history.get_all.return_value = records
        return history

    def test_bootstrap_no_data(self):
        """0 posts → bootstrap phase, universal tier."""
        mock_hist = self._mock_history(0)
        with (
            patch("snshack_threads.post_history.PostHistory", return_value=mock_hist),
            patch("snshack_threads.data_resolver._genre_hooks", return_value=None),
            patch("snshack_threads.data_resolver._universal_hooks", return_value=None),
        ):
            phase, tier = resolve_phase(profile="test")

        assert phase == "bootstrap"
        assert tier == DataTier.UNIVERSAL

    def test_bootstrap_with_genre_intelligence(self):
        """0 account posts but genre data → bootstrap + GENRE tier."""
        mock_hist = self._mock_history(0)
        genre_hooks = [{"hook": "数字訴求", "avg_views": 500, "avg_likes": 10, "count": 20}]
        with (
            patch("snshack_threads.post_history.PostHistory", return_value=mock_hist),
            patch("snshack_threads.data_resolver._genre_hooks", return_value=genre_hooks),
        ):
            phase, tier = resolve_phase(profile="test")

        assert phase == "bootstrap"
        assert tier == DataTier.GENRE

    def test_learning_phase(self):
        """120 posts → learning phase."""
        mock_hist = self._mock_history(120)
        with patch("snshack_threads.post_history.PostHistory", return_value=mock_hist):
            phase, tier = resolve_phase(profile="test")

        assert phase == "learning"
        assert tier == DataTier.ACCOUNT

    def test_optimized_phase(self):
        """200 posts → optimized phase."""
        mock_hist = self._mock_history(200)
        with patch("snshack_threads.post_history.PostHistory", return_value=mock_hist):
            phase, tier = resolve_phase(profile="test")

        assert phase == "optimized"
        assert tier == DataTier.ACCOUNT

    def test_few_posts_genre_tier(self):
        """5 posts (below threshold) with genre data → bootstrap + GENRE tier."""
        mock_hist = self._mock_history(5)
        genre_hooks = [{"hook": "疑問形", "avg_views": 300, "avg_likes": 8, "count": 15}]
        with (
            patch("snshack_threads.post_history.PostHistory", return_value=mock_hist),
            patch("snshack_threads.data_resolver._genre_hooks", return_value=genre_hooks),
        ):
            phase, tier = resolve_phase(profile="test")

        assert phase == "bootstrap"
        assert tier == DataTier.GENRE
