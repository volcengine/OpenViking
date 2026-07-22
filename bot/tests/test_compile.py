import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.agent.tools.compile import CompileScopedTool, SubmitWikiBundleTool
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.compile.models import (
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
    empty = SkillLoader.parse(
        "---\nname: a\ndescription: A\nallowed-tools: []\n---\nDo it"
    )
    assert missing["allowed_tools_declared"] is False
    assert empty["allowed_tools_declared"] is True
    assert "allowed-tools: []" in SkillLoader.to_skill_md(empty)


def test_skill_loader_rejects_non_string_allowed_tools():
    with pytest.raises(ValueError, match="array of strings"):
        SkillLoader.parse(
            "---\nname: a\ndescription: A\nallowed-tools: [Read, 3]\n---\nDo it"
        )


def test_renderer_creates_okf_pages_links_and_citations():
    bundle = WikiBundleDraft.model_validate(
        {
            "pages": [
                _page(1, "Alpha", body_markdown="Read Beta next."),
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
    assert "Read [Beta](./beta.md) next." in first["content"]
    assert "[1] [source](viking://resources/source)" in first["content"]


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
    assert "tags:\n- stable\n- new" in content
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
        catalog_uris={"viking://resources/wiki/existing.md"},
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
    assert (
        await wrapped.execute(context, uri="viking://resources/other")
    ).startswith("Error:")
    assert (
        await wrapped.execute(
            context,
            uri="viking://resources/source/../../other",
        )
    ).startswith("Error:")
    accepted = await wrapped.execute(
        context, uri="viking://resources/source/child", recursive=True
    )
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
        async def attrs(self, uri):
            return {"uri": uri.rstrip("/")}

        async def stat(self, uri):
            return {"uri": uri, "isDir": True}

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
    assert normalized.skill == "viking://agent/skills/wiki"
    assert normalized.reason.startswith("Follow the loaded Skill's instructions")


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
        "web_search",
        "message",
        "openviking_list",
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

    empty, empty_ov_names = service._build_request_registry(
        parsed_skill={"allowed_tools_declared": True, "allowed_tools": []},
        **common,
    )
    assert empty.tool_names == [
        "read_file",
        "openviking_list",
        "openviking_multi_read",
        "submit_wiki_bundle",
    ]
    assert empty_ov_names == {"openviking_list", "openviking_multi_read"}

    selected, ov_names = service._build_request_registry(
        parsed_skill={
            "allowed_tools_declared": True,
            "allowed_tools": ["Read", "WebSearch", "openviking_list"],
        },
        **common,
    )
    assert selected.tool_names == [
        "read_file",
        "web_search",
        "openviking_list",
        "openviking_multi_read",
        "submit_wiki_bundle",
    ]
    assert ov_names == {"openviking_list", "openviking_multi_read"}

    with pytest.raises(CompileFailure) as error:
        service._build_request_registry(
            parsed_skill={
                "allowed_tools_declared": True,
                "allowed_tools": ["made_up_tool"],
            },
            **common,
        )
    assert error.value.code == "SKILL_CAPABILITY_UNAVAILABLE"


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
