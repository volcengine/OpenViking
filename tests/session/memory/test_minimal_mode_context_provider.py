from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import (
    MemoryTypeRegistry,
    resolve_memory_templates_dir,
)
from openviking.session.memory.memory_updater import MemoryUpdater, MemoryUpdateResult
from openviking.session.memory.minimal_mode_context_provider import (
    MinimalModeContextProvider,
    make_minimal_mode_registry,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.memory_config import MemoryConfig


@pytest.fixture
def setup(monkeypatch):
    config = SimpleNamespace(memory=MemoryConfig(minimal_mode=True))
    for module in (
        "openviking.session.compressor_v3",
        "openviking.session.memory.extract_loop",
        "openviking.session.memory.session_extract_context_provider",
        "openviking_cli.utils.config",
    ):
        monkeypatch.setattr(f"{module}.get_openviking_config", lambda: config)
    registry = MemoryTypeRegistry(load_schemas=False)
    for name in ("events", "profile"):
        registry.load_from_yaml(str(resolve_memory_templates_dir() / f"{name}.yaml"))
    ctx = RequestContext(user=UserIdentifier("default", "alice"), role=Role.ROOT)
    return SimpleNamespace(config=config, registry=registry, ctx=ctx)


def make_messages(*texts):
    return [
        Message(
            id=f"m{i}", role="user", parts=[TextPart(text=text)], created_at="2024-02-03T12:00:00Z"
        )
        for i, text in enumerate(texts)
    ]


def make_provider(setup, messages, *, source_uri="archive/a", **isolation_options):
    provider = MinimalModeContextProvider(
        messages=messages,
        source_uri=source_uri,
        memory_registry=make_minimal_mode_registry(setup.registry),
        ctx=setup.ctx,
    )
    provider._isolation_handler = MemoryIsolationHandler(
        setup.ctx, provider.get_extract_context(), **isolation_options
    )
    return provider


def response_for(output_format, *ranges):
    if output_format == "python":
        return "\n".join([*(f"sdk.create_events(ranges={r!r})" for r in ranges), "sdk.commit()"])
    return json.dumps({"events": [{"page_id": 100 + i, "ranges": r} for i, r in enumerate(ranges)]})


async def run_loop(setup, provider, responses):
    vlm = SimpleNamespace(model="test-model", get_completion_async=AsyncMock(side_effect=responses))
    loop = ExtractLoop(
        vlm=vlm,
        viking_fs=SimpleNamespace(
            _uri_to_path=lambda uri, *, ctx: uri,
            exists=AsyncMock(return_value=False),
        ),
        ctx=setup.ctx,
        context_provider=provider,
        isolation_handler=provider._isolation_handler,
        max_iterations=3,
        thinking=False,
    )
    operations, tools = await loop.run()
    return operations, tools, vlm, loop


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["python", "json"])
async def test_existing_loop_selects_interleaved_ranges_without_prose(setup, output_format):
    setup.config.memory.extraction_output_format = output_format
    setup.config.memory.link_enabled = True
    provider = make_provider(
        setup,
        make_messages(
            "Alice: I will travel to Hangzhou.",
            "Alice: The product launch is Friday.",
            "Alice: My Hangzhou hotel is booked.",
            "Haha!",
            "Alice: The product launch moved to Monday.",
        ),
    )
    result, tools, vlm, loop = await run_loop(
        setup, provider, [response_for(output_format, "0,2", "1,4")]
    )
    assert tools == []
    vlm.get_completion_async.assert_awaited_once()
    assert vlm.get_completion_async.await_args.kwargs["thinking"] is False
    assert set(loop._operations_model.model_fields) == {"events"}
    assert loop._output_context.link_enabled is False
    assert [op.memory_fields["ranges"] for op in result.upsert_operations] == ["0,2", "1,4"]
    assert not result.delete_file_contents and not result.resolved_links
    assert all(op.uris for op in result.upsert_operations)
    for op in result.upsert_operations:
        assert "summary" not in op.memory_fields
        assert "title" not in op.memory_fields
        assert op.memory_fields["event_name"].startswith("event_")
        assert op.memory_fields["goal"] == ""

    schemas = provider.get_memory_schemas(setup.ctx)
    assert [s.memory_type for s in schemas] == ["events"]
    assert [f.name for f in schemas[0].fields] == ["ranges"]
    assert "summary" in [f.name for f in setup.registry.get("events").fields]
    contract = loop._output_protocol.render_contract(loop._output_context)
    assert "event_name" not in contract
    assert "summary:" not in contract and '"summary"' not in contract
    assert "goal:" not in contract and '"goal"' not in contract

    updater = MemoryUpdater(registry=provider._registry)
    updater._viking_fs = SimpleNamespace(read_file=AsyncMock(side_effect=FileNotFoundError))
    bodies = []
    for operation in result.upsert_operations:
        [(uri, raw)] = await updater._prepare_upsert(
            operation, setup.ctx, extract_context=provider.get_extract_context()
        )
        restored = MemoryFileUtils.read(raw, uri=uri)
        assert "summary" not in restored.extra_fields
        assert "# Summary" not in restored.content
        assert restored.extra_fields["ranges"] == operation.memory_fields["ranges"]
        assert restored.content.startswith("# 2024-02-03 (Saturday) ChatLog:")
        bodies.append(restored.content)
    # Default padding may include adjacent noise/other events, but selections stay separate.
    assert "Hangzhou" in bodies[0] and "Friday" in bodies[0] and "Monday" not in bodies[0]
    assert "product launch" in bodies[1] and "Monday" in bodies[1]
    assert all("Haha!" in body for body in bodies)


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["python", "json"])
async def test_empty_selection_is_valid(setup, output_format):
    setup.config.memory.extraction_output_format = output_format
    provider = make_provider(setup, make_messages("Hello!", "Thanks!"))
    result, _, vlm, _ = await run_loop(setup, provider, [response_for(output_format)])
    assert result.upsert_operations == []
    vlm.get_completion_async.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_range", ["99", "0-99", "bad", ""])
async def test_bad_ranges_use_existing_llm_repair(setup, bad_range):
    provider = make_provider(setup, make_messages("I moved to Paris."))
    result, _, vlm, _ = await run_loop(
        setup, provider, [response_for("python", bad_range), response_for("python", "0")]
    )
    assert vlm.get_completion_async.await_count == 2
    assert [op.memory_fields["ranges"] for op in result.upsert_operations] == ["0"]
    assert result.upsert_operations[0].uris


@pytest.mark.asyncio
async def test_assistant_only_selection_is_repaired(setup):
    messages = make_messages("I live in Paris.", "You live in Paris.")
    messages[1].role = "assistant"
    provider = make_provider(setup, messages)
    result, _, vlm, _ = await run_loop(
        setup, provider, [response_for("python", "1"), response_for("python", "0-1")]
    )
    assert vlm.get_completion_async.await_count == 2
    assert [op.memory_fields["ranges"] for op in result.upsert_operations] == ["0-1"]
    assert result.upsert_operations[0].uris


@pytest.mark.asyncio
async def test_existing_peer_isolation_applies(setup):
    messages = make_messages("I live in Paris.")
    messages[0].peer_id = "conv-26"
    provider = make_provider(setup, messages, allow_self=False, allowed_peer_ids={"conv-26"})
    result, _, _, _ = await run_loop(setup, provider, [response_for("python", "0")])
    assert "/user/alice/peers/conv-26/memories/events/" in result.upsert_operations[0].uris[0]
    denied = make_provider(setup, messages, allowed_memory_types={"profile"})
    assert denied.get_memory_schemas(setup.ctx) == []


def test_server_names_are_stable_and_model_prose_is_not_preserved(setup):
    messages = make_messages("I live in Paris.")
    first = make_provider(setup, messages)
    fields = {"ranges": "0", "page_id": 100, "summary": "invented", "event_name": "invented"}
    prepared = first.prepare_operation_fields("events", fields)
    same = make_provider(setup, messages).prepare_operation_fields("events", fields)
    other = make_provider(setup, messages, source_uri="archive/b").prepare_operation_fields(
        "events", fields
    )
    assert prepared == same
    assert prepared["event_name"] != other["event_name"]
    assert "summary" not in prepared and "invented" not in prepared.values()


@pytest.mark.asyncio
@pytest.mark.parametrize("padding", [None, 0, 1, 2])
@pytest.mark.parametrize("prepared", [False, True])
async def test_compressor_uses_llm_and_skips_other_memory_learning(
    setup, monkeypatch, padding, prepared
):
    import openviking.session.compressor_v3 as module
    from openviking.session.memory import account_templates

    if padding is not None:
        from openviking.session.memory import minimal_mode_context_provider as minimal

        monkeypatch.setattr(
            minimal,
            "_EVENT_CONTENT_TEMPLATE",
            minimal._EVENT_CONTENT_TEMPLATE.replace(
                "get_event_content(ranges, '', 0, range_padding=1)",
                f"get_event_content(ranges, '', 0, range_padding={padding})",
            ),
        )
    messages = make_messages(*(f"Fact {i}." for i in range(5)))
    if prepared:
        from dataclasses import replace

        prepared_messages = [
            replace(message, parts=[TextPart(text=f"Prepared {message.parts[0].text}")])
            for message in messages
        ]
        monkeypatch.setattr(
            "openviking.session.memory.session_extract_context_provider.replace_image_parts_with_descriptions",
            AsyncMock(return_value=prepared_messages),
        )
    monkeypatch.setattr(
        module,
        "get_viking_fs",
        lambda: SimpleNamespace(
            _uri_to_path=lambda uri, *, ctx: uri, exists=AsyncMock(return_value=False)
        ),
    )
    setup.registry.initialize_memory_files = AsyncMock()
    monkeypatch.setattr(MemoryTypeRegistry, "initialize_memory_files", AsyncMock())
    monkeypatch.setattr(
        account_templates, "resolve_account_memory_registry", AsyncMock(return_value=setup.registry)
    )
    vlm = SimpleNamespace(
        model="test-model", get_completion_async=AsyncMock(return_value=response_for("python", "2"))
    )
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=vlm))
    submitted = []

    async def submit(request):
        submitted.append(request)
        return SimpleNamespace(apply_result=MemoryUpdateResult(), operations=request.operations)

    monkeypatch.setattr(
        module,
        "get_streaming_memory_updater",
        AsyncMock(return_value=SimpleNamespace(submit=submit)),
    )
    compressor = module.SessionCompressorV3(vikingdb=None, vlm_resolver=resolver)
    compressor._session_skill_extraction_enabled = lambda: True
    compressor.extract_session_skills = AsyncMock(side_effect=AssertionError("No Skill extraction"))
    compressor.train_from_extracted_cases = AsyncMock(
        side_effect=AssertionError("No Experience learning")
    )
    await compressor.extract_long_term_memories(
        messages, ctx=setup.ctx, allowed_memory_types={"events", "cases", "profile"}
    )
    resolver.get_vlm.assert_awaited_once_with(setup.ctx.account_id)
    vlm.get_completion_async.assert_awaited_once()
    assert submitted[0].isolation_options["allowed_memory_types"] == {"events"}
    assert [op.memory_type for op in submitted[0].operations.upsert_operations] == ["events"]
    assert "summary" not in submitted[0].operations.upsert_operations[0].memory_fields
    setup.registry.initialize_memory_files.assert_not_awaited()
    assert submitted[0].memory_registry is not setup.registry
    assert module.get_streaming_memory_updater.await_args.kwargs["registry"] is setup.registry
    assert submitted[0].memory_registry.list_names() == ["events"]
    assert "# Summary" in setup.registry.get("events").content_template
    assert "# Summary" not in submitted[0].memory_registry.get("events").content_template

    request = submitted[0]
    operation = request.operations.upsert_operations[0]
    assert operation.memory_fields["ranges"] == "2"
    from openviking.session.memory.memory_updater import ExtractContext

    updater = MemoryUpdater(registry=request.memory_registry)
    updater._viking_fs = SimpleNamespace(read_file=AsyncMock(side_effect=FileNotFoundError))
    [(uri, raw)] = await updater._prepare_upsert(
        operation, setup.ctx, extract_context=ExtractContext(request.messages)
    )
    body = MemoryFileUtils.read(raw, uri=uri).content
    assert "# Summary" not in body and "Fact 2." in body
    assert ("Prepared Fact 2." in body) is prepared
    assert all("Prepared" not in message.parts[0].text for message in messages)
    assert ("Fact 0." in body) is (padding == 2)
    assert ("Fact 1." in body) is (padding != 0)
    compressor.extract_session_skills.assert_not_awaited()
    compressor.train_from_extracted_cases.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_repair_fails_instead_of_returning_partial_events(setup):
    provider = make_provider(setup, make_messages("I moved to Paris."))
    with pytest.raises(ValueError, match="Provider operation validation failed after repair"):
        await run_loop(
            setup,
            provider,
            [response_for("python", "0", "99"), response_for("python", "0", "99")],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", [False, True])
async def test_excluded_events_do_not_call_llm(setup, monkeypatch, disabled):
    import openviking.session.compressor_v3 as module
    from openviking.session.memory import account_templates

    setup.registry.get("events").enabled = not disabled
    setup.registry.initialize_memory_files = AsyncMock()
    monkeypatch.setattr(module, "get_viking_fs", lambda: SimpleNamespace())
    monkeypatch.setattr(
        account_templates,
        "resolve_account_memory_registry",
        AsyncMock(return_value=setup.registry),
    )
    resolver = SimpleNamespace(get_vlm=AsyncMock(side_effect=AssertionError("No enabled events")))
    compressor = module.SessionCompressorV3(vikingdb=None, vlm_resolver=resolver)
    await compressor.extract_long_term_memories(
        make_messages("I moved to Paris."),
        ctx=setup.ctx,
        allowed_memory_types={"events"} if disabled else {"profile"},
    )
    resolver.get_vlm.assert_not_awaited()


def test_incompatible_event_schema_fails_explicitly(setup):
    setup.registry.get("events").operation_mode = "upsert"
    provider = make_provider(setup, make_messages("I moved to Paris."))
    with pytest.raises(ValueError, match="requires an add_only events schema with ranges"):
        provider.get_memory_schemas(setup.ctx)


def test_raw_template_is_request_local_even_with_account_override(setup):
    standard = setup.registry.get("events")
    standard._account_content_template = True
    snapshot = standard.model_dump()
    minimal = make_minimal_mode_registry(setup.registry)
    assert minimal.list_names() == ["events"]
    assert minimal.get("events") is not standard
    assert minimal.get("events")._account_content_template is False
    assert "# Summary" not in minimal.get("events").content_template
    assert standard.model_dump() == snapshot
    assert standard._account_content_template is True
    assert "profile" in setup.registry.list_names()


@pytest.mark.asyncio
@pytest.mark.parametrize("summary", ["A generated summary", ""])
async def test_standard_mode_keeps_original_summary_template(setup, summary):
    setup.config.memory.minimal_mode = False
    provider = make_provider(setup, make_messages("I live in Paris."))
    result, _, _, _ = await run_loop(setup, provider, [response_for("python", "0")])
    operation = result.upsert_operations[0]
    operation.memory_fields["summary"] = summary
    updater = MemoryUpdater(registry=setup.registry)
    updater._viking_fs = SimpleNamespace(read_file=AsyncMock(side_effect=FileNotFoundError))
    [(uri, raw)] = await updater._prepare_upsert(
        operation, setup.ctx, extract_context=provider.get_extract_context()
    )
    restored = MemoryFileUtils.read(raw, uri=uri)
    assert restored.content.startswith(f"# Summary\n{summary}\n# 2024-02-03")
    assert "I live in Paris." in restored.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "padding,expected", [(0, ["2", "3"]), (1, ["1-3", "2-4"]), (2, ["0-4", "1-5"])]
)
async def test_padding_preserves_separate_event_names_and_uris(setup, padding, expected):
    messages = make_messages(*(f"Fact {i}." for i in range(6)))
    provider = make_provider(setup, messages)
    operations, _, vlm, _ = await run_loop(setup, provider, [response_for("python", "2", "3")])
    identities = [
        (op.memory_fields["event_name"], list(op.uris)) for op in operations.upsert_operations
    ]
    before = operations.model_dump()
    context = provider.get_extract_context()
    for operation, expanded in zip(operations.upsert_operations, expected, strict=True):
        assert context.get_event_content(
            operation.memory_fields["ranges"], "", 0, range_padding=padding
        ) == (context.read_message_ranges(expanded).pretty_print())
    assert operations.model_dump() == before
    assert [
        (op.memory_fields["event_name"], op.uris) for op in operations.upsert_operations
    ] == identities
    vlm.get_completion_async.assert_awaited_once()  # Padding never calls an LLM.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selected_peer,other_peer", [("alice-peer", "bob-peer"), ("self", None), (None, "self")]
)
async def test_padding_cannot_copy_another_peers_private_messages(setup, selected_peer, other_peer):
    messages = make_messages("Alice fact.", "Bob private fact.")
    messages[0].peer_id = selected_peer
    messages[1].peer_id = other_peer
    provider = make_provider(
        setup, messages, allowed_peer_ids={selected_peer} if selected_peer else set()
    )
    operations, _, _, _ = await run_loop(setup, provider, [response_for("python", "0")])
    with pytest.raises(ValueError, match="memory-owner boundaries"):
        provider.get_extract_context().get_event_content("0", "", 0, range_padding=2)
    assert operations.upsert_operations[0].memory_fields["ranges"] == "0"


@pytest.mark.asyncio
async def test_padding_keeps_assistant_context_for_the_same_peer(setup):
    messages = make_messages("I booked a trip.", "Your hotel is confirmed.")
    messages[0].peer_id = "alice-peer"
    messages[1].role = "assistant"
    provider = make_provider(setup, messages, allowed_peer_ids={"alice-peer"})
    operations, _, _, _ = await run_loop(setup, provider, [response_for("python", "0")])
    body = provider.get_extract_context().get_event_content("0", "", 0, range_padding=2)
    assert "Your hotel is confirmed." in body
    assert operations.upsert_operations[0].memory_fields["ranges"] == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_false", [False, True])
async def test_disabled_or_omitted_mode_uses_standard_provider_and_learning(
    setup, monkeypatch, explicit_false
):
    from unittest.mock import Mock

    import openviking.session.compressor_v3 as module
    from openviking.session.memory import account_templates
    from openviking.session.memory.dataclass import ResolvedOperations
    from openviking.session.memory.session_extract_context_provider import (
        SessionExtractContextProvider,
    )

    setup.config.memory = MemoryConfig(**({"minimal_mode": False} if explicit_false else {}))
    setup.registry.initialize_memory_files = AsyncMock()
    monkeypatch.setattr(module, "get_viking_fs", lambda: SimpleNamespace())
    monkeypatch.setattr(
        account_templates, "resolve_account_memory_registry", AsyncMock(return_value=setup.registry)
    )
    submitted = []

    async def submit(request):
        submitted.append(request)
        return SimpleNamespace(apply_result=MemoryUpdateResult(), operations=request.operations)

    monkeypatch.setattr(
        module,
        "get_streaming_memory_updater",
        AsyncMock(return_value=SimpleNamespace(submit=submit)),
    )
    compressor = module.SessionCompressorV3(
        vikingdb=None,
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=SimpleNamespace(model="test"))),
    )
    compressor._get_or_create_react = Mock(
        return_value=SimpleNamespace(
            run=AsyncMock(
                return_value=(
                    ResolvedOperations(upsert_operations=[], delete_file_contents=[], errors=[]),
                    [],
                )
            )
        )
    )
    compressor.train_from_extracted_cases = AsyncMock(return_value={"submitted": 0})
    await compressor.extract_long_term_memories(make_messages("I live in Paris."), ctx=setup.ctx)
    provider = compressor._get_or_create_react.call_args.kwargs["context_provider"]
    assert type(provider) is SessionExtractContextProvider
    assert submitted[0].memory_registry is setup.registry
    assert submitted[0].isolation_options["allowed_memory_types"] is None
    setup.registry.initialize_memory_files.assert_awaited_once_with(
        setup.ctx, allowed_memory_types=None
    )
    compressor.train_from_extracted_cases.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("minimal_mode", [False, True])
async def test_mode_specific_error_handling_preserves_standard_behavior(
    setup, monkeypatch, minimal_mode
):
    import openviking.session.compressor_v3 as module

    setup.config.memory.minimal_mode = minimal_mode
    monkeypatch.setattr(module, "get_viking_fs", lambda: SimpleNamespace())
    compressor = module.SessionCompressorV3(vikingdb=None)
    compressor._extract_user_memories = AsyncMock(side_effect=ValueError("bad ranges"))
    if minimal_mode:
        with pytest.raises(ValueError, match="bad ranges"):
            await compressor.extract_long_term_memories(make_messages("A fact."), ctx=setup.ctx)
    else:
        result = await compressor.extract_long_term_memories(
            make_messages("A fact."), ctx=setup.ctx
        )
        assert result["contexts"] == []


@pytest.mark.asyncio
async def test_padding_errors_propagate_in_normal_mode(setup, monkeypatch):
    import openviking.session.compressor_v3 as module
    from openviking.session.memory.memory_updater import EventRangePaddingError

    setup.config.memory.minimal_mode = False
    monkeypatch.setattr(module, "get_viking_fs", lambda: SimpleNamespace())
    compressor = module.SessionCompressorV3(vikingdb=None)
    compressor._extract_user_memories = AsyncMock(
        side_effect=EventRangePaddingError("unsafe padding")
    )
    with pytest.raises(EventRangePaddingError, match="unsafe padding"):
        await compressor.extract_long_term_memories(make_messages("A fact."), ctx=setup.ctx)
