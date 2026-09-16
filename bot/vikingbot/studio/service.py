"""Lifecycle of Studio-owned IM connections. Secrets never leave this service."""

import asyncio
import uuid

from fastapi import HTTPException

from vikingbot.studio.providers.registry import get_provider, validate_settings
from vikingbot.studio.store import StudioStore


class StudioService:
    def __init__(self, config, manager):
        self.config = config
        self.manager = manager
        self.store = StudioStore(config.bot_data_path / "studio.sqlite3")
        self.tasks = {}
        self.lock = asyncio.Lock()
        from vikingbot.studio.onboarding import OnboardingJobs

        self.onboarding = OnboardingJobs(self)
        for record in self.store.connections():
            if record.get("enabled"):
                self.install(record)

    def get(self, account, connection_id):
        for record in self.store.connections(account):
            if record["id"] == connection_id:
                return record
        raise HTTPException(404, "Connection not found")

    def runtime(self, record):
        return self.manager.channels.get(get_provider(record).runtime_key(record))

    def public(self, record):
        runtime = self.runtime(record)
        status = (
            runtime.status()
            if record["enabled"] and runtime and hasattr(runtime, "status")
            else {"state": "paused"}
        )
        return (
            {key: record.get(key) for key in ("id", "bot_name", "enabled", "revision")}
            | get_provider(record).public_fields(record)
            | {
                "type": get_provider(record).type,
                "status": status,
                "identity_user": record["identity"]["user_id"],
            }
        )

    def install(self, record):
        return get_provider(record).install(self, record)

    async def create(self, account, body, identity):
        provider = get_provider(body)
        settings = validate_settings(provider, body.get("settings", {}))
        async with self.lock:
            fields = await provider.prepare(body.get("credentials", body))
            candidate = {**fields, "type": provider.type}
            key = provider.runtime_key(candidate)
            if (
                any(get_provider(r).runtime_key(r) == key for r in self.store.connections())
                or key in self.manager.channels
            ):
                raise HTTPException(409, "This app is already configured")
            record = {
                "id": str(uuid.uuid4()),
                "account": account,
                **candidate,
                "identity": identity,
                "settings": settings,
                "enabled": True,
                "revision": 1,
            }
            channel = self.install(record)
            self.store.save(record)
            self.tasks[record["id"]] = asyncio.create_task(channel.start())
            return self.public(record)

    async def update(self, account, connection_id, body):
        async with self.lock:
            record = self.get(account, connection_id)
            if body.get("revision") != record["revision"]:
                raise HTTPException(409, "Configuration changed; refresh before saving")
            action = body.get("action")
            runtime = self.runtime(record)
            if action == "delete":
                if any(
                    run.get("connection_id") == connection_id and run["id"] in self.onboarding.tasks
                    for run in self.store.onboarding_runs(account)
                ):
                    raise HTTPException(
                        409, "Setup is running; finish or cancel it before deleting"
                    )
                if runtime:
                    await runtime.stop()
                task = self.tasks.pop(connection_id, None)
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                self.manager.channels.pop(get_provider(record).runtime_key(record), None)
                self.store.delete_connection(record)
                return {"deleted": True}
            if action == "settings":
                provider = get_provider(record)
                if not hasattr(provider, "apply_settings"):
                    raise HTTPException(400, "Settings are unavailable for this platform")
                record["settings"] = validate_settings(provider, body["settings"])
                record["revision"] += 1
                self.store.save(record)
                provider.apply_settings(self, record)
                return self.public(record)
            if action == "pause":
                if runtime:
                    await runtime.stop()
                task = self.tasks.pop(connection_id, None)
                if task:
                    task.cancel()
                record["enabled"] = False
            elif action == "resume":
                if not record["enabled"]:
                    runtime = self.install(record)
                    self.tasks[connection_id] = asyncio.create_task(runtime.start())
                    record["enabled"] = True
            elif action == "credentials":
                candidate = await get_provider(record).credentials(
                    record, body.get("credentials", body)
                )
                if body.get("identity"):
                    if body["identity"]["user_id"] != record["identity"]["user_id"]:
                        raise HTTPException(409, "Rotate the key for the same Bot user")
                    candidate["identity"] = body["identity"]
                # Persist before replacing runtime. Validation failure preserves the old connection.
                self.store.save(candidate)
                if runtime:
                    await runtime.stop()
                old_task = self.tasks.pop(connection_id, None)
                if old_task:
                    old_task.cancel()
                record = candidate
                if record["enabled"]:
                    runtime = self.install(record)
                    self.tasks[connection_id] = asyncio.create_task(runtime.start())
            else:
                get_provider(record).onboarding(record, runtime, body)
            record["revision"] += 1
            self.store.save(record)
            result = self.public(record)
            if not record["enabled"]:
                result["status"] = {"state": "paused"}
            return result
