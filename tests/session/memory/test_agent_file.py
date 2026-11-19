import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.extraction_output_protocol import (
    ExtractionOutputContext,
    create_extraction_output_protocol,
)
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.schema_model_generator import SchemaModelGenerator
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.mark.asyncio
async def test_extraction_applies_memory_changes_and_preserves_agent_files(tmp_path):
    fs = VikingFS(MockLocalAGFS(root_path=tmp_path))
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    root = "viking://user/alice/memories"
    profile_uri = f"{root}/profile.md"
    protected = {f"{root}/{name}.md": f"# Custom {name}" for name in ("soul", "identity")}
    for uri, content in protected.items():
        await fs.write_file(uri, content, ctx=ctx)
    await fs.write_file(profile_uri, "Obsolete profile", ctx=ctx)

    registry = MemoryTypeRegistry()
    provider = SessionExtractContextProvider(
        messages=[], ctx=ctx, viking_fs=fs, memory_registry=registry
    )
    extract_context = provider.get_extract_context()
    isolation = MemoryIsolationHandler(ctx, extract_context)
    provider._isolation_handler = isolation
    deletes = []
    for uri in [*protected, profile_uri]:
        read = await provider.read_file(uri)
        deletes.append({"delete_page_id": read["page_id"], "replacement_page_id": None})
    schemas = provider.get_memory_schemas(ctx)
    output_context = ExtractionOutputContext(
        operations_model=SchemaModelGenerator(schemas).create_structured_operations_model(
            isolation.get_read_scope()
        ),
        schemas=tuple(schemas),
        page_id_map=extract_context.page_id_map,
        read_file_contents=provider.read_file_contents,
        link_enabled=False,
    )
    operations, error = create_extraction_output_protocol("json").parse(
        json.dumps({"delete_ids": deletes}),
        output_context,
    )
    assert error is None
    loop = ExtractLoop(
        vlm=SimpleNamespace(model="test"),
        viking_fs=fs,
        ctx=ctx,
        context_provider=provider,
        isolation_handler=isolation,
    )
    loop._extract_context = extract_context
    resolved, _ = await loop.resolve_operations(operations)

    updater = MemoryUpdater(registry=registry)
    updater._viking_fs = fs
    updater.generate_overview = AsyncMock()
    result = await updater.apply_operations(resolved, ctx, extract_context=extract_context)
    assert result.errors == []
    assert not await fs.exists(profile_uri, ctx=ctx)
    for uri, content in protected.items():
        assert await fs.read_file(uri, ctx=ctx) == content
