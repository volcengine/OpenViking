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
from openviking.server.user_config import patch_user_config
from openviking_cli.exceptions import InvalidArgumentError, ResourceExhaustedError
from openviking_cli.session.user_id import UserIdentifier

SECRET = "AKLT" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
SECOND_SECRET = "ghp_" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


@pytest.fixture
async def auto_privacy(service, ctx):
    await patch_user_config(service.viking_fs, ctx, {"privacy": {"mode": "auto"}})


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
async def test_http_skips_discovery_but_protects_registered_values(
    client, service, ctx, monkeypatch, batch
):
    await patch_user_config(service.viking_fs, ctx, {"privacy": {"mode": "registered"}})
    sid = "registered-mode"
    session = await service.sessions.create(ctx, sid)
    service.sessions.set_tool_output_externalization_config(
        ToolOutputExternalizationConfig(enabled=True, threshold_chars=1024, preview_chars=200)
    )
    await service.privacy_configs.upsert(ctx, "credential", "github", {"pat": "registered-secret"})
    reference = build_privacy_field_reference(ctx.user.user_id, "credential", "github", "pat")
    insert = AsyncMock(wraps=service.privacy_configs.insert_values)
    monkeypatch.setattr(service.privacy_configs, "insert_values", insert)
    output = "padding\n" * 1500 + f"password={SECRET} registered-secret"
    message = {
        "role": "assistant",
        "parts": [
            {"type": "text", "text": f"password={SECRET} registered-secret"},
            {
                "type": "tool",
                "tool_id": "read",
                "tool_input": {
                    "password": SECRET,
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
    assert parts[0]["text"] == f"password={SECRET} {reference}"
    assert parts[1]["tool_input"] == {
        "password": SECRET,
        "credentials": {"api_key": reference, "token": 1234},
    }
    output_uri = parts[1]["tool_output_ref"] + "/output.txt"
    stored_output = await service.viking_fs.read_file(output_uri, ctx=ctx)
    assert stored_output == output.replace("registered-secret", reference)
    assert await service.fs.read(output_uri, ctx=ctx) == output


@pytest.mark.usefixtures("auto_privacy")
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
                    "content": f"password={SECRET} registered-secret repeated {SECRET}",
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
    assert SECRET not in disk and "registered-secret" not in disk
    assert reference in disk
    for raw in [False, True]:
        read = await client.get("/api/v1/content/read", params={"uri": uri, "raw": raw})
        assert read.status_code == 200, read.text
        public = read.json()["result"]
        assert (SECRET in public) == raw
        assert ("registered-secret" in public) == raw
        assert (reference in public) == (not raw)
        json.loads(public.splitlines()[1])  # Pending tool output remains valid JSON.
    internal = await client.get(f"/api/v1/sessions/{sid}/context")
    assert internal.status_code == 200, internal.text
    assert "registered-secret" not in internal.text and SECRET not in internal.text
    current = await service.privacy_configs.get_current(ctx, "session", sid)
    assert list(current.values.values()) == [SECRET]
    assert (await client.delete(f"/api/v1/sessions/{sid}")).status_code == 200
    assert (await client.get(f"/api/v1/privacy-configs/session/{sid}")).status_code == 200


@pytest.mark.usefixtures("auto_privacy")
async def test_business_values_do_not_pollute_later_reads(client, service, ctx):
    original = {
        "api_key_example": "sample",
        "command": r"TOKEN=$LOCAL_TOKEN\"; API_KEY=$(cat settings.env)",
        "type": "Optional[str]",
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
    assert await service.privacy_configs.get_current(ctx, "session", session.session_id) is None
    await session.add_message_async(
        "assistant", [TextPart("sample: https://sample.example.test; token Optional[str]")]
    )
    disk = await service.viking_fs.read_file(uri, ctx=ctx)
    assert (
        json.loads(disk.splitlines()[1])["parts"][0]["text"]
        == "sample: https://sample.example.test; token Optional[str]"
    )


@pytest.mark.usefixtures("auto_privacy")
async def test_batch_repeated_text_values_allow_later_messages(service, ctx):
    session = await service.sessions.create(ctx, "batch-repeat")
    texts = [
        f"password={SECRET} password={SECRET}",
        f"**password={SECRET}**",
        f"password={SECRET}",
    ]
    await session.add_messages_async(
        [
            {"role": "user", "parts": [TextPart(texts[0])]},
            {"role": "assistant", "parts": [TextPart(texts[1])]},
        ]
    )
    await session.add_message_async("user", [TextPart(texts[2])])
    current = await service.privacy_configs.get_current(ctx, "session", session.session_id)
    assert list(current.values.values()) == [SECRET]
    assert await service.privacy_configs.list_versions(ctx, "session", session.session_id) == [1]
    disk = await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx)
    assert SECRET not in disk
    restored = await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx)
    assert [json.loads(line)["parts"][0]["text"] for line in restored.splitlines()] == texts


@pytest.mark.parametrize("sid", ["markdown-restatement", "中文会话"])
@pytest.mark.usefixtures("auto_privacy")
async def test_markdown_restatement_reuses_references_without_new_versions(service, ctx, sid):
    session = await service.sessions.create(ctx, sid)
    user_text = f"API_KEY={SECRET}\nAuthorization: Bearer {SECOND_SECRET}"
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
        [TextPart(assistant_text), ToolPart(tool_input={"api_key": SECRET})],
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


@pytest.mark.usefixtures("auto_privacy")
async def test_batch_discovery_protects_all_payloads_without_mutating_inputs(service, ctx):
    await service.privacy_configs.upsert(ctx, "credential", "github", {"pat": "registered-value"})
    fresh = SECRET
    credentials = {"api_key": SECOND_SECRET, "items": [False, None, "中文"]}
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
    assert set(current.values.values()) == {SECRET, SECOND_SECRET}
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


@pytest.mark.usefixtures("auto_privacy")
async def test_batch_discovery_rejects_key_collision_before_message_write(
    service, ctx, monkeypatch
):

    fixed_id = "same0001"
    monkeypatch.setattr(session_extractor, "token_urlsafe", lambda _: fixed_id)
    session = await service.sessions.create(ctx, "collision")
    reference = build_session_placeholder(session.session_id, fixed_id)
    part = ToolPart(tool_input={SECRET: 1, reference: 2}, tool_output=f"password={SECRET}")
    before = deepcopy(part)
    externalize = AsyncMock()
    monkeypatch.setattr(session._tool_outputs, "externalize_group", externalize)
    with pytest.raises(InvalidArgumentError, match="object key collision"):
        await session.add_message_async("assistant", [part])
    externalize.assert_not_awaited()
    assert part == before
    assert await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx) == ""


@pytest.mark.usefixtures("auto_privacy")
async def test_externalized_output_restores_without_corrupting_metadata(service, ctx):
    session = await service.sessions.create(ctx, "externalized")
    session._tool_output_externalization_config = ToolOutputExternalizationConfig(
        enabled=True, threshold_chars=1024, preview_chars=200
    )
    output = "padding\n" * 1500 + f"password={SECRET}"
    await session.add_message_async("assistant", [ToolPart(tool_id="read", tool_output=output)])
    part = session.messages[0].parts[0]
    ref = part.tool_output_ref
    assert ref
    stored = await service.viking_fs.read_file(ref + "/output.txt", ctx=ctx)
    assert SECRET not in stored
    assert await service.fs.read(ref + "/output.txt", ctx=ctx) == output
    visible = await service.fs.read_visible(ref + "/output.txt", ctx=ctx)
    assert SECRET not in visible
    assert "{{ov_privacy:" in visible
    metadata = json.loads(await service.viking_fs.read_file(ref + "/metadata.json", ctx=ctx))
    assert metadata["session_id"] == session.session_id
    reloaded = await service.sessions.get(session.session_id, ctx)
    assert SECRET not in json.dumps([message.to_dict() for message in reloaded.messages])


@pytest.mark.usefixtures("auto_privacy")
async def test_auto_created_session_extracts_before_persistence(service, ctx):
    session = await service.sessions.get("auto-created", ctx, auto_create=True)
    await session.add_message_async("user", [TextPart(f"password={SECRET}")])
    uri = session.uri + "/messages.jsonl"
    disk = await service.viking_fs.read_file(uri, ctx=ctx)
    assert SECRET not in disk
    current = await service.privacy_configs.get_current(ctx, "session", session.session_id)
    assert list(current.values.values()) == [SECRET]
    assert SECRET in await service.fs.read(uri, ctx=ctx)


@pytest.mark.parametrize(
    "sid",
    ["中文会话", "x" * 300, "group:100%~"],
)
@pytest.mark.usefixtures("auto_privacy")
async def test_custom_session_ids_keep_discoveries_in_their_own_session(service, ctx, sid):
    session = await service.sessions.create(ctx, sid)
    await session.add_message_async("user", [TextPart(f"password={SECRET}")])
    target_key = session_privacy_target(sid)
    current = await service.privacy_configs.get_current(ctx, "session", target_key)
    [field] = current.values
    meta = await service.privacy_configs.get_meta(ctx, "session", target_key)
    assert meta.labels == {"session_id": sid}
    uri = session.uri + "/messages.jsonl"
    disk = await service.viking_fs.read_file(uri, ctx=ctx)
    assert SECRET not in disk
    assert build_session_placeholder(target_key, field) in disk
    restored = await service.fs.read(uri, ctx=ctx)
    assert json.loads(restored)["parts"][0]["text"] == f"password={SECRET}"


@pytest.mark.usefixtures("auto_privacy")
async def test_concurrent_writers_preserve_messages_and_independent_fields(
    service, ctx, monkeypatch
):

    ids = iter(field for i in range(8) for field in ("same0001", f"next{i:04d}"))
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
                "user", [TextPart(f"password={SECRET}{i}")]
            )
            for i in range(8)
        )
    )
    loaded = await service.sessions.get("concurrent", ctx)
    current = await service.privacy_configs.get_current(ctx, "session", "concurrent")
    assert set(current.values.values()) == {"old-secret", *(f"{SECRET}{i}" for i in range(8))}
    assert current.version == 9
    restored = await service.fs.read(loaded.uri + "/messages.jsonl", ctx=ctx)
    assert sorted(json.loads(line)["parts"][0]["text"] for line in restored.splitlines()) == [
        "old-secret",
        *(f"password={SECRET}{i}" for i in range(8)),
    ]


@pytest.mark.usefixtures("auto_privacy")
async def test_concurrent_same_values_reuse_fields_with_stale_worker_caches(
    service, ctx, monkeypatch
):
    from openviking.privacy.sanitizer import PrivacySanitizer
    from openviking.privacy.service import UserPrivacyConfigService

    monkeypatch.setenv("OPENVIKING_PRIVACY_AMBIGUOUS_STRICT", "1")
    await service.privacy_configs.upsert(ctx, "session", "other", {"other001": SECRET})
    await service.privacy_configs.upsert(ctx, "skill", "other", {"other001": SECRET})
    session = await service.sessions.create(ctx, "same-values")
    writers = []
    for _ in range(8):
        writer = service.sessions.session(ctx, "same-values")
        writer._privacy_config_service = UserPrivacyConfigService(service.viking_fs)
        writer._privacy_sanitizer = PrivacySanitizer(writer._privacy_config_service)
        await writer._privacy_sanitizer.prepare_matcher(ctx, scope=("session", "same-values"))
        writers.append(writer)
    text = f"正文🙂 `{SECRET}` repeated {SECRET}"
    await asyncio.gather(
        *(
            writer.add_message_async(
                "user",
                [
                    TextPart(text),
                    ToolPart(tool_input={"nested": [SECRET, SECOND_SECRET, False, 17]}),
                ],
            )
            for writer in writers
        )
    )
    current = await service.privacy_configs.get_current(ctx, "session", "same-values")
    assert len(current.values) == 2 and set(current.values.values()) == {SECRET, SECOND_SECRET}
    assert await service.privacy_configs.list_versions(ctx, "session", "same-values") == [1]
    stored = await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx)
    rows = [json.loads(line) for line in stored.splitlines()]
    assert len(rows) == 8
    assert len({row["parts"][0]["text"] for row in rows}) == 1
    assert SECRET not in stored and SECOND_SECRET not in stored
    restored = await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx)
    for line in restored.splitlines():
        parts = json.loads(line)["parts"]
        assert parts[0]["text"] == text
        assert parts[1]["tool_input"] == {"nested": [SECRET, SECOND_SECRET, False, 17]}


@pytest.mark.usefixtures("auto_privacy")
async def test_concurrent_skill_preparations_reuse_current_fields_and_preserve_references(
    service, ctx, monkeypatch
):
    from openviking.privacy.service import UserPrivacyConfigService
    from openviking.privacy.skill_placeholder import build_placeholder
    from openviking.privacy.skill_restore import restore_skill_content
    from openviking.utils.skill_processor import SkillProcessor

    monkeypatch.setenv("OPENVIKING_PRIVACY_AMBIGUOUS_STRICT", "1")
    await service.privacy_configs.upsert(ctx, "session", "other", {"other001": SECRET})
    await service.privacy_configs.upsert(ctx, "skill", "other", {"other001": SECRET})
    await service.privacy_configs.upsert(ctx, "skill", "same-values", {"kept0001": SECOND_SECRET})
    explicit = build_placeholder("same-values", "kept0001")
    body = f"正文🙂 `{SECRET}` repeated {SECRET}\nexplicit={explicit}"
    processors = [
        SkillProcessor(
            vikingdb=None, privacy_config_service=UserPrivacyConfigService(service.viking_fs)
        )
        for _ in range(8)
    ]
    skill = {"name": "same-values", "content": body}
    prepared = await asyncio.gather(
        *(processor.prepare_skill_privacy(skill, ctx) for processor in processors)
    )
    results = await asyncio.gather(
        *(
            processor.apply_skill_privacy(
                skill, preparation, ctx, change_reason="Concurrent discovery", delete_if_empty=False
            )
            for processor, preparation in zip(processors, prepared, strict=True)
        )
    )
    current = await service.privacy_configs.get_current(ctx, "skill", "same-values")
    assert len(current.values) == 2 and set(current.values.values()) == {SECRET, SECOND_SECRET}
    assert await service.privacy_configs.list_versions(ctx, "skill", "same-values") == [1, 2]
    for result in results:
        assert explicit in result["content"] and SECRET not in result["content"]
        assert await restore_skill_content(
            service.privacy_configs, result["content"], ctx, skill_name="same-values"
        ) == body.replace(explicit, SECOND_SECRET)


@pytest.mark.parametrize(
    "error", [ResourceExhaustedError, OSError], ids=["id-exhaustion", "storage"]
)
@pytest.mark.usefixtures("auto_privacy")
async def test_privacy_write_failure_rejects_batch_before_externalization(
    service, ctx, monkeypatch, error
):

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
        await session.add_message_async("user", [TextPart(f"password={SECRET}")])
    externalize.assert_not_awaited()
    assert await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx) == ""
    current = await service.privacy_configs.get_current(ctx, "session", session.session_id)
    assert current.values == {"taken001": "old-secret"}
    assert await service.privacy_configs.list_versions(ctx, "session", session.session_id) == [1]


@pytest.mark.usefixtures("auto_privacy")
async def test_values_equal_to_json_keys_do_not_change_message_identity(service, ctx):
    session = await service.sessions.create(ctx, "identity")
    original = {"password": "tool_input", "token": "assistant", "secret": "default"}
    await service.privacy_configs.upsert(ctx, "session", session.session_id, original)
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


@pytest.mark.usefixtures("auto_privacy")
async def test_later_credential_registration_preserves_session_reference_identity(
    client, service, ctx
):
    value = SECRET
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


@pytest.mark.usefixtures("auto_privacy")
async def test_cross_session_plaintext_creates_own_field_and_explicit_references_stay_bound(
    service, ctx
):
    source = await service.sessions.create(ctx, "source")
    original = f"password={SECRET}"
    await source.add_message_async("user", [TextPart(original)])
    current = await service.privacy_configs.get_current(ctx, "session", "source")
    [field] = current.values
    short = build_session_placeholder("source", field)
    full = build_privacy_field_reference(ctx.user.user_id, "session", "source", field)
    target = await service.sessions.create(ctx, "target")
    await target.add_message_async("user", [TextPart(f"{original} {short} {full}")])
    own = await service.privacy_configs.get_current(ctx, "session", "target")
    [own_field] = own.values
    own_reference = build_session_placeholder("target", own_field)
    assert target.messages[0].parts[0].text == f"password={own_reference} {short} {full}"
    raw = json.loads(await service.fs.read(target.uri + "/messages.jsonl", ctx=ctx))
    assert raw["parts"][0]["text"] == f"{original} {short} {SECRET}"


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


async def test_session_discovery_follows_persisted_mode_changes(client, service, ctx, monkeypatch):
    session = await service.sessions.create(ctx, "mode-changes")
    settings = AsyncMock(wraps=service.privacy_configs.get_settings)
    monkeypatch.setattr(service.privacy_configs, "get_settings", settings)
    third = "AKAP" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    fourth = "AKTP" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    steps = [
        ("off", SECRET, []),
        ("registered", SECOND_SECRET, []),
        ("auto", third, [third]),
        ("registered", fourth, [third]),
    ]
    values = [value for _, value, _ in steps]
    for mode, value, expected in steps:
        response = await client.patch("/api/v1/user-settings/privacy", json={"mode": mode})
        assert response.status_code == 200, response.text
        settings.reset_mock()
        await session.add_message_async("user", [TextPart(value)])
        settings.assert_awaited_once_with(ctx)
        current = await service.privacy_configs.get_current(ctx, "session", session.session_id)
        assert (list(current.values.values()) if current else []) == expected
    stored = await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=ctx)
    texts = [json.loads(line)["parts"][0]["text"] for line in stored.splitlines()]
    assert texts[0:2] == values[0:2]
    assert third not in stored and texts[3] == fourth
    assert await service.privacy_configs.list_versions(ctx, "session", session.session_id) == [1]
    restored = await service.fs.read(session.uri + "/messages.jsonl", ctx=ctx)
    assert [json.loads(line)["parts"][0]["text"] for line in restored.splitlines()] == values
