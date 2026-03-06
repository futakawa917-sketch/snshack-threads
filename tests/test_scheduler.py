"""Tests for the post scheduler."""

from datetime import datetime, timedelta
from pathlib import Path

from snshack_threads.models import PostDraft
from snshack_threads.scheduler import PostQueue


def _make_queue(tmp_path: Path) -> PostQueue:
    return PostQueue(data_dir=tmp_path)


class TestPostQueue:
    def test_add_and_list(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        draft = PostDraft(text="Test post")
        scheduled = datetime.now() + timedelta(hours=1)
        entry = q.add(draft, scheduled)

        assert entry.published is False
        pending = q.list_pending()
        assert len(pending) == 1
        assert pending[0].draft.text == "Test post"

    def test_remove(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        draft = PostDraft(text="To be removed")
        entry = q.add(draft, datetime.now())

        assert q.remove(entry.id) is True
        assert q.list_pending() == []

    def test_remove_nonexistent(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        assert q.remove("nonexistent") is False

    def test_mark_published(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        draft = PostDraft(text="Publish me")
        entry = q.add(draft, datetime.now())

        q.mark_published(entry.id, "post_999")
        pending = q.list_pending()
        assert len(pending) == 0

        all_items = q.list_all()
        assert all_items[0].published is True
        assert all_items[0].published_post_id == "post_999"

    def test_get_due(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        past = datetime.now() - timedelta(minutes=5)
        future = datetime.now() + timedelta(hours=1)

        q.add(PostDraft(text="Past"), past)
        q.add(PostDraft(text="Future"), future)

        due = q.get_due()
        assert len(due) == 1
        assert due[0].draft.text == "Past"

    def test_list_pending_order(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        t1 = datetime.now() + timedelta(hours=3)
        t2 = datetime.now() + timedelta(hours=1)
        t3 = datetime.now() + timedelta(hours=2)

        q.add(PostDraft(text="Third"), t1)
        q.add(PostDraft(text="First"), t2)
        q.add(PostDraft(text="Second"), t3)

        pending = q.list_pending()
        assert [p.draft.text for p in pending] == ["First", "Second", "Third"]
