"""Account-scoped onboarding jobs with durable mutation checkpoints.

Live authorization sessions belong to the selected provider and stay in memory.
"""

import asyncio
import time
import uuid
from contextlib import suppress

from fastapi import HTTPException

from .providers.registry import get_provider, validate_settings

TERMINAL = {"ready", "cancelled", "failed", "expired", "interrupted"}


class OnboardingJobs:
    def __init__(self, service):
        self.service = service
        self.tasks = {}
        self.live = {}
        self.lock = asyncio.Lock()
        for run in service.store.onboarding_runs():
            if run["state"] not in TERMINAL:
                run.update(state="interrupted", error="interrupted")
                service.store.save_onboarding(run)

    def get(self, account, identifier):
        run = next(
            (r for r in self.service.store.onboarding_runs(account) if r["id"] == identifier), None
        )
        if run is None:
            raise HTTPException(404, "Onboarding not found")
        return run

    def public(self, run):
        result = {
            key: run.get(key)
            for key in (
                "id",
                "type",
                "state",
                "name",
                "expires_at",
                "error",
                "app_id",
                "connection_id",
            )
        }
        result["user_id"] = run["identity"]["user_id"]
        owner = run.get("owner")
        if owner:
            result["owner"] = {k: owner[k] for k in ("user_name", "tenant_name")}
        if run["state"] in ("waiting_for_scan", "scanned"):
            result["qr"] = self.live.get(run["id"], {}).get("qr")
        result["can_retry"] = run["state"] in ("failed", "expired", "interrupted") and not (
            (run.get("create_started") and not run.get("app_id"))
            or (run.get("version_started") and not run.get("version_id"))
        )
        return result

    def current(self, account, platform):
        get_provider({"type": platform})
        runs = self.service.store.onboarding_runs(account)
        run = next(
            (
                r
                for r in reversed(runs)
                if r["type"] == platform
                and not r.get("archived")
                and r["state"] not in {"ready", "cancelled"}
            ),
            None,
        )
        return self.public(run) if run else None

    async def start(self, account, body, identity):
        provider = get_provider(body)
        if not hasattr(provider, "run_onboarding"):
            raise HTTPException(400, "Automatic onboarding is unavailable for this platform")
        settings = validate_settings(provider, body.get("settings", {}))
        request_id = body.get("request_id")
        try:
            uuid.UUID(request_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(400, "A valid request ID is required") from exc
        async with self.lock:
            existing = next(
                (
                    r
                    for r in self.service.store.onboarding_runs(account)
                    if r["request_id"] == request_id and r["type"] == provider.type
                ),
                None,
            )
            if existing:
                return self.public(existing)
            current = self.current(account, provider.type)
            if current:
                return current
            name = str(body.get("name", "VikingBot")).strip()
            if not name or len(name) > 50:
                raise HTTPException(400, "Bot name must contain 1–50 characters")
            run = {
                "id": str(uuid.uuid4()),
                "request_id": request_id,
                "account": account,
                "type": provider.type,
                "identity": identity,
                "settings": settings,
                "name": name,
                "state": "initializing",
            }
            self.launch(run)
            return self.public(run)

    def launch(self, run):
        run.update(state="initializing", error=None, expires_at=time.time() + 120)
        self.service.store.save_onboarding(run)
        self.live[run["id"]] = {}
        self.tasks[run["id"]] = asyncio.create_task(self.execute(run))

    async def execute(self, run):
        try:
            await get_provider(run).run_onboarding(self, run)
        finally:
            self.live.pop(run["id"], None)
            self.tasks.pop(run["id"], None)

    def checkpoint(self, run, **values):
        run.update(values)
        self.service.store.save_onboarding(run)

    async def update(self, account, identifier, action):
        async with self.lock:
            run = self.get(account, identifier)
            if action == "cancel":
                if run["state"] not in {"initializing", "waiting_for_scan", "scanned", "expired"}:
                    raise HTTPException(
                        409, "Application setup has started; close and resume later"
                    )
                task = self.tasks.get(identifier)
                if task:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                self.tasks.pop(identifier, None)
                self.live.pop(identifier, None)
                self.checkpoint(run, state="cancelled")
            elif action == "manual":
                if run["state"] not in {"failed", "expired", "interrupted"}:
                    raise HTTPException(409, "Wait for automatic setup to finish")
                if run.get("connection_id"):
                    record = self.service.get(account, run["connection_id"])
                    record.update(setup_mode="manual", revision=record["revision"] + 1)
                    self.service.store.save(record)
                self.checkpoint(run, archived=True)
            elif action == "retry":
                if not self.public(run)["can_retry"]:
                    raise HTTPException(
                        409, "Check the application on its platform before continuing"
                    )
                previous = self.tasks.get(identifier)
                if previous:
                    # Terminal state can be persisted before its HTTP session
                    # finishes closing. Do not let old cleanup erase a new run.
                    with suppress(asyncio.CancelledError, Exception):
                        await previous
                self.launch(run)
            else:
                raise HTTPException(400, "Unknown onboarding action")
            return self.public(run)
