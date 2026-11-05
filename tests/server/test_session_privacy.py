# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Protect Session persistence and restore only on explicit raw reads."""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from openviking.message import ContextPart, ImagePart, TextPart, ToolPart
from openviking.privacy import session_extractor
from openviking.privacy.reference import build_privacy_field_reference
from openviking.privacy.session_extractor import (
    build_session_placeholder,
    extract_and_store_session_privacy,
    session_privacy_target,
)
from openviking.privacy.session_restore import restore_session_content
from openviking.server.config import ToolOutputExternalizationConfig
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError, ResourceExhaustedError
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


@pytest.fixture
def enable_session_privacy_discovery(monkeypatch):
    """Keep coverage of the retained discovery code while production disables it."""
    monkeypatch.setattr(session_extractor, "_SESSION_PRIVACY_DISCOVERY_ENABLED", True)


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
async def test_http_skips_discovery_but_protects_registered_values(
    client, service, ctx, monkeypatch, batch
):
    sid = "discovery-disabled"
    session = await service.sessions.create(ctx, sid)
    service.sessions.set_tool_output_externalization_config(
        ToolOutputExternalizationConfig(enabled=True, threshold_chars=1024, preview_chars=200)
    )
    await service.privacy_configs.upsert(ctx, "credential", "github", {"pat": "registered-secret"})
    reference = build_privacy_field_reference(ctx.user.user_id, "credential", "github", "pat")
    insert = AsyncMock(wraps=service.privacy_configs.insert_values)
    monkeypatch.setattr(service.privacy_configs, "insert_values", insert)
    output = "padding\n" * 1500 + "password=example-password registered-secret"
    message = {
        "role": "assistant",
        "parts": [
            {"type": "text", "text": "password=example-password registered-secret"},
            {
                "type": "tool",
                "tool_id": "read",
                "tool_input": {
                    "password": "example-password",
                    "credentials": {"api_key": "registered-secret", "token": 1234},
                },
                "tool_output": output,
            },
        ],
    }
    endpoint = f"/api/v1/sessions/{sid}/messages" + ("/batch" if batch else "")
    response = await client.post(endpoint, json={"messages": [message]} if batch else message)
    assert response.status_code == 200, response.text
    insert.assert_not_awaited()
    assert await service.privacy_configs.get_current(ctx, "session", sid) is None
    disk = await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx)
    assert "registered-secret" not in disk
    parts = json.loads(disk)["parts"]
    assert parts[0]["text"] == f"password=example-password {reference}"
    assert parts[1]["tool_input"] == {
        "password": "example-password",
        "credentials": {"api_key": reference, "token": 1234},
    }
    output_uri = parts[1]["tool_output_ref"] + "/output.txt"
    stored_output = await service.viking_fs.read_file(output_uri, ctx=ctx)
    assert stored_output == output.replace("registered-secret", reference)
    assert await service.fs.read(output_uri, ctx=ctx) == output


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_http_preserves_protected_content_and_restores_only_raw_reads(client, service, ctx):
    sid = "http-privacy"
    response = await client.post("/api/v1/sessions", json={"session_id": sid})
    assert response.status_code == 200, response.text
    uri = response.json()["result"]["uri"] + "/messages.jsonl"
    reference = build_privacy_field_reference(ctx.user.user_id, "credential", "github", "pat")
    await service.privacy_configs.upsert(ctx, "credential", "github", {"pat": "registered-secret"})
    response = await client.post(
        f"/api/v1/sessions/{sid}/messages/batch",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "password=http-secret registered-secret repeated http-secret",
                },
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "tool",
                            "tool_id": "pending",
                            "tool_status": "pending",
                            "tool_output": None,
                        }
                    ],
                },
            ]
        },
    )
    assert response.status_code == 200, response.text
    disk = await service.viking_fs.read_file(uri, ctx=ctx)
    assert "http-secret" not in disk and "registered-secret" not in disk
    assert reference in disk
    for raw in [False, True]:
        read = await client.get("/api/v1/content/read", params={"uri": uri, "raw": raw})
        assert read.status_code == 200, read.text
        public = read.json()["result"]
        assert ("http-secret" in public) == raw
        assert ("registered-secret" in public) == raw
        assert (reference in public) == (not raw)
        json.loads(public.splitlines()[1])  # Pending tool output remains valid JSON.
    internal = await client.get(f"/api/v1/sessions/{sid}/context")
    assert internal.status_code == 200, internal.text
    assert "registered-secret" not in internal.text and "http-secret" not in internal.text
    current = await service.privacy_configs.get_current(ctx, "session", sid)
    assert list(current.values.values()) == ["http-secret"]
    assert (await client.delete(f"/api/v1/sessions/{sid}")).status_code == 200
    assert (await client.get(f"/api/v1/privacy-configs/session/{sid}")).status_code == 200


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_default_read_keeps_escaped_short_and_container_values_protected(
    client, service, ctx
):
    original = {
        "password": 'quote"secret',
        "token": "line\nsecret",
        "api_key": "back\\secret",
        "secret": "pin",
        "credentials": {"api_key": "container-secret", "items": [False, None, 7]},
    }
    session = await service.sessions.create(ctx, "protected-read")
    await session.add_message_async("assistant", [ToolPart(tool_input=original)])
    uri = session.uri + "/messages.jsonl"
    disk = await service.viking_fs.read_file(uri, ctx=ctx)
    visible = await client.get("/api/v1/content/read", params={"uri": uri})
    assert visible.status_code == 200, visible.text
    assert visible.json()["result"] == disk
    raw = await client.get("/api/v1/content/read", params={"uri": uri, "raw": True})
    assert raw.status_code == 200, raw.text
    assert json.loads(raw.json()["result"])["parts"][0]["tool_input"] == original


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_new_equal_fields_keep_independent_references_when_one_rotates(service, ctx):
    session = await service.sessions.create(ctx, "equal-new-fields")
    original = {"password": "same-new-value", "api_key": "same-new-value"}
    await session.add_message_async("assistant", [ToolPart(tool_input=original)])
    protected = session.messages[0].parts[0].tool_input
    fields = {key: ref.removesuffix("}}").rsplit(":", 1)[1] for key, ref in protected.items()}
    current = await service.privacy_configs.get_current(ctx, "session", session.session_id)
    assert current.values == dict.fromkeys(fields.values(), "same-new-value")
    await service.privacy_configs.patch_values(
        ctx, "session", session.session_id, {fields["password"]: "rotated-value"}
    )
    restored = json.loads(await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx))
    assert restored["parts"][0]["tool_input"] == {
        "password": "rotated-value",
        "api_key": "same-new-value",
    }


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_batch_repeated_text_values_allow_later_messages(service, ctx):
    session = await service.sessions.create(ctx, "batch-repeat")
    texts = [
        "password=shared-secret password=shared-secret",
        "**password=shared-secret**",
        "password=shared-secret",
    ]
    await session.add_messages_async(
        [
            {"role": "user", "parts": [TextPart(texts[0])]},
            {"role": "assistant", "parts": [TextPart(texts[1])]},
        ]
    )
    await session.add_message_async("user", [TextPart(texts[2])])
    current = await service.privacy_configs.get_current(ctx, "session", session.session_id)
    assert list(current.values.values()) == ["shared-secret"]
    assert await service.privacy_configs.list_versions(ctx, "session", session.session_id) == [1]
    disk = await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx)
    assert "shared-secret" not in disk
    restored = await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx)
    assert [json.loads(line)["parts"][0]["text"] for line in restored.splitlines()] == texts


async def test_basic_prose_does_not_register_words_for_later_messages(service, ctx):
    session = await service.sessions.create(ctx, "basic-prose")
    text = "This is a basic example of configuration.\nBasic usage\nUse basic Python syntax."
    await session.add_message_async("user", [TextPart(text)])
    await session.add_message_async("assistant", [TextPart("Python usage example")])
    assert await service.privacy_configs.get_current(ctx, "session", session.session_id) is None
    disk = await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx)
    assert [json.loads(line)["parts"][0]["text"] for line in disk.splitlines()] == [
        text,
        "Python usage example",
    ]


@pytest.mark.parametrize("sid", ["markdown-restatement", "中文会话"])
@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_markdown_restatement_reuses_references_without_new_versions(service, ctx, sid):
    session = await service.sessions.create(ctx, sid)
    user_text = "API_KEY=demo-api-secret\nAuthorization: Bearer demo-bearer-secret\nsk-abcdefghijklmnopqrstuv"
    await session.add_message_async("user", [TextPart(user_text)])
    target_key = session_privacy_target(sid)
    initial = await service.privacy_configs.get_current(ctx, "session", target_key)
    wrappers = [
        "**{}**",
        "`{}`",
        "``{}``",
        "**`{}`**",
        "`**heading**` `{}`",
        "```text\n{}\n```",
        "~~~text\n{}\n~~~",
    ]
    assistant_text = "\n".join(
        wrapper.format(line) for wrapper in wrappers for line in user_text.splitlines()
    )
    await session.add_message_async(
        "assistant",
        [TextPart(assistant_text), ToolPart(tool_input={"api_key": "demo-api-secret"})],
    )
    current = await service.privacy_configs.get_current(ctx, "session", target_key)
    assert current.values == initial.values
    assert await service.privacy_configs.list_versions(ctx, "session", target_key) == [1]
    disk = await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx)
    assert all(value not in disk for value in initial.values.values())
    user, assistant = [json.loads(line)["parts"] for line in disk.splitlines()]
    assert assistant[0]["text"] == "\n".join(
        wrapper.format(line) for wrapper in wrappers for line in user[0]["text"].splitlines()
    )
    assert assistant[1]["tool_input"]["api_key"] == user[0]["text"].splitlines()[0].split("=", 1)[1]
    restored = await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx)
    assert [json.loads(line)["parts"][0]["text"] for line in restored.splitlines()] == [
        user_text,
        assistant_text,
    ]


@pytest.mark.parametrize("entrypoint", ["session", "skill"])
async def test_session_and_skill_require_explicit_reference_for_ambiguous_value(
    service, ctx, entrypoint
):
    await service.privacy_configs.upsert(
        ctx, "credential", "ambiguous", {"a": "same-registered-value", "b": "same-registered-value"}
    )
    with pytest.raises(InvalidArgumentError, match="mapped to multiple fields"):
        if entrypoint == "skill":
            await service._skill_processor.prepare_skill_privacy(
                {"name": "ambiguous", "content": "password=same-registered-value"}, ctx
            )
        else:
            session = await service.sessions.create(ctx, "ambiguous")
            await session.add_message_async("user", [TextPart("password=same-registered-value")])
    if entrypoint == "session":
        assert await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx) == ""
        assert await service.privacy_configs.get_current(ctx, "session", session.session_id) is None
        refs = [
            build_privacy_field_reference(ctx.user.user_id, "credential", "ambiguous", field)
            for field in ("a", "b")
        ]
        await session.add_message_async("user", [TextPart(" ".join(refs))])
        restored = json.loads(await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx))
        assert restored["parts"][0]["text"] == "same-registered-value same-registered-value"


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_batch_discovery_protects_all_payloads_without_mutating_inputs(service, ctx):
    await service.privacy_configs.upsert(ctx, "credential", "github", {"pat": "registered-value"})
    fresh = "alpha-987654"
    credentials = {"api_key": "nested-value", "items": [False, None, "中文"]}
    specs = [
        {
            "role": "assistant",
            "peer_id": fresh,
            "parts": [
                TextPart(f"retry {fresh}; registered-value"),
                ContextPart(uri=f"viking://resources/{fresh}", abstract=fresh),
                ImagePart(url=f"https://example.test/{fresh}"),
                ToolPart(
                    tool_id=fresh,
                    tool_input={
                        fresh: fresh,
                        "rows": ({"note": fresh}, False, None),
                        "credentials": credentials,
                    },
                    tool_output=fresh,
                    tool_output_externalization_error=fresh,
                ),
                ToolPart(tool_id="pending", tool_output=None),
                TextPart(f"password={fresh}"),
            ],
        }
    ]
    before = deepcopy(specs)
    protected = await extract_and_store_session_privacy(
        "traversal", specs, ctx, service.privacy_configs, service._privacy_sanitizer
    )
    assert specs == before
    parts = protected[0]["parts"]
    current = await service.privacy_configs.get_current(ctx, "session", "traversal")
    assert credentials in current.values.values()
    reference = next(
        build_session_placeholder("traversal", field)
        for field, value in current.values.items()
        if value == fresh
    )
    assert parts[0].text.startswith(f"retry {reference}; ")
    assert parts[1].abstract == reference
    assert parts[3].tool_input[reference] == reference
    assert parts[3].tool_input["rows"][0]["note"] == reference
    assert parts[3].tool_output == reference
    assert parts[3].tool_output_externalization_error == reference
    encoded = json.dumps({**protected[0], "parts": [asdict(part) for part in parts]})
    restored = await restore_session_content(
        service.privacy_configs,
        encoded,
        ctx,
        session_id="traversal",
        jsonl=True,
    )
    expected = json.dumps({**before[0], "parts": [asdict(part) for part in before[0]["parts"]]})
    assert json.loads(restored) == json.loads(expected)


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_batch_discovery_rejects_key_collision_before_message_write(
    service, ctx, monkeypatch
):
    from openviking.privacy import session_extractor

    fixed_id = "same0001"
    monkeypatch.setattr(session_extractor, "token_urlsafe", lambda _: fixed_id)
    session = await service.sessions.create(ctx, "collision")
    reference = build_session_placeholder(session.session_id, fixed_id)
    part = ToolPart(
        tool_input={"alpha-987654": 1, reference: 2}, tool_output="password=alpha-987654"
    )
    before = deepcopy(part)
    externalize = AsyncMock()
    monkeypatch.setattr(session._tool_outputs, "externalize_group", externalize)
    with pytest.raises(InvalidArgumentError, match="object key collision"):
        await session.add_message_async("assistant", [part])
    externalize.assert_not_awaited()
    assert part == before
    assert await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx) == ""


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_externalized_output_restores_without_corrupting_metadata(service, ctx):
    session = await service.sessions.create(ctx, "externalized")
    session._tool_output_externalization_config = ToolOutputExternalizationConfig(
        enabled=True, threshold_chars=1024, preview_chars=200
    )
    output = "padding\n" * 1500 + "password=session"
    await session.add_message_async("assistant", [ToolPart(tool_id="read", tool_output=output)])
    part = session.messages[0].parts[0]
    ref = part.tool_output_ref
    assert ref
    stored = await service.viking_fs.read_file(ref + "/output.txt", ctx=ctx)
    assert "password=session" not in stored
    assert await service.fs.read(ref + "/output.txt", ctx=ctx) == output
    visible = await service.fs.read_visible(ref + "/output.txt", ctx=ctx)
    assert "password=session" not in visible
    assert "{{ov_privacy:" in visible
    metadata = json.loads(await service.viking_fs.read_file(ref + "/metadata.json", ctx=ctx))
    assert metadata["session_id"] == session.session_id
    reloaded = await service.sessions.get(session.session_id, ctx)
    assert "password=session" not in json.dumps(
        [message.to_dict() for message in reloaded.messages]
    )


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_auto_created_session_extracts_before_persistence(service, ctx):
    session = await service.sessions.get("auto-created", ctx, auto_create=True)
    await session.add_message_async("user", [TextPart("password=default-secret")])
    uri = session.uri + "/messages.jsonl"
    disk = await service.viking_fs.read_file(uri, ctx=ctx)
    assert "default-secret" not in disk
    current = await service.privacy_configs.get_current(ctx, "session", session.session_id)
    assert list(current.values.values()) == ["default-secret"]
    assert "default-secret" in await service.fs.read(uri, ctx=ctx)


@pytest.mark.parametrize(
    "sid",
    ["中文会话", "team session@@group", " leading and trailing ", "x" * 300, "group:100%~"],
)
@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_custom_session_ids_support_privacy_and_cross_session_reuse(service, ctx, sid):
    session = await service.sessions.create(ctx, sid)
    await session.add_message_async("user", [TextPart("password=custom-session-secret")])
    target_key = session_privacy_target(sid)
    current = await service.privacy_configs.get_current(ctx, "session", target_key)
    [field] = current.values
    meta = await service.privacy_configs.get_meta(ctx, "session", target_key)
    assert meta.labels == {"session_id": sid}
    uri = session.uri + "/messages.jsonl"
    disk = await service.viking_fs.read_file(uri, ctx=ctx)
    assert "custom-session-secret" not in disk
    assert build_session_placeholder(target_key, field) in disk
    restored = await service.fs.read(uri, ctx=ctx)
    assert json.loads(restored)["parts"][0]["text"] == "password=custom-session-secret"
    target = await service.sessions.create(ctx, "reuse-custom-id")
    await target.add_message_async("user", [TextPart("password=custom-session-secret")])
    reference = build_privacy_field_reference(ctx.user.user_id, "session", target_key, field)
    assert target.messages[0].parts[0].text == f"password={reference}"
    restored = await service.fs.read(target.uri + "/messages.jsonl", ctx=ctx)
    assert json.loads(restored)["parts"][0]["text"] == "password=custom-session-secret"


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_concurrent_writers_preserve_messages_and_independent_fields(
    service, ctx, monkeypatch
):
    from openviking.privacy import session_extractor

    ids = iter(["same0001"] * 8 + [f"next{i:04d}" for i in range(8)])
    monkeypatch.setattr(session_extractor, "token_urlsafe", lambda _: next(ids))
    session = await service.sessions.create(ctx, "concurrent")
    await service.privacy_configs.upsert(
        ctx, "session", session.session_id, {"same0001": "old-secret"}
    )
    old_reference = build_privacy_field_reference(
        ctx.user.user_id, "session", session.session_id, "same0001"
    )
    await session.add_message_async("user", [TextPart(old_reference)])
    await asyncio.gather(
        *(
            service.sessions.session(ctx, "concurrent").add_message_async(
                "user", [TextPart(f"password=secret-{i}")]
            )
            for i in range(8)
        )
    )
    loaded = await service.sessions.get("concurrent", ctx)
    current = await service.privacy_configs.get_current(ctx, "session", "concurrent")
    assert set(current.values.values()) == {"old-secret", *(f"secret-{i}" for i in range(8))}
    assert current.version == 9
    restored = await service.fs.read(loaded.uri + "/messages.jsonl", ctx=ctx)
    assert sorted(json.loads(line)["parts"][0]["text"] for line in restored.splitlines()) == [
        "old-secret",
        *(f"password=secret-{i}" for i in range(8)),
    ]


@pytest.mark.parametrize(
    "error", [ResourceExhaustedError, OSError], ids=["id-exhaustion", "storage"]
)
@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_privacy_write_failure_rejects_batch_before_externalization(
    service, ctx, monkeypatch, error
):
    from openviking.privacy import session_extractor

    session = await service.sessions.create(ctx, "privacy-write-failure")
    await service.privacy_configs.upsert(
        ctx, "session", session.session_id, {"taken001": "old-secret"}
    )
    if error is ResourceExhaustedError:
        monkeypatch.setattr(session_extractor, "token_urlsafe", lambda _: "taken001")
    else:
        monkeypatch.setattr(
            service.privacy_configs,
            "insert_values",
            AsyncMock(side_effect=OSError("storage unavailable")),
        )
    externalize = AsyncMock()
    monkeypatch.setattr(session._tool_outputs, "externalize_group", externalize)
    with pytest.raises(error):
        await session.add_message_async("user", [TextPart("password=new-secret")])
    externalize.assert_not_awaited()
    assert await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx) == ""
    current = await service.privacy_configs.get_current(ctx, "session", session.session_id)
    assert current.values == {"taken001": "old-secret"}
    assert await service.privacy_configs.list_versions(ctx, "session", session.session_id) == [1]


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_values_equal_to_json_keys_do_not_change_message_identity(service, ctx):
    session = await service.sessions.create(ctx, "identity")
    original = {"password": "tool_input", "token": "assistant", "secret": "default"}
    await session.add_message_async("assistant", [ToolPart(tool_input=original)])
    # A full rewrite takes a different storage path than append.
    await session._write_to_agfs_async(session.messages)
    reloaded = await service.sessions.get(session.session_id, ctx)
    assert reloaded.messages[0].role == "assistant"
    assert isinstance(reloaded.messages[0].parts[0], ToolPart)
    restored = json.loads(await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx))
    assert restored["role"] == "assistant"
    assert restored["parts"][0]["tool_input"] == original
    archive = session.uri + "/history/archive_001"
    await session._merge_archive_meta(archive, {"phase1": {"user": ctx.user.to_dict()}})
    saved = json.loads(await service.viking_fs.read_file(archive + "/.meta.json", ctx=ctx))
    assert saved["phase1"]["user"] == ctx.user.to_dict()
    await session._write_done_file(archive, "default", "default")
    done = json.loads(await service.viking_fs.read_file(archive + "/.done", ctx=ctx))
    assert done["starting_message_id"] == "default"


async def test_repeated_explicit_references_restore_exact_types_without_new_fields(service, ctx):
    session = await service.sessions.create(ctx, "repeated")
    values = {
        "short": "a",
        "number": 1234,
        "object": {"a": [False, None]},
        "named-field": "existing-session-secret",
    }
    await service.privacy_configs.upsert(ctx, "session", session.session_id, values)
    row = {
        key: build_privacy_field_reference(ctx.user.user_id, "session", session.session_id, field)
        for key, field in [("password", "short"), ("token", "number"), ("secret", "object")]
    }
    await session.add_message_async(
        "assistant",
        [ToolPart(tool_input={"rows": [row] * 1300}), TextPart("password=existing-session-secret")],
    )
    current = await service.privacy_configs.get_current(ctx, "session", session.session_id)
    assert current.values == values
    restored = json.loads(await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx))
    assert (
        restored["parts"][0]["tool_input"]["rows"]
        == [{"password": "a", "token": 1234, "secret": {"a": [False, None]}}] * 1300
    )
    assert restored["parts"][1]["text"] == "password=existing-session-secret"


async def test_restore_leaves_foreign_missing_and_nested_references_unchanged(
    service, ctx, monkeypatch
):
    own = build_privacy_field_reference(ctx.user.user_id, "credential", "demo", "pat")
    missing = build_privacy_field_reference(ctx.user.user_id, "credential", "demo", "missing")
    unconfigured = build_privacy_field_reference(ctx.user.user_id, "credential", "absent", "pat")
    foreign = build_privacy_field_reference("alice", "credential", "demo", "pat")
    nested = build_privacy_field_reference(ctx.user.user_id, "credential", "other", "pat")
    await service.privacy_configs.upsert(ctx, "credential", "demo", {"pat": nested})
    await service.privacy_configs.upsert(ctx, "credential", "other", {"pat": "nested-secret"})
    session = await service.sessions.create(ctx, "reference-boundary")
    await session.add_message_async("user", [TextPart(f"{own} {missing} {unconfigured} {foreign}")])
    read = AsyncMock(wraps=service.privacy_configs.get_current)
    monkeypatch.setattr(service.privacy_configs, "get_current", read)
    restored = json.loads(await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx))
    assert restored["parts"][0]["text"] == f"{nested} {missing} {unconfigured} {foreign}"
    assert read.await_count == 2  # One read per target, not per occurrence.
    read.assert_any_await(ctx, "credential", "absent")


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_later_credential_registration_preserves_session_reference_identity(
    client, service, ctx
):
    value = "shared-managed-secret"
    first = await service.sessions.create(ctx, "first")
    await first.add_message_async("user", [TextPart(f"password={value}")])
    first_uri = first.uri + "/messages.jsonl"
    stored = await service.viking_fs.read_file(first_uri, ctx=ctx)
    current = await service.privacy_configs.get_current(ctx, "session", "first")
    [field] = current.values
    assert json.loads(stored)["parts"][0]["text"] == (
        f"password={build_session_placeholder('first', field)}"
    )

    await service.privacy_configs.upsert(ctx, "credential", "managed", {"key": value})
    credential = build_privacy_field_reference(ctx.user.user_id, "credential", "managed", "key")
    second = await service.sessions.create(ctx, "second")
    await second.add_message_async(
        "assistant", [TextPart(f"password={value}"), ToolPart(tool_input={"password": value})]
    )
    assert second.messages[0].parts[0].text == f"password={credential}"
    assert second.messages[0].parts[1].tool_input == {"password": credential}

    visible = await client.get("/api/v1/content/read", params={"uri": first_uri})
    assert visible.status_code == 200, visible.text
    assert visible.json()["result"] == stored

    await service.privacy_configs.patch_values(ctx, "credential", "managed", {"key": "rotated-key"})
    for session, expected in [(first, value), (second, "rotated-key")]:
        read = await client.get(
            "/api/v1/content/read", params={"uri": session.uri + "/messages.jsonl", "raw": True}
        )
        assert read.status_code == 200, read.text
        assert json.loads(read.json()["result"])["parts"][0]["text"] == f"password={expected}"
    assert await service.viking_fs.read_file(first_uri, ctx=ctx) == stored


@pytest.mark.usefixtures("enable_session_privacy_discovery")
async def test_cross_session_reuse_requires_full_reference_but_short_reference_stays_local(
    service, ctx
):
    source = await service.sessions.create(ctx, "source")
    original = "password=cross-session-secret"
    await source.add_message_async("user", [TextPart(original)])
    current = await service.privacy_configs.get_current(ctx, "session", "source")
    [field] = current.values
    short = build_session_placeholder("source", field)
    full = build_privacy_field_reference(ctx.user.user_id, "session", "source", field)
    target = await service.sessions.create(ctx, "target")
    await target.add_message_async("user", [TextPart(f"{original} {short}")])
    assert target.messages[0].parts[0].text == f"password={full} {short}"
    raw = json.loads(await service.fs.read(target.uri + "/messages.jsonl", ctx=ctx))
    assert raw["parts"][0]["text"] == f"{original} {short}"
    assert await service.privacy_configs.get_current(ctx, "session", "target") is None


async def test_short_reference_resolves_only_current_users_session_config(service):
    field = "AbCd1234"
    short = build_session_placeholder("same-id", field)
    for user in ["alice", "bob"]:
        ctx = RequestContext(user=UserIdentifier("default", user), role=Role.USER)
        await service.initialize_user_directories(ctx)
        await service.privacy_configs.upsert(ctx, "session", "same-id", {field: f"{user}-private"})
        session = await service.sessions.create(ctx, "same-id")
        await session.add_message_async("user", [TextPart(short)])
        restored = json.loads(await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx))
        assert restored["parts"][0]["text"] == f"{user}-private"
    alice = RequestContext(user=UserIdentifier("default", "alice"), role=Role.USER)
    restored = await service.fs.read(
        "viking://user/alice/sessions/same-id/messages.jsonl", ctx=alice
    )
    assert json.loads(restored)["parts"][0]["text"] == "alice-private"
