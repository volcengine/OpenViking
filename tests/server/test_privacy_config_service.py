# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core.namespace import canonical_user_root
from openviking.privacy.reference import build_privacy_field_reference
from openviking.privacy.service import UserPrivacyConfigService
from openviking.privacy.session_extractor import build_session_placeholder
from openviking.privacy.skill_extractor import (
    extract_skill_privacy_values,
)
from openviking.privacy.skill_placeholder import placeholderize_skill_content_with_blocks
from openviking.privacy.validation import PRIVACY_MAX_FIELDS, SESSION_PRIVACY_MAX_FIELDS
from openviking.server.config import UserSettingsPatch
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import AlreadyExistsError, InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier

SECRET = (
    "sk-proj-"
    + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    + "T3BlbkFJ"
    + "xY7zA9bC2dE4fG6hJ8kL0mN1pQ3sT5vW"
)


class _MemoryPrivacyStorage:
    def __init__(self):
        self.files: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._async_agfs = self
        self.lock_timeouts = []

    def _uri_to_path(self, uri, ctx=None):
        return uri

    async def pathlock_acquire_tree(self, _path, timeout_secs=0.0, owner_lease_ref=None):
        if owner_lease_ref is not None:
            return {**owner_lease_ref, "child": True}
        self.lock_timeouts.append(timeout_secs)
        if timeout_secs == 0.0:
            acquired = self._lock.locked() is False and await self._lock.acquire()
        else:
            try:
                await asyncio.wait_for(self._lock.acquire(), timeout_secs)
                acquired = True
            except asyncio.TimeoutError:
                acquired = False
        if not acquired:
            raise RuntimeError("lock acquire timed out")
        return {"lease_ref": "test"}

    async def pathlock_release(self, lease):
        if not lease.get("child"):
            self._lock.release()

    async def mkdir(self, *_args, **_kwargs):
        await asyncio.sleep(0)

    async def read_file(self, uri, **_kwargs):
        await asyncio.sleep(0)
        if uri not in self.files:
            raise NotFoundError(uri, "file")
        return self.files[uri]

    async def write_file(self, uri, content, **_kwargs):
        await asyncio.sleep(0)
        self.files[uri] = content

    async def ls(self, uri, **_kwargs):
        await asyncio.sleep(0)
        prefix = f"{uri}/"
        return [
            {"name": path.removeprefix(prefix)}
            for path in self.files
            if path.startswith(prefix) and "/" not in path.removeprefix(prefix)
        ]


@pytest.mark.asyncio
async def test_patch_values_preserves_snapshot_history():
    privacy = UserPrivacyConfigService(_MemoryPrivacyStorage())
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_merge"), role=Role.ROOT)
    for added, expected, version in [
        ({"one": "secret-1"}, {"one": "secret-1"}, 1),
        ({"one": "secret-1"}, {"one": "secret-1"}, 1),
        ({"two": "secret-2"}, {"one": "secret-1", "two": "secret-2"}, 2),
    ]:
        result = await privacy.patch_values(ctx, "session", "demo", added)
        assert result.values == expected
        assert result.version == version

    await privacy.activate_version(ctx, "session", "demo", 1)
    result = await privacy.patch_values(ctx, "session", "demo", {"three": "secret-3"})
    assert result.version == 3
    assert result.values == {"one": "secret-1", "three": "secret-3"}
    assert await privacy.list_versions(ctx, "session", "demo") == [1, 2, 3]


@pytest.mark.asyncio
async def test_privacy_config_service_versions(service):
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_user"), role=Role.ROOT)
    await service.initialize_user_directories(ctx)

    privacy = service.privacy_configs
    first = await privacy.upsert(
        ctx=ctx,
        category="skill",
        target_key="demo-skill",
        values={"api_key": "secret-1"},
        updated_by=ctx.user.user_id,
    )
    same = await privacy.upsert(
        ctx=ctx,
        category="skill",
        target_key="demo-skill",
        values={"api_key": "secret-1"},
        updated_by=ctx.user.user_id,
    )
    second = await privacy.upsert(
        ctx=ctx,
        category="skill",
        target_key="demo-skill",
        values={"api_key": "secret-2"},
        updated_by=ctx.user.user_id,
    )

    assert first.version == 1
    assert same.version == 1
    assert second.version == 2
    assert await privacy.list_versions(ctx, "skill", "demo-skill") == [1, 2]

    restored = await privacy.activate_version(
        ctx, "skill", "demo-skill", 1, updated_by=ctx.user.user_id
    )
    current = await privacy.get_current(ctx, "skill", "demo-skill")
    assert restored.version == 1
    assert current.version == 1
    assert current.values["api_key"] == "secret-1"


@pytest.mark.asyncio
async def test_privacy_config_concurrent_updates_keep_every_version():
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_race"), role=Role.ROOT)
    privacy = UserPrivacyConfigService(_MemoryPrivacyStorage())
    results = await asyncio.gather(
        *(
            privacy.upsert(
                ctx=ctx,
                category="skill",
                target_key="concurrent-skill",
                values={"api_key": f"secret-{index}"},
                updated_by=ctx.user.user_id,
            )
            for index in range(8)
        )
    )

    assert sorted(result.version for result in results) == list(range(1, 9))
    assert await privacy.list_versions(ctx, "skill", "concurrent-skill") == list(range(1, 9))


@pytest.mark.asyncio
async def test_privacy_config_concurrent_patches_do_not_lose_distinct_fields():
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_patch"), role=Role.ROOT)
    privacy = UserPrivacyConfigService(_MemoryPrivacyStorage())
    results = await asyncio.gather(
        *(
            privacy.patch_values(
                ctx=ctx,
                category="credential",
                target_key="github",
                updates={f"key_{index}": f"value-{index}"},
                updated_by=ctx.user.user_id,
            )
            for index in range(8)
        )
    )

    assert sorted(result.version for result in results) == list(range(1, 9))
    current = await privacy.get_current(ctx, "credential", "github")
    assert current is not None
    assert current.values == {f"key_{index}": f"value-{index}" for index in range(8)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category,limit", [("credential", PRIVACY_MAX_FIELDS), ("session", SESSION_PRIVACY_MAX_FIELDS)]
)
async def test_privacy_config_patch_validates_final_snapshot_and_preserves_current_on_failure(
    category, limit
):
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_limit"), role=Role.ROOT)
    privacy = UserPrivacyConfigService(_MemoryPrivacyStorage())
    original = {f"key_{index}": index for index in range(limit)}
    await privacy.upsert(ctx, category, "full", original, updated_by=ctx.user.user_id)

    with pytest.raises(InvalidArgumentError, match=f"at most {limit}"):
        await privacy.patch_values(
            ctx,
            category,
            "full",
            {"one_more": "value"},
            updated_by=ctx.user.user_id,
        )

    current = await privacy.get_current(ctx, category, "full")
    assert current is not None
    assert current.version == 1
    assert current.values == original


@pytest.mark.asyncio
async def test_privacy_config_exists_propagates_storage_errors():
    class FailingStorage:
        async def stat(self, *_args, **_kwargs):
            raise RuntimeError("storage unavailable")

    privacy = UserPrivacyConfigService(FailingStorage())
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_error"), role=Role.ROOT)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await privacy.exists(ctx, "skill", "demo-skill")


@pytest.mark.asyncio
@pytest.mark.parametrize("read_method", ["read_file", "read_file_bytes"])
async def test_viking_fs_read_propagates_storage_errors(read_method):
    class FailingReadStorage:
        def stat(self, _path, **_kwargs):
            return {"isDir": False}

        def read(self, _path, **_kwargs):
            raise RuntimeError("storage unavailable")

    fs = VikingFS(FailingReadStorage())
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_read_error"), role=Role.ROOT)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await getattr(fs, read_method)("viking://user/privacy/meta.json", ctx=ctx)


@pytest.mark.asyncio
async def test_skill_read_restores_placeholder(service, monkeypatch):
    name = "restore-skill-agent-segment"
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_restore"), role=Role.ROOT)
    await service.initialize_user_directories(ctx)
    from openviking.server.user_config import patch_user_config

    await patch_user_config(
        service.viking_fs, ctx, UserSettingsPatch.model_validate({"privacy": {"mode": "auto"}})
    )
    resolver = SimpleNamespace(
        get_vlm=AsyncMock(side_effect=AssertionError("Privacy must not resolve VLM"))
    )
    monkeypatch.setattr(service._skill_processor, "_vlm_resolver", resolver)
    content = f'api_key: "{SECRET}"\nbase_url: "https://example.com"\n'
    await service.resources.add_skill(
        data={"name": name, "description": "skill with secret", "content": content},
        ctx=ctx,
        wait=False,
    )
    skill_file_uri = f"{canonical_user_root(ctx)}/skills/{name}/SKILL.md"
    stored = await service.viking_fs.read_file(skill_file_uri, ctx=ctx)
    restored = await service.fs.read(skill_file_uri, ctx=ctx)
    assert SECRET not in stored
    assert "https://example.com" in stored
    assert content in restored
    current = await service.privacy_configs.get_current(ctx, "skill", name)
    [field] = current.values
    assert current.values == {field: SECRET}
    assert f"{{{{ov_privacy:skill:{name}:{field}}}}}" in stored
    resolver.get_vlm.assert_not_awaited()


@pytest.mark.asyncio
async def test_skill_default_read_preserves_references_and_raw_restores_credentials(
    client, service, monkeypatch
):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await service.initialize_user_directories(ctx)
    await service.privacy_configs.upsert(ctx, "skill", "read-boundary", {"token": "pin"})
    await service.privacy_configs.upsert(ctx, "credential", "managed", {"key": 'quote"secret'})
    reference = build_privacy_field_reference(ctx.user.user_id, "credential", "managed", "key")
    local = build_privacy_field_reference(ctx.user.user_id, "skill", "read-boundary", "token")
    content = (
        f"token={{{{ov_privacy:skill:read-boundary:token}}}}\nlocal={local}\nmanaged={reference}\n"
    )
    root = f"{canonical_user_root(ctx)}/skills/read-boundary"
    await service.viking_fs.mkdir(root, exist_ok=True, ctx=ctx)
    uri = root + "/SKILL.md"
    await service.viking_fs.write_file(uri, content, ctx=ctx)
    visible = await client.get("/api/v1/content/read", params={"uri": uri})
    assert visible.status_code == 200, visible.text
    assert visible.json()["result"] == content
    read = AsyncMock(wraps=service.privacy_configs.get_current)
    monkeypatch.setattr(service.privacy_configs, "get_current", read)
    raw = await client.get("/api/v1/content/read", params={"uri": uri, "raw": True})
    assert raw.status_code == 200, raw.text
    assert raw.json()["result"] == 'token=pin\nlocal=pin\nmanaged=quote"secret\n'
    assert read.await_count == 2


@pytest.mark.parametrize("entrypoint", ["skill", "session"])
async def test_read_preserves_empty_privacy_references(client, service, entrypoint):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await service.initialize_user_directories(ctx)
    values = {"empty000": "", "null0000": None, "false000": False, "zero0000": 0}
    references, expected = [], []
    for category in ["skill", "credential", "session"]:
        await service.privacy_configs.upsert(ctx, category, "empty-values", values)
        placeholders = [
            build_privacy_field_reference(ctx.user.user_id, category, "empty-values", field)
            for field in values
        ]
        references.extend(placeholders)
        expected.extend([*placeholders[:2], False, 0])
    if entrypoint == "session":
        placeholders = [build_session_placeholder("empty-values", field) for field in values]
        references.extend(placeholders)
        expected.extend([*placeholders[:2], False, 0])
    text = "\n".join(references)
    expected_text = "\n".join(map(str, expected))
    if entrypoint == "skill":
        root = f"{canonical_user_root(ctx)}/skills/empty-values"
        await service.viking_fs.mkdir(root, exist_ok=True, ctx=ctx)
        uri, content = root + "/SKILL.md", text
    else:
        session = await service.sessions.create(ctx, "empty-values")
        uri = session.uri + "/messages.jsonl"
        content = json.dumps(
            {
                "parts": [
                    {"type": "text", "text": text},
                    {"type": "tool", "tool_input": {"rows": references}},
                ]
            }
        )
    await service.viking_fs.write_file(uri, content, ctx=ctx)
    response = await client.get("/api/v1/content/read", params={"uri": uri, "raw": True})
    assert response.status_code == 200, response.text
    restored = response.json()["result"]
    if entrypoint == "skill":
        assert restored == expected_text
    else:
        parts = json.loads(restored)["parts"]
        assert parts[0]["text"] == expected_text
        assert json.dumps(parts[1]["tool_input"]["rows"]) == json.dumps(expected)


@pytest.mark.asyncio
async def test_skill_filters_vlm_candidates_before_matching():
    from openviking.privacy import SkillPrivacyExtractionResult

    values = {
        "api_key": SECRET,
        "password": "staging",
        "base_url": "https://example.com",
        "token": "sample",
        "count": 1234,
        "credentials": {"api_key": SECRET},
        "empty": None,
    }
    content = json.dumps(values)
    result = await extract_skill_privacy_values(
        skill_name="extract-block-skill",
        skill_description="mixed credentials and business values",
        content=content,
        vlm=type(
            "VLM",
            (),
            {"get_completion_async": AsyncMock(return_value=json.dumps({"values": values}))},
        )(),
    )
    assert isinstance(result, SkillPrivacyExtractionResult)
    assert result.values == {"api_key": SECRET}
    placeholder = "{{ov_privacy:skill:extract-block-skill:api_key}}"
    assert result.sanitized_content == content.replace(SECRET, placeholder)


@pytest.mark.asyncio
async def test_skill_read_appends_notice_for_unreplaced_placeholders(service):
    ctx = RequestContext(
        user=UserIdentifier.the_default_user("privacy_partial_restore"), role=Role.ROOT
    )
    await service.initialize_user_directories(ctx)

    skill_uri = f"{canonical_user_root(ctx)}/skills/partial-restore-skill"
    await service.viking_fs.mkdir(skill_uri, exist_ok=True, ctx=ctx)
    await service.viking_fs.write_file(
        f"{skill_uri}/SKILL.md",
        (
            'api_key: "{{ov_privacy:skill:partial-restore-skill:api_key}}"\n'
            'region: "{{ov_privacy:skill:partial-restore-skill:region}}"\n'
        ),
        ctx=ctx,
    )

    await service.privacy_configs.upsert(
        ctx=ctx,
        category="skill",
        target_key="partial-restore-skill",
        values={"api_key": "secret-xyz", "environment": "cn"},
        updated_by=ctx.user.user_id,
    )

    restored = await service.fs.read(f"{skill_uri}/SKILL.md", ctx=ctx)

    assert 'api_key: "secret-xyz"' in restored
    assert "{{ov_privacy:skill:partial-restore-skill:region}}" in restored
    assert "[Privacy Config Notice]" in restored
    assert "Missing config: region=<missing>" in restored
    assert "Configured but not referenced in content: environment=cn" in restored


@pytest.mark.asyncio
async def test_privacy_config_service_uses_user_root(service):
    contexts = [
        RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER),
        RequestContext(user=UserIdentifier("acme", "bob"), role=Role.USER),
        RequestContext(user=UserIdentifier("other-account", "alice"), role=Role.USER),
    ]
    values = [{"api_key": f"synthetic-value-{index}"} for index in range(len(contexts))]
    privacy = service.privacy_configs
    for ctx, value in zip(contexts, values, strict=True):
        await service.initialize_user_directories(ctx)
        created = await privacy.upsert(
            ctx, "skill", "same-skill", value, updated_by=ctx.user.user_id
        )
        assert created.version == 1

    for ctx, value in zip(contexts, values, strict=True):
        current = await privacy.get_current(ctx, "skill", "same-skill")
        assert current is not None
        assert current.values == value


def test_placeholderization_only_replaces_structured_values():
    content = (
        "region: cn\r\n"
        "notes: region is cn in docs\r\n"
        "token:\ta.b+c?[x]  \r\n"
        "text: a.b+c?[x] should stay here\r\n"
        'note: use "secret" in prose\n'
        'api_key: "secret"\n'
        "backup=secret"
    )
    values = {"region": "cn", "token": "a.b+c?[x]", "api_key": "secret"}
    result = placeholderize_skill_content_with_blocks(content, "skill-example", values)

    assert result.replaced_values == values
    assert result.sanitized_content == (
        "region: {{ov_privacy:skill:skill-example:region}}\r\n"
        "notes: region is cn in docs\r\n"
        "token:\t{{ov_privacy:skill:skill-example:token}}  \r\n"
        "text: a.b+c?[x] should stay here\r\n"
        'note: use "{{ov_privacy:skill:skill-example:api_key}}" in prose\n'
        'api_key: "{{ov_privacy:skill:skill-example:api_key}}"\n'
        "backup={{ov_privacy:skill:skill-example:api_key}}"
    )


@pytest.mark.asyncio
async def test_patch_preserves_equal_fields_and_rotates_only_named_field():
    category = "credential"

    privacy = UserPrivacyConfigService(_MemoryPrivacyStorage())
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await privacy.upsert(ctx, category, "demo", {"a": "old-secret", "b": "new-secret"})
    current = await privacy.patch_values(
        ctx, category, "demo", {"a": "new-secret", "c": "new-secret"}
    )
    assert current.values == {"a": "new-secret", "b": "new-secret", "c": "new-secret"}
    rotated = await privacy.patch_values(ctx, category, "demo", {"a": "rotated-secret"})
    assert rotated.values == {"a": "rotated-secret", "b": "new-secret", "c": "new-secret"}
    previous = await privacy.get_version(ctx, category, "demo", current.version)
    assert previous.values == current.values


@pytest.mark.asyncio
async def test_insert_values_rejects_the_entire_batch_even_when_existing_value_is_equal():
    privacy = UserPrivacyConfigService(_MemoryPrivacyStorage())
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await privacy.upsert(ctx, "session", "demo", {"taken001": "old-secret"})
    with pytest.raises(AlreadyExistsError):
        await privacy.insert_values(
            ctx,
            "session",
            "demo",
            updates={"taken001": "old-secret", "free0002": "new-secret"},
        )
    current = await privacy.get_current(ctx, "session", "demo")
    assert current.values == {"taken001": "old-secret"}
    assert await privacy.list_versions(ctx, "session", "demo") == [1]


def _token(seed: str) -> str:
    # Assembled at runtime; a synthetic GitHub-shaped token for detector tests.
    return "ghp_" + hashlib.sha256(seed.encode()).hexdigest()[:36]


async def _user_ctx(service, user_id: str) -> RequestContext:
    ctx = RequestContext(user=UserIdentifier("acme", user_id), role=Role.USER)
    await service.initialize_user_directories(ctx)
    return ctx


@pytest.mark.asyncio
async def test_credential_revision_is_persisted_and_changes_on_every_credential_write(service):
    privacy = service.privacy_configs
    ctx = await _user_ctx(service, "revision-alice")
    other = await _user_ctx(service, "revision-bob")
    revisions = [await privacy.credential_revision(ctx)]
    assert revisions[0] == await privacy.credential_revision(ctx)
    assert revisions[0] != await privacy.credential_revision(other)

    async def changed() -> None:
        revision = await privacy.credential_revision(ctx)
        assert revision not in revisions
        revisions.append(revision)

    await privacy.upsert(ctx, "credential", "svc", {"a": "secret-a1", "b": "secret-b1"})
    await changed()
    await privacy.patch_values(ctx, "credential", "svc", {"a": "secret-a2"})
    await changed()
    await privacy.insert_values(ctx, "credential", "svc", {"c": "secret-c1"})
    await changed()
    await privacy.delete_field(ctx, "credential", "svc", "c")
    await changed()
    await privacy.activate_version(ctx, "credential", "svc", 1)
    await changed()
    await privacy.delete(ctx, "credential", "svc")
    await changed()

    # Automatic entries are sealed too, so their writes change it as well.
    await privacy.insert_values(ctx, "auto", "github-rev001", {"token": "auto-1"})
    await changed()
    await privacy.delete(ctx, "auto", "github-rev001")
    await changed()

    # Writes that cannot change Credential matching keep the revision.
    await privacy.upsert(ctx, "skill", "demo", {"api_key": "skill-secret-1"})
    await privacy.upsert(ctx, "credential", "same", {"a": "same-secret-1"})
    stable = await privacy.credential_revision(ctx)
    await privacy.upsert(ctx, "credential", "same", {"a": "same-secret-1"})
    assert await privacy.credential_revision(ctx) == stable

    # A second service over the same storage (another worker) shares it.
    assert await UserPrivacyConfigService(service.viking_fs).credential_revision(ctx) == stable


@pytest.mark.asyncio
async def test_second_service_instance_sees_new_revision_and_value_in_seal(service):
    from openviking.privacy.sanitizer import PrivacySanitizer
    from openviking.privacy.seal import SealText, seal_texts

    ctx = await _user_ctx(service, "revision-workers")
    first = UserPrivacyConfigService(service.viking_fs)
    second = UserPrivacyConfigService(service.viking_fs)
    second_sanitizer = PrivacySanitizer(second)
    texts = [SealText("value worker-value-one and worker-value-two")]

    await first.upsert(ctx, "credential", "one", {"token": "worker-value-one"})
    before = await seal_texts(second, second_sanitizer, ctx, texts)
    assert [item["start"] for item in before["texts"][0]["replaced"]] == [6]

    await first.upsert(ctx, "credential", "two", {"token": "worker-value-two"})
    after = await seal_texts(second, second_sanitizer, ctx, texts)
    assert after["revision"] != before["revision"]
    assert len(after["texts"][0]["replaced"]) == 2


@pytest.mark.asyncio
async def test_concurrent_enrollment_from_two_service_instances_creates_one_entry(service):
    from openviking.privacy.seal import SealText, seal_texts

    ctx = await _user_ctx(service, "enroll-race")
    token = _token("Rk7Wq2Zp9Lm4")
    instances = [UserPrivacyConfigService(service.viking_fs) for _ in range(2)]
    results = await asyncio.gather(
        *(
            seal_texts(instance, None, ctx, [SealText(f"token {token}", enroll=True)])
            for instance in instances
            for _ in range(3)
        )
    )

    references = {result["texts"][0]["replaced"][0]["reference"] for result in results}
    assert len(references) == 1
    assert sum(result["texts"][0]["replaced"][0]["created"] for result in results) == 1
    assert await service.privacy_configs.list_targets(ctx, "credential") == []
    targets = await service.privacy_configs.list_targets(ctx, "auto")
    assert len(targets) == 1 and targets[0].startswith("github-")
    meta = await service.privacy_configs.get_meta(ctx, "auto", targets[0])
    assert meta.labels == {"ov.rule": "github"}


async def _enroll(privacy, ctx, target_key: str, value: str) -> str:
    """Store a value as the seal endpoint does and return its reference."""
    await privacy.insert_values(
        ctx,
        "auto",
        target_key,
        {"token": value},
        labels={"ov.rule": "github"},
    )
    return build_privacy_field_reference(ctx.user.user_id, "auto", target_key, "token")


@pytest.mark.asyncio
async def test_reference_value_renames_and_rotates_credentials(service):
    privacy = service.privacy_configs
    ctx = await _user_ctx(service, "reference-alice")
    owner = ctx.user.user_id

    reference = await _enroll(privacy, ctx, "github-abc123", "enrolled-secret-1")
    renamed = await privacy.upsert(ctx, "credential", "github-work", {"pat": reference})
    assert renamed.values == {"pat": "enrolled-secret-1"}
    assert not await privacy.exists(ctx, "auto", "github-abc123")

    # Rotation: an existing target gets a new version holding the referenced value.
    rotated = await privacy.patch_values(
        ctx,
        "credential",
        "github-work",
        {"pat": await _enroll(privacy, ctx, "github-def456", "enrolled-secret-2")},
    )
    assert rotated.version == 2
    assert rotated.values == {"pat": "enrolled-secret-2"}
    assert not await privacy.exists(ctx, "auto", "github-def456")
    assert await privacy.list_targets(ctx, "auto") == []

    # A Credential source is kept, whatever its labels say.
    await privacy.upsert(
        ctx, "credential", "manual", {"token": "manual-secret-1"}, labels={"ov.source": "x"}
    )
    patched = await privacy.patch_values(
        ctx,
        "credential",
        "github-work",
        {"backup": build_privacy_field_reference(owner, "credential", "manual", "token")},
    )
    assert patched.values["backup"] == "manual-secret-1"
    assert (await privacy.get_current(ctx, "credential", "manual")).values == {
        "token": "manual-secret-1"
    }

    # Another category may be the destination too; the auto source is still removed.
    skill = await privacy.upsert(
        ctx, "skill", "demo", {"api_key": await _enroll(privacy, ctx, "github-ghi789", "skill-1")}
    )
    assert skill.values == {"api_key": "skill-1"}
    assert not await privacy.exists(ctx, "auto", "github-ghi789")


@pytest.mark.asyncio
async def test_only_enrollment_writes_into_the_auto_category(service):
    privacy = service.privacy_configs
    ctx = await _user_ctx(service, "reference-auto-destination")
    source = await _enroll(privacy, ctx, "github-src001", "auto-secret-1")
    await privacy.upsert(ctx, "credential", "named", {"token": "named-secret-1"})
    named = build_privacy_field_reference(ctx.user.user_id, "credential", "named", "token")
    for value in ("plain-secret-1", source, named):
        for write in (privacy.upsert, privacy.patch_values):
            for target_key in ("github-src001", "fresh"):
                with pytest.raises(InvalidArgumentError) as error:
                    await write(ctx, "auto", target_key, {"token": value})
                assert error.value.details["reason"] == "automatic_category"
    assert await privacy.list_targets(ctx, "auto") == ["github-src001"]
    assert (await privacy.get_current(ctx, "auto", "github-src001")).values == {
        "token": "auto-secret-1"
    }
    # Version and field management still work on an automatic entry.
    assert await privacy.list_versions(ctx, "auto", "github-src001") == [1]
    assert (await privacy.activate_version(ctx, "auto", "github-src001", 1)).version == 1
    deleted = await privacy.delete_field(ctx, "auto", "github-src001", "token")
    assert deleted is not None and deleted.target_deleted


@pytest.mark.asyncio
async def test_reference_value_cleanup_failure_keeps_write(service, monkeypatch):
    privacy = service.privacy_configs
    ctx = await _user_ctx(service, "reference-cleanup")
    reference = await _enroll(privacy, ctx, "github-tmp001", "enrolled-secret-3")
    monkeypatch.setattr(privacy, "delete", AsyncMock(side_effect=RuntimeError("storage down")))
    result = await privacy.upsert(ctx, "credential", "github", {"pat": reference})
    assert result.values == {"pat": "enrolled-secret-3"}


@pytest.mark.asyncio
async def test_reference_value_rejects_every_other_reference_form(service):
    privacy = service.privacy_configs
    ctx = await _user_ctx(service, "reference-reject")
    owner = ctx.user.user_id
    await privacy.upsert(ctx, "credential", "source", {"token": "source-secret-1"})
    await privacy.upsert(ctx, "skill", "demo", {"api_key": "skill-secret-1"})
    valid = build_privacy_field_reference(owner, "credential", "source", "token")
    invalid_values = [
        build_privacy_field_reference(owner, "skill", "demo", "api_key"),
        build_privacy_field_reference("someone-else", "credential", "source", "token"),
        build_privacy_field_reference("someone-else", "auto", "source", "token"),
        build_privacy_field_reference(owner, "credential", "missing", "token"),
        build_privacy_field_reference(owner, "auto", "missing", "token"),
        build_privacy_field_reference(owner, "credential", "source", "missing"),
        f"Bearer {valid}",
        "{{ov_privacy:session:demo:abcdefgh}}",
    ]
    for value in invalid_values:
        for write in (privacy.upsert, privacy.patch_values):
            with pytest.raises(InvalidArgumentError):
                await write(ctx, "credential", "target", {"field": value})
    assert not await privacy.exists(ctx, "credential", "target")
    assert (await privacy.get_current(ctx, "credential", "source")).values == {
        "token": "source-secret-1"
    }


@pytest.mark.asyncio
async def test_value_only_patch_keeps_labels():
    privacy = UserPrivacyConfigService(_MemoryPrivacyStorage())
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_labels"), role=Role.ROOT)
    labels = {"description": "GitHub PAT", "ov.source": "manual"}
    await privacy.patch_values(ctx, "credential", "github", {"pat": "secret-1"}, labels=labels)
    await privacy.patch_values(ctx, "credential", "github", {"pat": "secret-2"})
    await privacy.patch_values(ctx, "credential", "github", {"pat": "secret-2"})
    assert (await privacy.get_meta(ctx, "credential", "github")).labels == labels
