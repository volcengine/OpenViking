"""Tests for KnowledgeRouter."""
from openviking.daemon.knowledge_router import KnowledgeRouter
from openviking.daemon.models import ExtractedKnowledge


def _make(**kwargs) -> ExtractedKnowledge:
    defaults = {
        "status": "EXTRACTED",
        "category": "memories",
        "title": "Test Title",
        "content": "Some content",
    }
    defaults.update(kwargs)
    return ExtractedKnowledge(**defaults)


def test_route_skills():
    router = KnowledgeRouter()
    k = _make(category="skills", title="PostgreSQL Config")
    uri = router.route(k)
    assert uri.startswith("viking://resources/skills/general/")
    assert uri.endswith(".md")


def test_route_skills_with_source_tool():
    router = KnowledgeRouter()
    k = _make(category="skills", title="PostgreSQL Config", source_tool="claude_code")
    uri = router.route(k)
    assert uri.startswith("viking://resources/skills/claude_code/")
    assert uri.endswith(".md")


def test_route_memories_with_project():
    router = KnowledgeRouter()
    k = _make(category="memories", title="Arch Decision", project_name="my-project")
    uri = router.route(k)
    assert "my-project" in uri
    assert uri.endswith("decisions.md")


def test_route_memories_global():
    router = KnowledgeRouter()
    k = _make(category="memories", title="Global Memory")
    uri = router.route(k)
    assert uri.startswith("viking://resources/memories/global/")


def test_route_resources():
    router = KnowledgeRouter()
    k = _make(category="resources", title="Redis Guide", entity_links=["Redis"])
    uri = router.route(k)
    assert "Redis" in uri
    assert uri.endswith(".md")


def test_route_resources_no_tags():
    router = KnowledgeRouter()
    k = _make(category="resources", title="General", entity_links=[])
    uri = router.route(k)
    assert "general" in uri


def test_route_unknown_category():
    router = KnowledgeRouter()
    k = _make(category="unknown")
    uri = router.route(k)
    assert uri is None


def test_sanitize_filename():
    router = KnowledgeRouter()
    assert router._sanitize_filename('file<>:name') == 'file___name'
    assert router._sanitize_filename("a" * 100) == "a" * 50


def test_sanitize_filename_non_ascii():
    router = KnowledgeRouter()
    result = router._sanitize_filename("中文标题测试")
    assert len(result) == 16
    assert result.isascii()
    assert all(c in "0123456789abcdef" for c in result)
