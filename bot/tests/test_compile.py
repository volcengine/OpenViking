import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.agent.loop import (
    AgentIterationLimitExceeded,
    AgentLoop,
)
from vikingbot.agent.tools.base import MultimodalToolResult, Tool, ToolContext
from vikingbot.agent.tools.compile import (
    CompileScopedTool,
    SubmitTargetCheckoutTool,
    SubmitWikiBundleTool,
)
from vikingbot.agent.tools.ov_file import (
    VikingExportTool,
    VikingMultiReadTool,
)
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.compile.models import (
    DEFAULT_COMPILE_INSTRUCTION,
    CompileFailure,
    CompileLimits,
    CompileRequest,
    CompileResult,
    CompileTask,
    SanitizedCompileRequest,
    WikiBundleDraft,
    utc_now,
)
from vikingbot.compile.readlist import (
    READLIST_PATH,
    ReadlistTracker,
    ReadTrackingTool,
)
from vikingbot.compile.renderer import (
    WikiRenderer,
)
from vikingbot.compile.service import BotCompileService, CompileCapabilities
from vikingbot.compile.store import CompileTaskStore
from vikingbot.config.schema import (
    DirectBackendConfig,
    SandboxBackend,
    SessionKey,
)
from vikingbot.sandbox.base import SandboxFileInfo

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


@pytest.mark.asyncio
async def test_submit_tool_accepts_only_one_complete_skill_package():
    tool = SubmitWikiBundleTool(
        source_ids={"src_1"},
        catalog_uris=set(),
        target_uri="viking://agent/skills",
        limits=CompileLimits(),
    )

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


@pytest.mark.parametrize(
    "instruction, provided, model_reply, expected_language, input_kind, selected_text, omitted_text",
    [
        pytest.param(
            "请用中文输出",
            True,
            "zh-CN",
            "zh-CN",
            "user_instruction",
            "请用中文输出",
            "source sample",
            id="explicit-language",
        ),
        pytest.param(
            DEFAULT_COMPILE_INSTRUCTION,
            False,
            "ja",
            "en",
            "source_content",
            "source sample",
            DEFAULT_COMPILE_INSTRUCTION,
            id="source-language",
        ),
    ],
)
async def test_wiki_language_classifier_selects_input(
    instruction, provided, model_reply, expected_language, input_kind, selected_text, omitted_text
):
    from unittest.mock import AsyncMock

    provider = SimpleNamespace(
        chat=AsyncMock(
            return_value=SimpleNamespace(
                content=model_reply,
                usage={"prompt_tokens": 12, "completion_tokens": 1},
            )
        )
    )
    service = object.__new__(BotCompileService)
    service.agent_loop = SimpleNamespace(provider=provider, model="test-model")
    request = SanitizedCompileRequest.model_validate(
        {
            "from": ["viking://resources/source"],
            "to": "viking://resources/wiki",
            "skill": "viking://agent/skills/wiki",
            "instruction": instruction,
            "instruction_provided": provided,
        }
    )
    language, usage = await service._classify_wiki_language(
        request=request,
        sources=[{"overview": "source overview"}],
        source_sample="source sample",
        session_key=_FakeCompileSessionKey(),
    )
    assert language == expected_language
    assert usage == {"prompt_tokens": 12, "completion_tokens": 1}
    provider.chat.assert_awaited_once()
    content = provider.chat.await_args.kwargs["messages"][1]["content"]
    assert f"input_kind={input_kind}" in content
    assert selected_text in content
    assert omitted_text not in content


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
    provider = _FakeChatProvider("SEGMENT")
    loop = _compact_loop(provider)

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

    assert provider.calls >= 3  # Multiple source segments plus the final merge.
    assert out[2]["content"].startswith("[Context compaction]")
    assert "SEGMENT" in out[2]["content"]
    # Budget-aware window: at least one complete recent turn is retained and the
    # compacted result fits the (small) budget.
    assert sum(len(json.dumps(m, ensure_ascii=False)) for m in out) <= 20_000
    assert out[3:][0]["role"] in {"user", "assistant"}


@pytest.mark.asyncio
async def test_materialize_sources_exports_full_tree_and_writes_manifest():
    service = object.__new__(BotCompileService)
    service.limits = CompileLimits()

    class Client:
        async def download_bytes(self, uri):
            if uri.endswith("a.jsonl"):
                return b'{"type":"event_msg","payload":{"message":"hi"}}\n'
            if uri.endswith("b.bin"):
                return b"\xff\xfe\x00"
            return b"# guide\n"

    class Sandbox:
        def __init__(self):
            self.writes = {}

        async def write_file(self, path, content):
            self.writes[path] = content

    sources = [
        {
            "source_id": "src_1",
            "directory_uri": "viking://resources/dream-sessions",
            "entries": [
                {
                    "uri": "viking://resources/dream-sessions/08/05/a.jsonl",
                    "is_dir": False,
                    "size": 40,
                },
                {
                    "uri": "viking://resources/dream-sessions/08/05/b.bin",
                    "is_dir": False,
                    "size": 3,
                },
                {"uri": "viking://resources/dream-sessions/08/05", "is_dir": True},
            ],
        },
        {
            "source_id": "src_2",
            "directory_uri": "viking://resources/dream-memory-store",
            "entries": [
                {
                    "uri": "viking://resources/dream-memory-store/guide.md",
                    "is_dir": False,
                    "size": 9,
                }
            ],
        },
    ]
    sandbox = Sandbox()
    warnings, manifest_path, language_sample = await service._materialize_sources(
        client=Client(),
        sources=sources,
        sandbox=sandbox,
    )

    assert warnings == []
    assert manifest_path == "compile_resources/_manifest.tsv"
    assert sandbox.writes["compile_resources/src_1/dream-sessions/08/05/a.jsonl"] == (
        '{"type":"event_msg","payload":{"message":"hi"}}\n'
    )
    assert sandbox.writes["compile_resources/src_2/dream-memory-store/guide.md"] == "# guide\n"
    assert "b.bin" not in sandbox.writes
    manifest = sandbox.writes["compile_resources/_manifest.tsv"]
    assert "source_id\turi\tworkspace_path\tsize\tstatus" in manifest
    assert "skipped:binary" in manifest
    assert "materialized" in manifest
    assert "# guide" in language_sample
    assert '"message":"hi"' in language_sample


@pytest.mark.asyncio
async def test_materialize_sources_records_download_failures_without_crashing():
    service = object.__new__(BotCompileService)
    service.limits = CompileLimits()

    class Client:
        async def download_bytes(self, uri):
            raise OpenVikingError("offline", code="UNAVAILABLE")

    class Sandbox:
        def __init__(self):
            self.writes = {}

        async def write_file(self, path, content):
            self.writes[path] = content

    sources = [
        {
            "source_id": "src_1",
            "directory_uri": "viking://resources/s",
            "entries": [{"uri": "viking://resources/s/a.jsonl", "is_dir": False, "size": 1}],
        }
    ]
    sandbox = Sandbox()
    warnings, manifest_path, language_sample = await service._materialize_sources(
        client=Client(),
        sources=sources,
        sandbox=sandbox,
    )

    assert manifest_path == "compile_resources/_manifest.tsv"
    assert language_sample == ""
    assert any("failed to materialize" in warning for warning in warnings)
    assert "skipped:download-error" in sandbox.writes["compile_resources/_manifest.tsv"]


@pytest.mark.asyncio
async def test_materialize_sources_enforces_limits_before_download():
    class Client:
        def __init__(self):
            self.downloads = []

        async def download_bytes(self, uri):
            self.downloads.append(uri)
            return b"x"

    service = object.__new__(BotCompileService)
    service.limits = CompileLimits(source_files=1)
    client = Client()
    sources = [
        {
            "source_id": "src_1",
            "entries": [
                {"uri": "viking://resources/s/a.txt", "is_dir": False, "size": 1},
                {"uri": "viking://resources/s/b.txt", "is_dir": False, "size": 1},
            ],
        }
    ]

    with pytest.raises(CompileFailure) as raised:
        await service._materialize_sources(
            client=client, sources=sources, sandbox=SimpleNamespace()
        )

    assert raised.value.code == "RESOURCE_EXHAUSTED"
    assert client.downloads == []


@pytest.mark.asyncio
async def test_materialize_sources_enforces_actual_total_size():
    class Client:
        async def download_bytes(self, uri):
            del uri
            return b"xx"

    service = object.__new__(BotCompileService)
    service.limits = CompileLimits(source_total_bytes=1)
    sources = [
        {
            "source_id": "src_1",
            "entries": [{"uri": "viking://resources/s/a.txt", "is_dir": False, "size": 0}],
        }
    ]

    with pytest.raises(CompileFailure) as raised:
        await service._materialize_sources(
            client=Client(), sources=sources, sandbox=SimpleNamespace()
        )

    assert raised.value.code == "RESOURCE_EXHAUSTED"


@pytest.mark.asyncio
async def test_materialize_sources_bounds_downloaded_payloads(monkeypatch):
    state = {"pending": 0, "peak": 0}
    writes = {}

    class Client:
        async def download_bytes(self, uri):
            del uri
            state["pending"] += 1
            state["peak"] = max(state["peak"], state["pending"])
            return b"x"

    class Sandbox:
        async def write_file(self, path, content):
            if path.endswith(".txt"):
                writes[path] = content
                await asyncio.sleep(0)
                state["pending"] -= 1

    monkeypatch.setattr("vikingbot.compile.service._MATERIALIZE_CONCURRENCY", 2)
    service = object.__new__(BotCompileService)
    service.limits = CompileLimits()
    sources = [
        {
            "source_id": "src_1",
            "entries": [
                {
                    "uri": f"viking://resources/s/{index}.txt",
                    "is_dir": False,
                    "size": 1,
                }
                for index in range(5)
            ],
        }
    ]

    await service._materialize_sources(client=Client(), sources=sources, sandbox=Sandbox())

    assert writes == {f"compile_resources/src_1/s/{index}.txt": "x" for index in range(5)}
    assert 1 <= state["peak"] <= 2
    assert state["pending"] == 0


@pytest.mark.asyncio
async def test_materialize_target_checkout_preserves_paths_and_bytes():
    service = object.__new__(BotCompileService)
    service.limits = CompileLimits()
    payloads = {
        "viking://resources/output/entities/callie.md": b"---\ntype: entity\n---\nCallie",
        "viking://resources/output/relations.jsonl": b'{"from":"callie"}\n',
        "viking://resources/output/data.bin": b"\xff\x00",
    }

    class Client:
        async def download_bytes(self, uri):
            return payloads[uri]

    class Sandbox:
        def __init__(self):
            self.writes = {}

        async def write_file_bytes(self, path, content):
            self.writes[path] = content

    inventory = {uri: {"uri": uri, "size": len(payload)} for uri, payload in payloads.items()}
    sandbox = Sandbox()

    warnings = await service._materialize_target_checkout(
        client=Client(),
        target_uri="viking://resources/output",
        inventory=inventory,
        sandbox=sandbox,
    )

    assert warnings == []
    assert sandbox.writes == {
        "__compile_staging__/target_checkout/entities/callie.md": payloads[
            "viking://resources/output/entities/callie.md"
        ],
        "__compile_staging__/target_checkout/relations.jsonl": payloads[
            "viking://resources/output/relations.jsonl"
        ],
        "__compile_staging__/target_checkout/data.bin": payloads[
            "viking://resources/output/data.bin"
        ],
    }


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
        f"__compile_staging__/target_checkout/{path}": payload
        for path, payload in relative_files.items()
    }

    class Sandbox:
        async def list_files(self, path, *, max_entries):
            assert path == "__compile_staging__/target_checkout"
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
    tool = SubmitTargetCheckoutTool(
        target_uri="viking://resources/wiki",
        source_roots={"src_1": "viking://resources/source"},
        limits=CompileLimits(),
    )

    accepted = await tool.execute(context)

    assert accepted == (
        "Target checkout accepted with 4 changed file(s) and "
        "2 Wiki page(s) in the preserved final tree."
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
    workspace_path = "__compile_staging__/target_checkout/entity/existing.md"

    class Sandbox:
        async def list_files(self, path, *, max_entries):
            del max_entries
            assert path == "__compile_staging__/target_checkout"
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
    tool = SubmitTargetCheckoutTool(
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

    assert accepted.startswith("Target checkout accepted with 1 changed file(s)")
    assert tool.bundle is not None
    assert tool.bundle.operations[0]["uri"] == page_uri
    assert tool.bundle.operations[0]["mode"] == "upsert"
    assert omitted_uri not in {operation["uri"] for operation in tool.bundle.operations}
    assert tool.page_count == 1
    assert tool.file_count == 1


@pytest.mark.asyncio
async def test_submit_tool_checkout_rejects_incomplete_wiki_frontmatter():
    workspace_path = "__compile_staging__/target_checkout/entity/existing.md"

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

    tool = SubmitTargetCheckoutTool(
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
        budget_lock=asyncio.Lock(),
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
    class ResultTool(_EchoTool):
        async def execute(self, tool_context, **kwargs):
            return kwargs["result"]

    limits = CompileLimits(tool_result_bytes=8, tool_total_result_bytes=12)
    budget = {"bytes": 0}
    wrapped = CompileScopedTool(
        ResultTool(),
        roots=("viking://resources/source",),
        limits=limits,
        result_budget=budget,
        budget_lock=asyncio.Lock(),
    )
    context = ToolContext()
    kwargs = {"uri": "viking://resources/source/child"}
    oversized = await wrapped.execute(context, **kwargs, result="x" * 9)
    assert "per-call size limit" in oversized
    assert budget["bytes"] == 0
    assert await wrapped.execute(context, **kwargs, result="x" * 8) == "x" * 8
    assert await wrapped.execute(context, **kwargs, result="y" * 4) == "y" * 4
    assert budget["bytes"] == 12
    exhausted = await wrapped.execute(context, **kwargs, result="z")
    assert "task tool-result budget exceeded" in exhausted
    assert budget["bytes"] == 12


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


@pytest.mark.asyncio
async def test_readlist_tracker_records_and_summarizes_without_duplicates():
    class Sandbox:
        def __init__(self):
            self.files = {}

        async def list_files(self, max_entries=None):
            del max_entries
            return [
                SandboxFileInfo(path="compile_resources/src_1/a.md", size=1),
                SandboxFileInfo(path="compile_resources/src_1/b.jsonl", size=1),
                SandboxFileInfo(path="compile_resources/_manifest.tsv", size=1),
                SandboxFileInfo(path="skills/wiki/SKILL.md", size=1),
            ]

        async def read_file(self, path):
            return self.files.get(path, "")

        async def write_file(self, path, content):
            self.files[path] = content

    sandbox = Sandbox()
    tracker = ReadlistTracker(sandbox=sandbox)
    await tracker.initialize()

    assert tracker.universe == {
        "compile_resources/src_1/a.md",
        "compile_resources/src_1/b.jsonl",
    }
    assert await tracker.summary() == "源文件共 2 个，尚未读取任何源文件；优先去读未读文件。"

    await tracker.record(["compile_resources/src_1/a.md"])
    await tracker.record(["compile_resources/src_1/a.md"])  # duplicate is a no-op
    assert tracker.read_paths == {"compile_resources/src_1/a.md"}

    summary = await tracker.summary()
    assert "已读 1/2 个源文件" in summary
    assert "未读 1 个" in summary
    assert "compile_resources/src_1/a.md" in summary
    assert "compile_resources/src_1/b.jsonl" in summary
    assert "不必再读" in summary
    # Persisted to the sandbox readlist so it survives context compaction.
    assert "compile_resources/src_1/a.md" in sandbox.files[READLIST_PATH]


@pytest.mark.asyncio
async def test_readlist_tracker_reloads_externally_appended_paths():
    class Sandbox:
        def __init__(self):
            self.files = {
                READLIST_PATH: "compile_resources/src_1/a.md\n",
            }

        async def list_files(self, max_entries=None):
            del max_entries
            return [SandboxFileInfo(path="compile_resources/src_1/a.md", size=1)]

        async def read_file(self, path):
            return self.files.get(path, "")

        async def write_file(self, path, content):
            self.files[path] = content

    tracker = ReadlistTracker(sandbox=Sandbox())
    await tracker.initialize()
    summary = await tracker.summary()
    assert "已读 1/1 个源文件" in summary
    assert "不必再读" in summary


class _ReadTrackingFake(Tool):
    def __init__(self, name, *, result="ok"):
        self._name = name
        self._result = result

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return "fake"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}}

    async def execute(self, tool_context, **kwargs):
        del tool_context, kwargs
        return self._result


@pytest.mark.asyncio
async def test_read_tracking_tool_records_read_file_edit_and_explicit_exec():
    class Sandbox:
        def __init__(self):
            self.files = {}

        async def list_files(self, max_entries=None):
            del max_entries
            return [
                SandboxFileInfo(path="compile_resources/src_1/a.md", size=1),
                SandboxFileInfo(path="compile_resources/src_1/b.jsonl", size=1),
                SandboxFileInfo(path="compile_resources/src_1/c.md", size=1),
            ]

        async def read_file(self, path):
            return self.files.get(path, "")

        async def write_file(self, path, content):
            self.files[path] = content

    tracker = ReadlistTracker(sandbox=Sandbox())
    await tracker.initialize()

    read_tool = ReadTrackingTool(_ReadTrackingFake("read_file"), tracker=tracker)
    await read_tool.execute(ToolContext(), path="compile_resources/src_1/a.md")
    edit_tool = ReadTrackingTool(_ReadTrackingFake("edit_file"), tracker=tracker)
    await edit_tool.execute(
        ToolContext(), path="compile_resources/src_1/c.md", old_text="x", new_text="y"
    )
    exec_tool = ReadTrackingTool(_ReadTrackingFake("exec"), tracker=tracker)
    await exec_tool.execute(
        ToolContext(), command="python3 scan.py compile_resources/src_1/b.jsonl | head"
    )

    assert tracker.read_paths == {
        "compile_resources/src_1/a.md",
        "compile_resources/src_1/c.md",
        "compile_resources/src_1/b.jsonl",
    }

    # A failed read is not recorded; a directory token in exec is ignored.
    failing = ReadTrackingTool(
        _ReadTrackingFake("read_file", result="Error: missing"), tracker=tracker
    )
    await failing.execute(ToolContext(), path="compile_resources/src_1/a.md")
    await exec_tool.execute(ToolContext(), command="find compile_resources/src_1 -name '*.md'")
    assert tracker.read_paths == {
        "compile_resources/src_1/a.md",
        "compile_resources/src_1/c.md",
        "compile_resources/src_1/b.jsonl",
    }


@pytest.mark.asyncio
async def test_request_normalization_uses_default_instruction_and_canonical_skill(monkeypatch):
    class Client:
        created = set()
        skill_content = "---\nname: wiki\ndescription: Wiki\n---\nCompile it"

        async def attrs(self, uri):
            if uri == "viking://resources/wiki" and uri not in self.created:
                raise OpenVikingError("missing", code="NOT_FOUND")
            return {"uri": uri.rstrip("/")}

        async def stat(self, uri):
            return {"uri": uri, "isDir": True}

        async def mkdir(self, uri):
            self.created.add(uri)

        async def get_skill(self, name, *, target_uri, include_integrity=False):
            assert name == "wiki"
            assert target_uri == "viking://agent/skills"
            assert include_integrity is False
            return {
                "root_uri": "viking://agent/skills/wiki",
                "content": self.skill_content,
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
                "instruction": "   ",
            }
        ),
        connection={"api_key": "secret"},
    )
    assert normalized.from_ == ["viking://resources/source"]
    assert normalized.to == "viking://resources/wiki"
    assert normalized.skill == "viking://agent/skills/wiki"
    assert normalized.instruction == DEFAULT_COMPILE_INSTRUCTION
    assert normalized.instruction_provided is False

    Client.created.clear()
    Client.skill_content = "---\nname: wiki\n---\nCompile it"
    with pytest.raises(CompileFailure) as raised:
        await service._normalize_request(
            CompileRequest.model_validate(
                {
                    "from": ["viking://resources/source"],
                    "to": "viking://resources/wiki",
                    "skill": "viking://agent/skills/wiki",
                }
            ),
            connection={"api_key": "secret"},
        )
    assert raised.value.code == "SKILL_INVALID"
    assert Client.created == set()


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
                mode=None, model_copy=lambda *, deep: SimpleNamespace(mode=None)
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
        roots,
        target_uri,
        source_ids,
        catalog_uris,
        file_catalog_uris,
        workspace_baseline,
        wiki_uri_resolver,
        target_checkout_enabled,
        source_roots,
        capabilities,
        materialized=False,
        source_fallback=False,
        readlist=None,
    ):
        del request_loop, roots, source_ids, materialized, source_fallback, readlist
        assert capabilities == CompileCapabilities(exec_enabled=False)
        assert catalog_uris == set()
        assert file_catalog_uris == set()
        assert workspace_baseline == set()
        assert callable(wiki_uri_resolver)
        assert target_checkout_enabled is False
        assert source_roots == {}
        registry = ToolRegistry()
        registry.register(
            SubmitWikiBundleTool(
                source_ids=set(),
                catalog_uris=set(),
                file_catalog_uris=set(),
                target_uri=target_uri,
                limits=CompileLimits(),
                exec_enabled=capabilities.exec_enabled,
            )
        )
        return registry, set()

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
            "instruction": "Create a weekly report Skill",
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
    monkeypatch.setattr(service, "_build_sources", build_sources)
    monkeypatch.setattr(service, "_build_compile_registry", build_registry)

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

        async def stat(self, uri):
            return {"isDir": True}

        async def list_resources(self, *, path, recursive, node_limit):
            assert path == "viking://resources/source"
            assert recursive is True
            assert node_limit == 5000
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
    sources = await service._build_sources(Client(), ["viking://resources/source"])

    assert sources == [
        {
            "source_id": "src_1",
            "directory_uri": "viking://resources/source",
            "overview": "Source overview",
            "file_count": 1,
            "total_bytes": 0,
            "entries": [
                {
                    "name": "guide.md",
                    "title": "Readable Guide",
                    "uri": "viking://resources/source/docs/guide.md",
                    "is_dir": False,
                    "size": 0,
                    "summary": "A" * 500,
                },
                {
                    "name": "docs",
                    "title": "docs",
                    "uri": "viking://resources/source/docs",
                    "is_dir": True,
                    "size": 0,
                    "summary": "Documentation",
                },
            ],
            "catalog_truncated": False,
        }
    ]


@pytest.mark.asyncio
async def test_source_file_synthesizes_single_entry_without_listing():
    class Client:
        def __init__(self):
            self.client = self
            self.listed = False

        async def overview(self, uri):
            return "Parent overview"

        async def stat(self, uri):
            return {"name": "2024.md", "size": 512, "isDir": False}

        async def list_resources(self, *, path, recursive, node_limit):
            self.listed = True
            raise AssertionError("file sources must not be listed")

    client = Client()
    service = object.__new__(BotCompileService)
    service.limits = CompileLimits()
    sources = await service._build_sources(client, ["viking://resources/weekly/2024.md"])

    assert client.listed is False
    assert sources == [
        {
            "source_id": "src_1",
            "directory_uri": "viking://resources/weekly/2024.md",
            "overview": "Parent overview",
            "file_count": 1,
            "total_bytes": 512,
            "entries": [
                {
                    "name": "2024.md",
                    "title": "2024",
                    "uri": "viking://resources/weekly/2024.md",
                    "is_dir": False,
                    "size": 512,
                    "summary": "",
                },
            ],
            "catalog_truncated": False,
        }
    ]


@pytest.mark.asyncio
async def test_target_catalog_includes_raw_files_and_marks_wiki_pages():
    class Client:
        def __init__(self):
            self.reads = []
            self.find_call = None

        async def tree(self, uri, *, node_limit):
            assert uri == "viking://resources/ara"
            assert node_limit == CompileLimits().target_inventory_entries + 1
            return [
                {"uri": f"{uri}/Overview.md", "isDir": False, "abstract": "Overview"},
                {"uri": f"{uri}/PAPER.md", "isDir": False},
                {"uri": f"{uri}/broken.md", "isDir": False},
                {"uri": f"{uri}/Long.md", "isDir": False, "size": 2048},
                {"uri": f"{uri}/trace/tree.yaml", "isDir": False},
                {"uri": f"{uri}/figures/chart.png", "isDir": False},
                {"uri": f"{uri}/.overview.md", "isDir": False},
            ]

        async def find(self, query, **kwargs):
            self.find_call = (query, kwargs)
            return {
                "resources": [
                    {
                        "uri": "viking://resources/ara/Overview.md",
                        "abstract": "Relevant overview",
                    },
                    {"uri": "viking://resources/ara/PAPER.md"},
                    {"uri": "viking://resources/ara/Long.md"},
                    {"uri": "viking://resources/ara/trace/tree.yaml"},
                    {"uri": "viking://resources/outside.md"},
                ]
            }

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
    catalog, inventory = await service._build_catalog(
        client,
        "viking://resources/ara",
        query="compile ResNet\nsource overview",
    )

    assert catalog == [
        {
            "uri": "viking://resources/ara/Overview.md",
            "kind": "wiki_page",
            "title": "Overview",
            "type": "overview",
            "summary": "Relevant overview",
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
    ]
    assert set(inventory) == {
        "viking://resources/ara/Overview.md",
        "viking://resources/ara/PAPER.md",
        "viking://resources/ara/broken.md",
        "viking://resources/ara/Long.md",
        "viking://resources/ara/trace/tree.yaml",
        "viking://resources/ara/figures/chart.png",
    }
    assert client.find_call == (
        "compile ResNet\nsource overview",
        {
            "target_uri": "viking://resources/ara",
            "context_type": "resource",
            "limit": CompileLimits().target_catalog_pages,
        },
    )
    assert client.reads.count(("viking://resources/ara/Long.md", 128)) == 1
    assert client.reads.count(("viking://resources/ara/Long.md", -1)) == 1
    assert {uri for uri, _ in client.reads} == {
        "viking://resources/ara/Overview.md",
        "viking://resources/ara/PAPER.md",
        "viking://resources/ara/Long.md",
    }


@pytest.mark.asyncio
async def test_target_catalog_search_failure_keeps_collision_inventory():
    class Client:
        async def tree(self, uri, *, node_limit):
            return [{"uri": f"{uri}/existing.md", "isDir": False, "size": 10}]

        async def find(self, query, **kwargs):
            raise RuntimeError("index unavailable")

        async def read_raw(self, uri, *, offset=0, limit=-1):
            raise AssertionError(f"unexpected content read: {uri}")

    service = object.__new__(BotCompileService)
    service.limits = CompileLimits()
    catalog, inventory = await service._build_catalog(
        Client(),
        "viking://resources/wiki",
        query="relevant",
    )

    assert catalog == []
    assert set(inventory) == {"viking://resources/wiki/existing.md"}


@pytest.mark.asyncio
async def test_memory_target_catalog_uses_memory_search_results():
    target = "viking://user/alice/memories/preferences/wiki"
    existing = f"{target}/topic.md"

    class Client:
        async def tree(self, uri, *, node_limit):
            assert uri == target
            return [{"uri": existing, "isDir": False, "abstract": "Existing topic"}]

        async def find(self, query, **kwargs):
            assert query == "topic"
            assert kwargs == {
                "target_uri": target,
                "context_type": "memory",
                "limit": CompileLimits().target_catalog_pages,
            }
            return {"memories": [{"uri": existing, "abstract": "Relevant topic"}]}

        async def read_raw(self, uri, *, offset=0, limit=-1):
            assert uri == existing
            return "---\ntype: concept\n---\n\n# Topic"

    service = object.__new__(BotCompileService)
    service.limits = CompileLimits()
    catalog, inventory = await service._build_catalog(
        Client(),
        target,
        query="topic",
    )

    assert set(inventory) == {existing}
    assert catalog == [
        {
            "uri": existing,
            "kind": "wiki_page",
            "title": "topic",
            "type": "concept",
            "summary": "Relevant topic",
            "page_id": 1,
        }
    ]


class _NamedTool(_EchoTool):
    def __init__(self, name):
        self._name = name

    @property
    def name(self):
        return self._name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("uri", "expected_path"),
    [
        ("skills/wiki/references/guide.md", "skills/wiki/references/guide.md"),
        ("viking://skills/wiki/references/guide.md", "skills/wiki/references/guide.md"),
    ],
)
async def test_scoped_tool_redirects_skill_workspace_reads(uri, expected_path):
    wrapped = CompileScopedTool(
        _NamedTool("openviking_multi_read"),
        roots=("viking://resources/source",),
        limits=CompileLimits(),
        result_budget={"bytes": 0},
        budget_lock=asyncio.Lock(),
    )

    result = await wrapped.execute(ToolContext(), uris=[uri])

    assert "read_file" in result
    assert expected_path in result


@pytest.mark.parametrize("exec_enabled", [True, False])
def test_compile_registry_has_a_fixed_ara_compatible_tool_set(exec_enabled):
    available = ToolRegistry()
    for name in (
        "read_file",
        "write_file",
        "edit_file",
        "exec",
        "web_search",
        "message",
        "cron",
        "spawn",
        "openviking_list",
        "openviking_search",
        "openviking_grep",
        "openviking_glob",
        "openviking_multi_read",
        "openviking_add_resource",
        "openviking_memory_commit",
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

    registry, ov_names = service._build_compile_registry(
        **common,
        capabilities=CompileCapabilities(exec_enabled=exec_enabled),
    )
    expected_tools = {
        "read_file",
        "write_file",
        "edit_file",
        "openviking_list",
        "openviking_search",
        "openviking_grep",
        "openviking_glob",
        "openviking_multi_read",
        "submit_wiki_bundle",
    }
    if exec_enabled:
        expected_tools.add("exec")
    assert set(registry.tool_names) == expected_tools
    assert ov_names == {
        "openviking_list",
        "openviking_search",
        "openviking_grep",
        "openviking_glob",
        "openviking_multi_read",
    }
    assert all(isinstance(registry.get(name), CompileScopedTool) for name in ov_names)
    submit = registry.get("submit_wiki_bundle")
    assert submit.exec_enabled is exec_enabled

    checkout_registry, _ = service._build_compile_registry(
        **common,
        capabilities=CompileCapabilities(exec_enabled=exec_enabled),
        target_checkout_enabled=True,
        source_roots={"src_1": "viking://resources/source"},
    )
    checkout_submit = checkout_registry.get("submit_wiki_bundle")
    assert isinstance(checkout_submit, SubmitTargetCheckoutTool)


def test_compile_registry_keeps_source_fallback_tools_only_when_needed():
    available = ToolRegistry()
    for name in (
        "read_file",
        "openviking_export",
        "openviking_list",
        "openviking_glob",
        "openviking_multi_read",
    ):
        available.register(_NamedTool(name))
    request_loop = SimpleNamespace(tools=available, config=None)
    service = object.__new__(BotCompileService)
    service.limits = CompileLimits()
    common = {
        "request_loop": request_loop,
        "roots": ("viking://resources/source",),
        "target_uri": "viking://resources/wiki",
        "source_ids": {"src_1"},
        "catalog_uris": set(),
    }

    materialized_registry, materialized_ov = service._build_compile_registry(
        **common,
        capabilities=CompileCapabilities(exec_enabled=False),
        materialized=True,
    )
    assert "openviking_export" not in materialized_registry.tool_names
    assert not {
        "openviking_list",
        "openviking_glob",
        "openviking_multi_read",
    } & set(materialized_registry.tool_names)

    fallback_registry, fallback_ov = service._build_compile_registry(
        **common,
        capabilities=CompileCapabilities(exec_enabled=False),
        materialized=True,
        source_fallback=True,
    )
    assert "openviking_export" not in fallback_registry.tool_names
    assert {
        "openviking_list",
        "openviking_glob",
        "openviking_multi_read",
    } <= set(fallback_registry.tool_names)
    assert fallback_ov == {
        "openviking_list",
        "openviking_glob",
        "openviking_multi_read",
    }

    eager_registry, eager_ov = service._build_compile_registry(
        **common,
        capabilities=CompileCapabilities(exec_enabled=False),
    )
    assert "openviking_export" in eager_registry.tool_names
    assert "openviking_export" in eager_ov


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
            "instruction": "Compile",
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
async def test_compile_without_exec_rejects_cli_skill_before_sandbox_access(
    tmp_path: Path,
):
    service = object.__new__(BotCompileService)

    class SandboxManager:
        async def get_sandbox(self, session_key):
            raise AssertionError(f"sandbox must not be accessed: {session_key}")

    with pytest.raises(CompileFailure) as raised:
        await service._check_requirements(
            {"requires": {"bins": ["python3"], "env": ["API_TOKEN"]}},
            capabilities=CompileCapabilities(exec_enabled=False),
            sandbox_manager=SandboxManager(),
            session_key=SessionKey(type="compile", channel_id="task", chat_id="task"),
            workspace=tmp_path,
            skill_name="cli-skill",
        )

    assert raised.value.code == "SKILL_CAPABILITY_UNAVAILABLE"
    assert "bin:python3" in str(raised.value)
    assert "env:API_TOKEN" in str(raised.value)
    assert "allow_compile_exec=true" in str(raised.value)


@pytest.mark.asyncio
async def test_compile_without_exec_syncs_ordinary_skill_without_running_commands(
    tmp_path: Path,
):
    service = object.__new__(BotCompileService)
    skill_dir = tmp_path / "skills" / "wiki"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Write Wiki pages.", encoding="utf-8")

    class Sandbox:
        def __init__(self):
            self.files = {}

        async def write_file(self, path, content):
            self.files[path] = content

        async def execute(self, command):
            raise AssertionError(f"command must not run: {command}")

    sandbox = Sandbox()

    class SandboxManager:
        async def get_sandbox(self, session_key):
            assert session_key.type == "compile"
            return sandbox

    await service._check_requirements(
        {},
        capabilities=CompileCapabilities(exec_enabled=False),
        sandbox_manager=SandboxManager(),
        session_key=SessionKey(type="compile", channel_id="task", chat_id="task"),
        workspace=tmp_path,
        skill_name="wiki",
    )

    assert sandbox.files == {"skills/wiki/SKILL.md": "Write Wiki pages."}


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
        "hash#name.txt": b"literal hash",
        "bad?name.txt": b"unsafe URI",
        "__compile_staging__/work/notes.txt": b"notes",
        "__compile_staging__/tmp/check.txt": b"check",
        READLIST_PATH: b"compile_resources/src_1/a.md\n",
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
    assert READLIST_PATH not in payloads
    assert "tmp_out/scratch.txt" not in payloads
    assert "TmpCache/case.txt" not in payloads
    assert "sandboxes/cmp-srt-settings.json" not in payloads
    assert "sandboxes/cmp-srt-settings.json" not in {path for path, _limit in sandbox.reads}
    assert sum(path.casefold() == "foo.md" for path in payloads) == 1
    assert payloads["hash#name.txt"] == b"literal hash"
    assert "bad?name.txt" not in payloads
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
                mode=None, model_copy=lambda *, deep: SimpleNamespace(mode=None)
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

    class FakeSkillsLoader:
        def __init__(self, workspace, *, builtin_skills_dir):
            del workspace, builtin_skills_dir

        def load_skills_for_context(self, names):
            assert names == ["wiki"]
            return "Write Wiki pages."

        def _get_skill_meta(self, name):
            assert name == "wiki"
            return {}

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

    async def build_sources(*args, **kwargs):
        del args, kwargs
        return []

    async def build_catalog(*args, **kwargs):
        del args, kwargs
        return [], {}

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
    monkeypatch.setattr("vikingbot.compile.service.SkillsLoader", FakeSkillsLoader)
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
    monkeypatch.setattr(service, "_materialize_skill", no_op)
    monkeypatch.setattr(service, "_check_requirements", no_op)
    monkeypatch.setattr(service, "_build_sources", build_sources)
    monkeypatch.setattr(service, "_build_catalog", build_catalog)
    submit_tool = SimpleNamespace(file_payloads=[], page_count=1, file_count=1)
    registry = SimpleNamespace(get=lambda name: submit_tool)
    monkeypatch.setattr(
        service, "_build_compile_registry", lambda *args, **kwargs: (registry, set())
    )
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
                "instruction": "Compile",
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
                "instruction": "Compile",
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
    service._start_lock = asyncio.Lock()
    assert await service.get_task("cmp_owner", principal_scope="other") is None
    assert (await service.get_task("cmp_owner", principal_scope="owner"))["task_id"] == "cmp_owner"


@pytest.mark.asyncio
async def test_compile_syncs_skill_package_files(tmp_path: Path):
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
