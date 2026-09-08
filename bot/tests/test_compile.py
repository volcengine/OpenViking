import asyncio
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.agent.loop import (
    AgentIterationLimitExceeded,
    AgentLoop,
    render_budget_reminder,
)
from vikingbot.agent.tools.base import MultimodalToolResult, Tool, ToolContext
from vikingbot.agent.tools.compile import (
    SubmitCompileOutputTool,
    SubmitWikiBundleTool,
)
from vikingbot.agent.tools.ov_file import (
    VikingExportTool,
    VikingMultiReadTool,
    local_path_for_viking_uri,
)
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.agent.tools.shell import ExecTool
from vikingbot.compile.models import (
    DEFAULT_COMPILE_REASON,
    CompileFailure,
    CompileLimits,
    CompileRequest,
    CompileResult,
    CompileTask,
    SanitizedCompileRequest,
    WikiBundleDraft,
    utc_now,
)
from vikingbot.compile.renderer import (
    WikiRenderer,
    wiki_page_path_from_title,
)
from vikingbot.compile.service import BotCompileService, CompileCapabilities
from vikingbot.compile.store import CompileTaskStore
from vikingbot.config.schema import (
    Config,
    DirectBackendConfig,
    SandboxBackend,
    SandboxConfig,
    SandboxMode,
    SessionKey,
)
from vikingbot.sandbox import SandboxManager
from vikingbot.sandbox.backends.srt import SrtBackend
from vikingbot.sandbox.base import SandboxFileInfo
from vikingbot.utils.session_paths import portable_path_component

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
    assert (
        "editable UTF-8 Markdown Wiki page" in page_properties["body_workspace_path"]["description"]
    )
    assert "__compile_staging__/output/" in page_properties["body_workspace_path"]["description"]
    assert "filename derives from title" in page_properties["path_hint"]["description"]
    assert "supplied source roots" in page_properties["source_ids"]["description"]
    assert "preserve every required path and format" in properties["files"]["description"]


def test_compile_limit_defaults_match_the_resource_envelope():
    limits = CompileLimits()

    assert limits.concurrent_tasks == 10
    assert limits.accepted_tasks == 40
    assert limits.accepted_tasks_per_principal == 10
    assert limits.agent_iterations == 60
    assert limits.salvage_grace_seconds == 120
    assert limits.cleanup_grace_seconds == 40
    assert limits.target_inventory_entries == 2000
    assert limits.output_pages == 128
    assert limits.output_files == 128
    assert limits.output_operations == 256
    assert DirectBackendConfig().allow_compile_exec is True


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


def test_submit_tool_checkout_schema_takes_no_file_manifest():
    tool = SubmitCompileOutputTool(
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        limits=CompileLimits(),
    )

    assert tool.parameters == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert "Pass no pages, files, paths, or content" in tool.description


@pytest.mark.parametrize(
    "target_uri",
    ["viking://resources/wiki", "viking://agent/skills"],
)
@pytest.mark.parametrize("exec_enabled", [True, False])
def test_submit_tool_schema_requires_workspace_artifacts_when_available(target_uri, exec_enabled):
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri=target_uri,
        limits=CompileLimits(),
        require_workspace_files=True,
        exec_enabled=exec_enabled,
    )

    file_schema = tool.parameters["$defs"]["CompileFileDraft"]
    assert "content" not in file_schema["properties"]
    assert "workspace_path" in file_schema["required"]
    hint = tool.validate_params({"raw": "{}"})[0]
    assert ("write_file or exec" in tool.description) is exec_enabled
    assert ("write_file or exec" in hint) is exec_enabled
    assert "workspace_path instead of inline content" in hint
    if not exec_enabled:
        assert "with write_file," in tool.description
        assert "with write_file and submit" in hint


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


def test_renderer_creates_okf_pages_links_and_source_fallbacks():
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
    assert rendered.wiki_uris == [
        "viking://resources/wiki/alpha.md",
        "viking://resources/wiki/beta.md",
    ]
    assert rendered.link_count == 1
    first = rendered.operations[0]
    assert first["mode"] == "upsert"
    assert "type: concept" in first["content"]
    assert f"description: {summary}\n" in first["content"]
    assert "Read [Beta](./beta.md) next." in first["content"]
    assert "## Sources" in first["content"]
    assert "- [source](viking://resources/source)" in first["content"]


def test_renderer_preserves_existing_link_without_adding_another_mention_or_backlink():
    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [
                _page(
                    1,
                    "Overview",
                    body_markdown=(
                        'Read [Details](./details.md "details") next. Details appears again.'
                    ),
                ),
                _page(2, "Details"),
            ],
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

    assert (
        operations["viking://resources/wiki/overview.md"].count('[Details](./details.md "details")')
        == 1
    )
    assert "Details appears again" in operations["viking://resources/wiki/overview.md"]
    assert "Related pages" not in operations["viking://resources/wiki/details.md"]
    assert rendered.link_count == 0


def test_renderer_links_first_filename_mention_in_body_but_not_page_title():
    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [
                _page(
                    1,
                    "Beta Overview",
                    path_hint="entity/beta-overview.md",
                    body_markdown="# Beta Overview\n\nBeta appears first. Beta appears again.",
                ),
                _page(
                    2,
                    "Beta",
                    path_hint="concept/beta.md",
                    body_markdown="# Beta\n\nDefinition.",
                ),
            ]
        }
    )

    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris=set(),
        existing_raw={},
    )
    content = {operation["uri"]: operation["content"] for operation in rendered.operations}[
        "viking://resources/wiki/entity/beta-overview.md"
    ]

    assert "# Beta Overview" in content
    assert "# [Beta]" not in content
    assert content.count("](../concept/beta.md)") == 1
    assert "[Beta](../concept/beta.md) appears first. Beta appears again." in content
    assert rendered.link_count == 1


def test_renderer_auto_links_existing_target_pages_when_a_new_page_is_added():
    alpha_uri = "viking://resources/wiki/entity/alpha.md"
    old_alpha = (
        "---\ntype: entity\ntitle: Alpha\ndescription: Alpha page\n---\n\n"
        "# Alpha\n\nBeta is mentioned here. Beta appears again.\n\n"
        "## Related pages\n\n- [Beta](../concept/beta.md)\n"
    )
    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [
                _page(
                    1,
                    "Beta",
                    path_hint="concept/beta.md",
                    body_markdown="# Beta\n\nDefinition.",
                )
            ]
        }
    )

    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris=set(),
        existing_raw={alpha_uri: old_alpha},
    )
    operations = {operation["uri"]: operation for operation in rendered.operations}

    assert alpha_uri in rendered.updated
    assert operations[alpha_uri]["mode"] == "upsert"
    assert operations[alpha_uri]["content"].count("](../concept/beta.md)") == 1
    assert (
        "[Beta](../concept/beta.md) is mentioned here. Beta appears again."
        in (operations[alpha_uri]["content"])
    )
    assert "Related pages" not in operations[alpha_uri]["content"]


def test_renderer_skips_ambiguous_duplicate_filenames_across_directories():
    existing_raw = {
        "viking://resources/wiki/entity/topic.md": (
            "---\ntype: entity\ntitle: Entity Topic\ndescription: Entity\n---\n\n"
            "# Entity Topic\n\nEntity body.\n"
        ),
        "viking://resources/wiki/concept/topic.md": (
            "---\ntype: concept\ntitle: Concept Topic\ndescription: Concept\n---\n\n"
            "# Concept Topic\n\nConcept body.\n"
        ),
    }
    bundle = WikiBundleDraft.model_validate(
        {"pages": [_page(1, "Overview", body_markdown="Topic is intentionally ambiguous.")]}
    )

    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris=set(),
        existing_raw=existing_raw,
    )
    overview = next(
        operation["content"]
        for operation in rendered.operations
        if operation["uri"] == "viking://resources/wiki/overview.md"
    )

    assert "Topic is intentionally ambiguous." in overview
    assert "[Topic]" not in overview
    assert rendered.updated == []


def test_wiki_page_title_path_normalizes_spaced_dashes_only():
    assert (
        wiki_page_path_from_title("Experimental Designs - Residual Networks")
        == "Experimental_Designs_Residual_Networks"
    )
    assert wiki_page_path_from_title("One-Page Overview") == "One-Page_Overview"


def test_renderer_linkifies_source_uris_without_repeating_them():
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
    assert overview.count(f"]({source_detail})") == 1
    assert "[source](viking://resources/source)" not in overview
    assert "# Citations" not in overview
    assert "## Related pages" not in details
    assert "- [Overview](./overview.md)" not in details
    assert "## Sources" in details
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
    assert rendered.wiki_uris == ["viking://resources/wiki/overview.md"]


@pytest.mark.asyncio
async def test_compile_rejects_combined_output_operation_overflow():
    limits = CompileLimits(output_pages=2, output_files=2, output_operations=3)
    bundle_data = {
        "pages": [_page(1, "One"), _page(2, "Two")],
        "files": [
            {"path": "one.txt", "content": "one"},
            {"path": "two.txt", "content": "two"},
        ],
    }
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=limits,
    )

    result = await tool.execute(ToolContext(), **bundle_data)

    assert "combined output operation limit exceeded" in result
    bundle = WikiBundleDraft.model_validate(bundle_data)
    with pytest.raises(ValueError, match="combined output operation limit"):
        WikiRenderer(limits).render(
            bundle=bundle,
            target_uri="viking://resources/wiki",
            source_roots={"src_1": "viking://resources/source"},
            catalog_uris=set(),
            existing_raw={},
        )


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
    assert rendered.wiki_uris == ["viking://resources/wiki/research.md"]


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
    bundle = WikiBundleDraft.model_validate({"pages": [_page(1, "PAPER", path_hint="PAPER.md")]})

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


def test_renderer_raw_file_update_uses_upsert_and_detects_unchanged():
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
    assert changed.operations[0]["mode"] == "upsert"


def test_renderer_empty_existing_update_uses_upsert_and_preserves_uri():
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
    assert rendered.operations[0]["mode"] == "upsert"


def test_renderer_preserves_unknown_frontmatter_and_skill_owned_citations():
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
    assert "[source](viking://resources/source)" not in content


def test_renderer_does_not_repeat_inline_source_links():
    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [
                _page(
                    1,
                    "主题",
                    body_markdown=("事实来自[原始资料](viking://resources/source/detail.md)。"),
                )
            ]
        }
    )
    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris=set(),
        existing_raw={},
    )
    content = rendered.operations[0]["content"]
    assert content.count("viking://resources/source/detail.md") == 1
    assert "## 参考来源" not in content
    assert "# Citations" not in content


def test_renderer_uses_chinese_source_heading_from_compile_language():
    bundle = WikiBundleDraft.model_validate(
        {"pages": [_page(1, "主题", body_markdown="这是没有显式来源链接的正文。")]}
    )
    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris=set(),
        existing_raw={},
        wiki_language="zh-CN",
    )
    content = rendered.operations[0]["content"]
    assert "## 来源" in content
    assert "# Citations" not in content


def test_renderer_uses_compile_language_for_sources_without_related_pages_section():
    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [
                _page(1, "入口", body_markdown="参见主题。"),
                _page(2, "主题", body_markdown="English body retained from an older page."),
            ],
            "links": [{"f": 1, "t": 2, "match_text": "主题"}],
        }
    )
    rendered = WikiRenderer().render(
        bundle=bundle,
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        catalog_uris=set(),
        existing_raw={},
        wiki_language="zh-CN",
    )
    content = rendered.operations[1]["content"]
    assert "## 来源" in content
    assert "## 相关页面" not in content
    assert "## Related pages" not in content
    assert "## Sources" not in content


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
    assert protected.startswith("Wiki bundle accepted")
    assert "was not found and the link was dropped" in protected
    assert tool.bundle is not None and tool.bundle.links == []

    accepted = await tool.execute(context, pages=[], links=[])
    assert not accepted.startswith("Error:")
    assert tool.bundle is not None and tool.bundle.pages == []


@pytest.mark.asyncio
async def test_submit_tool_accepts_existing_link_only_when_target_matches():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )
    context = ToolContext()

    accepted = await tool.execute(
        context,
        pages=[
            _page(1, "One", body_markdown="参见 [L2 行为标签库](./two.md)。"),
            _page(2, "Two"),
        ],
        links=[{"f": 1, "t": 2, "match_text": "行为标签库"}],
    )
    assert accepted.startswith("Wiki bundle accepted")
    assert tool.bundle is not None and len(tool.bundle.links) == 1

    accepted = await tool.execute(
        context,
        pages=[
            _page(1, "One", body_markdown="See [Version](./foo(1).md)."),
            _page(2, "Version", path_hint="foo(1).md"),
        ],
        links=[{"f": 1, "t": 2, "match_text": "Version"}],
    )
    assert accepted.startswith("Wiki bundle accepted")
    assert tool.bundle is not None and len(tool.bundle.links) == 1

    rejected = await tool.execute(
        context,
        pages=[
            _page(1, "One", body_markdown="参见 [行为标签库](./three.md)。"),
            _page(2, "Two"),
            _page(3, "Three"),
        ],
        links=[{"f": 1, "t": 2, "match_text": "行为标签库"}],
    )
    assert rejected.startswith("Wiki bundle accepted")
    assert "was not found and the link was dropped" in rejected
    assert tool.bundle is not None and tool.bundle.links == []


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
async def test_submit_tool_resolves_existing_updates_outside_relevant_catalog():
    wiki_uri = "viking://resources/wiki/existing.md"
    artifact_uri = "viking://resources/wiki/PAPER.md"
    resolved = []

    async def resolve(uri):
        resolved.append(uri)
        return uri == wiki_uri

    catalog_uris = set()
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=catalog_uris,
        file_catalog_uris={wiki_uri, artifact_uri},
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
        wiki_uri_resolver=resolve,
    )

    accepted = await tool.execute(
        ToolContext(),
        pages=[_page(1, "Existing", update_uri=wiki_uri, path_hint=None)],
    )
    assert accepted.startswith("Wiki bundle accepted")
    assert wiki_uri in catalog_uris

    rejected = await tool.execute(
        ToolContext(),
        pages=[],
        files=[{"update_uri": wiki_uri, "content": "# Plain Markdown"}],
    )
    assert "must retain valid OKF frontmatter" in rejected

    artifact = await tool.execute(
        ToolContext(),
        pages=[],
        files=[{"update_uri": artifact_uri, "content": "# Paper"}],
    )
    assert artifact.startswith("Wiki bundle accepted")
    assert resolved == [wiki_uri, artifact_uri]


@pytest.mark.asyncio
async def test_submit_tool_reports_structurally_invalid_links():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )

    result = await tool.execute(
        ToolContext(),
        pages=[_page(1, "One"), _page(2, "Two")],
        links=[
            {"f": 1, "t": 2, "match_text": "Two"},
            {"f": 1, "t": 1, "match_text": "One"},
            {"f": 1, "t": 3, "match_text": "Three"},
        ],
    )

    assert result.startswith("Error: Invalid Wiki bundle: 2 invalid link(s):")
    assert "links[1] must not be a self-link" in result
    assert "links[2] endpoints must reference bundle pages" in result
    assert tool.bundle is None


@pytest.mark.asyncio
async def test_submit_tool_drops_unrenderable_links_instead_of_failing():
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

    assert result.startswith("Wiki bundle accepted with 3 page(s)")
    assert "was not found and the link was dropped" in result
    assert tool.bundle is not None
    assert tool.bundle.links == []


@pytest.mark.asyncio
async def test_submit_tool_accepts_inline_content_by_default():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )

    page_schema = tool.parameters["$defs"]["WikiPageDraft"]
    file_schema = tool.parameters["$defs"]["CompileFileDraft"]
    assert "body_markdown" in page_schema["properties"]
    assert "body_workspace_path" in page_schema["properties"]
    assert "content" in file_schema["properties"]
    assert "workspace_path" in file_schema["properties"]

    result = await tool.execute(
        ToolContext(),
        pages=[_page(1, "Home")],
        files=[{"path": "extra/notes.yaml", "content": "kind: notes\n"}],
    )

    assert result == "Wiki bundle accepted with 1 page(s) and 1 file(s)."
    assert tool.bundle is not None
    assert tool.bundle.pages[0].body_markdown == "Body for Home"
    assert tool.bundle.files[0].content == "kind: notes\n"


@pytest.mark.asyncio
async def test_multi_read_hints_on_large_files_and_supports_offset_limit(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.calls = []

        async def stat(self, uri):
            return {"size": 2 * 1024 * 1024, "isDir": False}

        async def read_content(self, uri, level="abstract", user_id=None, offset=0, limit=-1):
            self.calls.append((uri, offset, limit))
            return f"window:{offset}:{limit}"

    tool = VikingMultiReadTool()
    fake = FakeClient()

    async def fake_get_client(ctx):
        return fake

    async def no_release(ctx, client):
        return None

    monkeypatch.setattr(tool, "_get_client", fake_get_client)
    monkeypatch.setattr(tool, "_release_client", no_release)

    large = await tool.execute(ToolContext(), uris=["viking://resources/big.jsonl"])
    assert "reading it fully would exceed" in large
    assert fake.calls == []

    window = await tool.execute(
        ToolContext(), uris=["viking://resources/big.jsonl"], offset=10, limit=20
    )
    assert "window:10:20" in window
    assert fake.calls == [("viking://resources/big.jsonl", 10, 20)]


@pytest.mark.asyncio
async def test_multi_read_returns_images_as_tool_result_content(monkeypatch):
    image_uri = "viking://resources/example/image.png"
    image_bytes = b"\x89PNG\r\n\x1a\nimage-data"

    class FakeClient:
        async def stat(self, uri):
            assert uri == image_uri
            return {"size": len(image_bytes), "isDir": False}

        async def download_bytes(self, uri):
            assert uri == image_uri
            return image_bytes

        async def read_content(self, *args, **kwargs):
            raise AssertionError("image reads must not use the text endpoint")

    tool = VikingMultiReadTool()

    async def fake_get_client(ctx):
        return FakeClient()

    async def no_release(ctx, client):
        return None

    monkeypatch.setattr(tool, "_get_client", fake_get_client)
    monkeypatch.setattr(tool, "_release_client", no_release)

    result = await tool.execute(ToolContext(), uris=[image_uri])

    assert isinstance(result, MultimodalToolResult)
    assert f"START OF {image_uri}" in str(result)
    assert "Image resource (image/png" in str(result)
    assert result.content[2] == {
        "type": "image_url",
        "image_url": {
            "url": f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"
        },
    }


@pytest.mark.asyncio
async def test_multi_read_treats_typescript_as_text_and_preserves_window(monkeypatch):
    typescript_uri = "viking://resources/web/index.ts"

    class FakeClient:
        async def read_content(self, uri, level="abstract", offset=0, limit=-1, **kwargs):
            assert uri == typescript_uri
            assert level == "read"
            assert (offset, limit) == (4, 2)
            return "export const answer = 42;"

        async def download_bytes(self, uri):
            raise AssertionError("TypeScript reads must not use the media download path")

    tool = VikingMultiReadTool()

    async def fake_get_client(ctx):
        return FakeClient()

    async def no_release(ctx, client):
        return None

    monkeypatch.setattr(tool, "_get_client", fake_get_client)
    monkeypatch.setattr(tool, "_release_client", no_release)

    result = await tool.execute(ToolContext(), uris=[typescript_uri], offset=4, limit=2)

    assert isinstance(result, str)
    assert "export const answer = 42;" in result


@pytest.mark.asyncio
async def test_multi_read_limits_aggregate_inline_image_bytes(monkeypatch):
    first_uri = "viking://resources/first.png"
    second_uri = "viking://resources/second.png"
    first_image = b"\x89PNG\r\n\x1a\nfirst"
    second_image = b"\x89PNG\r\n\x1a\nother"
    images = {first_uri: first_image, second_uri: second_image}

    class FakeClient:
        async def stat(self, uri):
            return {"size": len(images[uri]), "isDir": False}

        async def download_bytes(self, uri):
            return images[uri]

        async def read_content(self, *args, **kwargs):
            raise AssertionError("image reads must not use the text endpoint")

    tool = VikingMultiReadTool()
    monkeypatch.setattr(tool, "_MAX_INLINE_MEDIA_BYTES", len(first_image))

    async def fake_get_client(ctx):
        return FakeClient()

    async def no_release(ctx, client):
        return None

    monkeypatch.setattr(tool, "_get_client", fake_get_client)
    monkeypatch.setattr(tool, "_release_client", no_release)

    result = await tool.execute(ToolContext(), uris=[first_uri, second_uri])

    assert isinstance(result, MultimodalToolResult)
    assert sum(part.get("type") == "image_url" for part in result.content) == 1
    assert "combined inline media size" in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", ["mp3", "mp4"])
async def test_multi_read_uses_openviking_overview_for_audio_and_video(monkeypatch, extension):
    media_uri = f"viking://resources/interview/interview.{extension}"

    class FakeClient:
        async def read_content(self, uri, level="abstract", **kwargs):
            assert uri == media_uri
            assert level == "overview"
            return "Speaker: hello"

        async def download_bytes(self, uri):
            raise AssertionError("media reads must not create a second transcription path")

    tool = VikingMultiReadTool()

    async def fake_get_client(ctx):
        return FakeClient()

    async def no_release(ctx, client):
        return None

    monkeypatch.setattr(tool, "_get_client", fake_get_client)
    monkeypatch.setattr(tool, "_release_client", no_release)

    result = await tool.execute(ToolContext(), uris=[media_uri])

    assert isinstance(result, str)
    assert "Speaker: hello" in result


class _RecordingSandbox:
    def __init__(self):
        self.writes = []

    async def write_file(self, path, content):
        self.writes.append((path, content))


async def _export_into_sandbox(monkeypatch, client, *, uri: str, dest: str = "compile_resources"):
    """Run VikingExportTool against a fake client; returns (result, sandbox writes)."""

    class Manager:
        def __init__(self, sandbox):
            self.sandbox = sandbox

        async def get_sandbox(self, session_key):
            return self.sandbox

    tool = VikingExportTool()
    sandbox = _RecordingSandbox()

    async def fake_get_client(ctx):
        return client

    async def no_release(ctx, client_):
        return None

    monkeypatch.setattr(tool, "_get_client", fake_get_client)
    monkeypatch.setattr(tool, "_release_client", no_release)
    context = ToolContext(
        session_key=SessionKey(type="compile", channel_id="cmp", chat_id="cmp"),
        sandbox_manager=Manager(sandbox),
    )
    result = await tool.execute(context, uri=uri, dest=dest)
    return result, sandbox.writes


@pytest.mark.asyncio
async def test_export_materializes_sources_into_workspace(monkeypatch):
    class FakeClient:
        async def stat(self, uri):
            return {"size": 3, "isDir": False}

        async def list_resources(self, path, recursive, node_limit):
            return [{"uri": "viking://resources/s/rollout.jsonl"}]

        async def download_bytes(self, uri):
            return b'{"type":"event_msg","payload":{"type":"user_message","message":"hi"}}\n'

    result, writes = await _export_into_sandbox(
        monkeypatch, FakeClient(), uri="viking://resources/s/rollout.jsonl"
    )
    assert "Exported 1 file" in result
    assert writes == [
        (
            "compile_resources/s/rollout.jsonl",
            '{"type":"event_msg","payload":{"type":"user_message","message":"hi"}}\n',
        )
    ]
    assert "jq/wc/grep" in result


@pytest.mark.asyncio
async def test_export_directory_strips_namespace_and_skips_binary(monkeypatch):
    class FakeClient:
        async def stat(self, uri):
            return {"size": 0, "isDir": uri.endswith("/dream-sessions")}

        async def list_resources(self, path, recursive, node_limit):
            return [
                {
                    "uri": "viking://resources/dream-sessions/08/05/a.jsonl",
                    "size": 100,
                    "isDir": False,
                },
                {
                    "uri": "viking://resources/dream-sessions/08/05/b.bin",
                    "size": 50,
                    "isDir": False,
                },
            ]

        async def download_bytes(self, uri):
            if uri.endswith(".bin"):
                return b"\xff\xfe\x00"  # invalid UTF-8 -> binary, skipped
            return b'{"type":"event_msg","payload":{"type":"user_message","message":"hi"}}\n'

    result, writes = await _export_into_sandbox(
        monkeypatch,
        FakeClient(),
        uri="viking://resources/dream-sessions",
    )
    # viking://resources/... is stripped to dream-sessions/... under compile_resources/
    assert writes == [
        (
            "compile_resources/dream-sessions/08/05/a.jsonl",
            '{"type":"event_msg","payload":{"type":"user_message","message":"hi"}}\n',
        )
    ]
    assert "Skipped 1 non-UTF-8" in result


@pytest.mark.asyncio
async def test_export_reports_when_single_file_exceeds_byte_budget(monkeypatch):
    class FakeClient:
        async def stat(self, uri):
            return {"size": 100, "isDir": False}

        async def list_resources(self, path, recursive, node_limit):
            return []

        async def download_bytes(self, uri):
            return b"x" * 10

    monkeypatch.setattr(VikingExportTool, "_MAX_TOTAL_BYTES", 25)
    result, writes = await _export_into_sandbox(
        monkeypatch, FakeClient(), uri="viking://resources/a.jsonl"
    )
    assert "would exceed the total-byte budget" in result
    assert writes == []


@pytest.mark.asyncio
async def test_export_reports_mid_run_byte_budget_hit(monkeypatch):
    class FakeClient:
        async def stat(self, uri):
            return {"size": 10, "isDir": uri.endswith("/bigdir")}

        async def list_resources(self, path, recursive, node_limit):
            return [
                {"uri": f"viking://resources/bigdir/f{i}.jsonl", "size": 10, "isDir": False}
                for i in range(5)
            ]

        async def download_bytes(self, uri):
            return b'{"ok":true}\n'

    monkeypatch.setattr(VikingExportTool, "_MAX_TOTAL_BYTES", 25)
    result, writes = await _export_into_sandbox(
        monkeypatch, FakeClient(), uri="viking://resources/bigdir"
    )
    assert "NOTE: stopped before the total-byte budget" in result
    assert len(writes) == 2


@pytest.mark.asyncio
async def test_export_reports_listing_cap(monkeypatch):
    class FakeClient:
        async def stat(self, uri):
            return {"size": 0, "isDir": uri.endswith("/bigdir")}

        async def list_resources(self, path, recursive, node_limit):
            return [
                {"uri": f"viking://resources/bigdir/f{i}.jsonl", "size": 1, "isDir": False}
                for i in range(10)
            ]

        async def download_bytes(self, uri):
            return b'{"ok":true}\n'

    monkeypatch.setattr(VikingExportTool, "_MAX_FILES", 3)
    result, writes = await _export_into_sandbox(
        monkeypatch, FakeClient(), uri="viking://resources/bigdir"
    )
    assert "NOTE: the source listing was capped at 3 entries" in result
    assert len(writes) == 3


class _FakeChatProvider:
    """Minimal provider stub whose chat() returns a fixed summary and counts calls."""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    async def chat(self, messages, tools, model, temperature, session_id, **kwargs):
        del messages, tools, model, temperature, session_id, kwargs
        self.calls += 1
        return SimpleNamespace(content=self.content, reasoning_content=None, usage={})


class _FakeCompileSessionKey:
    def safe_name(self):
        return "cmp"


class _FlakyChatProvider:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = 0

    async def chat(self, messages, tools, model, temperature, session_id, **kwargs):
        del messages, tools, model, temperature, session_id, kwargs
        self.calls += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(content=outcome, reasoning_content=None, usage={})


def _compact_loop(provider):
    loop = object.__new__(AgentLoop)
    loop.provider = provider
    loop.model = "m"
    return loop


@pytest.mark.asyncio
async def test_compact_tool_loop_summarizes_and_truncates():
    loop = _compact_loop(_FakeChatProvider("KEY FINDINGS"))

    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "call1"},
        {"role": "user", "content": "result1 " + "x" * 500},
        {"role": "assistant", "content": "call2"},
        {"role": "user", "content": "result2"},
    ]
    out = await loop._compact_tool_loop(messages, _FakeCompileSessionKey())

    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == {"role": "user", "content": "TASK"}
    assert out[2]["content"].startswith("[Context compaction]")
    assert "KEY FINDINGS" in out[2]["content"]
    # A rolling window of the most recent complete turns is retained verbatim
    # (not just the last two arbitrary messages).
    assert out[3:] == messages[-3:]


@pytest.mark.asyncio
async def test_compact_tool_loop_retries_summary_then_uses_trim_fallback():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "important old finding"},
        {"role": "user", "content": "old result"},
        {"role": "assistant", "content": "recent action"},
        {"role": "user", "content": "recent result"},
    ]
    recovered = _FlakyChatProvider([RuntimeError("temporary"), "", "RECOVERED"])
    out = await _compact_loop(recovered)._compact_tool_loop(messages, _FakeCompileSessionKey())
    assert recovered.calls == 3
    assert "RECOVERED" in out[2]["content"]

    failed = _FlakyChatProvider([RuntimeError("down"), "", RuntimeError("still down")])
    out = await _compact_loop(failed)._compact_tool_loop(messages, _FakeCompileSessionKey())
    assert failed.calls == 3
    assert "Earlier turns were compacted" in out[2]["content"]
    assert "important old finding" not in str(out)
    assert out[-3:] == messages[-3:]


@pytest.mark.asyncio
async def test_compact_tool_loop_keeps_tool_turns_atomic():
    loop = _compact_loop(_FakeChatProvider("SUMMARY"))

    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        # old tool turn (summarized away)
        {
            "role": "assistant",
            "content": "read",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "old content"},
        {"role": "user", "content": "Reflect on the results and decide next steps."},
        # recent tool turn (kept verbatim)
        {
            "role": "assistant",
            "content": "write",
            "tool_calls": [
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c2", "name": "write_file", "content": "done"},
        {"role": "user", "content": "Reflect on the results and decide next steps."},
    ]
    out = await loop._compact_tool_loop(messages, _FakeCompileSessionKey())

    assert out[2]["content"].startswith("[Context compaction]")
    assert "SUMMARY" in out[2]["content"]
    # The retained window keeps the assistant tool-call together with its tool
    # result; a dangling `tool` message is never emitted.
    window = out[3:]
    roles = [m["role"] for m in window]
    assert roles == ["user", "assistant", "tool", "user"]
    assistant_idx = roles.index("assistant")
    assert window[assistant_idx]["tool_calls"][0]["id"] == "c2"
    assert window[assistant_idx + 1]["role"] == "tool"
    assert window[assistant_idx + 1]["tool_call_id"] == "c2"
    assert "c1" not in {m.get("tool_call_id") for m in window}


@pytest.mark.asyncio
async def test_compact_tool_loop_incremental_merges_previous_note():
    provider = _FakeChatProvider("NEW SUMMARY")
    loop = _compact_loop(provider)

    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "user", "content": "[Context compaction]\n## Key facts & progress\nold facts"},
        {"role": "assistant", "content": "intermediate action"},
        {"role": "user", "content": "intermediate result"},
        {"role": "assistant", "content": "recent action"},
        {"role": "user", "content": "recent result"},
    ]
    out = await loop._compact_tool_loop(messages, _FakeCompileSessionKey())

    # Only one summarization call: the previous note is reused verbatim and only
    # the region since it is summarized.
    assert provider.calls == 1
    note = out[2]["content"]
    assert note.startswith("[Context compaction]")
    assert "old facts" in note
    assert "NEW SUMMARY" in note
    assert out[3:] == messages[-3:]


@pytest.mark.asyncio
async def test_compact_tool_loop_chunks_large_transcript_and_merges():
    loop = _compact_loop(_FakeChatProvider("SEGMENT"))

    # Build enough history that the transcript spans multiple summarization chunks
    # (each chunk is capped at 32k chars; each message at 6k).
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
    ]
    for _ in range(20):
        messages.append({"role": "assistant", "content": "step " + "y" * 6_000})
        messages.append({"role": "user", "content": "result " + "z" * 6_000})

    out = await loop._compact_tool_loop(messages, _FakeCompileSessionKey(), budget_chars=20_000)

    assert out[2]["content"].startswith("[Context compaction]")
    assert "SEGMENT" in out[2]["content"]
    # Budget-aware window: at least one complete recent turn is retained and the
    # compacted result fits the (small) budget.
    assert sum(len(json.dumps(m, ensure_ascii=False)) for m in out) <= 20_000
    assert out[3:][0]["role"] in {"user", "assistant"}


@pytest.mark.asyncio
async def test_compact_tool_loop_keeps_system_messages_and_preserves_budget_churn_free():
    loop = _compact_loop(_FakeChatProvider("SUMMARY"))

    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "user", "content": "[Context compaction]\n## Key facts\nold facts"},
        {"role": "assistant", "content": "recent action"},
        {"role": "user", "content": "recent result"},
    ]
    # Not enough history since the last compaction to warrant a new summarization
    # call: re-emits the existing (small) state without churn.
    out = await loop._compact_tool_loop(messages, _FakeCompileSessionKey())
    assert out == messages


def test_local_path_for_viking_uri_drops_namespace_prefix():
    assert local_path_for_viking_uri("viking://resources/a/b.md") == "a/b.md"
    assert local_path_for_viking_uri("viking://user/alice/resources/a/b.md") == "a/b.md"
    assert local_path_for_viking_uri("viking://user/alice/skills/s/guide.md") == "s/guide.md"
    assert local_path_for_viking_uri("viking://resources/sessions/x.jsonl") == "sessions/x.jsonl"


@pytest.mark.asyncio
async def test_submit_tool_requires_workspace_paths_for_artifacts():
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

    single_inline = await tool.execute(
        ToolContext(),
        pages=[],
        files=[
            {
                "path": "PAPER.md",
                "content": "---\ntitle: ARA Paper\nauthors: [Ada]\n---\n\n# Paper",
            }
        ],
    )
    assert single_inline.startswith("Error: Invalid Wiki bundle:")
    assert "submitted using workspace_path instead of inline content" in single_inline

    inline_tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )
    rejected = await inline_tool.execute(
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
        target_uri="viking://user/alice/memories/preferences/wiki",
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
                "__compile_staging__/wiki_pages/overview.md": b"Read Details next.",
                "__compile_staging__/wiki_pages/details.md": b"Details body.",
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
    overview["body_workspace_path"] = "__compile_staging__/wiki_pages/overview.md"
    details = _page(2, "Details")
    details.pop("body_markdown")
    details["body_workspace_path"] = "__compile_staging__/wiki_pages/details.md"

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
async def test_submit_tool_checkout_writes_complete_tree_with_upsert():
    relative_files = {
        "entity/existing.md": (
            b"---\ntype: entity\ntitle: Existing\ndescription: Updated\n---\n\n"
            b"Merged body mentioning New."
        ),
        "concept/new.md": (
            b"---\ntype: concept\ntitle: New\ndescription: New concept.\n---\n\n# New\n\nBody."
        ),
        "relations.jsonl": b'{"from":"a","to":"b"}\n',
        "schema.json": b'{"version":1}\n',
    }
    files = {
        f"__compile_staging__/output/{path}": payload for path, payload in relative_files.items()
    }

    class Sandbox:
        async def list_files(self, path, *, max_entries):
            assert path == "__compile_staging__/output"
            assert len(files) <= max_entries
            return [
                SandboxFileInfo(path=file_path, size=len(payload))
                for file_path, payload in files.items()
            ]

        async def read_file_bytes(self, path, *, max_bytes=None):
            assert max_bytes is None or len(files[path]) <= max_bytes
            return files[path]

    class Manager:
        async def get_sandbox(self, session_key):
            assert session_key is not None
            return Sandbox()

    context = ToolContext(
        session_key=SessionKey(type="compile", channel_id="cmp", chat_id="cmp"),
        sandbox_manager=Manager(),
    )
    existing_page_uri = "viking://resources/wiki/entity/existing.md"
    tool = SubmitCompileOutputTool(
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        limits=CompileLimits(),
    )

    accepted = await tool.execute(context)

    assert accepted == (
        "Resource output accepted with 4 changed file(s) and "
        "2 Wiki page(s) in the submitted output."
    )
    assert tool.bundle is not None
    operations = {operation["uri"]: operation for operation in tool.bundle.operations}
    assert all(operation["mode"] == "upsert" for operation in operations.values())
    rendered_existing = base64.b64decode(operations[existing_page_uri]["content_base64"])
    assert b"[New](../concept/new.md)" in rendered_existing
    assert set(tool.bundle.wiki_uris) == {
        existing_page_uri,
        "viking://resources/wiki/concept/new.md",
    }
    assert tool.page_count == 2


@pytest.mark.asyncio
async def test_submit_tool_checkout_writes_every_checkout_file_without_deleting_omitted_files():
    page = b"---\ntype: entity\ntitle: Existing\ndescription: Existing page.\n---\n\nBody."
    workspace_path = "__compile_staging__/output/entity/existing.md"

    class Sandbox:
        async def list_files(self, path, *, max_entries):
            del max_entries
            assert path == "__compile_staging__/output"
            return [SandboxFileInfo(path=workspace_path, size=len(page))]

        async def read_file_bytes(self, path, *, max_bytes=None):
            del max_bytes
            assert path == workspace_path
            return page

    class Manager:
        async def get_sandbox(self, session_key):
            del session_key
            return Sandbox()

    page_uri = "viking://resources/wiki/entity/existing.md"
    omitted_uri = "viking://resources/wiki/data.jsonl"
    tool = SubmitCompileOutputTool(
        target_uri="viking://resources/wiki",
        source_roots={},
        limits=CompileLimits(),
    )

    accepted = await tool.execute(
        ToolContext(
            session_key=SessionKey(type="compile", channel_id="cmp", chat_id="cmp"),
            sandbox_manager=Manager(),
        )
    )

    assert accepted.startswith("Resource output accepted with 1 changed file(s)")
    assert tool.bundle is not None
    assert tool.bundle.operations[0]["uri"] == page_uri
    assert tool.bundle.operations[0]["mode"] == "upsert"
    assert omitted_uri not in {operation["uri"] for operation in tool.bundle.operations}
    assert tool.page_count == 1
    assert tool.file_count == 1


@pytest.mark.asyncio
async def test_submit_tool_checkout_rejects_incomplete_wiki_frontmatter():
    workspace_path = "__compile_staging__/output/entity/existing.md"

    class Sandbox:
        payload = b"---\ntype: concept\n---\n\n# New"

        async def list_files(self, path, *, max_entries):
            del path, max_entries
            return [SandboxFileInfo(path=workspace_path, size=len(self.payload))]

        async def read_file_bytes(self, path, *, max_bytes=None):
            del path, max_bytes
            return self.payload

    class Manager:
        async def get_sandbox(self, session_key):
            del session_key
            return Sandbox()

    tool = SubmitCompileOutputTool(
        target_uri="viking://resources/wiki",
        source_roots={},
        limits=CompileLimits(),
    )
    context = ToolContext(
        session_key=SessionKey(type="compile", channel_id="cmp", chat_id="cmp"),
        sandbox_manager=Manager(),
    )

    incomplete = await tool.execute(context)
    assert "must have non-empty YAML frontmatter fields: title, description" in incomplete
    assert tool.bundle is None


@pytest.mark.asyncio
async def test_submit_tool_checkout_rejects_manifest_arguments():
    tool = SubmitCompileOutputTool(
        target_uri="viking://resources/wiki",
        source_roots={},
        limits=CompileLimits(),
    )

    result = await tool.execute(ToolContext(), pages=[], files=[])

    assert result == "Error: submit_wiki_bundle takes no arguments for a Resource output."
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
    assert "must be editable files under __compile_staging__/wiki_pages/" in result
    assert tool.bundle is None


@pytest.mark.asyncio
async def test_submit_tool_preserves_generated_skill_artifacts_alongside_staged_wiki_pages():
    files = {
        "skills/compiler/SKILL.md": b"input skill",
        "ara-output/PAPER.md": b"---\ntitle: ARA\nauthors: [Ada]\nyear: 2026\n---\n\n# ARA",
        "ara-output/logic/problem.md": b"# Problem",
        "__compile_staging__/wiki_pages/overview.md": b"# Reader overview",
        "__compile_staging__/tmp/notes.md": b"scratch",
    }

    class Sandbox:
        async def list_dir(self, path):
            directories = {
                ".": [("ara-output", True), ("__compile_staging__", True), ("skills", True)],
                "ara-output": [("PAPER.md", False), ("logic", True)],
                "ara-output/logic": [("problem.md", False)],
                "skills": [("compiler", True)],
                "skills/compiler": [("SKILL.md", False)],
            }
            return directories[path]

        async def read_file_bytes(self, path):
            return files[path]

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
        require_workspace_files=True,
        require_workspace_pages=True,
        workspace_baseline={"skills/compiler/SKILL.md"},
    )
    page = _page(1, "Overview")
    page.pop("body_markdown")
    page["body_workspace_path"] = "__compile_staging__/wiki_pages/overview.md"

    rejected = await tool.execute(
        context,
        pages=[page],
        files=[],
    )
    assert "generated Skill artifacts are missing from files" in rejected
    assert "ara-output/PAPER.md" in rejected
    assert "ara-output/logic/problem.md" in rejected

    accepted = await tool.execute(
        context,
        pages=[page],
        files=[
            {
                "path": "PAPER.md",
                "workspace_path": "ara-output/PAPER.md",
            },
            {
                "path": "logic/problem.md",
                "workspace_path": "ara-output/logic/problem.md",
            },
        ],
    )
    assert accepted == "Wiki bundle accepted with 1 page(s) and 2 file(s)."
    assert tool.bundle is not None
    assert [file.path for file in tool.bundle.files] == [
        "PAPER.md",
        "logic/problem.md",
    ]
    assert tool.file_payloads == [
        files["ara-output/PAPER.md"],
        files["ara-output/logic/problem.md"],
    ]


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
@pytest.mark.parametrize(
    ("iteration", "error"),
    [(1, ValueError), (50, AgentIterationLimitExceeded)],
)
async def test_structured_wrapper_distinguishes_iteration_exhaustion(iteration, error):
    registry = ToolRegistry()
    registry.register(
        SubmitWikiBundleTool(
            source_ids=set(),
            catalog_uris=set(),
            target_uri="viking://resources/wiki",
            limits=CompileLimits(),
        )
    )

    class FakeLoop:
        max_iterations = 50

        async def _run_agent_loop(self, **kwargs):
            del kwargs
            return None, None, [], {}, iteration

    with pytest.raises(error):
        await AgentLoop.run_structured_task(
            FakeLoop(),
            system_prompt="system",
            user_prompt="user",
            session_key=SessionKey(type="compile", channel_id="cmp", chat_id="cmp"),
            tool_registry=registry,
            openviking_tool_names=set(),
            stop_tool_names=["submit_wiki_bundle"],
            openviking_connection=None,
        )


def test_budget_reminder_text_escalates_with_remaining_rounds():
    assert render_budget_reminder(70) is None
    assert render_budget_reminder(16) is None

    heads_up = render_budget_reminder(15)
    warn = render_budget_reminder(8)
    critical = render_budget_reminder(3)

    assert "还剩 15 轮" in heads_up
    assert "停止对已读文件的全量重扫" in heads_up
    assert "还剩 8 轮" in warn
    assert "必须开始提交" in warn
    assert "未覆盖/待确认" in warn
    assert "还剩 3 轮" in critical
    assert "立即提交当前最好结果" in critical
    assert "禁止再开启新的探索/读取" in critical
    # The consequence sentence ties the reminder to the real salvage failure mode.
    for note in (heads_up, warn, critical):
        assert "submit_wiki_bundle" in note
        assert "不经校验" in note
    assert "还剩 0 轮" in render_budget_reminder(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("iteration", "with_note"),
    [
        (42, True),  # max_iterations=50, remaining == 8 -> budget reminder fires
        (1, False),  # no thresholds -> provider stays quiet by default
    ],
)
async def test_structured_task_injects_status_note_provider(iteration, with_note):
    registry = ToolRegistry()
    submit = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://resources/wiki",
        limits=CompileLimits(),
    )
    registry.register(submit)

    captured: dict = {}

    class FakeLoop:
        max_iterations = 50

        async def _run_agent_loop(self, **kwargs):
            captured["provider"] = kwargs.get("status_note_provider")
            captured["note"] = await captured["provider"](iteration)
            submit.bundle = WikiBundleDraft.model_validate({"pages": []})
            return None, None, [], {}, iteration

    kwargs: dict = {
        "system_prompt": "system",
        "user_prompt": "user",
        "session_key": SessionKey(type="compile", channel_id="cmp", chat_id="cmp"),
        "tool_registry": registry,
        "openviking_tool_names": set(),
        "stop_tool_names": ["submit_wiki_bundle"],
        "openviking_connection": None,
    }
    if with_note:
        kwargs["budget_reminder_thresholds"] = (15, 8, 3)

    await AgentLoop.run_structured_task(FakeLoop(), **kwargs)

    note = captured["note"]
    if not with_note:
        assert note is None
        return
    assert "必须开始提交" in note


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

        async def get_skill(self, *args, **kwargs):
            raise AssertionError(
                "The agent reads the Skill through ov; normalization only checks URIs"
            )

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
    assert normalized.reason_provided is False

    Client.created.clear()
    with pytest.raises(CompileFailure) as raised:
        await service._normalize_request(
            CompileRequest.model_validate(
                {
                    "from": ["viking://resources/source"],
                    "to": "viking://resources/wiki",
                    "skill": "viking://resources/not-a-skill",
                }
            ),
            connection={"api_key": "secret"},
        )
    assert raised.value.code == "SKILL_INVALID"
    assert Client.created == set()


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
            self.sandbox = SimpleNamespace(
                backend=SandboxBackend.DIRECT,
                mode=None,
                model_copy=lambda *, deep: SimpleNamespace(mode=None),
            )

        def model_copy(self, *, update):
            copy = TaskConfig()
            for key, value in update.items():
                setattr(copy, key, value)
            return copy

    class FakeSandboxManager:
        def __init__(self, config, workspace_parent, workspace_path):
            del config, workspace_path
            self.workspace = workspace_parent / "workspace"
            self.workspace.mkdir(parents=True)

        def get_workspace_path(self, session_key):
            del session_key
            return self.workspace

        async def get_sandbox(self, session_key):
            del session_key

            class Sandbox:
                workspace = self.workspace

                async def list_files(self, max_entries=None):
                    del max_entries
                    return []

            return Sandbox()

        async def cleanup_session(self, session_key):
            del session_key

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

    def build_registry(request_loop, *, target_uri, source_roots, **kwargs):
        assert source_roots == {"src_1": "viking://resources/weekly"}
        registry = ToolRegistry()
        registry.register(
            SubmitWikiBundleTool(
                source_ids=set(source_roots),
                catalog_uris=set(),
                target_uri=target_uri,
                limits=CompileLimits(),
            )
        )
        return registry

    monkeypatch.setattr("vikingbot.compile.service.SandboxManager", FakeSandboxManager)
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
    monkeypatch.setattr(service, "_build_compile_registry", build_registry)

    await service._execute_task(task.task_id, request, {"api_key": "secret"})

    assert task.status == "completed"
    assert task.result is not None
    assert task.result.created == [f"{target_uri}/weekly-report"]
    assert task.result.updated == []
    assert task.result.page_count == 0
    assert client.added == f"{target_uri}/weekly-report"


class _NamedTool(_EchoTool):
    def __init__(self, name):
        self._name = name

    @property
    def name(self):
        return self._name


def _compile_service(
    tmp_path: Path,
    *,
    auth_mode: str,
    backend: SandboxBackend,
    allow_compile_exec: bool | None = None,
    limits: CompileLimits | None = None,
) -> BotCompileService:
    direct_exec_enabled = (
        DirectBackendConfig().allow_compile_exec
        if allow_compile_exec is None
        else allow_compile_exec
    )
    config = SimpleNamespace(
        bot_data_path=tmp_path,
        ov_server=SimpleNamespace(effective_auth_mode=auth_mode),
        sandbox=SimpleNamespace(
            backend=backend,
            backends=SimpleNamespace(
                direct=SimpleNamespace(allow_compile_exec=direct_exec_enabled),
            ),
        ),
    )
    return BotCompileService(
        agent_loop=SimpleNamespace(config=config),
        limits=limits,
    )


def _compile_request(*, connection: bool = False) -> CompileRequest:
    payload = {
        "from": ["viking://resources/source"],
        "to": "viking://resources/wiki",
        "skill": "viking://agent/skills/wiki",
    }
    if connection:
        payload["openviking_connection"] = {"api_key": "secret"}
    return CompileRequest.model_validate(payload)


def _sanitized_compile_request() -> SanitizedCompileRequest:
    return SanitizedCompileRequest.model_validate(
        {
            "from": ["viking://resources/source"],
            "to": "viking://resources/wiki",
            "reason": "Compile",
            "skill": "viking://agent/skills/wiki",
        }
    )


class _FakeWorkspaceSandbox:
    def __init__(
        self,
        files: dict[str, bytes],
        *,
        sizes: dict[str, int] | None = None,
    ):
        self.files = files
        self.sizes = sizes or {}
        self.reads: list[tuple[str, int | None]] = []

    async def list_files(self, path=".", *, max_entries):
        assert path == "."
        inventory = {name: len(payload) for name, payload in self.files.items()}
        inventory.update(self.sizes)
        assert len(inventory) <= max_entries
        return [SandboxFileInfo(path=name, size=size) for name, size in inventory.items()]

    async def read_file_bytes(self, path, *, max_bytes=None):
        self.reads.append((path, max_bytes))
        if path not in self.files:
            raise AssertionError(f"metadata-only file must not be read: {path}")
        payload = self.files[path]
        assert max_bytes is None or len(payload) <= max_bytes
        return payload


@pytest.mark.parametrize(
    ("backend", "allow_compile_exec", "expected"),
    [
        (SandboxBackend.DIRECT, False, False),
        (SandboxBackend.DIRECT, True, True),
        # No explicit setting: falls back to DirectBackendConfig().allow_compile_exec (True).
        (SandboxBackend.DIRECT, None, True),
        (SandboxBackend.SRT, False, True),
        (SandboxBackend.DOCKER, False, True),
        (SandboxBackend.OPENSANDBOX, False, True),
        (SandboxBackend.AIOSANDBOX, False, True),
    ],
)
def test_compile_exec_capability_depends_on_backend(
    tmp_path: Path,
    backend: SandboxBackend,
    allow_compile_exec: bool | None,
    expected: bool,
):
    service = _compile_service(
        tmp_path,
        auth_mode="dev",
        backend=backend,
        allow_compile_exec=allow_compile_exec,
    )

    assert service._compile_capabilities().exec_enabled is expected


def test_compile_exec_capability_fails_closed_for_unknown_backend(tmp_path: Path):
    service = _compile_service(
        tmp_path,
        auth_mode="dev",
        backend=SandboxBackend.DIRECT,
        allow_compile_exec=True,
    )
    service.config.sandbox.backend = "future-backend"

    assert service._compile_capabilities() == CompileCapabilities(exec_enabled=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_mode", "allow_compile_exec", "with_connection", "principal_scope", "expected_exec"),
    [
        # Local dev: config-backed connection, exec enabled by default on the direct backend.
        ("dev", None, False, "dev", True),
        # API-key auth: explicit connection, exec gated off even on the direct backend.
        ("api_key", False, True, "owner", False),
    ],
)
async def test_compile_create_task_connection_and_exec(
    monkeypatch,
    tmp_path: Path,
    auth_mode: str,
    allow_compile_exec: bool | None,
    with_connection: bool,
    principal_scope: str,
    expected_exec: bool,
):
    service = _compile_service(
        tmp_path,
        auth_mode=auth_mode,
        backend=SandboxBackend.DIRECT,
        allow_compile_exec=allow_compile_exec,
    )
    started = asyncio.Event()
    release = asyncio.Event()
    observed = {}

    async def normalize(request, *, connection):
        del request
        observed["connection"] = connection
        return _sanitized_compile_request()

    async def run_task(task_id, request, connection):
        del task_id, request
        observed["runner_connection"] = connection
        started.set()
        await release.wait()

    monkeypatch.setattr(service, "_normalize_request", normalize)
    monkeypatch.setattr(service, "_run_task", run_task)

    accepted = await service.create_task(
        _compile_request(connection=with_connection),
        principal_scope=principal_scope,
        task_id="cmp_ov_task",
    )
    await started.wait()
    repeated = await service.create_task(
        _compile_request(connection=with_connection),
        principal_scope=principal_scope,
        task_id="cmp_ov_task",
    )

    assert accepted.status == "accepted"
    assert accepted.session_id == "cmp_ov_task"
    assert repeated.session_id == accepted.session_id
    assert service._compile_capabilities().exec_enabled is expected_exec
    expected_connection = {"api_key": "secret"} if with_connection else {}
    assert observed == {
        "connection": expected_connection,
        "runner_connection": expected_connection,
    }
    runners = list(service._tasks)
    release.set()
    await asyncio.gather(*runners)
    assert service._admitted_tasks == 0


@pytest.mark.asyncio
async def test_compile_task_can_be_cancelled_by_its_owner(monkeypatch, tmp_path: Path):
    service = _compile_service(
        tmp_path,
        auth_mode="api_key",
        backend=SandboxBackend.AIOSANDBOX,
    )
    started = asyncio.Event()

    async def normalize(request, *, connection):
        del request, connection
        return _sanitized_compile_request()

    async def run_task(*args):
        del args
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_normalize_request", normalize)
    monkeypatch.setattr(service, "_run_task", run_task)

    accepted = await service.create_task(
        _compile_request(connection=True),
        principal_scope="owner",
    )
    await started.wait()
    runner = next(
        task for task in service._tasks if task.get_name() == f"compile:{accepted.task_id}"
    )

    assert await service.cancel_task(accepted.task_id, principal_scope="other") is None
    assert not runner.done()

    response = await service.cancel_task(accepted.task_id, principal_scope="owner")
    assert response is not None
    assert response["status"] in {"cancelling", "cancelled"}
    await asyncio.gather(runner, return_exceptions=True)

    cancelled = await service.store.get(accepted.task_id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.stage == "cancelled"
    assert cancelled.result is None
    assert cancelled.error is None
    assert service._admitted_tasks == 0
    assert await service.cancel_task(accepted.task_id, principal_scope="owner") == (
        cancelled.public_dict()
    )


@pytest.mark.asyncio
async def test_compile_admission_is_bounded_per_principal_and_globally(monkeypatch, tmp_path: Path):
    limits = CompileLimits(
        accepted_tasks=2,
        accepted_tasks_per_principal=1,
    )
    service = _compile_service(
        tmp_path,
        auth_mode="api_key",
        backend=SandboxBackend.AIOSANDBOX,
        limits=limits,
    )
    release = asyncio.Event()

    async def normalize(request, *, connection):
        del request, connection
        return _sanitized_compile_request()

    async def run_task(*args):
        del args
        await release.wait()

    monkeypatch.setattr(service, "_normalize_request", normalize)
    monkeypatch.setattr(service, "_run_task", run_task)

    await service.create_task(_compile_request(connection=True), principal_scope="alice")
    with pytest.raises(CompileFailure) as per_principal:
        await service.create_task(_compile_request(connection=True), principal_scope="alice")
    await service.create_task(_compile_request(connection=True), principal_scope="bob")
    with pytest.raises(CompileFailure) as global_limit:
        await service.create_task(_compile_request(connection=True), principal_scope="carol")

    assert per_principal.value.code == "RESOURCE_EXHAUSTED"
    assert global_limit.value.code == "RESOURCE_EXHAUSTED"
    runners = list(service._tasks)
    release.set()
    await asyncio.gather(*runners)
    assert service._admitted_tasks == 0
    assert service._admitted_by_principal == {}


@pytest.mark.asyncio
async def test_salvage_copies_workspace_and_repairs_links(tmp_path: Path):
    service = _compile_service(
        tmp_path,
        auth_mode="api_key",
        backend=SandboxBackend.DIRECT,
    )
    files = {
        "__compile_staging__/wiki_pages/home.md": b"# Home\n",
        "__compile_staging__/wiki_pages/guide/topic.md": "\n".join(
            [
                "[Home](home.md#top)",
                "[Meta](meta/readme.md)",
                "[Existing](../existing.md)",
                "[Missing](missing.md)",
                "![Missing image](missing.png)",
                '[Titled](../meta/title.md "Title")',
                "[Paren](../meta/foo(1).md)",
                "[Web](https://example.com)",
                "[Source](viking://resources/source)",
                "[Anchor](#local)",
                "`[Code](missing.md)`",
            ]
        ).encode(),
        "meta/readme.md": b"# Meta\n",
        "meta/title.md": b"# Title\n",
        "meta/foo(1).md": b"# Paren\n",
        "meta/events.jsonl": b"",
        "artifact.bin": b"\x00\x01",
        "home.md": b"# Artifact Home\n",
        "roadmap.md": b"[Roadmap](future.md)\n",
        "caseonly.md": b"case mismatch",
        "Foo.md": b"first",
        "foo.md": b"duplicate",
        "bad#name.txt": b"unsafe URI",
        "__compile_staging__/work/notes.txt": b"notes",
        "__compile_staging__/tmp/check.txt": b"check",
        "tmp_out/scratch.txt": b"scratch",
        "TmpCache/case.txt": b"case",
        "skills/wiki/SKILL.md": b"do not copy",
        "sandboxes/cmp-srt-settings.json": b'{"filesystem": "/private/host/path"}',
    }

    class Client:
        operations = []

        async def tree(self, uri, *, node_limit):
            assert uri == "viking://resources/wiki"
            assert node_limit == service.limits.target_inventory_entries + 1
            return [
                {"uri": f"{uri}/existing.md", "isDir": False, "size": 1},
                {"uri": f"{uri}/CaseOnly.md", "isDir": False, "size": 8},
                {"uri": f"{uri}/meta/events.jsonl", "isDir": False, "size": 3},
            ]

        async def download_bytes(self, uri):
            return {
                "viking://resources/wiki/CaseOnly.md": b"old case",
                "viking://resources/wiki/meta/events.jsonl": b"old",
            }[uri]

        async def batch_write(self, *, root_uri, operations, wait):
            assert root_uri == "viking://resources/wiki"
            assert wait is False
            self.operations = operations
            assert all(operation["mode"] == "upsert" for operation in operations)
            return {
                "created": [operation["uri"] for operation in operations],
                "updated": [],
                "unchanged": [],
            }

    client = Client()
    sandbox = _FakeWorkspaceSandbox(files)
    result = await service._salvage_workspace(
        client=client,
        request=_sanitized_compile_request(),
        sandbox=sandbox,
        workspace_baseline={"sandboxes/cmp-srt-settings.json"},
    )

    assert result is not None
    payloads = {
        operation["uri"].removeprefix("viking://resources/wiki/"): base64.b64decode(
            operation["content_base64"]
        )
        for operation in client.operations
    }
    assert "skills/wiki/SKILL.md" not in payloads
    assert "empty" not in payloads
    assert "__compile_staging__/wiki_pages/home.md" not in payloads
    assert payloads["home.md"] == b"# Artifact Home\n"
    assert payloads["roadmap.md"] == b"[Roadmap](future.md)\n"
    assert payloads["artifact.bin"] == b"\x00\x01"
    assert payloads["CaseOnly.md"] == b"case mismatch"
    assert payloads["meta/events.jsonl"] == b""
    assert "__compile_staging__/work/notes.txt" not in payloads
    assert "__compile_staging__/tmp/check.txt" not in payloads
    assert "tmp_out/scratch.txt" not in payloads
    assert "TmpCache/case.txt" not in payloads
    assert "sandboxes/cmp-srt-settings.json" not in payloads
    assert "sandboxes/cmp-srt-settings.json" not in {path for path, _limit in sandbox.reads}
    assert sum(path.casefold() == "foo.md" for path in payloads) == 1
    assert "bad#name.txt" not in payloads
    topic = payloads["guide/topic.md"].decode()
    assert "[Home](../home.md#top)" in topic
    assert "[Meta](../meta/readme.md)" in topic
    assert "[Existing](../existing.md)" in topic
    assert "Missing\nMissing image" in topic
    assert '[Titled](../meta/title.md "Title")' in topic
    assert "[Paren](../meta/foo(1).md)" in topic
    assert "[Web](https://example.com)" in topic
    assert "[Source](viking://resources/source)" in topic
    assert "[Anchor](#local)" in topic
    assert "`[Code](missing.md)`" in topic
    assert result.warnings and "partial output" in result.warnings[0]
    assert any("Skipped" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    "content",
    [
        '[Missing](missing.md "title")',
        "[Missing](missing(1).md)",
    ],
)
def test_salvage_removes_unresolved_complex_markdown_links(content: str):
    assert (
        BotCompileService._repair_salvaged_markdown(
            content,
            source_path="guide/topic.md",
            known_paths={"guide/topic.md"},
        )
        == "Missing"
    )


def test_salvage_preserves_existing_escaped_parenthesis_link():
    content = r"[Paren](../meta/foo\(1\).md)"

    assert (
        BotCompileService._repair_salvaged_markdown(
            content,
            source_path="guide/topic.md",
            known_paths={"meta/foo(1).md"},
        )
        == content
    )


@pytest.mark.asyncio
async def test_salvage_ignores_preexisting_srt_settings(tmp_path: Path):
    service = _compile_service(
        tmp_path,
        auth_mode="api_key",
        backend=SandboxBackend.SRT,
    )
    settings_path = "sandboxes/cmp-srt-settings.json"

    class Client:
        async def tree(self, uri, *, node_limit):
            raise AssertionError(f"target must not be read: {uri}, {node_limit}")

    result = await service._salvage_workspace(
        client=Client(),
        request=_sanitized_compile_request(),
        sandbox=_FakeWorkspaceSandbox({}, sizes={settings_path: 128}),
        workspace_baseline={settings_path},
    )

    assert result is None


@pytest.mark.asyncio
async def test_srt_settings_created_by_manager_are_in_the_workspace_baseline(
    monkeypatch, tmp_path: Path
):
    async def start_without_process(self):
        self.workspace.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(SrtBackend, "start", start_without_process)
    config = SimpleNamespace(
        sandbox=SandboxConfig(backend=SandboxBackend.SRT, mode=SandboxMode.PER_SESSION),
        skills=[],
    )
    manager = SandboxManager(config, tmp_path / "workspaces", tmp_path / "source")
    session_key = SessionKey(type="compile", channel_id="cmp", chat_id="cmp:windows")

    sandbox = await manager.get_sandbox(session_key)
    baseline = {
        entry.path
        for entry in await sandbox.list_files(max_entries=CompileLimits().target_inventory_entries)
    }

    workspace_id = manager.to_workspace_id(session_key)
    settings_name = portable_path_component(workspace_id)
    assert ":" in workspace_id
    assert ":" not in settings_name
    assert baseline == {f"sandboxes/{settings_name}-srt-settings.json"}


@pytest.mark.asyncio
async def test_salvage_skips_oversized_file_before_reading(tmp_path: Path):
    limits = CompileLimits(output_total_bytes=4)
    service = _compile_service(
        tmp_path,
        auth_mode="api_key",
        backend=SandboxBackend.AIOSANDBOX,
        limits=limits,
    )

    class Client:
        operations = []

        async def tree(self, uri, *, node_limit):
            del node_limit
            return [
                {
                    "uri": f"{uri}/existing.txt",
                    "isDir": False,
                    "size": 1024 * 1024 * 1024,
                }
            ]

        async def download_bytes(self, uri):
            raise AssertionError(f"oversized target must not be downloaded: {uri}")

        async def batch_write(self, *, root_uri, operations, wait):
            del root_uri, wait
            self.operations = operations
            return {
                "created": [operation["uri"] for operation in operations],
                "updated": [],
                "unchanged": [],
            }

    sandbox = _FakeWorkspaceSandbox(
        {"existing.txt": b"ok", "small.txt": b"ok"},
        sizes={"huge.bin": 1024 * 1024 * 1024},
    )
    client = Client()
    result = await service._salvage_workspace(
        client=client,
        request=_sanitized_compile_request(),
        sandbox=sandbox,
        workspace_baseline=set(),
    )

    assert result is not None
    assert sandbox.reads == [
        ("existing.txt", limits.output_total_bytes),
        ("small.txt", limits.output_total_bytes),
    ]
    assert [operation["uri"] for operation in client.operations] == [
        "viking://resources/wiki/small.txt"
    ]
    assert any("Skipped 2" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_salvage_grace_returns_when_cancellation_is_suppressed(monkeypatch, tmp_path: Path):
    service = _compile_service(
        tmp_path,
        auth_mode="api_key",
        backend=SandboxBackend.AIOSANDBOX,
        limits=CompileLimits(salvage_grace_seconds=0.01),
    )
    request = _sanitized_compile_request()
    task = CompileTask(
        task_id="cmp_stubborn_salvage",
        principal_scope="owner",
        sanitized_request=request,
        status="running",
        stage="agent",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    await service.store.create(task)
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def stubborn_salvage(**kwargs):
        del kwargs
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    monkeypatch.setattr(service, "_salvage_workspace", stubborn_salvage)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    try:
        with pytest.raises(CompileFailure) as raised:
            await asyncio.wait_for(
                service._complete_salvaged_task(
                    task_id=task.task_id,
                    client=object(),
                    request=request,
                    sandbox=object(),
                    workspace_baseline=set(),
                    reason="reached its iteration limit",
                    failure_code="AGENT_OUTPUT_INVALID",
                ),
                timeout=0.2,
            )
        assert raised.value.code == "AGENT_OUTPUT_INVALID"
        assert raised.value.stage == "salvaging"
        assert "grace limit" in str(raised.value)
        assert loop.time() - started_at < 0.2
        await asyncio.wait_for(cancelled.wait(), timeout=0.1)
    finally:
        release.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_salvage_keeps_its_grace_period_when_parent_is_cancelled(monkeypatch, tmp_path: Path):
    service = _compile_service(
        tmp_path,
        auth_mode="api_key",
        backend=SandboxBackend.AIOSANDBOX,
        limits=CompileLimits(salvage_grace_seconds=0.2),
    )
    request = _sanitized_compile_request()
    task = CompileTask(
        task_id="cmp_cancelled_salvage",
        principal_scope="owner",
        sanitized_request=request,
        status="running",
        stage="agent",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    await service.store.create(task)
    started = asyncio.Event()

    async def salvage(**kwargs):
        del kwargs
        started.set()
        await asyncio.sleep(0.02)
        return CompileResult(
            from_=request.from_,
            to=request.to,
            skill=request.skill,
            created=[f"{request.to}/partial.md"],
        )

    monkeypatch.setattr(service, "_salvage_workspace", salvage)
    runner = asyncio.create_task(
        service._complete_salvaged_task(
            task_id=task.task_id,
            client=object(),
            request=request,
            sandbox=object(),
            workspace_baseline=set(),
            reason="reached its iteration limit",
            failure_code="AGENT_OUTPUT_INVALID",
        )
    )
    await started.wait()
    runner.cancel()
    await asyncio.wait_for(runner, timeout=0.2)

    completed = await service.store.get(task.task_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.stage == "salvaged"


@pytest.mark.asyncio
async def test_cleanup_grace_releases_execution_slot_and_target_lock(tmp_path: Path):
    service = _compile_service(
        tmp_path,
        auth_mode="api_key",
        backend=SandboxBackend.DIRECT,
        limits=CompileLimits(
            concurrent_tasks=1,
            cleanup_grace_seconds=0.01,
        ),
    )
    request = _sanitized_compile_request()
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()
    second_started = asyncio.Event()

    class StubbornManager:
        async def cleanup_session(self, session_key):
            del session_key
            cleanup_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_cancelled.set()
                await release_cleanup.wait()
            cleanup_finished.set()

    async def execute(task_id, _request, connection):
        del connection
        if task_id == "cmp_first":
            await service._cleanup_execution_resources(
                sandbox_manager=StubbornManager(),
                session_key=SessionKey(type="compile", channel_id=task_id, chat_id=task_id),
                client=None,
                workspace_parent=tmp_path / task_id,
            )
        else:
            second_started.set()

    service._execute_task = execute
    first = asyncio.create_task(service._run_task("cmp_first", request, {}))
    await cleanup_started.wait()
    second = asyncio.create_task(service._run_task("cmp_second", request, {}))
    try:
        await asyncio.wait_for(cleanup_cancelled.wait(), timeout=0.2)
        await asyncio.wait_for(second_started.wait(), timeout=0.2)
        await asyncio.gather(first, second)
        assert service._semaphore._value == 1
        assert service._target_locks == {}
    finally:
        release_cleanup.set()
        await asyncio.wait_for(cleanup_finished.wait(), timeout=0.2)


@pytest.mark.asyncio
async def test_salvage_respects_combined_output_operation_limit(tmp_path: Path):
    limits = CompileLimits(output_pages=2, output_files=2, output_operations=3)
    service = _compile_service(
        tmp_path,
        auth_mode="api_key",
        backend=SandboxBackend.DIRECT,
        limits=limits,
    )
    files = {
        "a.txt": b"a",
        "b.txt": b"b",
        "__compile_staging__/wiki_pages/c.md": b"c",
        "__compile_staging__/wiki_pages/d.md": b"d",
    }

    class Client:
        operations = []

        async def tree(self, uri, *, node_limit):
            del uri, node_limit
            return []

        async def batch_write(self, *, root_uri, operations, wait):
            del root_uri, wait
            self.operations = operations
            return {
                "created": [operation["uri"] for operation in operations],
                "updated": [],
                "unchanged": [],
            }

    client = Client()
    result = await service._salvage_workspace(
        client=client,
        request=_sanitized_compile_request(),
        sandbox=_FakeWorkspaceSandbox(files),
        workspace_baseline=set(),
    )

    assert result is not None
    assert len(client.operations) == limits.output_operations
    assert result.warnings and any("Skipped 1" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_iteration_limit_salvages_before_workspace_cleanup(monkeypatch, tmp_path: Path):
    observed = []
    remote_files = {}

    sandbox = _FakeWorkspaceSandbox(remote_files)

    class TaskConfig:
        def __init__(self):
            self.bot_data_path = tmp_path
            self.workspace_path = tmp_path / "host-workspace"
            self.skills = []
            self.sandbox = SimpleNamespace(
                backend=SandboxBackend.DIRECT,
                mode=None,
                model_copy=lambda *, deep: SimpleNamespace(mode=None),
            )

        def model_copy(self, *, update):
            copy = TaskConfig()
            for key, value in update.items():
                setattr(copy, key, value)
            return copy

    class FakeSandboxManager:
        def __init__(self, config, workspace_parent, workspace_path):
            del config, workspace_path
            self.workspace = workspace_parent / "workspace"
            self.workspace.mkdir(parents=True)

        def get_workspace_path(self, session_key):
            del session_key
            return self.workspace

        async def get_sandbox(self, session_key):
            del session_key
            return sandbox

        async def cleanup_session(self, session_key):
            del session_key
            observed.append("cleanup")

    class FakeRequestLoop:
        def __init__(self, **kwargs):
            self.workspace = kwargs["workspace"]

        async def run_structured_task(self, **kwargs):
            del kwargs
            remote_files["output.md"] = b"partial"
            raise AgentIterationLimitExceeded(1)

    class Client:
        async def get_skill(self, skill_name, *, target_uri):
            assert skill_name == "wiki"
            assert target_uri == "viking://agent/skills"
            return {
                "root_uri": "viking://agent/skills/wiki",
                "content": "---\nname: wiki\ndescription: Write Wiki\n---\nWrite it.",
                "files": [],
            }

        async def close(self):
            return None

    async def create_client(**kwargs):
        del kwargs
        return Client()

    async def no_op(*args, **kwargs):
        del args, kwargs

    async def salvage(*, client, request, sandbox, workspace_baseline, reason):
        del client
        assert workspace_baseline == set()
        assert request.to == "viking://resources/wiki"
        assert await sandbox.read_file_bytes("output.md") == b"partial"
        assert "1-iteration limit" in reason
        observed.append("salvage")
        return CompileResult(
            **{
                "from": request.from_,
                "to": request.to,
                "skill": request.skill,
                "created": [f"{request.to}/output.md"],
                "page_count": 1,
                "warnings": ["partial output"],
            }
        )

    monkeypatch.setattr("vikingbot.compile.service.SandboxManager", FakeSandboxManager)
    monkeypatch.setattr("vikingbot.compile.service.AgentLoop", FakeRequestLoop)
    monkeypatch.setattr("vikingbot.compile.service.VikingClient.create", create_client)

    host_loop = SimpleNamespace(
        config=TaskConfig(),
        bus=None,
        provider=None,
        model=None,
        temperature=0,
        max_iterations=1,
        memory_window=1,
        brave_api_key=None,
        exa_api_key=None,
        gen_image_model=None,
        exec_config=None,
    )
    service = BotCompileService(agent_loop=host_loop)
    submit_tool = SimpleNamespace(file_payloads=[], page_count=1, file_count=1)
    registry = SimpleNamespace(get=lambda name: submit_tool)
    monkeypatch.setattr(service, "_build_compile_registry", lambda *args, **kwargs: registry)
    monkeypatch.setattr(service, "_salvage_workspace", salvage)

    request = _sanitized_compile_request()
    task = CompileTask(
        task_id="cmp_iterations",
        principal_scope="owner",
        sanitized_request=request,
        status="accepted",
        stage="queued",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    await service.store.create(task)
    await service._execute_task(task.task_id, request, {"api_key": "secret"})

    completed = await service.store.get(task.task_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.stage == "salvaged"
    assert completed.result is not None
    assert completed.result.created == ["viking://resources/wiki/output.md"]
    assert observed == ["salvage", "cleanup"]
    assert not (tmp_path / "compile_workspaces" / task.task_id).exists()

    await service._fail(
        task.task_id,
        CompileFailure("INTERNAL", "late cleanup failure", stage="salvaging"),
    )
    still_completed = await service.store.get(task.task_id)
    assert still_completed is not None
    assert still_completed.status == "completed"
    assert still_completed.result is not None


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
async def test_task_store_missing_lookups_do_not_retain_locks(tmp_path: Path):
    store = CompileTaskStore(tmp_path)

    for index in range(2_000):
        assert await store.get(f"cmp_missing_{index}") is None
    for invalid in ("missing", "cmp_bad/path", "cmp_bad\\path"):
        with pytest.raises(ValueError, match="invalid compile task id"):
            await store.get(invalid)

    assert store._locks == {}
    assert not list(store.root.iterdir())


@pytest.mark.asyncio
async def test_task_store_releases_lock_after_concurrent_users_finish(tmp_path: Path):
    store = CompileTaskStore(tmp_path)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first_user():
        async with store._task_lock("cmp_shared"):
            first_entered.set()
            await release_first.wait()

    async def second_user():
        await first_entered.wait()
        async with store._task_lock("cmp_shared"):
            second_entered.set()

    first = asyncio.create_task(first_user())
    second = asyncio.create_task(second_user())
    await first_entered.wait()
    await asyncio.sleep(0)

    assert not second_entered.is_set()
    assert store._locks["cmp_shared"][1] == 2

    release_first.set()
    await asyncio.gather(first, second)

    assert second_entered.is_set()
    assert store._locks == {}


@pytest.mark.asyncio
async def test_task_store_prunes_expired_and_excess_terminal_records(tmp_path: Path):
    store = CompileTaskStore(tmp_path)
    request = _sanitized_compile_request()
    now = datetime.now(timezone.utc)
    timestamps = {
        "cmp_expired": now - timedelta(days=2),
        "cmp_older": now - timedelta(minutes=2),
        "cmp_newest": now - timedelta(minutes=1),
    }
    for task_id, updated_at in timestamps.items():
        timestamp = updated_at.isoformat().replace("+00:00", "Z")
        await store.create(
            CompileTask(
                task_id=task_id,
                principal_scope="owner",
                sanitized_request=request,
                status="completed",
                stage="completed",
                created_at=timestamp,
                updated_at=timestamp,
            )
        )

    assert await store.prune_terminal(retention_seconds=24 * 60 * 60, max_records=1) == 2
    assert await store.get("cmp_expired") is None
    assert await store.get("cmp_older") is None
    assert await store.get("cmp_newest") is not None


@pytest.mark.asyncio
async def test_task_owner_isolation(tmp_path: Path):
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


@pytest.mark.asyncio
async def test_compile_reads_with_existing_shell_cli_and_submits_without_preloading(
    monkeypatch, tmp_path: Path
):
    """The first agent turn uses the environment CLI; only submission writes through the client."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ov = bin_dir / "ov"
    ov.write_text('#!/bin/sh\nprintf "runtime-skill %s" "$OPENVIKING_CLI_CONFIG_FILE"\n')
    ov.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", "/existing/ovcli.conf")
    writes = []
    shell = ExecTool()

    class Client:
        async def batch_write(self, *, root_uri, operations, wait, timeout):
            writes.extend(operations)
            assert root_uri == "viking://resources/wiki"
            assert all(op["mode"] == "upsert" for op in operations)
            return {"created": [op["uri"] for op in operations]}

        async def close(self):
            pass

    async def create_client(**kwargs):
        return Client()

    class RequestLoop:
        def __init__(self, **kwargs):
            self.config = kwargs["config"]
            self.manager = kwargs["sandbox_manager"]
            self.tools = ToolRegistry()
            self.tools.register(shell)

        async def run_structured_task(self, **kwargs):
            assert not writes
            assert len(kwargs["system_prompt"]) < 1100
            assert "ov read <skill>/SKILL.md" in kwargs["system_prompt"]
            assert set(json.loads(kwargs["user_prompt"])) == {"reason", "from", "to", "skill"}
            assert kwargs["openviking_tool_names"] == set()
            registry = kwargs["tool_registry"]
            assert registry.get("exec") is shell
            assert set(registry.tool_names) == {"exec", "submit_wiki_bundle"}
            context = ToolContext(session_key=kwargs["session_key"], sandbox_manager=self.manager)
            sandbox = await self.manager.get_sandbox(context.session_key)
            assert await sandbox.list_files(max_entries=10) == []
            read = await registry.get("exec").execute(
                context, command="ov read 'viking://agent/skills/wiki/SKILL.md'"
            )
            assert read == "runtime-skill /existing/ovcli.conf"
            await sandbox.write_file(
                "__compile_staging__/output/topic.md",
                "---\ntype: concept\ntitle: Topic\ndescription: Summary\n---\nBody.\n",
            )
            submit = registry.get("submit_wiki_bundle")
            assert (await submit.execute(context)).startswith("Resource output accepted")
            return submit.bundle, [], {}, 1

    config = Config(storage_workspace=str(tmp_path), sandbox=SandboxConfig(backend="direct"))
    host = SimpleNamespace(
        config=config,
        bus=None,
        provider=None,
        model=None,
        temperature=0,
        memory_window=1,
        brave_api_key=None,
        exa_api_key=None,
        gen_image_model=None,
        exec_config=None,
    )
    service = BotCompileService(agent_loop=host)
    request = _sanitized_compile_request()
    task = CompileTask(
        task_id="cmp_cli",
        principal_scope="owner",
        sanitized_request=request,
        status="accepted",
        stage="queued",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    await service.store.create(task)
    monkeypatch.setattr("vikingbot.compile.service.AgentLoop", RequestLoop)
    monkeypatch.setattr("vikingbot.compile.service.VikingClient.create", create_client)
    await service._execute_task(task.task_id, request, {"api_key": "request-key"})
    completed = await service.store.get(task.task_id)
    assert completed.status == "completed"
    assert completed.result.created == ["viking://resources/wiki/topic.md"]
    assert len(writes) == 1
    assert not (config.bot_data_path / "compile_workspaces" / task.task_id).exists()


@pytest.mark.asyncio
async def test_memory_update_resolves_only_submitted_target_without_inventory():
    """Updates work without a startup catalog and reject paths outside the target."""
    target = "viking://user/alice/memories/wiki"
    uri = f"{target}/topic.md"
    calls = []

    async def resolve(value):
        calls.append(value)
        return value == uri

    catalog = set()
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=catalog,
        target_uri=target,
        limits=CompileLimits(),
        wiki_uri_resolver=resolve,
    )
    result = await tool.execute(
        ToolContext(), pages=[_page(1, "Topic", update_uri=uri, path_hint=None)]
    )
    assert result.startswith("Wiki bundle accepted")
    assert catalog == {uri}
    result = await tool.execute(
        ToolContext(),
        pages=[_page(1, "Outside", update_uri="viking://resources/outside.md", path_hint=None)],
    )
    assert result.startswith("Error:")
    assert calls == [uri]
