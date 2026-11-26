# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.models.vlm.base import ToolCall
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.session.memory.utils.uri import generate_uri
from openviking_cli.session.user_id import UserIdentifier

_ROOT = "viking://user/alice/memories"
_ENTITIES = f"{_ROOT}/entities"
_PREFERENCES = f"{_ROOT}/preferences"


def _entry(root, name, *, directory=True, access="owner"):
    return {"name": name, "uri": f"{root}/{name}", "isDir": directory, "access": access}


class _ListingFS:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    async def ls(self, uri, *, node_limit, offset, ctx, **kwargs):
        assert ctx.user.user_id == "alice"
        assert kwargs["include_abstract"] is False
        assert kwargs["include_overview"] is False
        self.calls.append((uri, offset))
        return self.entries.get(uri, [])[offset : offset + node_limit]


def _provider(fs, *, enabled=True, peer_memory_enabled=False):
    ctx = RequestContext(user=UserIdentifier("account", "alice"), role=Role.USER)
    registry = MemoryTypeRegistry(load_schemas=False)
    for memory_type, owner, leaf in (
        ("entities", "category", "name"),
        ("preferences", "user", "topic"),
    ):
        registry.register(
            MemoryTypeSchema(
                memory_type=memory_type,
                directory="viking://user/{{ user_space }}/memories/" + memory_type,
                filename_template=f"{{{{ {owner}|lower }}}}/{{{{ {leaf}|lower }}}}.md",
            )
        )
    provider = SessionExtractContextProvider(
        messages=[],
        ctx=ctx,
        viking_fs=fs,
        memory_registry=registry,
        output_language_override="en",
        directory_maintenance_enabled=enabled,
    )
    provider._isolation_handler = MemoryIsolationHandler(
        ctx,
        provider.get_extract_context(),
        allowed_peer_ids={"bob"},
        peer_memory_enabled=peer_memory_enabled,
    )
    provider.search_files = AsyncMock(return_value=[])
    return provider


async def _call(provider, name, **arguments):
    return await provider.execute_tool(ToolCall(id="1", name=name, arguments=arguments))


@pytest.mark.asyncio
async def test_prefetch_inventory_is_paged_scoped_and_excludes_denied_and_hidden():
    existing = [_entry(_ENTITIES, f"category_{index}") for index in range(128)]
    existing.extend(
        [
            _entry(_ENTITIES, "技术排障"),
            _entry(_ENTITIES, "private", access="denied"),
            _entry(_ENTITIES, ".hidden"),
            _entry(_ENTITIES, "card.md", directory=False),
        ]
    )
    fs = _ListingFS({_ENTITIES: existing, _PREFERENCES: [_entry(_PREFERENCES, "alice")]})
    provider = _provider(fs)

    messages = await provider.prefetch()

    [inventory_message] = [
        item["content"]
        for item in messages
        if item["content"].startswith("## Prefetched Memory Directory Inventory")
    ]
    inventory = json.loads(inventory_message.split("\n", 1)[1])
    assert set(inventory) == {_ENTITIES, _PREFERENCES}
    assert inventory[_ENTITIES]["directories"] == [entry["name"] for entry in existing[:129]]
    assert fs.calls == [(_ENTITIES, 0), (_ENTITIES, 128), (_PREFERENCES, 0)]
    assert "even when its language differs" in provider.instruction()
    assert set(provider.get_tools()) == {"ls", "merge_dirs"}

    await _call(provider, "ls", uri=_ENTITIES)
    assert fs.calls == [(_ENTITIES, 0), (_ENTITIES, 128), (_PREFERENCES, 0)]


@pytest.mark.asyncio
async def test_native_tools_propose_only_visible_authorized_sibling_directories():
    source = f"{_ENTITIES}/runbooks"
    target = f"{_ENTITIES}/runbook"
    fs = _ListingFS(
        {
            _ENTITIES: [
                _entry(_ENTITIES, "runbooks"),
                _entry(_ENTITIES, "runbook"),
                _entry(_ENTITIES, "private", access="denied"),
            ],
            source: [_entry(source, "server.md", directory=False)],
            _PREFERENCES: [
                _entry(_PREFERENCES, " alice"),
                _entry(_PREFERENCES, "Alice"),
                _entry(_PREFERENCES, "alice"),
                _entry(_PREFERENCES, "bob"),
            ],
        }
    )
    provider = _provider(fs)
    await provider.prefetch()

    assert (await _call(provider, "ls", uri=source))["entries"] == [
        {"name": "server.md", "uri": f"{source}/server.md", "isDir": False}
    ]
    for invalid in (f"{_ENTITIES}/private", f"{source}/nested", _ROOT):
        assert "error" in await _call(provider, "ls", uri=invalid)
    assert "error" in await _call(provider, "ls", uri=source, recursive=True)

    accepted = await _call(provider, "merge_dirs", source_uri=source, target_uri=target)
    assert accepted["status"] == "proposed"
    assert accepted["canonical_directory"] == "runbook"
    assert [entry["uri"] for entry in accepted["source_listing"]["entries"]] == [
        f"{source}/server.md"
    ]
    assert await _call(provider, "merge_dirs", source_uri=source, target_uri=target) == accepted
    assert "error" in await _call(provider, "merge_dirs", source_uri=target, target_uri=source)
    assert "error" in await _call(
        provider, "merge_dirs", source_uri=f"{_ENTITIES}/private", target_uri=target
    )
    assert "error" in await _call(
        provider, "merge_dirs", source_uri=source, target_uri=f"{_PREFERENCES}/alice"
    )
    assert "error" in await _call(
        provider,
        "merge_dirs",
        source_uri=f"{_PREFERENCES}/bob",
        target_uri=f"{_PREFERENCES}/alice",
    )
    assert "error" in await _call(
        provider,
        "merge_dirs",
        source_uri=f"{_PREFERENCES}/Alice",
        target_uri=f"{_PREFERENCES}/alice",
    )
    preference_schema = provider._get_registry().get("preferences")
    legacy_file = generate_uri(
        preference_schema, {"user": " Alice", "topic": "editor"}, user_space="alice"
    )
    assert legacy_file == f"{_PREFERENCES}/ alice/editor.md"
    preference_proposal = await _call(
        provider,
        "merge_dirs",
        source_uri=legacy_file.rsplit("/", 1)[0],
        target_uri=f"{_PREFERENCES}/alice",
    )
    assert preference_proposal["status"] == "proposed"
    assert [(plan.memory_type, plan.target_uri) for plan in provider.directory_merge_plans] == [
        ("entities", target),
        ("preferences", f"{_PREFERENCES}/alice"),
    ]


@pytest.mark.asyncio
async def test_directory_tools_are_opt_in(monkeypatch):
    from openviking.session.memory import session_extract_context_provider as module

    monkeypatch.setattr(
        module,
        "get_openviking_config",
        lambda: SimpleNamespace(
            memory=SimpleNamespace(eager_prefetch=False, prefetch_search_topn=5, link_enabled=False)
        ),
    )
    fs = _ListingFS({_ENTITIES: [_entry(_ENTITIES, "runbook")]})
    provider = _provider(fs, enabled=False)

    assert provider.get_tools() == ["read"]
    assert "Existing Memory Directories" not in provider.instruction()
    await provider.prefetch()
    assert "Unknown tool" in (await _call(provider, "ls", uri=_ENTITIES))["error"]
    assert fs.calls == []
    proposal = await _call(
        provider,
        "merge_dirs",
        source_uri=f"{_ENTITIES}/runbooks",
        target_uri=f"{_ENTITIES}/runbook",
    )
    assert "Unknown tool" in proposal["error"]
