# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for Admin API endpoints (openviking/server/routers/admin.py)."""

import asyncio
import hashlib
import json
import threading
import uuid
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
import yaml
from fastapi import FastAPI
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.api_keys import APIKeyManager
from openviking.server.app import create_app
from openviking.server.config import ServerConfig, UserConfig
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.server.user_config import (
    read_user_add_targets,
    read_user_config,
    user_config_backup_uri,
)
from openviking.service.core import OpenVikingService
from openviking.service.deletion import setup_deletion
from openviking.service.task_store import (
    SYSTEM_TASK_ACCOUNT_ID,
    SYSTEM_TASK_USER_ID,
)
from openviking.service.task_tracker import get_task_tracker
from openviking_cli.exceptions import OpenVikingError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import get_openviking_config


def _uid() -> str:
    return f"acme_{uuid.uuid4().hex[:8]}"


def _seed_secret(user_id: str, seed: str) -> str:
    return hashlib.sha256(f"{user_id}\0{seed}".encode("utf-8")).hexdigest()


ROOT_KEY = "admin-api-test-root-key-abcdef1234567890ab"


class _FakeAGFS:
    def __init__(self):
        self._files = {}
        self._dirs = {"/", "/local"}

    def read(self, path, **_kwargs):
        if path not in self._files:
            raise AGFSNotFoundError(path)
        return self._files[path]

    def write(self, path, content, **_kwargs):
        self.ensure_parent_dirs(path)
        self._files[path] = content

    def mv(self, old_path, new_path, **_kwargs):
        self._files[new_path] = self._files.pop(old_path)

    def rm(self, path, **_kwargs):
        self._files.pop(path, None)

    def mkdir(self, path, **_kwargs):
        self._dirs.add(path)

    def ensure_parent_dirs(self, path, **_kwargs):
        parent = path.rsplit("/", 1)[0]
        if not parent:
            return
        current = ""
        for part in [part for part in parent.strip("/").split("/") if part]:
            current = f"{current}/{part}" if current else f"/{part}"
            self._dirs.add(current)

    def pathlock_acquire_exact(self, ctx, path, timeout_secs=0.0, owner_lease_ref=None):
        return {"lease_ref": f"test:{path}"}

    def pathlock_acquire_exact_batch(self, ctx, paths, timeout_secs=0.0, owner_lease_ref=None):
        return self.pathlock_acquire_exact(ctx, paths[0], timeout_secs, owner_lease_ref)

    def pathlock_release(self, ctx, lease):
        return None


class _FakeVikingFS:
    def __init__(self):
        self.agfs = _FakeAGFS()
        self.files = {}
        self.writes = []

    async def read_file(self, uri, **_kwargs):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def _ensure_access(self, uri, ctx, *, action):
        return None

    async def write_file(self, uri, content, **_kwargs):
        self.writes.append(uri)
        self.files[uri] = content

    async def rm(self, uri, **_kwargs):
        self.files.pop(uri, None)


class _FakeService:
    def __init__(self):
        self.viking_fs = _FakeVikingFS()
        self.sessions = self

    async def get_agent_evolution_enabled(self, account_id):
        del account_id
        return True

    async def initialize_account_directories(self, ctx):
        return None

    async def initialize_user_directories(self, ctx):
        return None

    async def initialize_account_workspace(self, ctx):
        return None


def _build_lightweight_admin_test_app() -> FastAPI:
    from openviking.server.auth.plugins import ApiKeyAuthPlugin
    from openviking.server.auth.registry import get_registry
    from openviking.server.routers import admin as admin_router

    app = FastAPI()
    app.state.config = ServerConfig(root_api_key=ROOT_KEY)
    fake_service = _FakeService()
    app.state.fake_service = fake_service
    set_service(fake_service)

    @app.exception_handler(OpenVikingError)
    async def openviking_error_handler(request: FastAPIRequest, exc: OpenVikingError):
        http_status = ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500)
        return JSONResponse(
            status_code=http_status,
            content=Response(
                status="error",
                error=ErrorInfo(code=exc.code, message=exc.message, details=exc.details),
            ).model_dump(),
        )

    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=fake_service.viking_fs)
    app.state.api_key_manager = manager

    # Set auth plugin (lifespan not triggered in ASGI tests)
    registry = get_registry()
    if registry.get("api_key") is None:
        registry.register(ApiKeyAuthPlugin)
    app.state.auth_plugin = registry.get("api_key")()

    app.include_router(admin_router.router)
    return app


@pytest_asyncio.fixture(scope="function")
async def lightweight_admin_app(monkeypatch):
    app = _build_lightweight_admin_test_app()
    await app.state.api_key_manager.load()
    return app


@pytest_asyncio.fixture(scope="function")
async def lightweight_admin_client(lightweight_admin_app):
    transport = httpx.ASGITransport(app=lightweight_admin_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture(scope="function")
async def admin_service(service):
    """Use the shared service fixture with local fake models."""
    yield service


@pytest_asyncio.fixture(scope="function")
async def admin_app(admin_service):
    from openviking.server.auth.plugins import ApiKeyAuthPlugin
    from openviking.server.auth.registry import get_registry

    config = ServerConfig(root_api_key=ROOT_KEY)
    app = create_app(config=config, service=admin_service)
    set_service(admin_service)

    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=admin_service.viking_fs)
    await manager.load()
    app.state.api_key_manager = manager
    app.state.deletion_service = await setup_deletion(
        service=admin_service,
        manager=manager,
    )

    # Set auth plugin (lifespan not triggered in ASGI tests)
    registry = get_registry()
    if registry.get("api_key") is None:
        registry.register(ApiKeyAuthPlugin)
    app.state.auth_plugin = registry.get("api_key")()

    return app


@pytest_asyncio.fixture(scope="function")
async def admin_client(admin_app):
    transport = httpx.ASGITransport(app=admin_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


def root_headers():
    return {"X-API-Key": ROOT_KEY}


def trusted_headers(
    *,
    account: str,
    user: str,
    include_api_key: bool = False,
):
    headers = {
        "X-OpenViking-Account": account,
        "X-OpenViking-User": user,
    }
    if include_api_key:
        headers["X-API-Key"] = ROOT_KEY
    return headers


async def _agfs_exists(service: OpenVikingService, path: str) -> bool:
    try:
        await service.viking_fs._async_agfs.stat(path)
    except Exception:
        return False
    return True


async def _agfs_mkdirp(service: OpenVikingService, path: str) -> None:
    parts = [part for part in path.strip("/").split("/") if part]
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else f"/{part}"
        if await _agfs_exists(service, current):
            continue
        await service.viking_fs._async_agfs.mkdir(current)


async def _agfs_write(service: OpenVikingService, path: str, content: str) -> None:
    await _agfs_mkdirp(service, path.rsplit("/", 1)[0])
    await service.viking_fs._async_agfs.write(path, content.encode("utf-8"))


async def _agfs_read_text(service: OpenVikingService, path: str) -> str:
    raw = await service.viking_fs._async_agfs.read(path)
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    if hasattr(raw, "content"):
        return raw.content.decode("utf-8")
    return str(raw)


async def _wait_for_task(client: httpx.AsyncClient, task_id: str) -> dict:
    # Budget ~30s: recursive AGFS cleanup can take a couple of seconds, and the
    # original 1s budget was too tight on slower backends/interpreters.
    for _ in range(600):
        resp = await client.get(f"/api/v1/tasks/{task_id}", headers=root_headers())
        assert resp.status_code == 200
        task = resp.json()["result"]
        if task["status"] in {"completed", "failed"}:
            return task
        await asyncio.sleep(0.05)
    raise AssertionError(f"Task {task_id} did not finish")


# ---- Account CRUD ----


@pytest_asyncio.fixture
async def template_account(lightweight_admin_client):
    account_id = _uid()
    response = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": account_id, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    assert response.status_code == 200, response.text
    return account_id, {"X-API-Key": response.json()["result"]["user_key"]}


@pytest.mark.parametrize(
    "memory_type",
    ["profile", "preferences", "entities", "events", "soul", "identity"],
)
async def test_account_memory_templates_publish_and_reset(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    memory_type,
):
    client = lightweight_admin_client
    account_id, headers = template_account
    root = f"/api/v1/admin/accounts/{account_id}/memory-templates"
    path = account_memory_template_path(account_id, memory_type)
    fs = lightweight_admin_app.state.fake_service.viking_fs
    settings_path = f"/local/{account_id}/_system/setting.json"
    fs.agfs._files[settings_path] = b'{"agent_evolution":{"enabled":false}}'
    registry = get_default_registry()
    original = {s.memory_type: s.model_dump() for s in registry.list_all(True)}
    initial = await client.get(root, headers=headers)
    assert initial.status_code == 200, initial.text
    assert {item["memory_type"] for item in initial.json()["result"]["templates"]} == {
        "profile",
        "events",
        "preferences",
        "entities",
        "soul",
        "identity",
    }
    assert all(item["status"] == "system_default" for item in initial.json()["result"]["templates"])
    assert path not in fs.agfs._files

    editable_fields = {
        "profile": ("content",),
        "events": ("event_name", "summary"),
        "preferences": ("topic", "content"),
        "entities": ("category", "name", "content"),
        "soul": ("core_truths", "boundaries", "vibe", "continuity"),
        "identity": ("creature", "name", "vibe", "avatar", "emoji", "introduction"),
    }[memory_type]
    body = {
        "description": "Remember business facts in {{ language.lower() }}.",
        "fields": [
            {"name": name, "description": f"Business {name}: {{{{ language }}}}"}
            for name in editable_fields
        ],
    }
    if memory_type in {"events", "soul", "identity"}:
        variable = {"events": "summary", "soul": "core_truths", "identity": "introduction"}[
            memory_type
        ]
        body["content_template"] = "# Business\n{{ " + variable + " }}"
    publish = await client.put(f"{root}/{memory_type}", json=body, headers=headers)
    assert publish.status_code == 200, publish.text
    result = publish.json()["result"]
    assert result["status"] == "custom"
    assert result["updated_at"]
    assert all(result["effective"][key] == value for key, value in body.items() if key != "fields")
    assert result["effective"]["fields"][0]["type"] == "string"
    stored = yaml.safe_load(fs.agfs._files[path])
    assert stored.pop("_updated_at") == result["updated_at"]
    assert stored == result["effective"]
    assert stored["memory_type"] == memory_type
    assert "stage" in stored and "embedding_template" in stored
    assert fs.agfs._files[settings_path] == b'{"agent_evolution":{"enabled":false}}'
    assert (await client.get(f"{root}/{memory_type}", headers=headers)).json()["result"] == result
    roundtrip = await client.put(f"{root}/{memory_type}", json=result["effective"], headers=headers)
    assert roundtrip.status_code == 200, roundtrip.text
    assert roundtrip.json()["result"] == result
    listed = (await client.get(root, headers=headers)).json()["result"]["templates"]
    assert (
        next(item for item in listed if item["memory_type"] == memory_type)["effective"] == stored
    )
    resolved = await resolve_account_memory_registry(fs, account_id, registry)
    assert resolved is not registry
    assert all(resolved.get(schema.memory_type) is not schema for schema in registry.list_all(True))
    schema = resolved.get(memory_type)
    assert schema.description == body["description"]
    expected = dict(original[memory_type])
    expected["description"] = body["description"]
    if "content_template" in body:
        expected["content_template"] = body["content_template"]
    expected["fields"] = [
        {**field, "description": f"Business {field['name']}: {{{{ language }}}}"}
        if field["name"] in editable_fields
        else field
        for field in expected["fields"]
    ]
    assert schema.model_dump() == expected
    # All fields, including Events goal/ranges, survive a partial request.
    assert [field.name for field in schema.fields] == [
        field["name"] for field in original[memory_type]["fields"]
    ]
    assert {s.memory_type: s.model_dump() for s in registry.list_all(True)} == original

    noop = await client.put(f"{root}/{memory_type}", json=body, headers=headers)
    assert noop.json()["result"]["updated_at"] == result["updated_at"]
    replacement = await client.put(
        f"{root}/{memory_type}", json={"description": "Replacement"}, headers=headers
    )
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()["result"]["effective"]["fields"] == result["defaults"]["fields"]
    assert yaml.safe_load(fs.agfs._files[path + ".backup"])["description"] == body["description"]
    # An earlier extraction retains its complete snapshot across publication.
    assert resolved.get(memory_type).description == body["description"]
    reset = await client.delete(f"{root}/{memory_type}", headers=headers)
    assert reset.status_code == 200, reset.text
    assert reset.json()["result"]["effective"] == result["defaults"]
    assert reset.json()["result"]["status"] == "system_default"
    assert path not in fs.agfs._files
    restored = await resolve_account_memory_registry(fs, account_id, registry)
    assert {s.memory_type: s.model_dump() for s in restored.list_all(True)} == original
    assert (await client.delete(f"{root}/{memory_type}", headers=headers)).status_code == 200
    default_form = await client.put(
        f"{root}/{memory_type}", json=result["defaults"], headers=headers
    )
    assert default_form.status_code == 200, default_form.text
    assert default_form.json()["result"]["effective"] == result["defaults"]


@pytest.mark.parametrize(
    "body",
    [
        {"description": 123},
        {"description": "{{ language | upper }}"},
        {"description": "{{ cycler.__init__.__globals__.__builtins__.len('harmless') }}"},
        {"description": "literal {{ unclosed"},
        {"fields": [{"name": "summary", "description": "{{ summary }}"}]},
        {"fields": [{"name": "summary", "description": "{{ language | lower }}"}]},
        {"fields": [{"name": "summary", "description": "literal {{ unclosed"}]},
        {"_account_description": False},
        {"fields": [{"name": "summary", "_account_description": False}]},
        {"memory_type": "other"},
        {"fields": None},
        {"fields": [{}]},
        {"fields": [{"name": "x", "type": "not-a-type"}]},
        {"fields": [{"name": "x", "merge_op": "not-an-op"}]},
        {"fields": [{"name": "x"}, {"name": "x"}]},
        {"fields": [{"name": "x", "description": "{{"}]},
        {"content_template": "{{"},
        {"content_template": "{{ unknown_field }}"},
        {"content_template": "{{ extract_context.messages }}"},
        {"content_template": "{{ extract_context.get_year('0-999999999') }}"},
        {"content_template": "{% include 'private.yaml' %}"},
        {"content_template": "{{ summary | attr('__class__') }}"},
        {"content_template": "{{ summary | upper }}"},
        {"content_template": "{{ summary | default('pending') }}"},
        {"content_template": "{{ summary.strip('x') }}"},
        {"content_template": "x" * (64 * 1024 + 1)},
    ],
)
async def test_account_memory_templates_reject_invalid_configuration(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    body,
):
    account_id, headers = template_account
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/events"
    assert (
        await lightweight_admin_client.put(
            url, json={"description": "Keep current"}, headers=headers
        )
    ).status_code == 200
    fs = lightweight_admin_app.state.fake_service.viking_fs
    original = dict(fs.agfs._files)
    response = await lightweight_admin_client.put(url, json=body, headers=headers)
    assert response.status_code in {400, 422}, response.text
    assert fs.agfs._files == original


@pytest.mark.parametrize("field", ["description", "fields.content.description"])
@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("{{ language | upper }}", "unsupported_filter"),
        ("{{ cycler.__init__.__globals__.__builtins__.len('harmless') }}", "unsupported_call"),
        ("{{", "invalid_jinja"),
    ],
)
async def test_account_memory_templates_description_diagnostics_and_legacy_recovery(
    lightweight_admin_client, lightweight_admin_app, template_account, field, text, reason
):
    account_id, headers = template_account
    client = lightweight_admin_client
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/profile"
    fs = lightweight_admin_app.state.fake_service.viking_fs
    active = await client.put(url, json={"description": "Current"}, headers=headers)
    assert active.status_code == 200
    before = dict(fs.agfs._files)
    body = (
        {"description": text}
        if field == "description"
        else {"fields": [{"name": "content", "description": text}]}
    )
    response = await client.put(url, json=body, headers=headers)
    assert response.status_code == 400, response.text
    details = response.json()["error"]["details"]
    assert details["field"] == field
    assert details["reason"] == reason
    assert details["line"] == 1
    assert fs.agfs._files == before

    # An old well-formed YAML override may contain now-unsupported expressions.
    # Reading/resetting must stay possible, while extraction must reject it.
    path = account_memory_template_path(account_id, "profile")
    stored = yaml.safe_load(fs.agfs._files[path])
    target = stored if field == "description" else stored["fields"][0]
    target["description"] = text
    target["_account_description"] = False
    fs.agfs._files[path] = yaml.safe_dump(stored).encode()
    assert (await client.get(url, headers=headers)).status_code == 200
    from openviking_cli.exceptions import FailedPreconditionError

    with pytest.raises(FailedPreconditionError, match="republish or reset"):
        await resolve_account_memory_registry(fs, account_id, MemoryTypeRegistry())
    assert (await client.delete(url, headers=headers)).status_code == 200
    assert path not in fs.agfs._files


@pytest.mark.parametrize("memory_type", list(EDITABLE_MEMORY_TEMPLATE_FIELDS))
@pytest.mark.parametrize("character", ["a", "中", "😀"])
async def test_account_memory_templates_description_character_limit(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    memory_type,
    character,
):
    account_id, headers = template_account
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/{memory_type}"
    text = character * 50_000
    fs = lightweight_admin_app.state.fake_service.viking_fs
    # Check one field at a time without exceeding the aggregate 1 MiB cap.
    for field_name in [None, *EDITABLE_MEMORY_TEMPLATE_FIELDS[memory_type]]:
        body = {"description": text}
        target = body
        if field_name is not None:
            target = {"name": field_name, "description": text}
            body["fields"] = [target]
        response = await lightweight_admin_client.put(url, json=body, headers=headers)
        assert response.status_code == 200, response.text
        effective = response.json()["result"]["effective"]
        assert effective["description"] == text
        if field_name is not None:
            field = next(field for field in effective["fields"] if field["name"] == field_name)
            assert field["description"] == text
        original = dict(fs.agfs._files)
        target["description"] = text + character
        response = await lightweight_admin_client.put(url, json=body, headers=headers)
        assert response.status_code == 400, response.text
        assert "50000 Unicode characters" in response.json()["error"]["message"]
        assert fs.agfs._files == original
    assert (await lightweight_admin_client.get(url, headers=headers)).status_code == 200
    assert (await lightweight_admin_client.delete(url, headers=headers)).status_code == 200


async def test_account_memory_templates_content_validation_error_details(
    lightweight_admin_client,
    template_account,
):
    account_id, headers = template_account
    response = await lightweight_admin_client.put(
        f"/api/v1/admin/accounts/{account_id}/memory-templates/events",
        json={"content_template": "# Summary\n{{ typo }}"},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"] == {
        "field": "content_template",
        "reason": "unknown_variable",
        "line": 2,
    }


@pytest.mark.parametrize(
    "memory_type, field_name",
    [("events", "summary"), ("soul", "core_truths"), ("identity", "introduction")],
)
async def test_account_memory_templates_string_methods_reach_file_body(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    memory_type,
    field_name,
):
    from openviking.session.memory.dataclass import MemoryFile
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

    account_id, headers = template_account
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/{memory_type}"
    body = {"content_template": "# {{ " + field_name + ".strip().upper() }}"}
    response = await lightweight_admin_client.put(url, json=body, headers=headers)
    assert response.status_code == 200, response.text
    fs = lightweight_admin_app.state.fake_service.viking_fs
    snapshot = await resolve_account_memory_registry(fs, account_id, MemoryTypeRegistry())
    schema = snapshot.get(memory_type)
    assert schema._account_content_template is True
    rendered = MemoryFileUtils.write(
        MemoryFile(memory_type=memory_type, extra_fields={field_name: " business fact "}),
        content_template=schema.content_template,
        account_content_template_type=schema.memory_type,
    )
    assert MemoryFileUtils.read(rendered).content == "# BUSINESS FACT"


async def test_account_memory_templates_description_only_keeps_safe_deployment_methods(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    monkeypatch,
):
    account_id, headers = template_account
    defaults = MemoryTypeRegistry()
    defaults.get("events").content_template = "# {{ summary.strip().upper() }}"
    monkeypatch.setattr("openviking.server.routers.admin.get_default_registry", lambda: defaults)
    response = await lightweight_admin_client.put(
        f"/api/v1/admin/accounts/{account_id}/memory-templates/events",
        json={"description": "Capture release decisions"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["effective"]["content_template"] == (
        "# {{ summary.strip().upper() }}"
    )
    snapshot = await resolve_account_memory_registry(
        lightweight_admin_app.state.fake_service.viking_fs, account_id, defaults
    )
    assert snapshot.get("events")._account_content_template is False
    assert snapshot.get("events").content_template == "# {{ summary.strip().upper() }}"


@pytest.mark.parametrize(
    "memory_type, field_name",
    [("events", "summary"), ("soul", "core_truths"), ("identity", "introduction")],
)
@pytest.mark.parametrize("request_kind", ["description_only", "empty", "roundtrip"])
async def test_account_memory_templates_inherit_exact_deployment_body(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    monkeypatch,
    memory_type,
    field_name,
    request_kind,
):
    from openviking.session.memory.dataclass import MemoryFile
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

    account_id, headers = template_account
    defaults = MemoryTypeRegistry()
    # Deployment code may use methods/filters that custom Account bodies cannot.
    deployment_body = "# {{ " + field_name + ".replace('old', 'new') | upper }}"
    defaults.get(memory_type).content_template = deployment_body
    monkeypatch.setattr("openviking.server.routers.admin.get_default_registry", lambda: defaults)
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/{memory_type}"
    body = {"description": "Account instructions"} if request_kind == "description_only" else {}
    if request_kind == "roundtrip":
        response = await lightweight_admin_client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()["result"]["effective"]
        body["description"] = "Account instructions"
    response = await lightweight_admin_client.put(url, json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["status"] == "custom"
    assert response.json()["result"]["effective"]["content_template"] == deployment_body
    # Roundtrip a persisted override too, not only defaults returned before a PUT.
    response = await lightweight_admin_client.put(
        url, json=response.json()["result"]["effective"], headers=headers
    )
    assert response.status_code == 200, response.text
    fs = lightweight_admin_app.state.fake_service.viking_fs
    stored = yaml.safe_load(fs.agfs._files[account_memory_template_path(account_id, memory_type)])
    assert "_account_content_template" not in stored
    snapshot = await resolve_account_memory_registry(fs, account_id, defaults)
    schema = snapshot.get(memory_type)
    assert schema._account_content_template is False
    assert schema.content_template == deployment_body
    assert defaults.get(memory_type)._account_content_template is False
    rendered = MemoryFileUtils.write(
        MemoryFile(memory_type=memory_type, extra_fields={field_name: "old fact"}),
        content_template=schema.content_template,
        account_content_template_type=(
            schema.memory_type if schema._account_content_template else None
        ),
    )
    assert MemoryFileUtils.read(rendered).content == "# NEW FACT"


@pytest.mark.parametrize("alteration", ["whitespace", "different_body"])
async def test_account_memory_templates_recheck_persisted_body_without_trusting_flags(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    monkeypatch,
    alteration,
):
    from openviking_cli.exceptions import FailedPreconditionError

    account_id, headers = template_account
    defaults = MemoryTypeRegistry()
    deployment_body = "{{ summary | upper }}"
    defaults.get("events").content_template = deployment_body
    monkeypatch.setattr("openviking.server.routers.admin.get_default_registry", lambda: defaults)
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/events"
    assert (await lightweight_admin_client.put(url, json={}, headers=headers)).status_code == 200
    fs = lightweight_admin_app.state.fake_service.viking_fs
    path = account_memory_template_path(account_id, "events")
    original = fs.agfs._files[path]
    altered_body = deployment_body + (" " if alteration == "whitespace" else "changed")
    # Even a whitespace change is custom; equality is exact, not normalized.
    response = await lightweight_admin_client.put(
        url, json={"content_template": altered_body}, headers=headers
    )
    assert response.status_code == 400, response.text
    assert fs.agfs._files[path] == original
    corrupted = yaml.safe_load(original)
    corrupted["content_template"] = altered_body
    corrupted["_account_content_template"] = False
    corrupted["deployment_content_template"] = altered_body
    fs.agfs._files[path] = yaml.safe_dump(corrupted).encode()
    with pytest.raises(FailedPreconditionError):
        await resolve_account_memory_registry(fs, account_id, defaults)
    # Management reads remain available for recovery; no unsafe body is rendered.
    assert (await lightweight_admin_client.get(url, headers=headers)).status_code == 200


async def test_account_memory_templates_recheck_trust_after_deployment_change(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    monkeypatch,
):
    from openviking_cli.exceptions import FailedPreconditionError

    account_id, headers = template_account
    defaults = MemoryTypeRegistry()
    old_body = "{{ summary | upper }}"
    defaults.get("events").content_template = old_body
    monkeypatch.setattr("openviking.server.routers.admin.get_default_registry", lambda: defaults)
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/events"
    assert (await lightweight_admin_client.put(url, json={}, headers=headers)).status_code == 200
    fs = lightweight_admin_app.state.fake_service.viking_fs
    old_snapshot = await resolve_account_memory_registry(fs, account_id, defaults)
    defaults.get("events").content_template = "# {{ summary }}"
    with pytest.raises(FailedPreconditionError):
        await resolve_account_memory_registry(fs, account_id, defaults)
    # Already-started extractions keep their snapshot, not a mutable defaults view.
    assert old_snapshot.get("events").content_template == old_body
    assert old_snapshot.get("events")._account_content_template is False
    response = await lightweight_admin_client.put(
        url, json={"description": "Use current defaults"}, headers=headers
    )
    assert response.status_code == 200, response.text
    new_snapshot = await resolve_account_memory_registry(fs, account_id, defaults)
    assert new_snapshot.get("events").content_template == "# {{ summary }}"
    assert new_snapshot.get("events")._account_content_template is False


async def test_account_memory_templates_old_content_can_be_read_replaced_and_reset(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
):
    from openviking_cli.exceptions import FailedPreconditionError

    account_id, headers = template_account
    fs = lightweight_admin_app.state.fake_service.viking_fs
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/events"
    path = account_memory_template_path(account_id, "events")
    assert (await lightweight_admin_client.put(url, json={}, headers=headers)).status_code == 200
    legacy = yaml.safe_load(fs.agfs._files[path])
    legacy["content_template"] = "{{ summary | upper }}"
    raw = yaml.safe_dump(legacy).encode()
    for operation in ("PUT", "DELETE"):
        fs.agfs._files[path] = raw
        response = await lightweight_admin_client.get(url, headers=headers)
        assert response.status_code == 200
        assert (
            response.json()["result"]["effective"]["content_template"] == legacy["content_template"]
        )
        with pytest.raises(FailedPreconditionError):
            await resolve_account_memory_registry(fs, account_id, MemoryTypeRegistry())
        response = await lightweight_admin_client.request(
            operation,
            url,
            headers=headers,
            **(
                {"json": {"content_template": "{{ summary.upper() }}"}}
                if operation == "PUT"
                else {}
            ),
        )
        assert response.status_code == 200, response.text
        assert fs.agfs._files[path + ".backup"] == raw


@pytest.mark.parametrize(
    "memory_type", ["profile", "events", "preferences", "entities", "soul", "identity"]
)
async def test_account_memory_templates_locked_fields_cannot_be_modified(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    memory_type,
):
    client = lightweight_admin_client
    account_id, headers = template_account
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/{memory_type}"
    active = await client.put(url, json={"description": "Active policy"}, headers=headers)
    assert active.status_code == 200, active.text
    defaults = active.json()["result"]["defaults"]
    fs = lightweight_admin_app.state.fake_service.viking_fs
    original = dict(fs.agfs._files)
    editable = {"description", "fields"}
    if memory_type in {"events", "soul", "identity"}:
        editable.add("content_template")
    invalid = [
        {key: None if value is not None else "changed"}
        for key, value in defaults.items()
        if key not in editable
    ]
    invalid.extend(
        [
            {"unknown": "ignored?"},
            {"Description": "wrong key"},
            {"_updated_at": "forged"},
            {"_account_content_template": False},
            {"deployment_content_template": "forged"},
            {"enabled": 1},
            {"fields": [{"name": "renamed_field", "description": "changed"}]},
            {"description": None},
            {"description": " \n"},
            {"fields": [None]},
        ]
    )
    for field in defaults["fields"]:
        invalid.extend(
            {"fields": [{"name": field["name"], key: None if value is not None else "changed"}]}
            for key, value in field.items()
            if key not in {"name", "description"}
        )
        invalid.extend(
            [
                {"fields": [{"name": field["name"], "unknown": "ignored?"}]},
                {"fields": [{"name": field["name"], "description": ""}]},
                {"fields": [{"name": field["name"]}, {"name": field["name"]}]},
            ]
        )
    if memory_type == "events":
        invalid.extend(
            {"fields": [{"name": name, "description": "changed"}]} for name in ("goal", "ranges")
        )
    if memory_type in {"events", "soul", "identity"}:
        invalid.extend([{"content_template": None}, {"content_template": " "}])
    for body in invalid:
        response = await client.put(url, json=body, headers=headers)
        assert response.status_code == 400, (memory_type, body, response.text)
        assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
        assert fs.agfs._files == original
    # Partial field lists are updates by name, never a way to remove locked fields.
    response = await client.put(url, json={"fields": []}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["effective"]["fields"] == defaults["fields"]
    response = await client.put(
        url, json={"fields": list(reversed(defaults["fields"]))}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["effective"]["fields"] == defaults["fields"]


@pytest.mark.parametrize("memory_type", ["experiences", "cases", "trajectories"])
async def test_account_memory_templates_unexposed_types_rejected(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    memory_type,
):
    account_id, headers = template_account
    fs = lightweight_admin_app.state.fake_service.viking_fs
    original = dict(fs.agfs._files)
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/{memory_type}"
    for method in ("GET", "PUT", "DELETE"):
        response = await lightweight_admin_client.request(
            method,
            url,
            headers=headers,
            **({"json": {"description": "New"}} if method == "PUT" else {}),
        )
        assert response.status_code == 400, response.text
        assert fs.agfs._files == original


async def test_account_memory_templates_permissions_and_isolation(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
):
    client = lightweight_admin_client
    account_id, admin_headers = template_account
    other = _uid()
    created = await client.post(
        "/api/v1/admin/accounts",
        json={"account_id": other, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    assert created.status_code == 200
    user = await client.post(
        f"/api/v1/admin/accounts/{account_id}/users", json={"user_id": "bob"}, headers=admin_headers
    )
    assert user.status_code == 200
    user_headers = {"X-API-Key": user.json()["result"]["user_key"]}
    for method in ("GET", "PUT", "DELETE"):
        body = {"json": {"description": "custom"}} if method == "PUT" else {}
        url = f"/api/v1/admin/accounts/{account_id}/memory-templates/profile"
        assert (await client.request(method, url, headers=user_headers, **body)).status_code == 403
        other_url = f"/api/v1/admin/accounts/{other}/memory-templates/profile"
        assert (
            await client.request(method, other_url, headers=admin_headers, **body)
        ).status_code == 403
        missing_url = "/api/v1/admin/accounts/missing-account/memory-templates/profile"
        assert (
            await client.request(method, missing_url, headers=root_headers(), **body)
        ).status_code == 404
        assert (
            await client.request(
                method, url.replace("/profile", "/missing-template"), headers=admin_headers, **body
            )
        ).status_code == 404
    root = f"/api/v1/admin/accounts/{account_id}/memory-templates"
    assert (await client.get(root, headers=user_headers)).status_code == 403
    for memory_type in ("profile", "events"):
        assert (
            await client.put(
                f"{root}/{memory_type}", json={"description": memory_type}, headers=root_headers()
            )
        ).status_code == 200
    await client.delete(f"{root}/profile", headers=admin_headers)
    assert (await client.get(f"{root}/events", headers=admin_headers)).json()["result"][
        "effective"
    ]["description"] == "events"
    other_result = await client.get(
        f"/api/v1/admin/accounts/{other}/memory-templates", headers=root_headers()
    )
    assert all(
        item["status"] == "system_default" for item in other_result.json()["result"]["templates"]
    )


@pytest.mark.parametrize("merge", [False, True], ids=["extraction", "patch-merge"])
@pytest.mark.parametrize("output_format", ["python", "json"])
@pytest.mark.parametrize(
    "marker",
    ["{{ language.upper() }}", "{% if language == 'en' %}EN{% else %}OTHER{% endif %}"],
    ids=["safe-method", "safe-condition"],
)
async def test_account_memory_templates_reach_live_prompts(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    merge,
    output_format,
    marker,
    monkeypatch,
):
    monkeypatch.setattr(get_openviking_config().memory, "extraction_output_format", output_format)
    account_id, headers = template_account
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/profile"
    fs = lightweight_admin_app.state.fake_service.viking_fs
    registry = MemoryTypeRegistry()
    base_description = registry.get("profile").description

    async def prompt_for(account, user, peer=None):
        snapshot = await resolve_account_memory_registry(fs, account, registry)
        ctx = RequestContext(user=UserIdentifier(account, user), role=Role.USER, actor_peer_id=peer)
        if merge:
            provider = PatchMergeContextProvider(
                memory_type="profile", patches=[], memory_registry=snapshot
            )
        else:
            provider = SessionExtractContextProvider(
                messages=[], ctx=ctx, viking_fs=fs, memory_registry=snapshot
            )
        provider._output_language = "en"
        provider.prefetch = AsyncMock(return_value=[])
        vlm = Mock(
            model="test-memory-templates",
            get_completion_async=AsyncMock(
                return_value="sdk.commit()" if output_format == "python" else '{"delete_ids":[]}'
            ),
        )
        isolation = MemoryIsolationHandler(
            ctx,
            provider.get_extract_context(),
            allowed_memory_types={"profile"},
            allowed_peer_ids={peer} if peer else None,
        )
        isolation.prepare_messages()
        provider._isolation_handler = isolation
        loop = ExtractLoop(
            vlm=vlm, viking_fs=fs, ctx=ctx, context_provider=provider, isolation_handler=isolation
        )
        operations, _ = await loop.run()
        assert not operations.has_errors()
        return vlm.get_completion_async.await_args.kwargs["messages"][0]["content"]

    initial = await prompt_for(account_id, "alice")
    # Account descriptions render through the same restricted schema path.
    body = {
        "description": "CUSTOM_ACCOUNT_SCOPE " + marker,
        "fields": [
            {
                "name": "content",
                "description": "ACCOUNT_FIELD " + marker + " {{ language }}",
            }
        ],
    }
    assert (await lightweight_admin_client.put(url, json=body, headers=headers)).status_code == 200
    for user, peer in (("alice", None), ("bob", None), ("bob", "customer")):
        prompt = await prompt_for(account_id, user, peer)
        assert "CUSTOM_ACCOUNT_SCOPE EN" in prompt
        # Preserve the Python protocol's existing static field contract. This
        # change does not add a language context to that separate path.
        expected_field = (
            body["fields"][0]["description"]
            if output_format == "python"
            else "ACCOUNT_FIELD EN en"
        )
        assert expected_field in prompt
    assert "CUSTOM_ACCOUNT_SCOPE" not in await prompt_for("other-account", "alice")
    assert registry.get("profile").description == base_description
    assert (await lightweight_admin_client.delete(url, headers=headers)).status_code == 200
    assert await prompt_for(account_id, "alice") == initial


async def test_account_memory_templates_commit_keeps_snapshot_through_file_write(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    monkeypatch,
):
    from openviking.message import Message, TextPart
    from openviking.session.compressor_v3 import SessionCompressorV3
    from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
    from openviking.session.memory.streaming_memory_updater import (
        StreamingMemoryUpdater,
        StreamingMemoryUpdaterConfig,
    )
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

    account_id, headers = template_account
    fs = lightweight_admin_app.state.fake_service.viking_fs
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/soul"
    template = {
        "description": "ACCOUNT_DESCRIPTION",
        "content_template": "# OLD_TEMPLATE\n{{ core_truths }}",
        "fields": [{"name": "core_truths", "description": "Business core values"}],
    }
    assert (
        await lightweight_admin_client.put(url, json=template, headers=headers)
    ).status_code == 200
    for module in (
        "openviking.session.compressor_v3",
        "openviking.session.memory.memory_updater",
        "openviking.session.memory.streaming_memory_updater",
        "openviking.storage.viking_fs",
    ):
        monkeypatch.setattr(f"{module}.get_viking_fs", lambda: fs)
    initialized = []

    async def initialize(registry, ctx, allowed_memory_types=None):
        initialized.append(registry.get("soul").filename_template)

    monkeypatch.setattr(MemoryTypeRegistry, "initialize_memory_files", initialize)
    # Deliberately retain a cached deployment registry: request snapshots must win.
    updater = StreamingMemoryUpdater(
        registry=MemoryTypeRegistry(),
        config=StreamingMemoryUpdaterConfig(max_operations_per_update=1),
    )
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_streaming_memory_updater",
        AsyncMock(return_value=updater),
    )
    ctx = RequestContext(user=UserIdentifier(account_id, "alice"), role=Role.USER)
    uri = "viking://user/alice/memories/soul.md"

    def orchestrator(**kwargs):
        provider = kwargs["context_provider"]
        assert provider.get_memory_schemas(ctx)[0].description == "ACCOUNT_DESCRIPTION"

        async def run():
            # A publish during the LLM call must not switch the writer's template.
            changed = {**template, "content_template": "# NEW_TEMPLATE\n{{ core_truths }}"}
            assert (
                await lightweight_admin_client.put(url, json=changed, headers=headers)
            ).status_code == 200
            return ResolvedOperations(
                delete_file_contents=[],
                errors=[],
                upsert_operations=[
                    ResolvedOperation(
                        memory_type="soul",
                        uris=[uri],
                        memory_fields={"core_truths": "Business fact"},
                    )
                ],
            ), []

        return Mock(run=run)

    compressor = SessionCompressorV3(vikingdb=None)
    monkeypatch.setattr(compressor, "_get_or_create_react", orchestrator)
    try:
        await compressor._extract_user_memories(
            messages=[Message(id="m1", role="user", parts=[TextPart("Business fact")])],
            ctx=ctx,
            allowed_memory_types={"soul"},
        )
    finally:
        await updater.close()
    assert initialized == ["soul.md"]
    content = MemoryFileUtils.read(fs.files[uri], uri=uri).content
    assert "# OLD_TEMPLATE" in content and "Business fact" in content
    assert "# NEW_TEMPLATE" not in content


async def test_account_memory_templates_write_failure_preserves_active_configuration(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    monkeypatch,
):
    account_id, headers = template_account
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/profile"
    client = lightweight_admin_client
    assert (
        await client.put(url, json={"description": "Active"}, headers=headers)
    ).status_code == 200
    fs = lightweight_admin_app.state.fake_service.viking_fs
    path = account_memory_template_path(account_id, "profile")
    original = fs.agfs._files[path]
    write = fs.agfs.write
    failed = False

    def fail_once(target, content, **kwargs):
        nonlocal failed
        if target.startswith(path + ".") and target.endswith(".tmp") and not failed:
            failed = True
            fs.agfs._files[target] = b"partial"
            raise OSError("simulated write failure")
        return write(target, content, **kwargs)

    monkeypatch.setattr(fs.agfs, "write", fail_once)
    with pytest.raises(OSError, match="simulated write failure"):
        await client.put(url, json={"description": "Unpublished"}, headers=headers)
    assert fs.agfs._files[path] == original
    assert (await client.get(url, headers=headers)).json()["result"]["effective"][
        "description"
    ] == "Active"


@pytest.fixture
def template_path_lock(
    lightweight_admin_app,
    template_account,
    monkeypatch,
):
    account_id, _ = template_account
    fs = lightweight_admin_app.state.fake_service.viking_fs
    path = account_memory_template_path(account_id, "profile")
    lock = threading.Lock()

    def acquire(ctx, target, timeout_secs=0, owner_lease_ref=None):
        from openviking.storage.errors import LockAcquisitionError

        assert target == path
        assert ctx["account_id"] == account_id
        if not lock.acquire(timeout=timeout_secs):
            raise LockAcquisitionError("publication in progress")
        return {"lease_ref": "publication-test"}

    def release(ctx, lease):
        lock.release()

    monkeypatch.setattr(fs.agfs, "pathlock_acquire_exact", acquire)
    monkeypatch.setattr(fs.agfs, "pathlock_release", release)
    return lock


async def test_account_memory_templates_concurrent_publications_keep_both_types(
    lightweight_admin_client,
    template_account,
):
    account_id, headers = template_account
    root = f"/api/v1/admin/accounts/{account_id}/memory-templates"
    responses = await asyncio.gather(
        lightweight_admin_client.put(
            f"{root}/profile", json={"description": "Profile override"}, headers=headers
        ),
        lightweight_admin_client.put(
            f"{root}/events", json={"description": "Events override"}, headers=headers
        ),
    )
    assert [response.status_code for response in responses] == [200, 200]
    result = (await lightweight_admin_client.get(root, headers=headers)).json()["result"]
    effective = {item["memory_type"]: item["effective"] for item in result["templates"]}
    assert effective["profile"]["description"] == "Profile override"
    assert effective["events"]["description"] == "Events override"


async def test_account_memory_templates_read_does_not_wait_for_publication(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    template_path_lock,
    monkeypatch,
):
    account_id, headers = template_account
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/profile"
    client = lightweight_admin_client
    assert (
        await client.put(url, json={"description": "Active"}, headers=headers)
    ).status_code == 200
    fs = lightweight_admin_app.state.fake_service.viking_fs
    path = account_memory_template_path(account_id, "profile")
    acquiring = threading.Event()
    acquire = fs.agfs.pathlock_acquire_exact

    def observe_acquire(*args, **kwargs):
        acquiring.set()
        return acquire(*args, **kwargs)

    monkeypatch.setattr(fs.agfs, "pathlock_acquire_exact", observe_acquire)
    with template_path_lock:
        # A writer may be preparing a partial staging file, but the active path
        # remains a complete version and readers never request its lock.
        fs.agfs._files[path + ".test.tmp"] = b"partial"
        response = await asyncio.wait_for(client.get(url, headers=headers), timeout=1)
        assert not acquiring.is_set()
    assert response.status_code == 200, response.text
    assert response.json()["result"]["effective"]["description"] == "Active"


async def test_account_memory_templates_reject_oversized_serialized_config(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
    monkeypatch,
):
    account_id, headers = template_account
    fs = lightweight_admin_app.state.fake_service.viking_fs
    original = dict(fs.agfs._files)
    monkeypatch.setattr("openviking.session.memory.account_templates._MAX_CONFIG_BYTES", 100)
    response = await lightweight_admin_client.put(
        f"/api/v1/admin/accounts/{account_id}/memory-templates/profile",
        json={"description": "A valid description"},
        headers=headers,
    )
    assert response.status_code == 400, response.text
    assert fs.agfs._files == original


async def test_account_memory_templates_corrupt_storage_is_not_overwritten(
    lightweight_admin_client,
    lightweight_admin_app,
    template_account,
):
    account_id, headers = template_account
    fs = lightweight_admin_app.state.fake_service.viking_fs
    path = account_memory_template_path(account_id, "profile")
    fs.agfs._files[path] = b'{"invalid-json"'
    original = dict(fs.agfs._files)
    url = f"/api/v1/admin/accounts/{account_id}/memory-templates/profile"
    for method in ("GET", "PUT", "DELETE"):
        response = await lightweight_admin_client.request(
            method,
            url,
            headers=headers,
            **({"json": {"description": "New"}} if method == "PUT" else {}),
        )
        assert response.status_code == 412, response.text
        assert response.json()["error"]["code"] == "FAILED_PRECONDITION"
        assert fs.agfs._files == original


async def test_create_account(admin_client: httpx.AsyncClient, admin_service: OpenVikingService):
    """ROOT can create an account with first admin."""
    acct = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["account_id"] == acct
    assert body["result"]["admin_user_id"] == "alice"
    assert "user_key" in body["result"]

    ctx = RequestContext(user=UserIdentifier(acct, "alice"), role=Role.ADMIN)
    assert await admin_service.viking_fs.abstract("viking://resources", ctx=ctx)
    assert await admin_service.viking_fs.abstract("viking://user", ctx=ctx)


async def test_create_user_paths_accept_initial_user_config(
    lightweight_admin_client: httpx.AsyncClient,
    lightweight_admin_app: FastAPI,
):
    acct = _uid()
    viking_fs = lightweight_admin_app.state.fake_service.viking_fs

    resp = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={
            "account_id": acct,
            "admin_user_id": "alice",
            # Legacy uid-less spelling, normalized to the viking://~ home alias.
            "user_config": {"add_targets": {"resource_uri": "viking://user/resources/admin"}},
        },
        headers=root_headers(),
    )
    assert resp.status_code == 200, resp.text

    alice_settings = await read_user_add_targets(
        viking_fs,
        RequestContext(user=UserIdentifier(acct, "alice"), role=Role.ADMIN),
    )
    assert alice_settings.resource_uri == "viking://user/alice/resources/admin"

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={
            "user_id": "bob",
            "role": "user",
            "user_config": {"add_targets": {"resource_uri": "viking://~/resources/bob"}},
        },
        headers=root_headers(),
    )
    assert resp.status_code == 200, resp.text

    bob_settings = await read_user_add_targets(
        viking_fs,
        RequestContext(user=UserIdentifier(acct, "bob"), role=Role.USER),
    )
    assert bob_settings.resource_uri == "viking://user/bob/resources/bob"


async def test_user_memory_policy_can_be_initialized_and_hot_updated(
    lightweight_admin_client: httpx.AsyncClient,
    lightweight_admin_app: FastAPI,
):
    acct = _uid()
    create_account = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    assert create_account.status_code == 200, create_account.text

    create_user = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={
            "user_id": "bob",
            "role": "user",
            "user_config": {"memory_policy": {"memory_types": ["profile"]}},
        },
        headers=root_headers(),
    )
    assert create_user.status_code == 200, create_user.text

    get_settings = await lightweight_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users/bob/settings",
        headers=root_headers(),
    )
    assert get_settings.status_code == 200, get_settings.text
    assert get_settings.json()["result"]["memory_policy"] == {
        "self": {"enabled": True},
        "peer": {"enabled": True},
        "memory_types": ["profile"],
    }

    patch_settings = await lightweight_admin_client.patch(
        f"/api/v1/admin/accounts/{acct}/users/bob/settings",
        json={"memory_policy": {"memory_types": ["experiences"]}},
        headers=root_headers(),
    )
    assert patch_settings.status_code == 200, patch_settings.text
    memory_policy = patch_settings.json()["result"]["memory_policy"]
    assert memory_policy["memory_types"] == [
        "cases",
        "experiences",
        "trajectories",
    ]

    viking_fs = lightweight_admin_app.state.fake_service.viking_fs
    persisted = await read_user_config(
        viking_fs,
        RequestContext(user=UserIdentifier(acct, "bob"), role=Role.USER),
    )
    assert persisted.memory_policy["memory_types"] == [
        "cases",
        "experiences",
        "trajectories",
    ]
    user_ctx = RequestContext(user=UserIdentifier(acct, "bob"), role=Role.USER)
    backup = json.loads(viking_fs.files[user_config_backup_uri(user_ctx)])
    assert backup["memory_policy"]["memory_types"] == ["profile"]

    writes_before_noop = len(viking_fs.writes)
    noop_patch = await lightweight_admin_client.patch(
        f"/api/v1/admin/accounts/{acct}/users/bob/settings",
        json={"memory_policy": persisted.memory_policy},
        headers=root_headers(),
    )
    assert noop_patch.status_code == 200, noop_patch.text
    assert len(viking_fs.writes) == writes_before_noop

    lightweight_admin_app.state.config.user_config_defaults = UserConfig(
        memory_policy={"memory_types": ["events"]}
    )
    reset_settings = await lightweight_admin_client.patch(
        f"/api/v1/admin/accounts/{acct}/users/bob/settings",
        json={"memory_policy": None},
        headers=root_headers(),
    )
    assert reset_settings.status_code == 200, reset_settings.text
    assert reset_settings.json()["result"]["memory_policy"] == {
        "self": {"enabled": True},
        "peer": {"enabled": True},
        "memory_types": ["events"],
    }

    persisted = await read_user_config(viking_fs, user_ctx)
    assert persisted.memory_policy is None
    backup = json.loads(viking_fs.files[user_config_backup_uri(user_ctx)])
    assert backup["memory_policy"]["memory_types"] == [
        "cases",
        "experiences",
        "trajectories",
    ]


async def test_user_memory_policy_uses_server_default_without_user_override(
    lightweight_admin_client: httpx.AsyncClient,
    lightweight_admin_app: FastAPI,
):
    acct = _uid()
    lightweight_admin_app.state.config.user_config_defaults = UserConfig(
        memory_policy={"memory_types": ["profile"]}
    )
    create_account = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    assert create_account.status_code == 200, create_account.text

    get_settings = await lightweight_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users/alice/settings",
        headers=root_headers(),
    )
    assert get_settings.status_code == 200, get_settings.text
    assert get_settings.json()["result"]["memory_policy"] == {
        "self": {"enabled": True},
        "peer": {"enabled": True},
        "memory_types": ["profile"],
    }


async def test_create_user_paths_ignore_deprecated_agent_evolution_config(
    lightweight_admin_client: httpx.AsyncClient,
    lightweight_admin_app: FastAPI,
):
    acct = _uid()
    viking_fs = lightweight_admin_app.state.fake_service.viking_fs

    resp = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={
            "account_id": acct,
            "admin_user_id": "alice",
            "user_config": {"agent_evolution": {"enabled": True}},
        },
        headers=root_headers(),
    )
    assert resp.status_code == 200, resp.text

    alice_config = await read_user_config(
        viking_fs,
        RequestContext(user=UserIdentifier(acct, "alice"), role=Role.ADMIN),
    )
    assert alice_config.agent_evolution.enabled is None

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={
            "user_id": "bob",
            "role": "user",
            "user_config": {"agent_evolution": {"enabled": False}},
        },
        headers=root_headers(),
    )
    assert resp.status_code == 200, resp.text

    bob_config = await read_user_config(
        viking_fs,
        RequestContext(user=UserIdentifier(acct, "bob"), role=Role.USER),
    )
    assert bob_config.agent_evolution.enabled is None


async def test_list_accounts(admin_client: httpx.AsyncClient):
    """ROOT can list all accounts."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.get("/api/v1/admin/accounts", headers=root_headers())
    assert resp.status_code == 200
    accounts = resp.json()["result"]
    account_ids = {a["account_id"] for a in accounts}
    assert "default" in account_ids
    assert acct in account_ids


async def test_list_accounts_without_watcher_reads_only_accounts_registry(
    lightweight_admin_client: httpx.AsyncClient,
    lightweight_admin_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
):
    """Without a watcher, account listing must not load user registries."""
    manager = lightweight_admin_app.state.api_key_manager
    original_read_json = manager._legacy._read_json
    read_paths: list[str] = []

    async def _record_read(path: str):
        read_paths.append(path)
        return await original_read_json(path)

    monkeypatch.setattr(manager._legacy, "_read_json", _record_read)

    resp = await lightweight_admin_client.get(
        "/api/v1/admin/accounts?limit=5&page=1", headers=root_headers()
    )

    assert resp.status_code == 200, resp.text
    assert read_paths == ["/local/_system/accounts.json"]


async def test_list_users_without_watcher_reads_only_target_user_registry(
    lightweight_admin_client: httpx.AsyncClient,
    lightweight_admin_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
):
    """Without a watcher, user listing must not load unrelated registries."""
    acct = _uid()
    await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    manager = lightweight_admin_app.state.api_key_manager
    original_read_json = manager._legacy._read_json
    read_paths: list[str] = []

    async def _record_read(path: str):
        read_paths.append(path)
        return await original_read_json(path)

    monkeypatch.setattr(manager._legacy, "_read_json", _record_read)

    resp = await lightweight_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users", headers=root_headers()
    )

    assert resp.status_code == 200, resp.text
    assert read_paths == [f"/local/{acct}/_system/users.json"]


async def test_list_users_for_default_account_without_users_file_returns_empty_list(
    lightweight_admin_client: httpx.AsyncClient,
):
    """The initialized default account may legitimately have no users.json."""
    resp = await lightweight_admin_client.get(
        "/api/v1/admin/accounts/default/users", headers=root_headers()
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["result"] == []


async def test_list_accounts_with_watcher_uses_memory(
    lightweight_admin_client: httpx.AsyncClient,
    lightweight_admin_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
):
    """A running watcher keeps list requests on the in-memory fast path."""
    loop = asyncio.get_running_loop()
    watch_task = loop.create_future()
    lightweight_admin_app.state.auth_plugin._watch_task = watch_task

    async def _unexpected_read(_path: str):
        raise AssertionError("list accounts should not read AGFS while watcher is running")

    manager = lightweight_admin_app.state.api_key_manager
    monkeypatch.setattr(manager._legacy, "_read_json", _unexpected_read)
    try:
        resp = await lightweight_admin_client.get(
            "/api/v1/admin/accounts", headers=root_headers()
        )
        users_resp = await lightweight_admin_client.get(
            "/api/v1/admin/accounts/default/users", headers=root_headers()
        )
    finally:
        watch_task.cancel()

    assert resp.status_code == 200, resp.text
    assert users_resp.status_code == 200, users_resp.text


async def test_identity_settings_refreshes_a_stale_registry_on_demand(
    admin_client: httpx.AsyncClient,
    admin_app: FastAPI,
    admin_service: OpenVikingService,
):
    """Detail settings reads must not depend on a preceding list request."""
    replica = admin_app.state.api_key_manager
    writer = APIKeyManager(
        root_key=ROOT_KEY,
        viking_fs=admin_service.viking_fs,
    )
    await writer.load()
    acct = _uid()

    await writer.ensure_trusted_identities({acct: {"trusted-user"}})
    assert replica.has_user(acct, "trusted-user") is False

    account_settings = await admin_client.get(
        f"/api/v1/admin/accounts/{acct}/settings",
        headers=root_headers(),
    )
    assert account_settings.status_code == 200, account_settings.text
    assert replica.has_user(acct, "trusted-user") is False

    await writer.ensure_trusted_identities({acct: {"trusted-user-2"}})
    assert replica.has_user(acct, "trusted-user-2") is False

    user_settings = await admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users/trusted-user-2/settings",
        headers=root_headers(),
    )
    assert user_settings.status_code == 200, user_settings.text
    assert replica.has_user(acct, "trusted-user") is True
    assert replica.has_user(acct, "trusted-user-2") is True


async def test_identity_settings_without_watcher_use_scoped_registry_reads(
    lightweight_admin_client: httpx.AsyncClient,
    lightweight_admin_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
):
    """Settings checks must not fall back to a full identity-registry reload."""
    acct = _uid()
    await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    manager = lightweight_admin_app.state.api_key_manager
    original_read_json = manager._legacy._read_json
    read_paths: list[str] = []

    async def _record_read(path: str):
        read_paths.append(path)
        return await original_read_json(path)

    monkeypatch.setattr(manager._legacy, "_read_json", _record_read)

    account_resp = await lightweight_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/settings", headers=root_headers()
    )
    assert account_resp.status_code == 200, account_resp.text
    assert read_paths == ["/local/_system/accounts.json"]

    read_paths.clear()
    user_resp = await lightweight_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users/alice/settings",
        headers=root_headers(),
    )
    assert user_resp.status_code == 200, user_resp.text
    assert read_paths == [
        "/local/_system/accounts.json",
        f"/local/{acct}/_system/users.json",
    ]


@pytest.mark.parametrize("index_updates_immediately", [True, False])
async def test_delete_account(
    admin_client, admin_service, admin_app, monkeypatch, index_updates_immediately
):
    """Cleanup submits all IDs without depending on immediate query consistency."""
    from itertools import islice
    from types import SimpleNamespace

    from openviking.storage.expr import Eq
    from openviking.storage.queuefs.queue_manager import QueueManager
    from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter
    from openviking.storage.viking_vector_index_backend import _SingleAccountBackend

    acct = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    key = resp.json()["result"]["user_key"]
    path = f"/local/{acct}/resources/project/nested/file.md"
    await _agfs_write(admin_service, path, "project data")
    await _agfs_write(admin_service, "/local/other/resources/keep.md", "keep")
    old_task = await get_task_tracker().create(
        "session_commit", account_id=acct, user_id="alice", resource_id="old-session"
    )
    user_cleanup = await get_task_tracker().create(
        "user_delete", account_id=acct, user_id="alice", resource_id=f"{acct}/bob"
    )

    manager = admin_app.state.api_key_manager

    # Exercise the real filter-deletion loop with bounded in-memory I/O.
    rows = {str(i): {"id": str(i), "account_id": acct} for i in range(100_001)}
    rows["keep"] = {"id": "keep", "account_id": "other"}
    indexed_rows = rows.copy()
    deleted_ids = []
    adapter = LocalCollectionAdapter("context", "", "default")

    def delete_data(ids):
        assert 0 < len(ids) <= 100
        deleted_ids.extend(ids)
        for record_id in ids:
            rows.pop(record_id, None)

    adapter._collection = SimpleNamespace(delete_data=delete_data)
    scan_rows = rows if index_updates_immediately else indexed_rows
    adapter.query = lambda *, filter, limit, offset=0, **kwargs: list(
        islice(
            (row for row in scan_rows.values() if row[filter.field] == filter.value),
            offset,
            offset + limit,
        )
    )
    # Other read APIs may still expose data after delete requests succeed.
    adapter.get = lambda ids: [
        indexed_rows[record_id] for record_id in ids if record_id in indexed_rows
    ]
    adapter.count = lambda filter: sum(
        row[filter.field] == filter.value for row in indexed_rows.values()
    )
    vectors = admin_service.viking_fs.vector_store
    vectors._root_backend = _SingleAccountBackend(
        vectors._config, bound_account_id=None, shared_adapter=adapter
    )
    original_delete = vectors.delete_account_data
    started, release = asyncio.Event(), asyncio.Event()

    async def fail_vectors(account_id, *, ctx):
        started.set()
        await release.wait()
        raise RuntimeError("injected vector failure")

    monkeypatch.setattr(vectors, "delete_account_data", fail_vectors)

    resp = await admin_client.delete(f"/api/v1/admin/accounts/{acct}", headers=root_headers())
    assert resp.status_code == 202
    result = resp.json()["result"]
    assert result["status"] == "deleting"
    task_id = result["task_id"]
    await asyncio.wait_for(started.wait(), 10)
    duplicate = await admin_client.delete(f"/api/v1/admin/accounts/{acct}", headers=root_headers())
    assert duplicate.json()["result"]["task_id"] == task_id
    denied = await admin_client.get("/api/v1/fs/ls?uri=viking://", headers={"X-API-Key": key})
    assert denied.status_code == 401
    conflict = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "new"},
        headers=root_headers(),
    )
    assert conflict.status_code == 409
    release.set()
    failed = await _wait_for_task(admin_client, task_id)
    assert failed["status"] == "failed"
    assert "injected vector failure" in failed["error"]
    assert await _agfs_exists(admin_service, path)
    await manager.reload()
    assert manager.get_deletion(acct)["task_id"] == task_id

    retried = await admin_client.delete(f"/api/v1/admin/accounts/{acct}", headers=root_headers())
    retry_task_id = retried.json()["result"]["task_id"]
    assert retry_task_id != task_id
    assert (await _wait_for_task(admin_client, retry_task_id))["status"] == "failed"

    monkeypatch.setattr(vectors, "delete_account_data", original_delete)
    # Reconcile a durable fence whose previous process stopped before enqueue.
    retry = await manager.replace_deletion_task(
        acct,
        None,
        expected_task_id=retry_task_id,
        task_id=str(uuid.uuid4()),
        owner_account_id=SYSTEM_TASK_ACCOUNT_ID,
        owner_user_id=SYSTEM_TASK_USER_ID,
    )
    await admin_app.state.deletion_service.initialize()
    completed = await _wait_for_task(admin_client, retry["task_id"])
    assert completed["status"] == "completed", completed
    assert completed["result"] == {"deleted": True}
    assert set(rows) == {"keep"}
    assert len(deleted_ids) == len(set(deleted_ids)) == 100_001
    assert adapter.count(Eq("account_id", acct)) == 100_001
    assert adapter.get(deleted_ids[:100])
    assert not await _agfs_exists(admin_service, f"/local/{acct}")
    assert await _agfs_exists(admin_service, "/local/other/resources/keep.md")
    assert manager.get_deletion(acct) is None
    assert not manager.has_user(acct, "alice")
    assert await get_task_tracker().get(old_task.task_id, account_id=acct, user_id="alice") is None
    assert await get_task_tracker().get(user_cleanup.task_id, account_id=acct, user_id="alice") is None
    assert (await _wait_for_task(admin_client, task_id))["status"] == "failed"

    # Late cleanup deliveries are harmless after deletion, including when the
    # same account/user IDs have since been recreated.
    queue = admin_service._queue_manager.get_queue(QueueManager.DATA_CLEANUP)
    for recreated in (False, True):
        if recreated:
            replacement_key = await manager.create_account(acct, "bob")
            await _agfs_write(admin_service, path, "new data")
        late_task_id = str(uuid.uuid4())
        await get_task_tracker().create(
            "user_delete",
            task_id=late_task_id,
            resource_id=f"{acct}/bob",
            account_id=SYSTEM_TASK_ACCOUNT_ID,
            user_id=SYSTEM_TASK_USER_ID,
        )
        await queue.enqueue(
            {
                "task_id": user_cleanup.task_id,
                "account_id": acct,
                "user_id": "alice",
                "target": {"account_id": acct, "user_id": "bob"},
            }
        )
        await queue.enqueue(
            {
                "task_id": late_task_id,
                "account_id": SYSTEM_TASK_ACCOUNT_ID,
                "user_id": SYSTEM_TASK_USER_ID,
                "target": {"account_id": acct, "user_id": "bob"},
            }
        )
        skipped = await _wait_for_task(admin_client, late_task_id)
        assert skipped["status"] == "completed"
        assert skipped["result"] == {"deleted": not recreated, "stale": True}
        assert await get_task_tracker().get(
            user_cleanup.task_id, account_id=acct, user_id="alice"
        ) is None
        assert await _agfs_exists(admin_service, path) is recreated
        if recreated:
            assert manager.resolve(replacement_key).user_id == "bob"


async def test_create_duplicate_account_fails(admin_client: httpx.AsyncClient):
    """Creating duplicate account should fail."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "bob"},
        headers=root_headers(),
    )
    assert resp.status_code == 409  # ALREADY_EXISTS


# ---- User CRUD ----


async def test_register_user(admin_client: httpx.AsyncClient):
    """ROOT can register a user in an account."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["user_id"] == "bob"
    assert "user_key" in body["result"]

    # Bob's key should work
    bob_key = body["result"]["user_key"]
    resp = await admin_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": bob_key},
    )
    assert resp.status_code == 200


async def test_root_can_register_admin_role_user(
    lightweight_admin_client: httpx.AsyncClient,
):
    """ROOT can create an ADMIN user via register_user."""
    acct = _uid()
    await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob-admin", "role": "admin"},
        headers=root_headers(),
    )
    assert resp.status_code == 200

    admin_key = resp.json()["result"]["user_key"]
    list_users = await lightweight_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users",
        headers={"X-API-Key": admin_key},
    )
    assert list_users.status_code == 200


async def test_root_cannot_register_root_role_user(
    lightweight_admin_client: httpx.AsyncClient,
):
    """ROOT is the configured server identity, not an account user role."""
    acct = _uid()
    await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "mallory-root", "role": "root"},
        headers=root_headers(),
    )
    assert resp.status_code == 403


async def test_admin_can_register_user_in_own_account(admin_client: httpx.AsyncClient):
    """ADMIN can register users in their own account."""
    acct = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]

    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers={"X-API-Key": alice_key},
    )
    assert resp.status_code == 200


async def test_admin_can_register_admin_role_user(
    lightweight_admin_client: httpx.AsyncClient,
):
    """ADMIN can create another ADMIN in the same account via register_user."""
    acct = _uid()
    resp = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "mallory-admin", "role": "admin"},
        headers={"X-API-Key": alice_key},
    )
    assert resp.status_code == 200

    admin_key = resp.json()["result"]["user_key"]
    list_users = await lightweight_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users",
        headers={"X-API-Key": admin_key},
    )
    assert list_users.status_code == 200


async def test_admin_cannot_register_root_role_user(
    lightweight_admin_client: httpx.AsyncClient,
):
    """ADMIN should not be able to mint a ROOT key via register_user."""
    acct = _uid()
    resp = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "mallory-root", "role": "root"},
        headers={"X-API-Key": alice_key},
    )
    assert resp.status_code == 403


async def test_admin_cannot_mint_root_key_that_reaches_root_only_endpoint(
    lightweight_admin_client: httpx.AsyncClient,
):
    """ADMIN registration must never yield a key that works on ROOT-only endpoints."""
    acct = _uid()
    resp = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "mallory-root", "role": "root"},
        headers={"X-API-Key": alice_key},
    )
    assert resp.status_code == 403
    result = resp.json().get("result") or {}
    assert "user_key" not in result

    mallory_key = result.get("user_key")
    if mallory_key:
        root_only = await lightweight_admin_client.get(
            "/api/v1/admin/accounts",
            headers={"X-API-Key": mallory_key},
        )
        assert root_only.status_code == 403


async def test_admin_cannot_register_user_in_other_account(admin_client: httpx.AsyncClient):
    """ADMIN cannot register users in another account."""
    acct = _uid()
    other = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]

    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": other, "admin_user_id": "eve"},
        headers=root_headers(),
    )

    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{other}/users",
        json={"user_id": "bob", "role": "user"},
        headers={"X-API-Key": alice_key},
    )
    assert resp.status_code == 403


async def test_list_users(admin_client: httpx.AsyncClient):
    """ROOT can list users in an account."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    resp = await admin_client.get(f"/api/v1/admin/accounts/{acct}/users", headers=root_headers())
    assert resp.status_code == 200
    users = resp.json()["result"]
    user_ids = {u["user_id"] for u in users}
    assert user_ids == {"alice", "bob"}


async def test_remove_user(
    admin_client: httpx.AsyncClient,
    admin_service: OpenVikingService,
    admin_app: FastAPI,
):
    """Deletion revokes the user and removes only their private data, uploads, and tasks."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    bob_key = resp.json()["result"]["user_key"]
    bob_ctx = RequestContext(user=UserIdentifier(acct, "bob"), role=Role.USER)
    private_uri = "viking://user/bob/memories/private.md"
    await admin_service.viking_fs.write_file(private_uri, "private", ctx=bob_ctx)
    alice_upload_uri = "viking://upload/1800000000000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    bob_upload_uri = "viking://upload/1800000000000-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    upload_ctx = RequestContext(user=UserIdentifier(acct, "alice"), role=Role.ROOT)
    for upload_uri, user_id in ((alice_upload_uri, "alice"), (bob_upload_uri, "bob")):
        await admin_service.viking_fs.write_file(
            f"{upload_uri}/meta",
            json.dumps({"account": acct, "user": user_id}),
            ctx=upload_ctx,
        )
    bob_task = await get_task_tracker().create(
        "session_commit",
        resource_id="bob-session",
        account_id=acct,
        user_id="bob",
    )
    resp = await admin_client.delete(
        f"/api/v1/admin/accounts/{acct}/users/bob", headers=root_headers()
    )
    assert resp.status_code == 202
    task_id = resp.json()["result"]["task_id"]

    # The fence invalidates Bob before background cleanup finishes.
    resp = await admin_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": bob_key},
    )
    assert resp.status_code == 401

    deletion_task = await _wait_for_task(admin_client, task_id)
    assert deletion_task["status"] == "completed"
    assert not await admin_service.viking_fs.exists(private_uri, ctx=bob_ctx)
    assert not await admin_service.viking_fs.exists(bob_upload_uri, ctx=upload_ctx)
    assert await admin_service.viking_fs.exists(alice_upload_uri, ctx=upload_ctx)
    assert not admin_app.state.api_key_manager.has_user(acct, "bob")
    assert (
        await get_task_tracker().get(
            bob_task.task_id,
            account_id=acct,
            user_id="bob",
        )
        is None
    )

    missing = await admin_client.delete(
        f"/api/v1/admin/accounts/{acct}/users/bob",
        headers=root_headers(),
    )
    assert missing.status_code == 404


# ---- Role management ----


async def test_admin_can_set_user_role_in_own_account(
    lightweight_admin_client: httpx.AsyncClient,
):
    """ADMIN can promote a user in its own account to ADMIN."""
    acct = _uid()
    create_account = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = create_account.json()["result"]["user_key"]
    create_user = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    bob_key = create_user.json()["result"]["user_key"]

    resp = await lightweight_admin_client.put(
        f"/api/v1/admin/accounts/{acct}/users/bob/role",
        json={"role": "admin"},
        headers={"X-API-Key": alice_key},
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["role"] == "admin"

    list_users = await lightweight_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users",
        headers={"X-API-Key": bob_key},
    )
    assert list_users.status_code == 200

    invalid_role = await lightweight_admin_client.put(
        f"/api/v1/admin/accounts/{acct}/users/bob/role",
        json={"role": "root"},
        headers={"X-API-Key": alice_key},
    )
    assert invalid_role.status_code == 400


async def test_regenerate_key(admin_client: httpx.AsyncClient):
    """ROOT can regenerate a user's key."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    old_key = resp.json()["result"]["user_key"]

    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users/bob/key",
        headers=root_headers(),
    )
    assert resp.status_code == 200
    new_key = resp.json()["result"]["user_key"]
    assert new_key != old_key

    # Old key invalid
    resp = await admin_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": old_key},
    )
    assert resp.status_code == 401

    # New key valid
    resp = await admin_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": new_key},
    )
    assert resp.status_code == 200


async def test_seeded_admin_key_endpoints(admin_client: httpx.AsyncClient):
    from openviking.server.api_keys import parse_api_key

    acct = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice", "seed": "admin-seed"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    admin_key = resp.json()["result"]["user_key"]
    account_id, user_id, secret = parse_api_key(admin_key)
    assert account_id == acct
    assert user_id == "alice"
    assert secret == _seed_secret("alice", "admin-seed")

    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user", "seed": "bob-seed"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    old_key = resp.json()["result"]["user_key"]
    _, _, old_secret = parse_api_key(old_key)
    assert old_secret == _seed_secret("bob", "bob-seed")

    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users/bob/key",
        json={"seed": "bob-new-seed"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    new_key = resp.json()["result"]["user_key"]
    _, _, new_secret = parse_api_key(new_key)
    assert new_secret == _seed_secret("bob", "bob-new-seed")
    assert new_key != old_key

    resp = await admin_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": old_key},
    )
    assert resp.status_code == 401


async def test_empty_seed_rejected(admin_client: httpx.AsyncClient):
    acct = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice", "seed": ""},
        headers=root_headers(),
    )
    assert resp.status_code == 400


# ---- Permission guard ----


async def test_user_role_cannot_access_admin_api(admin_client: httpx.AsyncClient):
    """USER role should not access admin endpoints."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    bob_key = resp.json()["result"]["user_key"]

    # USER cannot register users
    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "charlie", "role": "user"},
        headers={"X-API-Key": bob_key},
    )
    assert resp.status_code == 403


async def test_no_auth_admin_api_returns_401(admin_client: httpx.AsyncClient):
    """Admin API without key should return 401."""
    resp = await admin_client.get("/api/v1/admin/accounts")
    assert resp.status_code == 401


# ---- Legacy migration ----


async def test_user_role_cannot_run_legacy_migration(admin_client: httpx.AsyncClient):
    """Legacy migration is ROOT-only."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    bob_key = resp.json()["result"]["user_key"]

    resp = await admin_client.post(
        "/api/v1/admin/migrate",
        headers={"X-API-Key": bob_key},
    )
    assert resp.status_code == 403


async def test_legacy_migration_preflight_failure_does_not_create_task(
    admin_client: httpx.AsyncClient,
    admin_service: OpenVikingService,
):
    """Ambiguous session ownership fails preflight before task creation."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    await _agfs_write(admin_service, f"/local/{acct}/session/orphan/messages.jsonl", "{}\n")

    resp = await admin_client.post("/api/v1/admin/migrate", headers=root_headers())
    assert resp.status_code == 412
    error = resp.json()["error"]
    assert error["code"] == "FAILED_PRECONDITION"
    assert error["details"]["operation_count"] == 0
    assert error["details"]["errors"][0]["session_id"] == "orphan"

    tasks_resp = await admin_client.get(
        "/api/v1/tasks?task_type=legacy_migration",
        headers=root_headers(),
    )
    assert tasks_resp.status_code == 200
    assert tasks_resp.json()["result"] == []


async def test_legacy_migration_task_migrates_legacy_data(
    admin_client: httpx.AsyncClient,
    admin_app,
    admin_service: OpenVikingService,
):
    """ROOT migrate fans out shared agent data and moves sessions under users."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    register_bob = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    bob_key = register_bob.json()["result"]["user_key"]

    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/code-agent/memories/facts/project.md",
        "shared fact",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/code-agent/skills/code-review/SKILL.md",
        "legacy skill",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/code-agent/instructions/system.md",
        "do not migrate",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/user/bob/skills/code-review/SKILL.md",
        "existing skill",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/session/sess-001/.meta.json",
        json.dumps({"created_by_user_id": "alice"}),
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/session/sess-001/messages.jsonl",
        '{"role":"user"}\n',
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/session/sess-002/.meta.json",
        json.dumps({"user_id": "charlie"}),
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/session/sess-002/messages.jsonl",
        '{"role":"assistant"}\n',
    )

    resp = await admin_client.post("/api/v1/admin/migrate", headers=root_headers())
    assert resp.status_code == 200
    task_id = resp.json()["result"]["task_id"]
    assert await _agfs_exists(
        admin_service,
        f"/local/{SYSTEM_TASK_ACCOUNT_ID}/tasks/{SYSTEM_TASK_USER_ID}/{task_id}.json",
    )
    hidden_resp = await admin_client.get(f"/api/v1/tasks/{task_id}", headers={"X-API-Key": bob_key})
    assert hidden_resp.status_code == 404

    task = await _wait_for_task(admin_client, task_id)
    assert task["status"] == "completed"
    result = task["result"]
    created_user = next(item for item in result["created_users"] if item["user_id"] == "charlie")
    assert created_user["account_id"] == acct
    assert "user_key" not in created_user
    assert result["migrated"]["operations"]["agent_memories"] == 3
    assert result["migrated"]["operations"]["agent_skills"] == 2
    assert result["migrated"]["operations"]["sessions"] == 2
    assert any(item["reason"] == "target skill already exists" for item in result["skipped"])
    assert any("Skipped legacy instructions" in item for item in result["warnings"])

    manager = admin_app.state.api_key_manager
    assert manager.has_user(acct, "charlie")
    for user_id in ("alice", "bob", "charlie"):
        assert (
            await _agfs_read_text(
                admin_service,
                f"/local/{acct}/user/{user_id}/peers/code-agent/memories/facts/project.md",
            )
            == "shared fact"
        )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/alice/skills/code-review/SKILL.md",
        )
        == "legacy skill"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/bob/skills/code-review/SKILL.md",
        )
        == "existing skill"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/alice/sessions/sess-001/messages.jsonl",
        )
        == '{"role":"user"}\n'
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/charlie/sessions/sess-002/messages.jsonl",
        )
        == '{"role":"assistant"}\n'
    )
    assert not await _agfs_exists(
        admin_service,
        f"/local/{acct}/user/alice/peers/code-agent/instructions/system.md",
    )


async def test_legacy_migration_covers_all_accounts_and_agent_user_layout(
    admin_client: httpx.AsyncClient,
    admin_app,
    admin_service: OpenVikingService,
):
    """One ROOT migration scans all accounts and handles agent/user scoped legacy data."""
    acct = _uid()
    other_acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "admin"},
        headers=root_headers(),
    )
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": other_acct, "admin_user_id": "dana"},
        headers=root_headers(),
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/user/charlie/memories/.overview.md",
        "legacy physical user",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/code-agent/memories/facts/shared.md",
        "shared fact",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/review-agent/user/charlie/memories/facts/private.md",
        "private fact",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/review-agent/user/charlie/skills/review/SKILL.md",
        "review skill",
    )
    await _agfs_write(
        admin_service,
        f"/local/{other_acct}/agent/code-agent/memories/facts/other.md",
        "other account fact",
    )

    resp = await admin_client.post("/api/v1/admin/migrate", headers=root_headers())
    assert resp.status_code == 200
    task = await _wait_for_task(admin_client, resp.json()["result"]["task_id"])
    assert task["status"] == "completed"

    result = task["result"]
    assert any(
        item["account_id"] == acct and item["user_id"] == "charlie"
        for item in result["created_users"]
    )
    manager = admin_app.state.api_key_manager
    assert manager.has_user(acct, "charlie")
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/admin/peers/code-agent/memories/facts/shared.md",
        )
        == "shared fact"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/charlie/peers/code-agent/memories/facts/shared.md",
        )
        == "shared fact"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/charlie/peers/review-agent/memories/facts/private.md",
        )
        == "private fact"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/charlie/skills/review/SKILL.md",
        )
        == "review skill"
    )
    assert not await _agfs_exists(
        admin_service,
        f"/local/{acct}/user/admin/peers/review-agent/memories/facts/private.md",
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{other_acct}/user/dana/peers/code-agent/memories/facts/other.md",
        )
        == "other account fact"
    )


async def test_legacy_cleanup_removes_only_legacy_namespaces(
    admin_client: httpx.AsyncClient,
    admin_service: OpenVikingService,
):
    """Cleanup removes legacy agent/session roots without deleting migrated user data."""
    acct = _uid()
    other_acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": other_acct, "admin_user_id": "dana"},
        headers=root_headers(),
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/code-agent/memories/facts/old.md",
        "legacy agent",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/session/sess-001/messages.jsonl",
        '{"role":"user"}\n',
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/user/alice/agent/review-agent/memories/facts/old.md",
        "legacy user agent",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/user/alice/peers/code-agent/memories/facts/new.md",
        "new peer data",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/user/alice/sessions/sess-001/messages.jsonl",
        '{"role":"assistant"}\n',
    )
    await _agfs_write(
        admin_service,
        f"/local/{other_acct}/agent/code-agent/memories/facts/old.md",
        "other legacy agent",
    )

    resp = await admin_client.post(
        "/api/v1/admin/migrate",
        json={"action": "cleanup"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    task = await _wait_for_task(admin_client, resp.json()["result"]["task_id"])
    assert task["status"] == "completed"
    assert task["task_type"] == "legacy_cleanup"
    assert task["result"]["cleanup"]["directories"] == 4
    removed = {
        (item["account_id"], item["source"]) for item in task["result"]["cleanup"]["targets"]
    }
    # Cleanup targets each legacy agent_id individually (reserved subdirs such as
    # viking://agent/skills are preserved), so the agent roots appear per agent.
    assert (acct, "viking://agent/code-agent") in removed
    assert (acct, "viking://session") in removed
    assert (acct, "viking://user/alice/agent") in removed
    assert (other_acct, "viking://agent/code-agent") in removed

    assert not await _agfs_exists(admin_service, f"/local/{acct}/agent/code-agent")
    assert not await _agfs_exists(admin_service, f"/local/{acct}/session")
    assert not await _agfs_exists(admin_service, f"/local/{acct}/user/alice/agent")
    assert not await _agfs_exists(admin_service, f"/local/{other_acct}/agent/code-agent")
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/alice/peers/code-agent/memories/facts/new.md",
        )
        == "new peer data"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/alice/sessions/sess-001/messages.jsonl",
        )
        == '{"role":"assistant"}\n'
    )


async def test_legacy_agent_uri_is_read_only_and_session_storage_uses_canonical_uri(
    admin_service: OpenVikingService,
):
    """Old agent/session URIs remain readable but not mutable."""
    ctx = RequestContext(user=UserIdentifier("default", "admin_user"), role=Role.USER)
    await _agfs_write(
        admin_service,
        "/local/default/agent/code-agent/memories/facts/project.md",
        "legacy agent fact",
    )
    await _agfs_write(
        admin_service,
        "/local/default/session/old-session/messages.jsonl",
        '{"role":"user"}\n',
    )
    await _agfs_write(
        admin_service,
        "/local/default/session/old-session/.meta.json",
        '{"created_by_user_id":"admin_user"}',
    )

    assert (
        await admin_service.viking_fs.read_file(
            "viking://agent/code-agent/memories/facts/project.md",
            ctx=ctx,
        )
        == "legacy agent fact"
    )
    assert (
        await admin_service.viking_fs.read_file(
            "viking://user/admin_user/sessions/old-session/messages.jsonl",
            ctx=ctx,
        )
        == '{"role":"user"}\n'
    )
    with pytest.raises(PermissionDeniedError):
        await admin_service.viking_fs.write_file(
            "viking://agent/code-agent/memories/facts/new.md",
            "blocked",
            ctx=ctx,
        )


@pytest_asyncio.fixture(scope="function")
async def trusted_admin_app(admin_service):
    from openviking.server.auth.plugins import TrustedAuthPlugin
    from openviking.server.auth.registry import get_registry

    config = ServerConfig(auth_mode="trusted", root_api_key=ROOT_KEY)
    app = create_app(config=config, service=admin_service)
    set_service(admin_service)
    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=admin_service.viking_fs)
    await manager.load()
    # Create test users for trusted mode tests if they don't exist
    if "platform" not in manager._accounts:
        await manager.create_account("platform", "gateway-admin")
    app.state.api_key_manager = manager

    # Set auth plugin (lifespan not triggered in ASGI tests)
    registry = get_registry()
    if registry.get("trusted") is None:
        registry.register(TrustedAuthPlugin)
    app.state.auth_plugin = registry.get("trusted")()

    return app


@pytest_asyncio.fixture(scope="function")
async def trusted_admin_client(trusted_admin_app):
    transport = httpx.ASGITransport(app=trusted_admin_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_trusted_mode_root_can_create_account(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted ROOT requests should be able to create accounts."""
    acct = _uid()
    resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={
            "account_id": acct,
            "admin_user_id": "alice",
        },
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["account_id"] == acct
    assert body["result"]["admin_user_id"] == "alice"
    assert "user_key" not in body["result"]


async def test_trusted_mode_admin_can_register_user_in_own_account(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted ADMIN requests should be able to manage users in their own account."""
    acct = _uid()
    create_resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert create_resp.status_code == 200

    resp = await trusted_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=trusted_headers(
            account=acct,
            user="alice",
            include_api_key=True,
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["account_id"] == acct
    assert resp.json()["result"]["user_id"] == "bob"
    assert "user_key" not in resp.json()["result"]


async def test_trusted_mode_admin_can_list_users_with_account_only_in_url(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted ADMIN requests may omit X-OpenViking-Account when the URL already provides it."""
    acct = _uid()
    create_resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert create_resp.status_code == 200

    resp = await trusted_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users",
        headers={
            "X-API-Key": ROOT_KEY,
            "X-OpenViking-User": "alice",
            "X-OpenViking-Account": acct,
        },
    )
    assert resp.status_code == 200
    assert any(user["user_id"] == "alice" for user in resp.json()["result"])


async def test_trusted_mode_admin_can_list_users_without_account_or_user_headers(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted admin routes may omit caller account/user when the route itself identifies the target."""
    acct = _uid()
    create_resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert create_resp.status_code == 200

    resp = await trusted_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users",
        headers={"X-API-Key": ROOT_KEY},
    )
    assert resp.status_code == 200
    assert any(user["user_id"] == "alice" for user in resp.json()["result"])


async def test_trusted_mode_admin_cannot_register_user_in_other_account(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted ADMIN requests should reject conflicting account identity."""
    acct = _uid()
    other = _uid()
    for account_id, admin_user_id in ((acct, "alice"), (other, "eve")):
        create_resp = await trusted_admin_client.post(
            "/api/v1/admin/accounts",
            json={"account_id": account_id, "admin_user_id": admin_user_id},
            headers=trusted_headers(
                account="platform",
                user="gateway-admin",
                include_api_key=True,
            ),
        )
        assert create_resp.status_code == 200

    resp = await trusted_admin_client.post(
        f"/api/v1/admin/accounts/{other}/users",
        json={"user_id": "bob", "role": "user"},
        headers=trusted_headers(
            account=acct,
            user="alice",
            include_api_key=True,
        ),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_trusted_mode_admin_api_uses_trusted_gateway_identity(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted admin routes use the trusted gateway identity instead of tenant user role."""
    acct = _uid()
    manager = trusted_admin_app.state.api_key_manager
    create_resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert create_resp.status_code == 200

    # Change alice to USER role
    await manager.set_role(acct, "alice", "user")

    resp = await trusted_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=trusted_headers(
            account=acct,
            user="alice",
            include_api_key=True,
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["account_id"] == acct
    assert resp.json()["result"]["user_id"] == "bob"
    assert "user_key" not in resp.json()["result"]


async def test_trusted_mode_requires_matching_api_key_for_admin_api(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted admin requests should require the configured server API key when present."""
    resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": _uid(), "admin_user_id": "alice"},
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=False,
        ),
    )
    assert resp.status_code == 401


async def test_trusted_mode_create_account_lists_current_account_metadata(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted account creation should list the current account metadata shape."""
    acct = _uid()
    resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={
            "account_id": acct,
            "admin_user_id": "alice",
        },
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert resp.status_code == 200

    manager = trusted_admin_app.state.api_key_manager
    account = next(item for item in manager.get_accounts() if item["account_id"] == acct)
    assert set(account) == {"account_id", "created_at", "user_count"}
