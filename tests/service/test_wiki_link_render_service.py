# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.wiki_link_render_service import WikiLinkRenderService
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, store):
        self.store = dict(store)
        self.writes = []

    async def glob(self, pattern, uri="viking://", node_limit=None, ctx=None):
        del pattern, node_limit, ctx
        prefix = uri.rstrip("/") + "/"
        matches = [key for key in self.store if key.startswith(prefix) and key.endswith(".md")]
        return {"matches": matches, "count": len(matches)}

    async def read_file(self, uri, ctx=None):
        del ctx
        return self.store[uri]

    async def write_file(self, uri, content, ctx=None):
        del ctx
        self.store[uri] = content
        self.writes.append(uri)


@pytest.fixture
def request_context():
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)


def _entity(uri: str, name: str, heading: str | None = None) -> str:
    return MemoryFileUtils.write(
        MemoryFile(
            uri=uri,
            content=f"# {heading or name}\nEntity facts.",
            memory_type="entities",
            extra_fields={"name": name},
        )
    )


@pytest.mark.asyncio
async def test_renders_current_resource_and_all_ancestor_sidecars(request_context):
    wiki_pages_root = "viking://user/alice/memories/entities"
    organization_uri = f"{wiki_pages_root}/组织/天穹财团.md"
    project_uri = f"{wiki_pages_root}/项目/星尘计划.md"
    resource_uri = "viking://resources/宇宙/星尘计划"
    resource_markdown_uri = f"{resource_uri}/星尘计划.md"
    overview_uri = f"{resource_uri}/.overview.md"
    ancestor_overview_uri = "viking://resources/宇宙/.overview.md"
    root_abstract_uri = "viking://resources/.abstract.md"
    old_other_user_link = "[旧实体](viking://user/bob/memories/entities/组织/旧实体.md)"
    store = {
        organization_uri: _entity(organization_uri, "天穹财团"),
        project_uri: _entity(project_uri, "star_dust", heading="星尘计划"),
        resource_markdown_uri: "# 原始档案\n天穹财团推进星尘计划。",
        overview_uri: (
            "# 星尘计划\n"
            f"**[天穹财团]({organization_uri})**发起星尘计划，天穹财团继续负责。\n"
            f"保留[外部链接](https://example.com)，清理{old_other_user_link}。\n"
            "`天穹财团`\n"
            "```text\n天穹财团\n```\n\n"
            "<!-- MEMORY_FIELDS\n"
            '{"links": [{"to_uri": "viking://user/alice/memories/entities/旧.md"}]}\n'
            "-->"
        ),
        f"{resource_uri}/.abstract.md": (
            '天穹财团与星尘计划。\n\n<!-- MEMORY_FIELDS\n{"version": 1}\n-->'
        ),
        ancestor_overview_uri: "# 宇宙\n天穹财团与星尘计划。",
        root_abstract_uri: "收录天穹财团。",
    }
    fs = _FakeVikingFS(store)
    renderer = WikiLinkRenderService(fs)

    result = await renderer.render(
        ctx=request_context,
        resource_uri=resource_uri,
        wiki_pages_root=wiki_pages_root,
    )

    overview = fs.store[overview_uri]
    assert "<!-- MEMORY_FIELDS" not in overview
    assert "[外部链接](https://example.com)" in overview
    assert "[旧实体]" not in overview
    assert "清理旧实体" in overview
    assert overview.count("](../../../user/alice/memories/entities/组织/天穹财团.md)") == 1
    assert overview.count("](../../../user/alice/memories/entities/项目/星尘计划.md)") == 1
    assert "`天穹财团`" in overview
    assert "```text\n天穹财团\n```" in overview
    assert "<!-- MEMORY_FIELDS" not in fs.store[f"{resource_uri}/.abstract.md"]
    assert (
        "[天穹财团](../../../user/alice/memories/entities/组织/天穹财团.md)"
        in fs.store[resource_markdown_uri]
    )
    assert (
        "[星尘计划](../../../user/alice/memories/entities/项目/星尘计划.md)"
        in fs.store[resource_markdown_uri]
    )
    assert (
        "](../../user/alice/memories/entities/组织/天穹财团.md)" in fs.store[ancestor_overview_uri]
    )
    assert "](../user/alice/memories/entities/组织/天穹财团.md)" in fs.store[root_abstract_uri]
    assert result == {
        "wiki_pages_scanned": 2,
        "files_seen": 5,
        "files_updated": 5,
        "links_created": 9,
    }

    first_render = dict(fs.store)
    second = await renderer.render(
        ctx=request_context,
        resource_uri=resource_uri,
        wiki_pages_root=wiki_pages_root,
    )
    assert fs.store == first_render
    assert second["files_updated"] == 0


@pytest.mark.asyncio
async def test_skips_ambiguous_aliases_and_missing_sidecars(request_context):
    wiki_pages_root = "viking://user/alice/memories/entities"
    first_uri = f"{wiki_pages_root}/组织/共同名称.md"
    second_uri = f"{wiki_pages_root}/项目/另一个.md"
    overview_uri = "viking://user/alice/resources/docs/.overview.md"
    fs = _FakeVikingFS(
        {
            first_uri: _entity(first_uri, "共同名称"),
            second_uri: _entity(second_uri, "共同名称", heading="另一个"),
            overview_uri: "# Docs\n共同名称和另一个。",
        }
    )

    result = await WikiLinkRenderService(fs).render(
        ctx=request_context,
        resource_uri="viking://user/alice/resources/docs",
        wiki_pages_root=wiki_pages_root,
    )

    assert "[共同名称]" not in fs.store[overview_uri]
    assert "[另一个](../../memories/entities/项目/另一个.md)" in fs.store[overview_uri]
    assert result["files_seen"] == 1
    assert result["links_created"] == 1
