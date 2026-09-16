"""One QR session creates, configures and publishes a group-chat application."""

import asyncio
import time

import httpx

from .console import configure_app, create_app, prepare_app, publication_status, publish_app
from .web_session import FeishuWebSession, SetupError


async def run_onboarding(jobs, run):
    session = FeishuWebSession()
    service = jobs.service

    def checkpoint(**values):
        jobs.checkpoint(run, **values)

    try:
        qr = await session.begin()
        jobs.live[run["id"]]["qr"] = qr
        checkpoint(state="waiting_for_scan", expires_at=time.time() + 120)
        while time.time() < run["expires_at"]:
            state = await session.poll()
            if state == "authorized":
                break
            if state == "expired":
                checkpoint(state="expired", error="expired")
                return
            if run["state"] != state:
                checkpoint(state=state)
            await asyncio.sleep(1.5)
        else:
            checkpoint(state="expired", error="expired")
            return
        jobs.live[run["id"]].clear()
        owner = await session.identity()
        if run.get("owner") and any(owner[k] != run["owner"][k] for k in ("user_id", "tenant_id")):
            raise SetupError("identity_changed")
        checkpoint(owner=owner, state="creating")
        app_id = await create_app(session, run, checkpoint)
        if not run.get("configured"):
            checkpoint(state="configuring")
            secret = await prepare_app(session, app_id)
            if not run.get("connection_id"):
                # A crash after create() persisted a connection must not create it a second time.
                existing = next(
                    (
                        r
                        for r in service.store.connections(run["account"])
                        if r.get("type", "feishu") == "feishu" and r.get("app_id") == app_id
                    ),
                    None,
                )
                if existing:
                    connection = service.public(existing)
                else:
                    connection = await service.create(
                        run["account"],
                        {
                            "type": "feishu",
                            "settings": run.get("settings", {}),
                            "app_id": app_id,
                            "app_secret": secret,
                        },
                        run["identity"],
                    )
                record = service.get(run["account"], connection["id"])
                record.update(onboarding_id=run["id"], setup_mode="qr")
                service.store.save(record)
                checkpoint(connection_id=connection["id"])
            record = service.get(run["account"], run["connection_id"])
            runtime = service.runtime(record)
            for _ in range(30):
                if runtime and runtime.status()["state"] == "connected":
                    break
                await asyncio.sleep(1)
            else:
                raise SetupError("connection_unavailable")
            await configure_app(
                session,
                app_id,
                require_mention=run.get("settings", {}).get("thread_require_mention", True),
            )
            checkpoint(configured=True)
        checkpoint(state="publishing")
        await publish_and_wait(session, run, checkpoint)
        record = service.get(run["account"], run["connection_id"])
        # Keep optimistic concurrency intact when a paused connection was changed.
        record.update(step=4, revision=record["revision"] + 1)
        service.store.save(record)
        checkpoint(state="ready", error=None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # HTTP/SDK exceptions may contain URLs, cookies and credentials. Never
        # expose or log the upstream exception text in the onboarding API.
        checkpoint(
            state="failed", error=str(exc) if isinstance(exc, SetupError) else "setup_failed"
        )
    finally:
        await session.close()


async def publish_and_wait(session, run, checkpoint):
    try:
        state = await publish_app(session, run, checkpoint)
    except (SetupError, httpx.HTTPError):
        if not run.get("publish_started"):
            raise
        # A dropped commit response is ambiguous. Reconcile by reading, never
        # replay the publish request. Console reads can lag behind the write.
        state = "publishing"
    deadline = time.time() + 600
    reconcile_deadline = time.time() + 45
    while state != "published":
        checkpoint(state=state)
        if time.time() > deadline:
            raise SetupError("approval_pending")
        await asyncio.sleep(3 if state == "publishing" else 10)
        try:
            state = await publication_status(session, run)
        except SetupError:
            if time.time() > reconcile_deadline:
                raise
    return state
