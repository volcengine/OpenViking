import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.agent.tools.compile import CompileScopedTool, SubmitWikiBundleTool
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.compile.models import (
    DEFAULT_COMPILE_REASON,
    CompileFailure,
    CompileLimits,
    CompileRequest,
    CompileTask,
    SanitizedCompileRequest,
    WikiBundleDraft,
    utc_now,
)
from vikingbot.compile.renderer import WikiRenderer, content_hash
from vikingbot.compile.service import BotCompileService
from vikingbot.compile.store import CompileTaskStore
from vikingbot.config.schema import SessionKey

from openviking.core.skill_loader import SkillLoader
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.exceptions import OpenVikingError


def _page(page_id: int, title: str, **overrides):
    value = {
        "page_id": page_id,
        "title": title,
        "page_type": "concept",
        "summary": f"Summary for {title}",
        "body_markdown": f"Body for {title}",
        "source_ids": ["src_1"],
        "path_hint": f"{title.lower()}.md",
    }
    value.update(overrides)
    return value


def test_skill_loader_distinguishes_missing_and_explicit_empty_allowed_tools():
    missing = SkillLoader.parse("---\nname: a\ndescription: A\n---\nDo it")
    empty = SkillLoader.parse("---\nname: a\ndescription: A\nallowed-tools: []\n---\nDo it")
    assert missing["allowed_tools_declared"] is False
    assert empty["allowed_tools_declared"] is True
    assert "allowed-tools: ''" in SkillLoader.to_skill_md(empty)


def test_skill_loader_accepts_standard_and_legacy_allowed_tools_forms():
    standard = SkillLoader.parse(
        "---\nname: a\ndescription: A\nallowed-tools: Read Write Bash(git:*)\n---\nDo it"
    )
    ara = SkillLoader.parse(
        "---\nname: a\ndescription: A\n"
        "allowed-tools: Read, Write, Bash(python *|git clone *), Glob\n---\nDo it"
    )
    legacy = SkillLoader.parse(
        "---\nname: a\ndescription: A\nallowed-tools: [Read, Write]\n---\nDo it"
    )

    assert standard["allowed_tools"] == ["Read", "Write", "Bash(git:*)"]
    assert ara["allowed_tools"] == [
        "Read",
        "Write",
        "Bash(python *|git clone *)",
        "Glob",
    ]
    assert legacy["allowed_tools"] == ["Read", "Write"]


def test_skill_loader_rejects_invalid_allowed_tools():
    with pytest.raises(ValueError, match="space-separated string or an array of strings"):
        SkillLoader.parse("---\nname: a\ndescription: A\nallowed-tools: [Read, 3]\n---\nDo it")
    with pytest.raises(ValueError, match="unbalanced parentheses"):
        SkillLoader.parse(
            "---\nname: a\ndescription: A\nallowed-tools: Read Bash(git:*\n---\nDo it"
        )


def test_compile_bundle_schema_distinguishes_wiki_pages_and_artifact_files():
    schema = WikiBundleDraft.model_json_schema()
    properties = schema["properties"]
    page_properties = schema["$defs"]["WikiPageDraft"]["properties"]

    assert "Actual Wiki pages only" in properties["pages"]["description"]
    assert "Markdown, YAML, JSON" in properties["files"]["description"]
    assert "generated Wiki pages only" in properties["links"]["description"]
    assert "known source URIs" in page_properties["body_markdown"]["description"]
    assert "task workspace" in page_properties["body_workspace_path"]["description"]
    assert "reader-oriented" in page_properties["body_workspace_path"]["description"]
    assert "source catalog entries" in page_properties["body_workspace_path"]["description"]
    assert "supplied source roots" in page_properties["source_ids"]["description"]


def test_wiki_page_requires_exactly_one_body_source():
    body = _page(1, "One")
    body.pop("body_markdown")

    with pytest.raises(ValueError, match="exactly one of body_markdown"):
        WikiBundleDraft.model_validate({"pages": [body]})
    with pytest.raises(ValueError, match="exactly one of body_markdown"):
        WikiBundleDraft.model_validate(
            {
                "pages": [
                    {
                        **body,
                        "body_markdown": "Inline",
                        "body_workspace_path": "pages/one.md",
                    }
                ]
            }
        )


def test_submit_tool_rejects_raw_payload_wrapper_with_actionable_hint():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )

    assert tool.validate_params({"raw": "{}"}) == [
        "use the tool schema directly; do not wrap the payload in a JSON string"
    ]
    assert {"pages", "files"} <= set(tool.parameters["required"])
    assert "missing required files" in tool.validate_params({"pages": []})
    assert tool.validate_params({"pages": [], "files": []}) == []


def test_submit_tool_schema_requires_workspace_page_bodies_when_available():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
        require_workspace_pages=True,
    )

    page_schema = tool.parameters["$defs"]["WikiPageDraft"]
    assert "body_markdown" not in page_schema["properties"]
    assert "body_workspace_path" in page_schema["required"]


@pytest.mark.asyncio
async def test_submit_tool_accepts_only_one_complete_skill_package():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://agent/skills",
        limits=CompileLimits(),
    )

    schema = tool.parameters
    assert set(schema["properties"]) == {"files"}
    assert schema["required"] == ["files"]
    assert set(schema["$defs"]) == {"CompileFileDraft"}
    assert "update_uri" not in schema["$defs"]["CompileFileDraft"]["properties"]
    assert "path" in schema["$defs"]["CompileFileDraft"]["required"]

    accepted = await tool.execute(
        ToolContext(),
        files=[
            {
                "path": "weekly-report/SKILL.md",
                "content": (
                    "---\n"
                    "name: weekly-report\n"
                    "description: Generate a concise weekly report.\n"
                    "---\n\n"
                    "Follow the source material and produce the report."
                ),
            },
            {
                "path": "weekly-report/references/format.md",
                "content": "# Weekly report format\n",
            },
        ],
    )

    assert accepted == "Skill bundle accepted for 'weekly-report' with 2 file(s)."
    assert tool.skill_name == "weekly-report"
    assert tool.bundle is not None and tool.bundle.pages == []

    missing_skill_md = await tool.execute(
        ToolContext(),
        files=[{"path": "weekly-report/references/format.md", "content": "# Format"}],
    )
    assert "must include weekly-report/SKILL.md" in missing_skill_md

    multiple_skills = await tool.execute(
        ToolContext(),
        files=[
            {
                "path": "one/SKILL.md",
                "content": "---\nname: one\ndescription: One\n---\nOne",
            },
            {"path": "two/guide.md", "content": "Two"},
        ],
    )
    assert "exactly one top-level Skill directory" in multiple_skills

    derived_file = await tool.execute(
        ToolContext(),
        files=[
            {
                "path": "weekly-report/SKILL.md",
                "content": ("---\nname: weekly-report\ndescription: Weekly report\n---\nWrite it"),
            },
            {"path": "weekly-report/.overview.md", "content": "Generated"},
        ],
    )
    assert "invalid output file path" in derived_file
    assert tool.bundle is None

    invalid_yaml = await tool.execute(
        ToolContext(),
        files=[
            {
                "path": "weekly-report/SKILL.md",
                "content": "---\nname: [\ndescription: Weekly report\n---\nWrite it",
            }
        ],
    )
    assert invalid_yaml.startswith("Error: Invalid Skill bundle:")

    long_description = await tool.execute(
        ToolContext(),
        files=[
            {
                "path": "weekly-report/SKILL.md",
                "content": (
                    "---\nname: weekly-report\ndescription: " + "x" * 1025 + "\n---\nWrite it"
                ),
            }
        ],
    )
    assert "description must not exceed 1024 characters" in long_description


def test_renderer_creates_okf_pages_links_and_citations():
    summary = "Residual building block designs, network variants, shortcut connection types, and design principles aligned with VGG architecture."
    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [
                _page(1, "Alpha", body_markdown="Read Beta next.", summary=summary),
                _page(2, "Beta"),
            ],
            "links": [{"f": 1, "t": 2, "match_text": "Beta"}],
        }
    )
    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris=set(),
        existing_raw={},
    )
    assert rendered.created == [
        "viking://resources/wiki/alpha.md",
        "viking://resources/wiki/beta.md",
    ]
    assert rendered.link_count == 1
    first = rendered.operations[0]
    assert first["precondition"] == {"kind": "create_if_absent"}
    assert "type: concept" in first["content"]
    assert f"description: {summary}\n" in first["content"]
    assert "Read [Beta](./beta.md) next." in first["content"]
    assert "[1] [source](viking://resources/source)" in first["content"]


def test_renderer_linkifies_source_uris_and_adds_resource_backlinks():
    source_detail = "viking://resources/source/chapter_1.md"
    outside = "viking://resources/outside/chapter.md"
    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [
                _page(
                    1,
                    "Overview",
                    body_markdown=(
                        "Read Details next.\n\n"
                        f"Source: {source_detail} section 2\n\n"
                        f"Keep code unchanged: `{source_detail}`\n\n"
                        f"Outside stays plain: {outside}"
                    ),
                ),
                _page(2, "Details"),
            ],
            "links": [{"f": 1, "t": 2, "match_text": "Details"}],
        }
    )

    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris=set(),
        existing_raw={},
    )
    operations = {operation["uri"]: operation["content"] for operation in rendered.operations}
    overview = operations["viking://resources/wiki/overview.md"]
    details = operations["viking://resources/wiki/details.md"]

    assert "[Details](./details.md)" in overview
    assert f"[chapter_1]({source_detail})" in overview
    assert f"`{source_detail}`" in overview
    assert outside in overview and f"]({outside})" not in overview
    assert f"[1] [chapter_1]({source_detail})" in overview
    assert "[2] [source](viking://resources/source)" in overview
    assert "## Related pages" in details
    assert "- [Overview](./overview.md)" in details
    assert rendered.link_count == 1


def test_renderer_adds_raw_text_and_workspace_binary_to_same_bundle():
    paper = "---\ntitle: ARA Demo\nauthors: [Ada]\n---\n\n# Layer Index\n"
    image_bytes = b"\x89PNG\r\n\x1a\nfigure"
    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [_page(1, "Overview")],
            "files": [
                {"path": "PAPER.md", "content": paper},
                {
                    "path": "trace/exploration_tree.yaml",
                    "content": "nodes: []\n",
                },
                {
                    "path": "evidence/figures/figure1.png",
                    "workspace_path": "ara-output/figure1.png",
                },
            ],
        }
    )
    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris=set(),
        existing_raw={},
        file_payloads=[None, None, image_bytes],
    )
    operations = {operation["uri"]: operation for operation in rendered.operations}
    assert operations["viking://resources/wiki/PAPER.md"]["content"] == paper
    assert (
        operations["viking://resources/wiki/trace/exploration_tree.yaml"]["content"]
        == "nodes: []\n"
    )
    encoded = operations["viking://resources/wiki/evidence/figures/figure1.png"]["content_base64"]
    assert base64.b64decode(encoded) == image_bytes
    assert rendered.created == [
        "viking://resources/wiki/overview.md",
        "viking://resources/wiki/PAPER.md",
        "viking://resources/wiki/trace/exploration_tree.yaml",
        "viking://resources/wiki/evidence/figures/figure1.png",
    ]


def test_renderer_accepts_minimal_okf_artifact_with_unknown_fields():
    content = (
        "---\n"
        "type: research_artifact\n"
        "ara_version: 1.0\n"
        "custom: anything\n"
        "---\n\n"
        "# Research artifact\n"
    )
    bundle = WikiBundleDraft.model_validate(
        {"pages": [], "files": [{"path": "research.md", "content": content}]}
    )

    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={},
        catalog_uris=set(),
        existing_raw={},
    )

    assert rendered.operations[0]["content"] == content


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("---\ntype:\n---\n", 'field "type" must be a non-empty string'),
        ("---\ntype: [concept]\n---\n", 'field "type" must be a non-empty string'),
        ("---\ntype: concept\nauthors: [\n---\n", "invalid YAML frontmatter"),
        ("---\ntype: concept\n# no closing delimiter", "unterminated YAML frontmatter"),
    ],
)
def test_renderer_rejects_invalid_declared_okf_artifact(content, message):
    bundle = WikiBundleDraft.model_validate(
        {"pages": [], "files": [{"path": "invalid.md", "content": content}]}
    )

    with pytest.raises(ValueError, match=message):
        WikiRenderer().render(
            bundle=bundle,
            target_uri="viking://resources/wiki",
            source_roots={},
            catalog_uris=set(),
            existing_raw={},
        )


def test_renderer_checks_size_before_parsing_okf_artifact():
    content = "---\ntype: [broken\n---\n"
    bundle = WikiBundleDraft.model_validate(
        {"pages": [], "files": [{"path": "large.md", "content": content}]}
    )

    with pytest.raises(ValueError, match="final content size limit"):
        WikiRenderer(CompileLimits(output_total_bytes=8)).render(
            bundle=bundle,
            target_uri="viking://resources/wiki",
            source_roots={},
            catalog_uris=set(),
            existing_raw={},
        )


def test_renderer_rejects_page_path_colliding_with_artifact():
    bundle = WikiBundleDraft.model_validate(
        {"pages": [_page(1, "PAPER", path_hint="PAPER.md")]}
    )

    with pytest.raises(ValueError, match="already exists"):
        WikiRenderer().render(
            bundle=bundle,
            target_uri="viking://resources/wiki",
            source_roots={"src_1": "viking://resources/source"},
            catalog_uris=set(),
            file_catalog_uris={"viking://resources/wiki/PAPER.md"},
            existing_raw={},
        )


def test_renderer_raw_update_cannot_remove_okf_from_existing_wiki_page():
    uri = "viking://resources/wiki/existing.md"
    bundle = WikiBundleDraft.model_validate(
        {"pages": [], "files": [{"update_uri": uri, "content": "# Plain Markdown"}]}
    )

    with pytest.raises(ValueError, match="must retain valid OKF"):
        WikiRenderer().render(
            bundle=bundle,
            target_uri="viking://resources/wiki",
            source_roots={},
            catalog_uris={uri},
            file_catalog_uris={uri},
            existing_raw={},
            existing_bytes={uri: b"---\ntype: concept\n---\n\n# Existing"},
        )


def test_renderer_raw_file_update_uses_byte_hash_and_detects_unchanged():
    uri = "viking://resources/wiki/trace/exploration_tree.yaml"
    old = b"nodes: []\n"
    unchanged_bundle = WikiBundleDraft.model_validate(
        {"pages": [], "files": [{"update_uri": uri, "content": old.decode()}]}
    )
    renderer = WikiRenderer()
    unchanged = renderer.render(
        bundle=unchanged_bundle,
        target_uri="viking://resources/wiki",
        source_roots={},
        catalog_uris=set(),
        existing_raw={},
        file_catalog_uris={uri},
        existing_bytes={uri: old},
    )
    assert unchanged.unchanged == [uri]
    assert unchanged.operations == []

    changed_bundle = WikiBundleDraft.model_validate(
        {"pages": [], "files": [{"update_uri": uri, "content": "nodes: [root]\n"}]}
    )
    changed = renderer.render(
        bundle=changed_bundle,
        target_uri="viking://resources/wiki",
        source_roots={},
        catalog_uris=set(),
        existing_raw={},
        file_catalog_uris={uri},
        existing_bytes={uri: old},
    )
    assert changed.updated == [uri]
    assert changed.operations[0]["precondition"] == {
        "kind": "replace_if_hash",
        "base_hash": content_hash(old),
    }


def test_renderer_empty_existing_update_uses_hash_precondition_and_preserves_uri():
    uri = "viking://resources/wiki/empty.md"
    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [
                _page(1, "Empty", path_hint=None, update_uri=uri),
            ]
        }
    )
    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris={uri},
        existing_raw={uri: ""},
    )
    assert rendered.updated == [uri]
    assert rendered.created == []
    assert rendered.operations[0]["precondition"] == {
        "kind": "replace_if_hash",
        "base_hash": content_hash(""),
    }


def test_renderer_preserves_unknown_frontmatter_and_merges_scoped_citations():
    uri = "viking://resources/wiki/topic.md"
    old = """---
type: legacy
title: Old
description: Old summary
custom: keep-me
---

Old body

# Citations

[1] [Detail](viking://resources/source/detail.md)
[2] [Outside](viking://resources/other/no.md)
"""
    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [
                _page(
                    1,
                    "Topic",
                    update_uri=uri,
                    path_hint=None,
                    tags=[" stable ", "stable", "new"],
                    body_markdown=(
                        "New body\n\n# Citations\n\n"
                        "[9] [Detail](viking://resources/source/detail.md)"
                    ),
                )
            ]
        }
    )
    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris={uri},
        existing_raw={uri: old},
    )
    content = rendered.operations[0]["content"]
    assert "custom: keep-me" in content
    assert "tags: [stable, new]" in content
    assert content.count("viking://resources/source/detail.md") == 1
    assert "viking://resources/other/no.md" not in content
    assert "[2] [source](viking://resources/source)" in content


def test_memory_renderer_round_trips_fields_and_only_bumps_changed_version():
    uri = "viking://user/alice/memories/preferences/wiki/topic.md"
    initial_bundle = WikiBundleDraft.model_validate(
        {"pages": [_page(1, "Topic", path_hint="topic.md")]}
    )
    renderer = WikiRenderer()
    created = renderer.render(
        bundle=initial_bundle,
        target_uri="viking://user/alice/memories/preferences/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris=set(),
        existing_raw={},
    )
    raw = created.operations[0]["content"]
    memory = MemoryFileUtils.read(raw, uri=uri)
    assert memory.extra_fields["category"] == "concept"
    assert memory.extra_fields["version"] == 1

    update = WikiBundleDraft.model_validate(
        {"pages": [_page(1, "Topic", path_hint=None, update_uri=uri)]}
    )
    unchanged = renderer.render(
        bundle=update,
        target_uri="viking://user/alice/memories/preferences/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris={uri},
        existing_raw={uri: raw},
    )
    assert unchanged.unchanged == [uri]
    assert unchanged.operations == []

    changed_bundle = WikiBundleDraft.model_validate(
        {
            "pages": [
                _page(
                    1,
                    "Topic",
                    path_hint=None,
                    update_uri=uri,
                    body_markdown="Changed body",
                )
            ]
        }
    )
    changed = renderer.render(
        bundle=changed_bundle,
        target_uri="viking://user/alice/memories/preferences/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris={uri},
        existing_raw={uri: raw},
    )
    assert MemoryFileUtils.read(changed.operations[0]["content"]).extra_fields["version"] == 2


@pytest.mark.asyncio
async def test_submit_tool_rejects_protected_anchor_and_path_collision():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        file_catalog_uris={"viking://resources/wiki/existing.md"},
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )
    context = ToolContext()
    collision = await tool.execute(
        context,
        pages=[_page(1, "Existing", path_hint="existing.md")],
    )
    assert collision.startswith("Error:")
    protected = await tool.execute(
        context,
        pages=[
            _page(1, "One", body_markdown="`Two`"),
            _page(2, "Two"),
        ],
        links=[{"f": 1, "t": 2, "match_text": "Two"}],
    )
    assert protected.startswith("Error:")
    assert tool.bundle is None

    accepted = await tool.execute(context, pages=[], links=[])
    assert not accepted.startswith("Error:")
    assert tool.bundle is not None and tool.bundle.pages == []


@pytest.mark.asyncio
async def test_submit_tool_checks_size_before_parsing_okf_artifact():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(output_total_bytes=8),
    )

    result = await tool.execute(
        ToolContext(),
        pages=[],
        files=[
            {
                "path": "large.md",
                "content": "---\ntype: [broken\n---\n",
            }
        ],
    )

    assert result.startswith("Error: Invalid Wiki bundle:")
    assert "draft content size limit exceeded" in result


@pytest.mark.asyncio
async def test_submit_tool_raw_update_cannot_remove_okf_from_existing_wiki_page():
    uri = "viking://resources/wiki/existing.md"
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris={uri},
        file_catalog_uris={uri},
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )

    result = await tool.execute(
        ToolContext(),
        pages=[],
        files=[{"update_uri": uri, "content": "# Plain Markdown"}],
    )

    assert result.startswith("Error: Invalid Wiki bundle:")
    assert "must retain valid OKF frontmatter" in result


@pytest.mark.asyncio
async def test_submit_tool_reports_all_invalid_links():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )

    result = await tool.execute(
        ToolContext(),
        pages=[
            _page(1, "One", body_markdown="First page body"),
            _page(2, "Two"),
            _page(3, "Three"),
        ],
        links=[
            {"f": 1, "t": 2, "match_text": "Missing One"},
            {"f": 1, "t": 3, "match_text": "Missing Two"},
        ],
    )

    assert result.startswith("Error: Invalid Wiki bundle: 2 invalid link(s):")
    assert "links[0] from page 1 has non-linkable anchor 'Missing One'" in result
    assert "links[1] from page 1 has non-linkable anchor 'Missing Two'" in result
    assert tool.bundle is None


@pytest.mark.asyncio
async def test_submit_tool_requires_workspace_paths_for_multi_file_artifacts():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
        require_workspace_files=True,
    )
    result = await tool.execute(
        ToolContext(),
        pages=[],
        files=[
            {
                "path": "logic/claims.md",
                "content": "# Claims",
            },
            {
                "path": "logic/concepts.md",
                "workspace_path": "ara-output/logic/concepts.md",
            },
        ],
    )

    assert result.startswith("Error: Invalid Wiki bundle:")
    assert "must be generated with write_file" in result
    assert tool.bundle is None

    accepted = await tool.execute(
        ToolContext(),
        pages=[],
        files=[
            {
                "path": "PAPER.md",
                "content": "---\ntitle: ARA Paper\nauthors: [Ada]\n---\n\n# Paper",
            }
        ],
    )
    assert accepted == "Wiki bundle accepted with 0 page(s) and 1 file(s)."

    rejected = await tool.execute(
        ToolContext(),
        pages=[],
        files=[{"path": "concept.md", "content": "---\ntype: ''\n---\n"}],
    )
    assert rejected.startswith("Error: Invalid Wiki bundle:")
    assert 'field "type" must be a non-empty string' in rejected


@pytest.mark.asyncio
async def test_submit_tool_reads_explicit_workspace_file_and_rejects_memory_files():
    class Sandbox:
        async def read_file_bytes(self, path):
            assert path == "ara-output/figure.png"
            return b"PNG"

    class Manager:
        async def get_sandbox(self, session_key):
            assert session_key is not None
            return Sandbox()

    context = ToolContext(
        session_key=SessionKey(type="compile", channel_id="cmp", chat_id="cmp"),
        sandbox_manager=Manager(),
    )
    resource_tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )
    params = {
        "pages": [],
        "files": [
            {
                "path": "evidence/figure.png",
                "workspace_path": "ara-output/figure.png",
            }
        ],
    }
    accepted = await resource_tool.execute(context, **params)
    assert not accepted.startswith("Error:")
    assert resource_tool.file_payloads == [b"PNG"]

    memory_tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://user/memories/preferences/wiki",
        limits=CompileLimits(),
    )
    rejected = await memory_tool.execute(
        context,
        pages=[],
        files=[{"path": "trace/tree.yaml", "content": "nodes: []"}],
    )
    assert rejected.startswith("Error:")
    assert "Resource targets" in rejected


@pytest.mark.asyncio
async def test_submit_tool_rejects_non_utf8_declared_okf_workspace_markdown():
    class Sandbox:
        async def read_file_bytes(self, path):
            assert path == "generated/concept.md"
            return b"---\ntype: concept\n---\n\xff"

    class Manager:
        async def get_sandbox(self, session_key):
            return Sandbox()

    context = ToolContext(
        session_key=SessionKey(type="compile", channel_id="cmp", chat_id="cmp"),
        sandbox_manager=Manager(),
    )
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )

    rejected = await tool.execute(
        context,
        pages=[],
        files=[
            {
                "path": "concept.md",
                "workspace_path": "generated/concept.md",
            }
        ],
    )

    assert rejected.startswith("Error: Invalid Wiki bundle:")
    assert "must be UTF-8" in rejected


@pytest.mark.asyncio
async def test_submit_tool_materializes_workspace_page_body_before_validation():
    class Sandbox:
        async def read_file_bytes(self, path):
            return {
                "wiki-pages/overview.md": b"Read Details next.",
                "wiki-pages/details.md": b"Details body.",
            }[path]

    class Manager:
        async def get_sandbox(self, session_key):
            assert session_key is not None
            return Sandbox()

    context = ToolContext(
        session_key=SessionKey(type="compile", channel_id="cmp", chat_id="cmp"),
        sandbox_manager=Manager(),
    )
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
        require_workspace_pages=True,
    )
    overview = _page(1, "Overview")
    overview.pop("body_markdown")
    overview["body_workspace_path"] = "wiki-pages/overview.md"
    details = _page(2, "Details")
    details.pop("body_markdown")
    details["body_workspace_path"] = "wiki-pages/details.md"

    accepted = await tool.execute(
        context,
        pages=[overview, details],
        files=[],
        links=[{"f": 1, "t": 2, "match_text": "Details"}],
    )
    assert accepted == "Wiki bundle accepted with 2 page(s) and 0 file(s)."
    assert tool.bundle is not None
    assert tool.bundle.pages[0].body_markdown == "Read Details next."
    assert tool.bundle.pages[0].body_workspace_path is None

    rejected = await tool.execute(
        context,
        pages=[_page(1, "Inline")],
        files=[],
    )
    assert "must be generated with write_file" in rejected
    assert tool.bundle is None


@pytest.mark.asyncio
async def test_submit_tool_rejects_artifact_reused_as_wiki_body():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
        require_workspace_pages=True,
    )
    page = _page(1, "Overview")
    page.pop("body_markdown")
    page["body_workspace_path"] = "./ara-output/PAPER.md"

    result = await tool.execute(
        ToolContext(),
        pages=[page],
        files=[
            {
                "path": "PAPER.md",
                "workspace_path": "ara-output/PAPER.md",
            }
        ],
    )

    assert result.startswith("Error: Invalid Wiki bundle:")
    assert "separate reader-oriented workspace file" in result
    assert tool.bundle is None


class _EchoTool(Tool):
    @property
    def name(self):
        return "openviking_list"

    @property
    def description(self):
        return "echo"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}}

    async def execute(self, tool_context, **kwargs):
        del tool_context
        return json.dumps(kwargs)


@pytest.mark.asyncio
async def test_scoped_tool_requires_and_bounds_openviking_uri():
    wrapped = CompileScopedTool(
        _EchoTool(),
        roots=("viking://resources/source",),
        limits=CompileLimits(),
        result_budget={"bytes": 0},
        budget_lock=__import__("asyncio").Lock(),
    )
    context = ToolContext()
    assert (await wrapped.execute(context)).startswith("Error:")
    assert (await wrapped.execute(context, uri="viking://resources/other")).startswith("Error:")
    assert (
        await wrapped.execute(
            context,
            uri="viking://resources/source/../../other",
        )
    ).startswith("Error:")
    accepted = await wrapped.execute(context, uri="viking://resources/source/child", recursive=True)
    assert '"node_limit": 2000' in accepted


@pytest.mark.asyncio
async def test_scoped_tool_enforces_per_call_and_total_result_budgets():
    limits = CompileLimits(tool_result_bytes=8, tool_total_result_bytes=12)
    budget = {"bytes": 0}
    wrapped = CompileScopedTool(
        _EchoTool(),
        roots=("viking://resources/source",),
        limits=limits,
        result_budget=budget,
        budget_lock=__import__("asyncio").Lock(),
    )
    context = ToolContext()
    oversized = await wrapped.execute(context, uri="viking://resources/source/child")
    assert oversized.startswith("Error:")
    assert budget["bytes"] == 0


@pytest.mark.asyncio
async def test_structured_wrapper_delegates_to_only_existing_loop_without_fallback():
    registry = ToolRegistry()
    submit = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )
    registry.register(submit)
    expected = WikiBundleDraft.model_validate({"pages": []})

    class FakeLoop:
        async def _run_agent_loop(self, **kwargs):
            assert kwargs["tool_registry"] is registry
            assert kwargs["stop_tool_names"] == ["submit_wiki_bundle"]
            assert kwargs["allow_final_fallback"] is False
            assert kwargs["inject_write_experience"] is False
            assert kwargs["messages"] == [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ]
            submit.bundle = expected
            return None, None, [], {"total_tokens": 1}, 1

    bundle, tools, usage, iterations = await AgentLoop.run_structured_task(
        FakeLoop(),
        system_prompt="system",
        user_prompt="user",
        session_key=SessionKey(type="compile", channel_id="cmp", chat_id="cmp"),
        tool_registry=registry,
        openviking_tool_names=set(),
        stop_tool_names=["submit_wiki_bundle"],
        openviking_connection={"api_key": "secret"},
    )
    assert bundle is expected
    assert tools == []
    assert usage == {"total_tokens": 1}
    assert iterations == 1


@pytest.mark.asyncio
async def test_request_normalization_uses_default_reason_and_canonical_skill(monkeypatch):
    class Client:
        created = set()

        async def attrs(self, uri):
            if uri == "viking://resources/wiki" and uri not in self.created:
                raise OpenVikingError("missing", code="NOT_FOUND")
            return {"uri": uri.rstrip("/")}

        async def stat(self, uri):
            return {"uri": uri, "isDir": True}

        async def mkdir(self, uri):
            self.created.add(uri)

        async def get_skill(self, name, *, target_uri):
            assert name == "wiki"
            assert target_uri == "viking://agent/skills"
            return {
                "root_uri": "viking://agent/skills/wiki",
                "content": "---\nname: wiki\ndescription: Wiki\n---\nCompile it",
            }

        async def close(self):
            return None

    async def create_client(**kwargs):
        assert kwargs["connection"]["api_key"] == "secret"
        return Client()

    monkeypatch.setattr("vikingbot.compile.service.VikingClient.create", create_client)
    service = object.__new__(BotCompileService)
    service.config = None
    service.limits = CompileLimits()
    normalized = await service._normalize_request(
        CompileRequest.model_validate(
            {
                "from": ["viking://resources/source", "viking://resources/source"],
                "to": "viking://resources/wiki",
                "skill": "viking://agent/skills/wiki/SKILL.md",
                "reason": "   ",
            }
        ),
        connection={"api_key": "secret"},
    )
    assert normalized.from_ == ["viking://resources/source"]
    assert normalized.to == "viking://resources/wiki"
    assert normalized.skill == "viking://agent/skills/wiki"
    assert normalized.reason == DEFAULT_COMPILE_REASON


def test_compile_target_accepts_only_exact_skill_namespaces():
    directory = {"isDir": True}

    BotCompileService._validate_target_directory("viking://agent/skills", directory)
    BotCompileService._validate_target_directory("viking://user/alice/skills", directory)
    BotCompileService._validate_target_directory("viking://user/skills", directory)

    with pytest.raises(CompileFailure, match="supported skills namespace"):
        BotCompileService._validate_target_directory(
            "viking://agent/skills/existing-skill", directory
        )
    with pytest.raises(CompileFailure, match="supported skills namespace"):
        BotCompileService._validate_target_directory(
            "viking://agent/legacy-agent/skills", directory
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    "target_uri",
    ["viking://agent/skills", "viking://user/alice/skills"],
)
async def test_write_skill_bundle_reuses_add_and_update_skill(existing, target_uri):
    class Client:
        def __init__(self):
            self.called = ""

        async def stat(self, uri):
            assert uri == f"{target_uri}/weekly-report"
            if not existing:
                raise OpenVikingError("missing", code="NOT_FOUND")
            return {"uri": uri, "isDir": True}

        async def get_skill(self, skill_name, *, target_uri: str):
            assert existing
            assert skill_name == "weekly-report"
            return {
                "content": "---\nname: weekly-report\ndescription: Old\n---\nOld",
                "files": [
                    {
                        "path": "assets/keep.bin",
                        "uri": f"{target_uri}/weekly-report/assets/keep.bin",
                        "is_dir": False,
                    },
                    {
                        "path": ".overview.md",
                        "uri": f"{target_uri}/weekly-report/.overview.md",
                        "is_dir": False,
                    },
                ],
            }

        async def download_bytes(self, uri):
            assert uri == f"{target_uri}/weekly-report/assets/keep.bin"
            return b"keep"

        async def add_skill(self, path, *, target_uri, wait, timeout):
            self.called = "add"
            self._assert_package(path, target_uri, wait, timeout)
            return {"root_uri": f"{target_uri}/weekly-report"}

        async def update_skill(self, skill_name, path, *, target_uri, wait, timeout):
            assert skill_name == "weekly-report"
            self.called = "update"
            self._assert_package(path, target_uri, wait, timeout)
            return {"root_uri": f"{target_uri}/weekly-report"}

        @staticmethod
        def _assert_package(path, target_uri, wait, timeout):
            skill_dir = Path(path)
            assert skill_dir.name == "weekly-report"
            assert wait is True
            assert timeout == 30
            assert "name: weekly-report" in (skill_dir / "SKILL.md").read_text()
            assert (skill_dir / "assets" / "logo.bin").read_bytes() == b"\x00\x01"
            if existing:
                assert (skill_dir / "assets" / "keep.bin").read_bytes() == b"keep"
                assert not (skill_dir / ".overview.md").exists()

    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [],
            "files": [
                {
                    "path": "weekly-report/SKILL.md",
                    "content": (
                        "---\nname: weekly-report\ndescription: Weekly report\n---\nWrite it"
                    ),
                },
                {
                    "path": "weekly-report/assets/logo.bin",
                    "workspace_path": "generated/logo.bin",
                },
            ],
        }
    )
    client = Client()
    service = object.__new__(BotCompileService)
    service.limits = CompileLimits()

    action, root_uri = await service._write_skill_bundle(
        client=client,
        target_uri=target_uri,
        bundle=bundle,
        file_payloads=[None, b"\x00\x01"],
        skill_name="weekly-report",
        timeout=30,
    )

    assert action == ("update" if existing else "create")
    assert client.called == ("update" if existing else "add")
    assert root_uri == f"{target_uri}/weekly-report"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_uri",
    ["viking://agent/skills", "viking://user/alice/skills"],
)
async def test_execute_skill_target_skips_recursive_catalog_and_completes(
    monkeypatch, tmp_path: Path, target_uri: str
):
    class TaskConfig:
        def __init__(self):
            self.bot_data_path = tmp_path
            self.workspace_path = tmp_path / "host-workspace"
            self.skills = []
            self.sandbox = SimpleNamespace(mode=None)

        def model_copy(self, *, deep):
            assert deep is True
            return TaskConfig()

    class FakeSandboxManager:
        def __init__(self, config, workspace_parent, workspace_path):
            del config, workspace_path
            self.workspace = workspace_parent / "workspace"
            self.workspace.mkdir(parents=True)

        def get_workspace_path(self, session_key):
            del session_key
            return self.workspace

        async def cleanup_session(self, session_key):
            del session_key

    class FakeSkillsLoader:
        def __init__(self, workspace, *, builtin_skills_dir):
            del workspace, builtin_skills_dir

        def load_skills_for_context(self, names):
            assert names == ["skill-creator"]
            return "Create a standards-compliant Skill."

        def _get_skill_meta(self, name):
            assert name == "skill-creator"
            return {}

    class FakeRequestLoop:
        def __init__(self, **kwargs):
            del kwargs

        async def run_structured_task(self, **kwargs):
            tool = kwargs["tool_registry"].get("submit_wiki_bundle")
            accepted = await tool.execute(
                ToolContext(),
                files=[
                    {
                        "path": "weekly-report/SKILL.md",
                        "content": (
                            "---\nname: weekly-report\ndescription: Weekly report\n---\nWrite it"
                        ),
                    }
                ],
            )
            assert accepted.startswith("Skill bundle accepted")
            return tool.bundle, [], {}, 1

        async def close_mcp(self):
            return None

    class Client:
        added = ""

        async def get_skill(self, skill_name, *, target_uri):
            assert skill_name == "skill-creator"
            assert target_uri == "viking://agent/skills"
            return {
                "root_uri": "viking://agent/skills/skill-creator",
                "content": (
                    "---\nname: skill-creator\ndescription: Create Skills\n---\nCreate one."
                ),
                "files": [],
            }

        async def stat(self, uri):
            assert uri == f"{target_uri}/weekly-report"
            raise OpenVikingError("missing", code="NOT_FOUND")

        async def add_skill(self, path, *, target_uri, wait, timeout):
            assert wait is True
            assert timeout == 300
            assert "name: weekly-report" in (Path(path) / "SKILL.md").read_text()
            self.added = f"{target_uri}/weekly-report"
            return {"root_uri": self.added}

        async def tree(self, uri, *, node_limit):
            raise AssertionError(
                f"Skill target must not preload recursive catalog: {uri}, {node_limit}"
            )

        async def close(self):
            return None

    class Store:
        def __init__(self, task):
            self.task = task

        async def update(self, task_id, mutate):
            assert task_id == self.task.task_id
            mutate(self.task)
            return self.task

    async def create_client(**kwargs):
        del kwargs
        return client

    async def no_op(*args, **kwargs):
        del args, kwargs

    async def build_sources(client, roots):
        del client, roots
        return []

    def build_registry(
        request_loop,
        *,
        parsed_skill,
        roots,
        target_uri,
        source_ids,
        catalog_uris,
        file_catalog_uris,
    ):
        del request_loop, parsed_skill, roots, source_ids
        assert catalog_uris == set()
        assert file_catalog_uris == set()
        registry = ToolRegistry()
        registry.register(
            SubmitWikiBundleTool(
                source_ids=set(),
                catalog_uris=set(),
                file_catalog_uris=set(),
                target_uri=target_uri,
                limits=CompileLimits(),
            )
        )
        return registry, set(), []

    monkeypatch.setattr("vikingbot.compile.service.SandboxManager", FakeSandboxManager)
    monkeypatch.setattr("vikingbot.compile.service.SkillsLoader", FakeSkillsLoader)
    monkeypatch.setattr("vikingbot.compile.service.AgentLoop", FakeRequestLoop)
    monkeypatch.setattr("vikingbot.compile.service.VikingClient.create", create_client)

    host_loop = SimpleNamespace(
        config=TaskConfig(),
        bus=None,
        provider=None,
        workspace=tmp_path,
        model=None,
        temperature=0,
        max_iterations=1,
        memory_window=1,
        brave_api_key=None,
        exa_api_key=None,
        gen_image_model=None,
        exec_config=None,
        _mcp_servers={},
    )
    service = BotCompileService(agent_loop=host_loop)
    request = SanitizedCompileRequest.model_validate(
        {
            "from": ["viking://resources/weekly"],
            "to": target_uri,
            "skill": "viking://agent/skills/skill-creator",
            "reason": "Create a weekly report Skill",
        }
    )
    task = CompileTask(
        task_id="cmp_skill",
        principal_scope="owner",
        sanitized_request=request,
        status="accepted",
        stage="queued",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    client = Client()
    service.store = Store(task)
    monkeypatch.setattr(service, "_materialize_skill", no_op)
    monkeypatch.setattr(service, "_check_requirements", no_op)
    monkeypatch.setattr(service, "_connect_mcp_if_needed", no_op)
    monkeypatch.setattr(service, "_build_sources", build_sources)
    monkeypatch.setattr(service, "_build_request_registry", build_registry)

    await service._execute_task(task.task_id, request, {"api_key": "secret"})

    assert task.status == "completed"
    assert task.result is not None
    assert task.result.created == [f"{target_uri}/weekly-report"]
    assert task.result.updated == []
    assert task.result.page_count == 0
    assert client.added == f"{target_uri}/weekly-report"


@pytest.mark.asyncio
async def test_source_context_builds_bounded_compact_recursive_catalog():
    class Client:
        client = None

        def __init__(self):
            self.client = self

        async def overview(self, uri):
            assert uri == "viking://resources/source"
            return "Source overview"

        async def list_resources(self, *, path, recursive, node_limit):
            assert path == "viking://resources/source"
            assert recursive is True
            assert node_limit == 3
            return [
                {
                    "name": "guide.md",
                    "title": "Readable Guide",
                    "uri": f"{path}/docs/guide.md",
                    "isDir": False,
                    "abstract": "A" * 600,
                },
                {
                    "uri": f"{path}/docs",
                    "isDir": True,
                    "summary": "Documentation",
                },
                {
                    "name": ".overview.md",
                    "uri": f"{path}/.overview.md",
                    "isDir": False,
                },
            ]

    service = object.__new__(BotCompileService)
    service.limits = CompileLimits(source_catalog_entries=3)
    sources = await service._build_sources(
        Client(), ["viking://resources/source"]
    )

    assert sources == [
        {
            "source_id": "src_1",
            "directory_uri": "viking://resources/source",
            "overview": "Source overview",
            "entries": [
                {
                    "name": "guide.md",
                    "title": "Readable Guide",
                    "uri": "viking://resources/source/docs/guide.md",
                    "is_dir": False,
                    "summary": "A" * 500,
                },
                {
                    "name": "docs",
                    "title": "docs",
                    "uri": "viking://resources/source/docs",
                    "is_dir": True,
                    "summary": "Documentation",
                },
            ],
            "catalog_truncated": True,
        }
    ]


@pytest.mark.asyncio
async def test_target_catalog_includes_raw_files_and_marks_wiki_pages():
    class Client:
        def __init__(self):
            self.reads = []

        async def tree(self, uri, *, node_limit):
            assert uri == "viking://resources/ara"
            assert node_limit == CompileLimits().target_catalog_pages + 1
            return [
                {"uri": f"{uri}/Overview.md", "isDir": False, "abstract": "Overview"},
                {"uri": f"{uri}/PAPER.md", "isDir": False},
                {"uri": f"{uri}/broken.md", "isDir": False},
                {"uri": f"{uri}/Long.md", "isDir": False, "size": 2048},
                {"uri": f"{uri}/trace/tree.yaml", "isDir": False},
                {"uri": f"{uri}/figures/chart.png", "isDir": False},
                {"uri": f"{uri}/.overview.md", "isDir": False},
            ]

        async def read_raw(self, uri, *, offset=0, limit=-1):
            assert offset == 0
            self.reads.append((uri, limit))
            content = {
                "viking://resources/ara/Overview.md": (
                    "---\ntype: overview\ncustom: kept\n---\n\n# Overview"
                ),
                "viking://resources/ara/PAPER.md": (
                    "---\ntitle: ARA Paper\nauthors: [Ada]\n---\n\n# Paper"
                ),
                "viking://resources/ara/broken.md": "---\ntype:\n---\n",
                "viking://resources/ara/Long.md": (
                    "---\n"
                    + "".join(f"custom_{index}: value\n" for index in range(130))
                    + "type: long_form\n---\n\n# Long"
                ),
            }[uri]
            if limit == -1:
                return content
            return "".join(content.splitlines(keepends=True)[:limit])

    service = object.__new__(BotCompileService)
    service.limits = CompileLimits()
    client = Client()
    catalog = await service._build_catalog(client, "viking://resources/ara")

    assert catalog == [
        {
            "uri": "viking://resources/ara/Overview.md",
            "kind": "wiki_page",
            "title": "Overview",
            "type": "overview",
            "summary": "Overview",
            "page_id": 1,
        },
        {
            "uri": "viking://resources/ara/PAPER.md",
            "kind": "file",
            "title": "PAPER.md",
            "type": "",
            "summary": "",
        },
        {
            "uri": "viking://resources/ara/broken.md",
            "kind": "file",
            "title": "broken.md",
            "type": "",
            "summary": "",
        },
        {
            "uri": "viking://resources/ara/Long.md",
            "kind": "wiki_page",
            "title": "Long",
            "type": "long_form",
            "summary": "",
            "page_id": 2,
        },
        {
            "uri": "viking://resources/ara/trace/tree.yaml",
            "kind": "file",
            "title": "tree.yaml",
            "type": "",
            "summary": "",
        },
        {
            "uri": "viking://resources/ara/figures/chart.png",
            "kind": "file",
            "title": "chart.png",
            "type": "",
            "summary": "",
        },
    ]
    assert client.reads.count(("viking://resources/ara/Long.md", 128)) == 1
    assert client.reads.count(("viking://resources/ara/Long.md", -1)) == 1
    assert {uri for uri, _ in client.reads} == {
        "viking://resources/ara/Overview.md",
        "viking://resources/ara/PAPER.md",
        "viking://resources/ara/broken.md",
        "viking://resources/ara/Long.md",
    }


class _NamedTool(_EchoTool):
    def __init__(self, name):
        self._name = name

    @property
    def name(self):
        return self._name


def test_request_registry_honors_allowed_tools_and_compile_blocklist():
    available = ToolRegistry()
    for name in (
        "read_file",
        "write_file",
        "edit_file",
        "exec",
        "web_search",
        "message",
        "openviking_list",
        "openviking_grep",
        "openviking_glob",
        "openviking_multi_read",
        "openviking_add_resource",
    ):
        available.register(_NamedTool(name))
    request_loop = SimpleNamespace(tools=available, config=None)
    service = object.__new__(BotCompileService)
    service.limits = CompileLimits()
    common = {
        "request_loop": request_loop,
        "roots": ("viking://resources/source", "viking://resources/wiki"),
        "target_uri": "viking://resources/wiki",
        "source_ids": {"src_1"},
        "catalog_uris": set(),
    }

    empty, empty_ov_names, empty_unavailable = service._build_request_registry(
        parsed_skill={"allowed_tools_declared": True, "allowed_tools": []},
        **common,
    )
    assert empty.tool_names == [
        "read_file",
        "write_file",
        "openviking_list",
        "openviking_multi_read",
        "submit_wiki_bundle",
    ]
    assert empty_ov_names == {"openviking_list", "openviking_multi_read"}
    assert empty_unavailable == []

    selected, ov_names, unavailable = service._build_request_registry(
        parsed_skill={
            "allowed_tools_declared": True,
            "allowed_tools": [
                "Read",
                "WebSearch",
                "openviking_list",
                "message",
                "openviking_add_resource",
                "made_up_tool",
            ],
        },
        **common,
    )
    assert selected.tool_names == [
        "read_file",
        "write_file",
        "web_search",
        "openviking_list",
        "openviking_multi_read",
        "submit_wiki_bundle",
    ]
    assert ov_names == {"openviking_list", "openviking_multi_read"}
    assert unavailable == ["made_up_tool", "message", "openviking_add_resource"]

    ara, ara_ov_names, ara_unavailable = service._build_request_registry(
        parsed_skill={
            "allowed_tools_declared": True,
            "allowed_tools": [
                "Read",
                "Write",
                "Edit",
                "Bash(python *|git clone *|ls *|mkdir *)",
                "Glob",
                "Grep",
                "Task",
            ],
        },
        **common,
    )
    assert ara.tool_names == [
        "read_file",
        "write_file",
        "edit_file",
        "openviking_list",
        "openviking_grep",
        "openviking_glob",
        "openviking_multi_read",
        "submit_wiki_bundle",
    ]
    assert ara_ov_names == {
        "openviking_list",
        "openviking_grep",
        "openviking_glob",
        "openviking_multi_read",
    }
    assert ara_unavailable == [
        "Bash(python *|git clone *|ls *|mkdir *)",
        "Task",
    ]
    assert ara.get("submit_wiki_bundle").require_workspace_files is True
    assert ara.get("submit_wiki_bundle").require_workspace_pages is True
    assert empty.get("submit_wiki_bundle").require_workspace_files is True
    assert empty.get("submit_wiki_bundle").require_workspace_pages is True

    lowercase, lowercase_ov_names, lowercase_unavailable = service._build_request_registry(
        parsed_skill={
            "allowed_tools_declared": True,
            "allowed_tools": ["glob", "grep"],
        },
        **common,
    )
    assert "openviking_glob" in lowercase.tool_names
    assert "openviking_grep" in lowercase.tool_names
    assert {"openviking_glob", "openviking_grep"} <= lowercase_ov_names
    assert lowercase_unavailable == []


def test_compile_prompt_explains_unavailable_tools_to_agent_only():
    request = SanitizedCompileRequest.model_validate(
        {
            "from": ["viking://resources/source"],
            "to": "viking://resources/wiki",
            "skill": "viking://agent/skills/ara",
            "reason": "Compile the research",
        }
    )

    system, user = BotCompileService._build_prompts(
        request=request,
        skill_content="Follow the ARA method.",
        sources=[],
        catalog=[],
        available_tools=["read_file", "submit_wiki_bundle"],
        unavailable_tools=["Bash(...)", "Glob", "Grep", "Task"],
    )

    assert "Compile host capability notice" in system
    assert '"Bash(...)"' in system
    assert "Do not claim that unavailable validation or generation steps" in system
    assert "Preserve every required output type, path, and format" in system
    assert "preserve Skill-prescribed artifact file trees as exact files" in system
    assert "bundle.links" not in system
    assert "match_text" not in system
    assert "pages=[]" not in system
    assert "workspace_path" not in system
    assert "reference those workspace outputs in the final structured submission" in system
    assert "Keep reader-oriented Wiki page bodies separate" in system
    assert "use its URI as an ordinary Markdown link" in system
    assert "unavailable" not in user
    assert "verify every output path and format explicitly required by the Skill" in user
    for implementation_name in (
        "submit_wiki_bundle",
        "source_id",
        "update_uri",
        "workspace_path",
    ):
        assert implementation_name not in user


def test_compile_prompt_requires_one_complete_skill_package_for_skill_target():
    request = SanitizedCompileRequest.model_validate(
        {
            "from": ["viking://resources/weekly"],
            "to": "viking://agent/skills",
            "skill": "viking://agent/skills/skill-creator",
            "reason": "Create a weekly report Skill",
        }
    )

    system, user = BotCompileService._build_prompts(
        request=request,
        skill_content="Create a standards-compliant Skill.",
        sources=[],
        catalog=[],
        available_tools=["write_file", "submit_wiki_bundle"],
        unavailable_tools=[],
    )

    assert "exactly one complete Skill package as artifact files" in system
    assert "<skill-name>/SKILL.md" in system
    assert "Do not produce Wiki pages, links" in system
    assert "one complete Skill package" in user
    assert "on demand" in user
    assert "existing auxiliary files not included in the submission are preserved" in user
    assert "Existing target files" not in user


@pytest.mark.asyncio
async def test_task_store_restart_marks_nonterminal_without_persisting_connection(tmp_path: Path):
    store = CompileTaskStore(tmp_path)
    now = utc_now()
    task = CompileTask(
        task_id="cmp_test",
        principal_scope="owner",
        sanitized_request=SanitizedCompileRequest.model_validate(
            {
                "from": ["viking://resources/source"],
                "to": "viking://resources/wiki",
                "reason": "Compile",
                "skill": "viking://agent/skills/wiki",
            }
        ),
        status="running",
        stage="agent",
        created_at=now,
        updated_at=now,
    )
    await store.create(task)
    assert "api_key" not in (store.root / "cmp_test.json").read_text()
    assert await store.mark_interrupted_failed() == 1
    failed = await store.get("cmp_test")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error is not None and failed.error.code == "BOT_RESTARTED"


@pytest.mark.asyncio
async def test_task_owner_isolation_and_skill_snapshot_sync(tmp_path: Path):
    store = CompileTaskStore(tmp_path)
    now = utc_now()
    task = CompileTask(
        task_id="cmp_owner",
        principal_scope="owner",
        sanitized_request=SanitizedCompileRequest.model_validate(
            {
                "from": ["viking://resources/source"],
                "to": "viking://resources/wiki",
                "reason": "Compile",
                "skill": "viking://agent/skills/wiki",
            }
        ),
        status="accepted",
        stage="queued",
        created_at=now,
        updated_at=now,
    )
    await store.create(task)
    service = object.__new__(BotCompileService)
    service.store = store
    service._started = True
    service._start_lock = __import__("asyncio").Lock()
    assert await service.get_task("cmp_owner", principal_scope="other") is None
    assert (await service.get_task("cmp_owner", principal_scope="owner"))["task_id"] == "cmp_owner"

    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "wiki" / "references"
    skill_dir.mkdir(parents=True)
    (workspace / "skills" / "wiki" / "SKILL.md").write_text("Skill", encoding="utf-8")
    (skill_dir / "guide.md").write_text("Guide", encoding="utf-8")
    (skill_dir / "asset.bin").write_bytes(b"\xff\x00")

    class Sandbox:
        def __init__(self):
            self.files = {}

        async def write_file(self, path, content):
            self.files[path] = content

    sandbox = Sandbox()
    await BotCompileService._sync_skill_snapshot(
        sandbox=sandbox,
        workspace=workspace,
        skill_name="wiki",
    )
    assert sandbox.files == {
        "skills/wiki/SKILL.md": "Skill",
        "skills/wiki/references/guide.md": "Guide",
    }
