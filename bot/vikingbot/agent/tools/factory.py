"""Tool factory for centralized tool registration."""

from typing import TYPE_CHECKING, Callable

from vikingbot.agent.tools.cron import CronTool
from vikingbot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from vikingbot.agent.tools.image import ImageGenerationTool
from vikingbot.agent.tools.message import MessageTool
from vikingbot.agent.tools.ov_file import (
    VikingListTool,
    VikingSearchTool,
    VikingGrepTool,
    VikingGlobTool,
    VikingMultiReadTool,
    VikingMemoryCommitTool,
    VikingAddResourceTool,
)
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.agent.tools.shell import ExecTool
from vikingbot.agent.tools.web import WebFetchTool
from vikingbot.agent.tools.websearch import WebSearchTool
from vikingbot.config.loader import load_config

if TYPE_CHECKING:
    from vikingbot.agent.tools.spawn import SpawnTool


def register_default_tools(
    registry: ToolRegistry,
    config: "Config",
    send_callback: Callable[["OutboundMessage"], None] | None = None,
    subagent_manager: "SubagentManager | None" = None,
    cron_service: "CronService | None" = None,
    include_message_tool: bool = True,
    include_spawn_tool: bool = True,
    include_cron_tool: bool = True,
    include_image_tool: bool = True,
    include_viking_tools: bool = True,
) -> None:
    """
    Register default tools to a tool registry.

    Args:
        registry: Tool registry to register tools to
        config: Config object (all other parameters derived from this)
        send_callback: Callback for sending messages
        subagent_manager: Subagent manager
        cron_service: Cron service
        include_message_tool: Whether to include message tool
        include_spawn_tool: Whether to include spawn tool
        include_cron_tool: Whether to include cron tool
        include_image_tool: Whether to include image tool
        include_viking_tools: Whether to include Viking tools
    """
    # Derive all parameters from config
    workspace = config.workspace_path
    exec_config = config.tools.exec
    brave_api_key = config.tools.web.search.api_key if config.tools.web.search else None
    exa_api_key = None  # TODO: Add to config if needed
    tavily_api_key = config.tools.web.search.tavily_api_key if config.tools.web.search else None

    # Get provider API key and base from config

    agent_config = load_config().agents
    provider_api_key = agent_config.api_key if agent_config else None
    provider_api_base = agent_config.api_base if agent_config else None
    gen_image_model = agent_config.gen_image_model
    # File tools
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(ListDirTool())

    # Shell tool
    registry.register(
        ExecTool(
            timeout=exec_config.timeout,
        )
    )

    # Web tools
    registry.register(
        WebSearchTool(backend="auto", brave_api_key=brave_api_key, exa_api_key=exa_api_key, tavily_api_key=tavily_api_key)
    )
    registry.register(WebFetchTool())

    # Open Viking tools
    if include_viking_tools:
        registry.register(VikingMultiReadTool())
        registry.register(VikingListTool())
        registry.register(VikingSearchTool())
        registry.register(VikingGrepTool())
        registry.register(VikingGlobTool())
        registry.register(VikingMemoryCommitTool())
        if not config.read_only:
            registry.register(VikingAddResourceTool())



    # Image generation tool
    if include_image_tool:
        registry.register(
            ImageGenerationTool(
                gen_image_model=gen_image_model,
                api_key=provider_api_key,
                api_base=provider_api_base,
                send_callback=send_callback,
            )
        )

    # Message tool
    if include_message_tool and send_callback:
        message_tool = MessageTool(send_callback=send_callback)
        registry.register(message_tool)

    # Spawn tool
    if include_spawn_tool and subagent_manager:
        from vikingbot.agent.tools.spawn import SpawnTool

        spawn_tool = SpawnTool(manager=subagent_manager)
        registry.register(spawn_tool)

    # Cron tool
    if include_cron_tool and cron_service:
        registry.register(CronTool(cron_service))


def register_subagent_tools(
    registry: ToolRegistry,
    config: "Config",
) -> None:
    """
    Register tools for subagents (limited set).

    Args:
        registry: Tool registry to register tools to
        config: Config object (all parameters derived from this)
    """
    register_default_tools(
        registry=registry,
        config=config,
        include_message_tool=False,
        include_spawn_tool=False,
        include_cron_tool=False,
        include_image_tool=False,
        include_viking_tools=False,
    )
