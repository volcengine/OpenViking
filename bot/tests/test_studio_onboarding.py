"""Contract and recovery tests; no live Feishu account or app is used."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from vikingbot.studio.onboarding import OnboardingJobs
from vikingbot.studio.providers.feishu import console, onboarding
from vikingbot.studio.providers.feishu.web_session import FeishuWebSession, SetupError, allowed_url
from vikingbot.studio.store import StudioStore


def make_jobs(tmp_path):
    return OnboardingJobs(SimpleNamespace(store=StudioStore(tmp_path / "studio.sqlite3")))


def run_record(**extra):
    return {
        "id": "run",
        "account": "a",
        "type": "feishu",
        "name": "VikingBot",
        "identity": {"user_id": "bot", "api_key": "private-key"},
        "state": "creating",
        "request_id": str(uuid.uuid4()),
        **extra,
    }


async def test_start_is_idempotent_and_scoped(tmp_path, monkeypatch):
    jobs = make_jobs(tmp_path)
    launches = []

    def launch(run):
        jobs.service.store.save_onboarding(run)
        launches.append(run)

    monkeypatch.setattr(jobs, "launch", launch)
    body = {"type": "feishu", "request_id": str(uuid.uuid4())}
    identity = {"user_id": "bot", "api_key": "private-key"}
    first = await jobs.start("a", body, identity)
    second = await jobs.start("a", body, identity)
    third = await jobs.start("a", {**body, "request_id": str(uuid.uuid4())}, identity)
    assert first["id"] == second["id"] == third["id"]
    assert len(launches) == 1
    with pytest.raises(HTTPException) as error:
        jobs.get("other-account", first["id"])
    assert error.value.status_code == 404
    assert "private-key" not in json.dumps(first)
    assert jobs.current("other-account", "feishu") is None


def test_restart_keeps_checkpoints_but_not_fake_live_qr(tmp_path):
    jobs = make_jobs(tmp_path)
    run = run_record(app_id="cli_existing", create_started=True, state="configuring")
    jobs.service.store.save_onboarding(run)
    restarted = OnboardingJobs(jobs.service)
    restored = restarted.get("a", "run")
    assert restored["app_id"] == "cli_existing"
    assert restored["state"] == "interrupted"
    public = restarted.public(restored)
    assert public["can_retry"]
    assert "qr" not in public and "identity" not in public


async def test_unknown_creation_result_cannot_retry(tmp_path):
    jobs = make_jobs(tmp_path)
    run = run_record(create_started=True, state="failed")
    jobs.service.store.save_onboarding(run)
    assert not jobs.public(run)["can_retry"]
    with pytest.raises(HTTPException):
        await jobs.update("a", "run", "retry")
    session = SimpleNamespace(post=AsyncMock())
    with pytest.raises(SetupError, match="creation_uncertain"):
        await console.create_app(session, run, lambda **kwargs: None)
    session.post.assert_not_called()


async def test_creation_checkpoint_precedes_uncertain_network_call():
    run = run_record()

    async def post(path, *args, **kwargs):
        if path.endswith("upload/image"):
            return {"data": {"url": "avatar"}}
        assert run["create_started"]
        raise httpx.ReadTimeout("possibly committed")

    with pytest.raises(httpx.ReadTimeout):
        await console.create_app(SimpleNamespace(post=post), run, lambda **kw: run.update(kw))
    assert run["create_started"] and "app_id" not in run


async def test_retries_only_read_an_already_submitted_version():
    run = run_record(app_id="cli_existing", version_id="version", publish_started=True)
    session = SimpleNamespace(
        post=AsyncMock(
            return_value={
                "data": {
                    "versions": [
                        {"versionId": "version", "versionStatus": 1},
                    ]
                }
            }
        )
    )
    assert (
        await console.publish_app(session, run, lambda **kw: run.update(kw)) == "awaiting_approval"
    )
    assert session.post.await_args.args[0] == "/developers/v1/app_version/list/cli_existing"
    assert session.post.await_count == 1
    session.post.return_value = {
        "data": {"versions": [{"versionId": "version", "versionStatus": 2}]}
    }
    assert await console.publish_app(session, run, lambda **kw: run.update(kw)) == "published"


async def test_committed_response_is_not_publication_proof():
    run = run_record(app_id="cli_existing", version_id="version", publish_started=True)
    session = SimpleNamespace(
        post=AsyncMock(
            return_value={
                "data": {
                    "versions": [
                        {"versionId": "version", "versionStatus": 0},
                    ]
                }
            }
        )
    )
    with pytest.raises(SetupError, match="publication_uncertain"):
        await console.publication_status(session, run)


async def test_cannot_cancel_once_app_mutation_started(tmp_path):
    jobs = make_jobs(tmp_path)
    jobs.service.store.save_onboarding(run_record())
    with pytest.raises(HTTPException) as error:
        await jobs.update("a", "run", "cancel")
    assert error.value.status_code == 409


async def test_event_write_success_requires_readback():
    async def post(path, *args):
        if "/scope/all/" in path:
            return {"data": {"appScopes": [{"name": name, "id": name} for name in console.SCOPES]}}
        if path == "/developers/v1/event/cli_test":
            return {"data": {"eventMode": 4, "appEvents": []}}
        return {"code": 0}

    with pytest.raises(SetupError, match="events_not_ready"):
        await console.configure_app(SimpleNamespace(post=post), "cli_test")


async def test_qr_session_follows_cookie_scopes_and_rejects_unsafe_redirect():
    async def handler(request):
        if request.url.path.endswith("/init"):
            return httpx.Response(
                200,
                headers={"x-flow-key": "flow", "set-cookie": "login=secret; Path=/; Secure"},
                json={"code": 0, "data": {"step_info": {"token": "qr-token"}}},
            )
        assert request.headers["x-flow-key"] == "flow"
        assert request.headers["cookie"] == "login=secret"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "next_step": "enter_app",
                    "step_info": {"cross_login_uri": "http://127.0.0.1/private"},
                },
            },
        )

    session = FeishuWebSession(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert json.loads(await session.begin()) == {"qrlogin": {"token": "qr-token"}}
    with pytest.raises(SetupError, match="untrusted_redirect"):
        await session.poll()
    await session.close()
    assert not session.client.cookies


@pytest.mark.parametrize(
    "url",
    [
        "https://feishu.cn.evil.test",
        "http://open.feishu.cn",
        "https://user@open.feishu.cn",
        "https://open.feishu.cn:999/app",
    ],
)
def test_redirect_allowlist(url):
    assert not allowed_url(url)


async def test_resume_with_wrong_owner_never_mutates_app(tmp_path, monkeypatch):
    jobs = make_jobs(tmp_path)
    run = run_record(owner={"user_id": "original", "tenant_id": "tenant"})
    jobs.live["run"] = {}
    session = SimpleNamespace(
        begin=AsyncMock(return_value="qr"),
        poll=AsyncMock(return_value="authorized"),
        identity=AsyncMock(return_value={"user_id": "other", "tenant_id": "tenant"}),
        close=AsyncMock(),
    )
    monkeypatch.setattr(onboarding, "FeishuWebSession", lambda: session)
    create = AsyncMock()
    monkeypatch.setattr(onboarding, "create_app", create)
    await onboarding.run_onboarding(jobs, run)
    assert run["state"] == "failed" and run["error"] == "identity_changed"
    create.assert_not_called()
    session.close.assert_awaited_once()


async def test_job_failure_never_returns_upstream_secrets(tmp_path, monkeypatch):
    jobs = make_jobs(tmp_path)
    run = run_record()
    jobs.live["run"] = {}
    session = SimpleNamespace(
        begin=AsyncMock(side_effect=RuntimeError("cookie=secret client_secret=private")),
        close=AsyncMock(),
    )
    monkeypatch.setattr(onboarding, "FeishuWebSession", lambda: session)
    await onboarding.run_onboarding(jobs, run)
    assert run["error"] == "setup_failed"
    assert "secret" not in json.dumps(jobs.public(run))
    session.close.assert_awaited_once()


@pytest.mark.parametrize("require_mention", [True, False])
async def test_complete_scan_configure_publish_flow(tmp_path, monkeypatch, require_mention):
    jobs = make_jobs(tmp_path)
    run = run_record(state="initializing")
    run["settings"] = {"thread_require_mention": require_mention}
    jobs.live["run"] = {}
    owner = {"user_id": "owner", "tenant_id": "tenant", "user_name": "User", "tenant_name": "Team"}
    session = SimpleNamespace(
        begin=AsyncMock(return_value="qr"),
        poll=AsyncMock(return_value="authorized"),
        identity=AsyncMock(return_value=owner),
        close=AsyncMock(),
    )
    monkeypatch.setattr(onboarding, "FeishuWebSession", lambda: session)

    async def create(session, run, checkpoint):
        checkpoint(app_id="cli_new")
        return "cli_new"

    monkeypatch.setattr(onboarding, "create_app", create)
    monkeypatch.setattr(onboarding, "prepare_app", AsyncMock(return_value="secret"))
    configure = AsyncMock()
    monkeypatch.setattr(onboarding, "configure_app", configure)
    monkeypatch.setattr(onboarding, "publish_app", AsyncMock(return_value="published"))
    record = {
        "id": "connection",
        "account": "a",
        "type": "feishu",
        "app_id": "cli_new",
        "revision": 1,
    }

    async def install(account, body, identity):
        assert body["app_secret"] == "secret" and identity["user_id"] == "bot"
        assert body["settings"] == {"thread_require_mention": require_mention}
        jobs.service.store.save(record)
        return record

    jobs.service.create = install
    jobs.service.get = lambda *_: record
    jobs.service.runtime = lambda _: SimpleNamespace(status=lambda: {"state": "connected"})
    await onboarding.run_onboarding(jobs, run)
    assert run["state"] == "ready" and run["configured"]
    assert run["connection_id"] == "connection"
    assert record["setup_mode"] == "qr" and record["step"] == 4
    assert "qr" not in jobs.public(run)
    configure.assert_awaited_once_with(session, "cli_new", require_mention=require_mention)
    session.close.assert_awaited_once()


async def test_manual_recovery_releases_current_job_without_forgetting_app(tmp_path):
    jobs = make_jobs(tmp_path)
    run = run_record(state="failed", app_id="cli_saved")
    jobs.service.store.save_onboarding(run)
    await jobs.update("a", "run", "manual")
    assert jobs.current("a", "feishu") is None
    assert jobs.get("a", "run")["app_id"] == "cli_saved"


async def test_lost_publish_response_reconciles_without_replaying(monkeypatch):
    run = run_record(app_id="cli_saved", version_id="v1", publish_started=True)
    publish = AsyncMock(side_effect=httpx.ReadTimeout("response lost"))
    read = AsyncMock(side_effect=[SetupError("publication_uncertain"), "published"])
    monkeypatch.setattr(onboarding, "publish_app", publish)
    monkeypatch.setattr(onboarding, "publication_status", read)
    monkeypatch.setattr(onboarding.asyncio, "sleep", AsyncMock())
    result = await onboarding.publish_and_wait(None, run, lambda **kw: run.update(kw))
    assert result == "published"
    publish.assert_awaited_once()
    assert read.await_count == 2


async def test_retry_waits_for_previous_session_cleanup(tmp_path, monkeypatch):
    jobs = make_jobs(tmp_path)
    run = run_record(state="expired")
    jobs.service.store.save_onboarding(run)
    closed = []

    async def finish_cleanup():
        closed.append(True)

    import asyncio

    jobs.tasks["run"] = asyncio.create_task(finish_cleanup())

    def launch(restarted):
        assert closed == [True]
        restarted["state"] = "initializing"

    monkeypatch.setattr(jobs, "launch", launch)
    result = await jobs.update("a", "run", "retry")
    assert result["state"] == "initializing"


async def test_configure_app_requests_tenant_permissions_from_catalog():
    scopes = [
        "im:message.group_at_msg:readonly",
        "im:message:send_as_bot",
        "im:chat:read",
        "im:chat.members:read",
    ]
    calls = []

    async def post(path, body=None):
        calls.append((path, body))
        if "/scope/all/" in path:
            return {
                "data": {
                    "appScopeList": [
                        {"name": name, "id": index + 101} for index, name in enumerate(scopes)
                    ],
                    "userScopeList": [
                        {"name": name, "id": index + 201} for index, name in enumerate(scopes)
                    ],
                }
            }
        if path == "/developers/v1/event/cli_test":
            return {"data": {"eventMode": 4, "appEvents": [console.EVENT]}}
        return {"code": 0}

    await console.configure_app(SimpleNamespace(post=post), "cli_test")
    update = next(body for path, body in calls if "/scope/update/" in path)
    assert update["appScopeIDs"] == ["101", "102", "103", "104"]
    assert update["userScopeIDs"] == []


async def test_new_app_uses_agent_template_and_persists_request_identity():
    run = run_record()
    calls = []

    async def post(path, body, **kwargs):
        calls.append(path)
        if path.endswith("upload/image"):
            return {"data": {"url": "avatar"}}
        assert path == "/developers/v1/manifest/upsert_by_template"
        assert body["appManifestTemplateID"] == "developer_console"
        assert body["cid"] == run["template_request_id"]
        assert run["create_started"]
        assert body["createAppUserCustomField"]["i18n"]["zh_cn"]["name"] == run["name"]
        return {"data": {"ClientID": "cli_agent"}}

    assert (
        await console.create_app(SimpleNamespace(post=post), run, lambda **kw: run.update(kw))
        == "cli_agent"
    )
    assert run["app_id"] == "cli_agent"
    assert "/developers/v1/app/create" not in calls


async def test_existing_app_is_preserved_without_attempting_template_conversion():
    session = SimpleNamespace(post=AsyncMock())
    run = run_record(app_id="cli_existing")
    assert await console.create_app(session, run, lambda **kw: run.update(kw)) == "cli_existing"
    session.post.assert_not_called()


async def test_rejected_agent_template_never_falls_back_to_ordinary_bot():
    session = SimpleNamespace(
        post=AsyncMock(
            side_effect=[
                {"data": {"url": "avatar"}},
                SetupError("platform_rejected"),
            ]
        )
    )
    run = run_record()
    with pytest.raises(SetupError, match="platform_rejected"):
        await console.create_app(session, run, lambda **kw: run.update(kw))
    assert session.post.call_count == 2
    assert run["create_started"]


@pytest.mark.parametrize("require_mention", [True, False])
async def test_setup_requests_all_group_messages_only_when_selected(require_mention):
    all_scopes = console.SCOPES | {"im:message.group_msg"}
    calls = []

    async def post(path, body=None):
        calls.append((path, body))
        if "/scope/all/" in path:
            return [{"name": name, "id": name} for name in all_scopes]
        return {"eventMode": 4, "appEvents": [console.EVENT]}

    await console.configure_app(
        SimpleNamespace(post=post), "cli_test", require_mention=require_mention
    )
    requested = next(body["appScopeIDs"] for path, body in calls if "/scope/update/" in path)
    assert ("im:message.group_msg" in requested) is (not require_mention)
