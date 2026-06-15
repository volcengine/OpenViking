"""Tests for KnowledgeDeduplicator."""
from openviking.daemon.deduplicator import KnowledgeDeduplicator
from openviking.daemon.models import ExtractedKnowledge


def _make_knowledge(title: str, content: str) -> ExtractedKnowledge:
    return ExtractedKnowledge(
        status="EXTRACTED",
        category="memories",
        title=title,
        content=content,
    )


def test_first_occurrence_not_duplicate():
    dedup = KnowledgeDeduplicator()
    k = _make_knowledge("Test", "Some unique content")
    assert not dedup.is_duplicate(k)


def test_same_content_is_duplicate():
    dedup = KnowledgeDeduplicator()
    k1 = _make_knowledge("Title A", "Same content")
    k2 = _make_knowledge("Title B", "Same content")
    assert not dedup.is_duplicate(k1)
    assert dedup.is_duplicate(k2)


def test_different_content_not_duplicate():
    dedup = KnowledgeDeduplicator()
    k1 = _make_knowledge("A", "Content 1")
    k2 = _make_knowledge("B", "Content 2")
    assert not dedup.is_duplicate(k1)
    assert not dedup.is_duplicate(k2)


def test_clear_resets_cache():
    dedup = KnowledgeDeduplicator()
    k = _make_knowledge("Test", "Content")
    dedup.is_duplicate(k)
    dedup.clear()
    assert not dedup.is_duplicate(k)
