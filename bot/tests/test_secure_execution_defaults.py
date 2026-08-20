from vikingbot.agent.tools.factory import register_default_tools
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.base import BaseChannel
from vikingbot.config.schema import Config, SandboxBackend, TelegramChannelConfig


class _TestChannel(BaseChannel):
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg) -> bool:
        return True


def _default_registry(config: Config) -> ToolRegistry:
    registry = ToolRegistry(config=config)
    register_default_tools(
        registry,
        config,
        include_message_tool=False,
        include_spawn_tool=False,
        include_cron_tool=False,
        include_image_tool=False,
        include_viking_tools=False,
    )
    return registry


def test_empty_sender_allowlist_fails_closed():
    channel = _TestChannel(TelegramChannelConfig(token="test-token"), MessageBus())

    assert channel.is_allowed("unknown-sender") is False


def test_sender_allowlist_supports_exact_ids_and_explicit_wildcard():
    exact = _TestChannel(
        TelegramChannelConfig(token="exact", allow_from=["trusted-sender"]),
        MessageBus(),
    )
    public = _TestChannel(
        TelegramChannelConfig(token="public", allow_from=["*"]),
        MessageBus(),
    )

    assert exact.is_allowed("trusted-sender") is True
    assert exact.is_allowed("unknown-sender") is False
    assert public.is_allowed("unknown-sender") is True


def test_exec_tool_is_not_registered_by_default():
    registry = _default_registry(Config())

    assert registry.has("exec") is False


def test_direct_exec_requires_both_explicit_opt_ins():
    config = Config()
    config.tools.exec.enabled = True

    assert _default_registry(config).has("exec") is False

    config.tools.exec.allow_direct = True

    assert _default_registry(config).has("exec") is True


def test_isolated_backend_exec_requires_only_enabled_flag():
    config = Config()
    config.sandbox.backend = SandboxBackend.SRT
    config.tools.exec.enabled = True

    assert _default_registry(config).has("exec") is True
