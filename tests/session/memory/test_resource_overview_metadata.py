import pytest

from openviking.service.reindex_executor import ReindexExecutor
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.queuefs.semantic_processor import SemanticProcessor

OVERVIEW_URI = "viking://resources/docs/.overview.md"
ENTITY_URI = "viking://user/alice/memories/entities/projects/openviking.md"


def _linked_overview(content: str, match_text: str) -> str:
    return MemoryFileUtils.write(
        MemoryFile(
            uri=OVERVIEW_URI,
            content=content,
            links=[
                {
                    "from_uri": OVERVIEW_URI,
                    "to_uri": ENTITY_URI,
                    "link_type": "related_to",
                    "match_text": match_text,
                }
            ],
        )
    )


def test_semantic_overview_parser_ignores_wiki_link_metadata(monkeypatch):
    monkeypatch.setattr(
        "openviking.session.memory.merge_op.link_merge.wiki_links_enabled",
        lambda: True,
    )
    raw = _linked_overview("### OpenViking\nMemory system", "OpenViking")

    assert SemanticProcessor()._parse_overview_md(raw) == {"OpenViking": "Memory system"}


def test_reindex_overview_parser_ignores_wiki_link_metadata(monkeypatch):
    monkeypatch.setattr(
        "openviking.session.memory.merge_op.link_merge.wiki_links_enabled",
        lambda: True,
    )
    raw = _linked_overview("## OpenViking\nMemory system", "OpenViking")

    assert ReindexExecutor._parse_overview_md(raw) == {"OpenViking": "Memory system"}


def test_overview_parsers_bypass_wiki_metadata_handling_when_links_disabled(monkeypatch):
    semantic_raw = _linked_overview("### OpenViking\nMemory system", "OpenViking")
    reindex_raw = _linked_overview("## OpenViking\nMemory system", "OpenViking")
    monkeypatch.setattr(
        "openviking.session.memory.merge_op.link_merge.wiki_links_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        MemoryFileUtils,
        "read",
        lambda *args, **kwargs: pytest.fail("Wiki metadata parsing must be bypassed"),
    )

    SemanticProcessor()._parse_overview_md(semantic_raw)
    ReindexExecutor._parse_overview_md(reindex_raw)
