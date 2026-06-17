"""Tests for KnowledgeExtractor."""
import json
from typing import Optional

import pytest

from openviking.daemon.knowledge_extractor import KnowledgeExtractor
from openviking.daemon.models import ConversationTurn, ExtractedKnowledge


class MockVLMConfig:
    """Mock VLM config that returns canned string responses via get_completion_async."""

    def __init__(self, response_text: str):
        self.response_text = response_text

    async def get_completion_async(self, prompt: str = "", **kwargs) -> str:
        return self.response_text


def _make_turn(user="How to configure PostgreSQL?", assistant="Edit postgresql.conf"):
    return ConversationTurn(
        user_prompt=user,
        assistant_response=assistant,
        timestamp="2026-06-15T10:00:00Z",
    )


@pytest.mark.asyncio
async def test_extract_valid_knowledge():
    llm_response = json.dumps({
        "status": "EXTRACTED",
        "category": "skills",
        "confidence": 0.9,
        "title": "PostgreSQL Config",
        "content": "Configure PostgreSQL by editing postgresql.conf",
        "project_name": "my-project",
        "entity_links": ["PostgreSQL"],
        "actionable_steps": ["Edit postgresql.conf"],
    })
    extractor = KnowledgeExtractor(vlm_config=MockVLMConfig(llm_response))
    result = await extractor.extract(_make_turn())

    assert result is not None
    assert result.status == "EXTRACTED"
    assert result.category == "skills"
    assert result.confidence == 0.9


@pytest.mark.asyncio
async def test_extract_ignored_status():
    llm_response = json.dumps({
        "status": "IGNORED",
        "category": "memories",
        "confidence": 0.3,
        "title": "Typo fix",
        "content": "Fixed typo",
    })
    extractor = KnowledgeExtractor(vlm_config=MockVLMConfig(llm_response))
    result = await extractor.extract(_make_turn())

    assert result is None


@pytest.mark.asyncio
async def test_extract_low_confidence():
    llm_response = json.dumps({
        "status": "EXTRACTED",
        "category": "memories",
        "confidence": 0.4,
        "title": "Low confidence",
        "content": "Some content",
    })
    extractor = KnowledgeExtractor(vlm_config=MockVLMConfig(llm_response))
    result = await extractor.extract(_make_turn())

    assert result is None


@pytest.mark.asyncio
async def test_extract_invalid_json():
    extractor = KnowledgeExtractor(vlm_config=MockVLMConfig("not valid json"))
    result = await extractor.extract(_make_turn())

    assert result is None


@pytest.mark.asyncio
async def test_extract_cleans_markdown():
    llm_response = json.dumps({
        "status": "EXTRACTED",
        "category": "memories",
        "confidence": 0.8,
        "title": "Test",
        "content": "```python\nsome code\n```\nActual content here",
    })
    extractor = KnowledgeExtractor(vlm_config=MockVLMConfig(llm_response))
    result = await extractor.extract(_make_turn())

    assert result is not None
    assert "```" not in result.content


@pytest.mark.asyncio
async def test_title_truncated():
    llm_response = json.dumps({
        "status": "EXTRACTED",
        "category": "memories",
        "confidence": 0.8,
        "title": "A" * 100,
        "content": "Content",
    })
    extractor = KnowledgeExtractor(vlm_config=MockVLMConfig(llm_response))
    result = await extractor.extract(_make_turn())

    assert result is not None
    assert len(result.title) <= 50
