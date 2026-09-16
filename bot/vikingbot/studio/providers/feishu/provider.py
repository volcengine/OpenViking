"""Feishu-specific credentials, channel configuration and onboarding actions."""

import secrets
import time

import httpx
from fastapi import HTTPException

from vikingbot.config.schema import FeishuChannelConfig


class FeishuProvider:
    type = "feishu"

    def validate_settings(self, value):
        if not isinstance(value, dict) or set(value) - {"thread_require_mention"}:
            raise HTTPException(400, "Unsupported Feishu settings")
        required = value.get("thread_require_mention", True)
        if type(required) is not bool:
            raise HTTPException(400, "thread_require_mention must be boolean")
        return {"thread_require_mention": required}

    def apply_settings(self, service, record):
        required = self.validate_settings(record.get("settings", {}))["thread_require_mention"]
        runtime = service.runtime(record)
        if runtime:
            runtime.config.thread_require_mention = required
        for config in service.config.channels:
            if (
                isinstance(config, dict)
                and config.get("type", "feishu") == "feishu"
                and config.get("app_id") == record["app_id"]
            ):
                config["thread_require_mention"] = required

    async def run_onboarding(self, jobs, run):
        from .onboarding import run_onboarding

        await run_onboarding(jobs, run)

    def runtime_key(self, record):
        return "feishu__" + record["app_id"]

    def validate_input(self, body):
        app_id = str(body.get("app_id", "")).strip()
        secret = str(body.get("app_secret", "")).strip()
        if not app_id.startswith("cli_") or not secret or len(secret) > 4096:
            raise HTTPException(400, "App ID and App Secret are required")
        return app_id, secret

    async def prepare(self, body):
        app_id, secret = self.validate_input(body)
        info = await self.validate_app(app_id, secret)
        return {
            "app_id": app_id,
            "app_secret": secret,
            "bot_name": info.get("app_name") or "VikingBot",
            "bot_open_id": info["open_id"],
            "step": 2,
        }

    async def credentials(self, record, body):
        secret = str(body.get("app_secret") or record["app_secret"]).strip()
        info = await self.validate_app(record["app_id"], secret)
        if info["open_id"] != record["bot_open_id"]:
            raise HTTPException(409, "Robot identity changed")
        return {**record, "app_secret": secret}

    def public_fields(self, record):
        return {
            key: record.get(key) for key in ("app_id", "step", "setup_mode", "onboarding_id")
        } | {"settings": self.validate_settings(record.get("settings", {}))}

    def install(self, service, record):
        from vikingbot.studio.providers.feishu.channel import StudioFeishuChannel

        channel_config = FeishuChannelConfig(
            app_id=record["app_id"],
            app_secret=record["app_secret"],
            bot_name=record["bot_name"],
            thread_require_mention=self.validate_settings(record.get("settings", {}))[
                "thread_require_mention"
            ],
            memory_peer=[],
            memory_user=[],
        )
        channel = StudioFeishuChannel(
            channel_config,
            service.manager.bus,
            record=record,
            store=service.store,
            workspace_path=service.config.workspace_path,
            bot_config=service.config,
        )
        service.manager.add_channel(channel)
        # Agent configuration is the same object; keep runtime channel policy in sync.
        service.config.channels = [
            c
            for c in service.config.channels
            if not (
                isinstance(c, dict)
                and c.get("type", "feishu") == "feishu"
                and c.get("app_id") == record["app_id"]
            )
        ]
        service.config.channels.append(channel_config.model_dump(mode="json"))
        return channel

    async def validate_app(self, app_id, secret):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                auth_response = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": app_id, "app_secret": secret},
                )
                auth_response.raise_for_status()
                auth = auth_response.json()
                if auth.get("code") != 0 or not auth.get("tenant_access_token"):
                    raise HTTPException(400, "Feishu rejected the application credentials")
                bot_response = await client.get(
                    "https://open.feishu.cn/open-apis/bot/v3/info",
                    headers={"Authorization": "Bearer " + auth["tenant_access_token"]},
                )
                bot_response.raise_for_status()
                bot = bot_response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, "Cannot reach Feishu; retry the connection check") from exc
        info = bot.get("bot", {})
        if bot.get("code") != 0 or not info.get("open_id"):
            raise HTTPException(400, "Enable the application's bot capability first")
        return info

    def onboarding(self, record, runtime, body):
        action = body.get("action")
        if action == "verify":
            if not record["enabled"] or not runtime:
                raise HTTPException(409, "Resume the connection first")
            runtime.verification = {
                "code": secrets.token_hex(3).upper(),
                "expires_at": time.time() + 600,
                "received": False,
                "sent": False,
            }
        else:
            raise HTTPException(400, "Unknown action")
