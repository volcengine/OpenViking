"""CLI commands for vikingbot."""

import asyncio
from dataclasses import dataclass
import json
import os
import random
import re
import select
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import typer
from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from vikingbot import __logo__, __version__
from vikingbot.agent.loop import AgentLoop
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.manager import ChannelManager
from vikingbot.config.loader import ensure_config, get_config_path, get_data_dir, load_config
from vikingbot.config.schema import SessionKey
from vikingbot.cron.service import CronService
from vikingbot.cron.types import CronJob
from vikingbot.heartbeat.service import HeartbeatService
from vikingbot.integrations.langfuse import LangfuseClient

# Create sandbox manager
from vikingbot.sandbox.manager import SandboxManager
from vikingbot.session.manager import SessionManager
from vikingbot.utils.helpers import (
    get_bridge_path,
    get_history_path,
    get_source_workspace_path,
    set_bot_data_path,
)

# Ignore Pydantic V1 compatibility warning with Python 3.14+ from volcenginesdkarkruntime
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.",
    category=UserWarning,
    module="volcenginesdkarkruntime._compat",
)

app = typer.Typer(
    name="vikingbot",
    help=f"{__logo__} vikingbot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}


def get_or_create_machine_id() -> str:
    """Get a unique machine ID using py-machineid.

    Uses the system's machine ID, falls back to "default" if unavailable.
    """
    try:
        from machineid import machine_id

        return machine_id()
    except ImportError:
        # Fallback if py-machineid is not installed
        pass
    except Exception:
        pass

    # Default fallback
    return "default"


def _init_bot_data(config):
    """Initialize bot data directory and set global paths."""
    set_bot_data_path(config.bot_data_path)


# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    history_file = get_history_path() / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,  # Enter submits (single line mode)
    )


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} vikingbot[/cyan]")
    console.print(body)
    console.print()


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblack'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} vikingbot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(None, "--version", "-v", callback=version_callback, is_eager=True),
):
    """vikingbot - Personal AI Assistant."""
    pass


def _make_provider(config, langfuse_client: None = None):
    """Create LiteLLM provider from configuration."""
    from vikingbot.providers.litellm_provider import LiteLLMProvider

    p = config.agents
    model = p.model if p else None
    api_key = p.api_key if p else None
    api_base = p.api_base if p else None
    provider_name = p.provider if p else None
    extra_headers = p.extra_headers if p else {}

    if not model:
        raise RuntimeError("No LLM model configured. Please set it in ~/.openviking/ov.conf")

    if not api_key and not model.startswith("bedrock/"):
        console.print("[yellow]Warning: No API key configured.[/yellow]")
        console.print("You can configure providers later in the Console UI.")

    return LiteLLMProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=model,
        extra_headers=extra_headers,
        provider_name=provider_name,
        langfuse_client=langfuse_client,
    )


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    # console_port: int = typer.Option(18791, "--console-port", help="Console web UI port"),
    enable_console: bool = typer.Option(
        True, "--console/--no-console", help="Enable console web UI"
    ),
    agent: bool = typer.Option(
        True, "--agent/--no-agent", help="Enable agent loop for OpenAPI/chat"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config_path: str = typer.Option(None, "--config", "-c", help="ov.conf path"),
):
    """Start the vikingbot gateway with OpenAPI chat enabled by default."""

    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    bus = MessageBus()
    path = Path(config_path).expanduser() if config_path is not None else None
    config = ensure_config(path)
    _init_bot_data(config)
    session_manager = SessionManager(config.bot_data_path)

    # Create FastAPI app for OpenAPI
    from fastapi import FastAPI

    fastapi_app = FastAPI(
        title="Vikingbot OpenAPI",
        description="HTTP API for Vikingbot chat",
        version="1.0.0",
    )

    cron = prepare_cron(bus)
    channels = prepare_channel(
        config, bus, fastapi_app=fastapi_app, enable_openapi=True, openapi_port=port
    )
    agent_loop = prepare_agent_loop(config, bus, session_manager, cron)
    heartbeat = prepare_heartbeat(config, agent_loop, session_manager)

    async def run():
        import uvicorn

        # Start uvicorn server for OpenAPI
        config_uvicorn = uvicorn.Config(
            fastapi_app,
            host="0.0.0.0",
            port=port,
            log_level="info",
        )
        server = uvicorn.Server(config_uvicorn)

        tasks = []
        tasks.append(cron.start())
        tasks.append(heartbeat.start())
        tasks.append(channels.start_all())
        tasks.append(agent_loop.run())
        tasks.append(server.serve())  # Start HTTP server
        # if enable_console:
        #     tasks.append(start_console(console_port))

        await asyncio.gather(*tasks)

    asyncio.run(run())


def prepare_agent_loop(config, bus, session_manager, cron, quiet: bool = False, eval: bool = False):
    sandbox_parent_path = config.workspace_path
    source_workspace_path = get_source_workspace_path()
    sandbox_manager = SandboxManager(config, sandbox_parent_path, source_workspace_path)
    if config.sandbox.backend == "direct":
        logger.warning("[SANDBOX] disabled (using DIRECT mode - commands run directly on host)")
    else:
        logger.info(
            f"Sandbox: enabled (backend={config.sandbox.backend}, mode={config.sandbox.mode})"
        )

    # Initialize Langfuse if enabled
    langfuse_client = None
    # logger.info(f"[LANGFUSE] Config check: has langfuse attr={hasattr(config, 'langfuse')}")

    if hasattr(config, "langfuse") and config.langfuse.enabled:
        langfuse_client = LangfuseClient(
            enabled=config.langfuse.enabled,
            secret_key=config.langfuse.secret_key,
            public_key=config.langfuse.public_key,
            base_url=config.langfuse.base_url,
        )
        LangfuseClient.set_instance(langfuse_client)
        if langfuse_client.enabled:
            logger.info(f"Langfuse: enabled (base_url={config.langfuse.base_url})")
        else:
            logger.warning("Langfuse: configured but failed to initialize")

    provider = _make_provider(config, langfuse_client)
    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.model,
        max_iterations=config.agents.max_tool_iterations,
        memory_window=config.agents.memory_window,
        brave_api_key=config.tools.web.search.api_key or None,
        exa_api_key=None,
        gen_image_model=config.agents.gen_image_model,
        exec_config=config.tools.exec,
        cron_service=cron,
        session_manager=session_manager,
        sandbox_manager=sandbox_manager,
        config=config,
        eval=eval,
    )
    # Set the agent reference in cron if it uses the holder pattern
    if hasattr(cron, "_agent_holder"):
        cron._agent_holder["agent"] = agent
    return agent


def prepare_cron(bus, quiet: bool = False) -> CronService:
    # Create cron service first (callback set after agent creation)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # Use a mutable holder for the agent reference
    agent_holder = {"agent": None}

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        session_key = SessionKey(**json.loads(job.payload.session_key_str))
        message = job.payload.message

        if agent_holder["agent"] is None:
            raise RuntimeError("Agent not initialized yet")

        # Clear instructions: let agent know this is a cron task to deliver
        cron_instruction = f"""[CRON TASK]
This is a scheduled task triggered by cron job: '{job.name}'
Your task is to deliver the following reminder message to the user.

IMPORTANT:
- This is NOT a user message - it's a scheduled reminder you need to send
- You should acknowledge/confirm the reminder and send it in a friendly way
- DO NOT treat this as a question from the user
- Simply deliver the reminder message as requested

Reminder message to deliver:
\"\"\"{message}\"\"\"
"""

        response = await agent_holder["agent"].process_direct(
            cron_instruction,
            session_key=session_key,
        )
        if job.payload.deliver:
            from vikingbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    session_key=session_key,
                    content=response or "",
                )
            )
        return response

    cron.on_job = on_cron_job
    cron._agent_holder = agent_holder

    cron_status = cron.status()
    if cron_status["jobs"] > 0 and not quiet:
        logger.info(f"Cron: {cron_status['jobs']} scheduled jobs")

    return cron


def prepare_channel(
    config, bus, fastapi_app=None, enable_openapi: bool = False, openapi_port: int = 18790
):
    """Prepare channels for the bot.

    Args:
        config: Bot configuration
        bus: Message bus for communication
        fastapi_app: External FastAPI app to register OpenAPI routes on
        enable_openapi: Whether to enable OpenAPI channel for gateway mode
        openapi_port: Port for OpenAPI channel (default: 18790)
    """
    channels = ChannelManager(bus)
    channels.load_channels_from_config(config)

    # Enable OpenAPI channel for gateway mode if requested
    if enable_openapi and fastapi_app is not None:
        from vikingbot.channels.openapi import OpenAPIChannel, OpenAPIChannelConfig

        openapi_config = OpenAPIChannelConfig(
            enabled=True,
            port=openapi_port,
            api_key="",  # No auth required by default
        )
        openapi_channel = OpenAPIChannel(
            openapi_config,
            bus,
            app=fastapi_app,  # Pass the external FastAPI app
        )
        channels.add_channel(openapi_channel)
        logger.info(f"OpenAPI channel enabled on port {openapi_port}")

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")
    return channels


def prepare_heartbeat(config, agent_loop, session_manager) -> HeartbeatService:
    # Create heartbeat service
    async def on_heartbeat(prompt: str, session_key: SessionKey | None = None) -> str:
        return await agent_loop.process_direct(
            prompt,
            session_key=session_key,
        )

    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        on_heartbeat=on_heartbeat,
        interval_s=config.heartbeat.interval_seconds,
        enabled=config.heartbeat.enabled,
        sandbox_mode=config.sandbox.mode,
        session_manager=session_manager,
    )

    console.print(
        f"[green]✓[/green] Heartbeat: every {config.heartbeat.interval_seconds}s"
        if config.heartbeat.enabled
        else "[yellow]✗[/yellow] Heartbeat: disabled"
    )
    return heartbeat


async def start_console(console_port):
    """Start the console web UI in a separate thread within the same process."""
    try:
        import threading

        from vikingbot.console.console_gradio_simple import run_console_server

        def run_in_thread():
            try:
                run_console_server(console_port)
            except Exception as e:
                console.print(f"[yellow]Console server error: {e}[/yellow]")

        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()
        console.print(f"[green]✓[/green] Console: http://localhost:{console_port}")
    except Exception as e:
        console.print(f"[yellow]Warning: Console not available ({e})[/yellow]")


# ============================================================================
# Agent Commands
# ============================================================================


# Helper for thinking spinner context
def _thinking_ctx(logs: bool):
    """Return a context manager for showing thinking spinner."""
    if logs:
        from contextlib import nullcontext

        return nullcontext()
    return console.status("[dim]vikingbot is thinking...[/dim]", spinner="dots")


def prepare_agent_channel(
    config,
    bus,
    message: str | None,
    session_id: str,
    markdown: bool,
    logs: bool,
    eval: bool = False,
    sender: str | None = None,
):
    """Prepare channel for agent command."""
    from vikingbot.channels.chat import ChatChannel, ChatChannelConfig
    from vikingbot.channels.single_turn import SingleTurnChannel, SingleTurnChannelConfig

    channels = ChannelManager(bus)
    if message is not None:
        # Single message mode - use SingleTurnChannel for clean output
        channel_config = SingleTurnChannelConfig()
        channel = SingleTurnChannel(
            channel_config,
            bus,
            workspace_path=config.workspace_path,
            message=message,
            session_id=session_id,
            markdown=markdown,
            eval=eval,
            sender=sender,
        )
        channels.add_channel(channel)
    else:
        # Interactive mode - use ChatChannel with thinking display
        channel_config = ChatChannelConfig()
        channel = ChatChannel(
            channel_config,
            bus,
            workspace_path=config.workspace_path,
            session_id=session_id,
            markdown=markdown,
            logs=logs,
            sender=sender,
        )
        channels.add_channel(channel)

    return channels


@app.command()
def chat(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option(None, "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(
        True, "--markdown/--no-markdown", help="Render assistant output as Markdown"
    ),
    logs: bool = typer.Option(
        False, "--logs/--no-logs", help="Show vikingbot runtime logs during chat"
    ),
    eval: bool = typer.Option(
        False, "--eval", "-e", help="Run evaluation mode, output JSON results"
    ),
    config_path: str = typer.Option(
        None, "--config", "-c", help="Path to ov.conf, default .openviking/ov.conf"
    ),
    sender: str = typer.Option(
        None, "--sender", help="Sender ID, same usage as feishu channel sender"
    ),
):
    """Interact with the agent directly."""
    path = Path(config_path).expanduser() if config_path is not None else None

    bus = MessageBus()
    config = ensure_config(path)
    _init_bot_data(config)

    logger.remove()
    log_file = get_data_dir() / "log" / f"vikingbot.debug.{os.getpid()}.log"
    logger.add(
        log_file,
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
        backtrace=True,
        diagnose=True,
    )

    if logs:
        logger.add(sys.stderr, level="DEBUG")
    else:
        logger.add(sys.stderr, level="ERROR")

    session_manager = SessionManager(config.bot_data_path)

    is_single_turn = message is not None
    # Use unified default session ID
    if session_id is None:
        session_id = get_or_create_machine_id()
    cron = prepare_cron(bus, quiet=is_single_turn)
    channels = prepare_agent_channel(config, bus, message, session_id, markdown, logs, eval, sender)
    agent_loop = prepare_agent_loop(
        config, bus, session_manager, cron, quiet=is_single_turn, eval=eval
    )

    async def run():
        if is_single_turn:
            # Single-turn mode: run channels and agent, exit after response
            task_cron = asyncio.create_task(cron.start())
            task_channels = asyncio.create_task(channels.start_all())
            task_agent = asyncio.create_task(agent_loop.run())

            # Wait for channels to complete (it will complete after getting response)
            done, pending = await asyncio.wait([task_channels], return_when=asyncio.FIRST_COMPLETED)

            # Cancel all other tasks
            for task in pending:
                task.cancel()
            task_cron.cancel()
            task_agent.cancel()

            # Wait for cancellation
            await asyncio.gather(task_cron, task_agent, return_exceptions=True)
        else:
            # Interactive mode: run forever
            tasks = []
            tasks.append(cron.start())
            tasks.append(channels.start_all())
            tasks.append(agent_loop.run())

            await asyncio.gather(*tasks)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\nGoodbye!")


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from vikingbot.config.schema import ChannelType

    config = load_config()
    channels_config = config.channels_config
    all_channels = channels_config.get_all_channels()

    table = Table(title="Channel Status")
    table.add_column("Type", style="cyan")
    table.add_column("ID", style="magenta")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    for channel in all_channels:
        channel_type = str(channel.type)
        channel_id = channel.channel_id()

        config_info = ""
        if channel.type == ChannelType.WHATSAPP:
            config_info = channel.bridge_url
        elif channel.type == ChannelType.FEISHU:
            config_info = f"app_id: {channel.app_id[:10]}..." if channel.app_id else ""
        elif channel.type == ChannelType.DISCORD:
            config_info = channel.gateway_url
        elif channel.type == ChannelType.MOCHAT:
            config_info = channel.base_url or ""
        elif channel.type == ChannelType.TELEGRAM:
            config_info = f"token: {channel.token[:10]}..." if channel.token else ""
        elif channel.type == ChannelType.SLACK:
            config_info = "socket" if channel.app_token and channel.bot_token else ""

        table.add_row(
            channel_type, channel_id, "✓" if channel.enabled else "✗", config_info or "[dim]—[/dim]"
        )

    if not all_channels:
        table.add_row("[dim]No channels configured[/dim]", "", "", "")

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    user_bridge = get_bridge_path()

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # vikingbot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: uv pip install --force-reinstall openviking[bot]")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess

    from vikingbot.config.schema import ChannelType

    config = load_config()
    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    env = {**os.environ}

    # Find WhatsApp channel config
    channels_config = config.channels_config
    all_channels = channels_config.get_all_channels()
    whatsapp_channel = next((c for c in all_channels if c.type == ChannelType.WHATSAPP), None)

    if whatsapp_channel and whatsapp_channel.bridge_token:
        env["BRIDGE_TOKEN"] = whatsapp_channel.bridge_token

    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Cron Commands
# ============================================================================

cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """List scheduled jobs."""
    from vikingbot.config.loader import get_data_dir
    from vikingbot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    jobs = service.list_jobs(include_disabled=all)

    if not jobs:
        console.print("No scheduled jobs.")
        return

    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Next Run")

    for job in jobs:
        # Format schedule
        if job.schedule.kind == "every":
            sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
        elif job.schedule.kind == "cron":
            sched = job.schedule.expr or ""
        else:
            sched = "one-time"

        # Format next run
        next_run = ""
        if job.state.next_run_at_ms:
            next_time = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(job.state.next_run_at_ms / 1000)
            )
            next_run = next_time

        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"

        table.add_row(job.id, job.name, sched, status, next_run)

    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
):
    """Add a scheduled job."""
    from vikingbot.config.loader import get_data_dir
    from vikingbot.cron.service import CronService
    from vikingbot.cron.types import CronSchedule

    # Determine schedule type
    if every:
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr)
    elif at:
        import datetime

        dt = datetime.datetime.fromisoformat(at)
        schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
    else:
        console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
        raise typer.Exit(1)

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    session_key = SessionKey(type="cli", channel_id="default", chat_id="default")

    job = service.add_job(
        name=name,
        schedule=schedule,
        message=message,
        deliver=deliver,
        session_key=session_key,
    )

    console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id})")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    from vikingbot.config.loader import get_data_dir
    from vikingbot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    if service.remove_job(job_id):
        console.print(f"[green]✓[/green] Removed job {job_id}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
):
    """Enable or disable a job."""
    from vikingbot.config.loader import get_data_dir
    from vikingbot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    job = service.enable_job(job_id, enabled=not disable)
    if job:
        status = "disabled" if disable else "enabled"
        console.print(f"[green]✓[/green] Job '{job.name}' {status}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
):
    """Manually run a job."""
    from vikingbot.config.loader import get_data_dir
    from vikingbot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    async def run():
        return await service.run_job(job_id, force=force)

    if asyncio.run(run()):
        console.print("[green]✓[/green] Job executed")
    else:
        console.print(f"[red]Failed to run job {job_id}[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show vikingbot status."""

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} vikingbot Status\n")

    console.print(
        f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}"
    )
    console.print(
        f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}"
    )

    if config_path.exists():
        from vikingbot.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(
                    f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}"
                )


# ============================================================================
# Test Commands
# ============================================================================

try:
    from vikingbot.cli.test_commands import test_app

    app.add_typer(test_app, name="test")
except ImportError:
    # If test commands not available, don't add them
    pass


demo_app = typer.Typer(help="Run built-in demos")
app.add_typer(demo_app, name="demo")


@dataclass(frozen=True)
class _WerewolfPlayer:
    seat: int
    name: str
    role: str
    session_key: SessionKey


def _ww_roles_for_player_count(player_count: int) -> list[str]:
    if player_count == 6:
        return ["Werewolf", "Werewolf", "Seer", "Hunter", "Villager", "Villager"]
    if player_count == 8:
        return [
            "Werewolf",
            "Werewolf",
            "Werewolf",
            "Seer",
            "Witch",
            "Hunter",
            "Villager",
            "Villager",
        ]
    if player_count == 9:
        return [
            "Werewolf",
            "Werewolf",
            "Werewolf",
            "Seer",
            "Witch",
            "Hunter",
            "Villager",
            "Villager",
            "Villager",
        ]
    if player_count == 10:
        return [
            "Werewolf",
            "Werewolf",
            "Werewolf",
            "Werewolf",
            "Seer",
            "Witch",
            "Hunter",
            "Guard",
            "Villager",
            "Villager",
        ]
    if player_count == 12:
        return [
            "Werewolf",
            "Werewolf",
            "Werewolf",
            "Werewolf",
            "Seer",
            "Witch",
            "Hunter",
            "Guard",
            "Idiot",
            "Villager",
            "Villager",
            "Villager",
        ]
    raise ValueError(f"Unsupported player_count={player_count}. Use 6/8/9/10/12.")


def _ww_alive_seats(alive: dict[int, bool]) -> list[int]:
    return [seat for seat, ok in sorted(alive.items(), key=lambda x: x[0]) if ok]


def _ww_pick_random_target(rng: random.Random, alive_seats: list[int], exclude: set[int] | None = None) -> int:
    exclude = exclude or set()
    candidates = [s for s in alive_seats if s not in exclude]
    if not candidates:
        return alive_seats[0] if alive_seats else 0
    return rng.choice(candidates)


def _ww_parse_last_int(text: str) -> int | None:
    nums = re.findall(r"\b(\d{1,2})\b", text or "")
    if not nums:
        return None
    try:
        return int(nums[-1], 10)
    except Exception:
        return None


def _ww_read_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _ww_append_record(record_path: Path, text: str) -> None:
    record_path.parent.mkdir(parents=True, exist_ok=True)
    prev = _ww_read_file(record_path)
    combined = (prev.rstrip() + "\n\n" + text.strip() + "\n").lstrip()
    record_path.write_text(combined, encoding="utf-8")


def _ww_init_player_game_md(player: _WerewolfPlayer, all_players: list[_WerewolfPlayer]) -> str:
    wolves = [p for p in all_players if p.role in ("Werewolf", "WhiteWolfKing")]
    wolf_names = [f"{w.seat + 1}号{w.name}" for w in wolves if w.seat != player.seat]
    teammates = "、".join(wolf_names) if wolf_names else "无"
    skills = []
    if player.role == "Witch":
        skills.append("解药：未使用")
        skills.append("毒药：未使用")
    if player.role == "Hunter":
        skills.append("开枪：可用")
    if player.role == "Idiot":
        skills.append("翻牌：未翻")
    if player.role == "Guard":
        skills.append("守护：未使用")
    skill_status = "；".join(skills) if skills else "无"
    return "\n".join(
        [
            "# 本局身份",
            f"座位：{player.seat + 1}号",
            f"昵称：{player.name}",
            f"身份：{player.role}",
            f"同伴：{teammates if player.role in ('Werewolf', 'WhiteWolfKing') else '无'}",
            f"技能状态：{skill_status}",
            "",
            "# 操作历史",
            "",
            "# 临时信息（裁判写入）",
            "",
        ]
    ).strip() + "\n"


def _ww_player_prompt_header(day: int, phase: str, player: _WerewolfPlayer, roster: list[_WerewolfPlayer]) -> str:
    roster_lines = "\n".join([f"- {p.seat + 1}号：{p.name}" for p in roster])
    return (
        "你在参与一局“机器人狼人杀”演示。你必须通过文件完成操作，不要在回复里暴露关键动作细节。\n"
        "规则：\n"
        "1) 先用 read_file 读取 GAME.md（路径：GAME.md）\n"
        "2) 根据裁判要求，把你的决定写回 GAME.md（优先用 edit_file 精准替换，或用 write_file 重写完整文件）\n"
        "3) 最后只回复一行：操作完成\n"
        "\n"
        f"当前：第{day}天 / 阶段：{phase}\n"
        f"你的座位：{player.seat + 1}号，你的昵称：{player.name}\n"
        "本局座位表：\n"
        f"{roster_lines}\n"
    )


async def _ww_call_player(agent_loop: AgentLoop, player: _WerewolfPlayer, content: str) -> str:
    return await agent_loop.process_direct(content=content, session_key=player.session_key)


@demo_app.command("werewolf")
def demo_werewolf(
    player_count: int = typer.Option(8, "--players", "-p", help="Player count (6/8/9/10/12)"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    days: int = typer.Option(1, "--days", help="How many day cycles to run (demo uses 1 by default)"),
    config_path: str = typer.Option(None, "--config", "-c", help="ov.conf path"),
):
    path = Path(config_path).expanduser() if config_path is not None else None
    bus = MessageBus()
    config = ensure_config(path)
    _init_bot_data(config)

    session_manager = SessionManager(config.bot_data_path)
    cron = prepare_cron(bus, quiet=True)
    agent_loop = prepare_agent_loop(config, bus, session_manager, cron, quiet=True, eval=False)

    rng = random.Random(seed)

    referee_key = SessionKey(type="demo", channel_id="werewolf", chat_id="referee")
    player_keys = [
        SessionKey(type="demo", channel_id="werewolf", chat_id=f"player_{i + 1:02d}")
        for i in range(player_count)
    ]

    roles = _ww_roles_for_player_count(player_count)
    rng.shuffle(roles)
    roster: list[_WerewolfPlayer] = [
        _WerewolfPlayer(
            seat=i,
            name=f"Bot{i + 1:02d}",
            role=roles[i],
            session_key=player_keys[i],
        )
        for i in range(player_count)
    ]

    async def run():
        outbound_task = asyncio.create_task(_ww_drain_outbound(bus))
        try:
            if agent_loop.sandbox_manager is None:
                raise RuntimeError("SandboxManager not initialized")

            await agent_loop.sandbox_manager.get_sandbox(referee_key)
            for p in roster:
                await agent_loop.sandbox_manager.get_sandbox(p.session_key)

            referee_ws = agent_loop.sandbox_manager.get_workspace_path(referee_key)
            record_path = referee_ws / "GAME_RECORD.md"
            _ww_append_record(
                record_path,
                "\n".join(
                    [
                        "# 狼人杀 Demo 记录",
                        f"- 玩家数：{player_count}",
                        f"- 随机种子：{seed}",
                        f"- 沙箱模式：{config.sandbox.mode}",
                        "",
                        "## 座位表",
                        *[f"- {p.seat + 1}号：{p.name}" for p in roster],
                    ]
                ),
            )

            for p in roster:
                ws = agent_loop.sandbox_manager.get_workspace_path(p.session_key)
                (ws / "GAME.md").write_text(_ww_init_player_game_md(p, roster), encoding="utf-8")

            alive: dict[int, bool] = {p.seat: True for p in roster}
            badge_holder: int | None = None

            await _ww_run_badge_election(agent_loop, rng, roster, alive, record_path, day=0)
            badge_holder = _ww_read_badge_holder(roster, alive, agent_loop.sandbox_manager) or badge_holder
            if badge_holder is not None:
                _ww_append_record(record_path, f"警长当选：{badge_holder + 1}号")

            for day in range(1, max(1, days) + 1):
                night_result = await _ww_run_night(
                    agent_loop=agent_loop,
                    rng=rng,
                    roster=roster,
                    alive=alive,
                    record_path=record_path,
                    day=day,
                    badge_holder=badge_holder,
                )
                if night_result is not None:
                    alive[night_result] = False
                    _ww_append_record(record_path, f"昨夜死亡：{night_result + 1}号")

                winner = _ww_check_winner(roster, alive)
                if winner:
                    _ww_append_record(record_path, f"游戏结束：{winner}")
                    break

                await _ww_run_day(
                    agent_loop=agent_loop,
                    rng=rng,
                    roster=roster,
                    alive=alive,
                    record_path=record_path,
                    day=day,
                    badge_holder=badge_holder,
                )

                winner = _ww_check_winner(roster, alive)
                if winner:
                    _ww_append_record(record_path, f"游戏结束：{winner}")
                    break

            console.print("\n[green]Demo completed.[/green]")
            console.print(f"Referee workspace: {referee_ws}")
            console.print("Player workspaces:")
            for p in roster:
                ws = agent_loop.sandbox_manager.get_workspace_path(p.session_key)
                console.print(f"- {p.seat + 1}号 {p.name}: {ws}")
        finally:
            outbound_task.cancel()
            try:
                await outbound_task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


@demo_app.command("werewolf-continue")
def demo_werewolf_continue(
    days: int = typer.Option(1, "--days", help="How many additional day cycles to run"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    config_path: str = typer.Option(None, "--config", "-c", help="ov.conf path"),
):
    path = Path(config_path).expanduser() if config_path is not None else None
    bus = MessageBus()
    config = ensure_config(path)
    _init_bot_data(config)

    session_manager = SessionManager(config.bot_data_path)
    cron = prepare_cron(bus, quiet=True)
    agent_loop = prepare_agent_loop(config, bus, session_manager, cron, quiet=True, eval=False)

    rng = random.Random(seed)

    referee_key = SessionKey(type="demo", channel_id="werewolf", chat_id="referee")

    async def run():
        outbound_task = asyncio.create_task(_ww_drain_outbound(bus))
        try:
            if agent_loop.sandbox_manager is None:
                raise RuntimeError("SandboxManager not initialized")

            await agent_loop.sandbox_manager.get_sandbox(referee_key)
            referee_ws = agent_loop.sandbox_manager.get_workspace_path(referee_key)
            record_path = referee_ws / "GAME_RECORD.md"
            if not record_path.exists():
                raise RuntimeError("GAME_RECORD.md not found, run `demo werewolf` first")

            roster = _ww_load_roster_from_workspace(
                workspace_root=agent_loop.sandbox_manager.get_workspace_path(referee_key).parent
            )
            if not roster:
                raise RuntimeError("No player workspaces found")

            for p in roster:
                await agent_loop.sandbox_manager.get_sandbox(p.session_key)

            alive = _ww_compute_alive_from_record(record_path, roster)
            badge_holder = _ww_read_badge_holder(roster, alive, agent_loop.sandbox_manager)
            start_day = _ww_get_next_day_from_record(record_path)

            _ww_append_record(
                record_path,
                f"## 继续游戏：从第{start_day}天开始（追加{max(1, days)}天）",
            )

            winner = _ww_check_winner(roster, alive)
            if winner:
                _ww_append_record(record_path, f"游戏结束：{winner}")
                console.print("[yellow]Game already ended.[/yellow]")
                return

            for day in range(start_day, start_day + max(1, days)):
                night_result = await _ww_run_night(
                    agent_loop=agent_loop,
                    rng=rng,
                    roster=roster,
                    alive=alive,
                    record_path=record_path,
                    day=day,
                    badge_holder=badge_holder,
                )
                if night_result is not None:
                    alive[night_result] = False
                    _ww_append_record(record_path, f"昨夜死亡：{night_result + 1}号")

                winner = _ww_check_winner(roster, alive)
                if winner:
                    _ww_append_record(record_path, f"游戏结束：{winner}")
                    break

                await _ww_run_day(
                    agent_loop=agent_loop,
                    rng=rng,
                    roster=roster,
                    alive=alive,
                    record_path=record_path,
                    day=day,
                    badge_holder=badge_holder,
                )

                winner = _ww_check_winner(roster, alive)
                if winner:
                    _ww_append_record(record_path, f"游戏结束：{winner}")
                    break

            console.print("\n[green]Continue completed.[/green]")
            console.print(f"Referee workspace: {referee_ws}")
        finally:
            outbound_task.cancel()
            try:
                await outbound_task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


@demo_app.command("werewolf-ui")
def demo_werewolf_ui(
    port: int = typer.Option(18791, "--port", "-p", help="UI port"),
    config_path: str = typer.Option(None, "--config", "-c", help="ov.conf path"),
):
    path = Path(config_path).expanduser() if config_path is not None else None
    config = ensure_config(path)
    _init_bot_data(config)

    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn

    workspace_root = config.workspace_path
    storage_root = (config.storage_workspace or "~/.openviking/data")
    storage_root = Path(storage_root).expanduser()

    def read_text(path: Path, limit: int = 20000) -> str:
        if not path.exists() or not path.is_file():
            return ""
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return ""
        if len(content) > limit:
            return content[-limit:]
        return content

    def extract_role(game_md: str) -> str:
        for line in game_md.splitlines():
            if line.strip().startswith("身份："):
                return line.split("身份：", 1)[1].strip()
        return "未知"

    def extract_last_speech(game_md: str) -> str:
        idx = game_md.rfind("发言：")
        if idx == -1:
            return ""
        segment = game_md[idx:].splitlines()
        if not segment:
            return ""
        first = segment[0].replace("发言：", "").strip()
        return first

    def list_players() -> list[dict[str, Any]]:
        if not workspace_root.exists():
            return []
        players = []
        for item in sorted(workspace_root.iterdir()):
            if not item.is_dir():
                continue
            if not item.name.startswith("demo__werewolf__player_"):
                continue
            game_path = item / "GAME.md"
            game_md = read_text(game_path)
            memory_md = read_text(item / "memory" / "MEMORY.md")
            history_md = read_text(item / "memory" / "HISTORY.md")
            role = extract_role(game_md)
            last_speech = extract_last_speech(game_md)
            updated_at = ""
            try:
                updated_at = str(int(game_path.stat().st_mtime))
            except Exception:
                updated_at = ""
            players.append(
                {
                    "id": item.name,
                    "game": game_md,
                    "memory": memory_md,
                    "history": history_md,
                    "role": role,
                    "last_speech": last_speech,
                    "updated_at": updated_at,
                }
            )
        return players

    def read_world_memory() -> dict[str, str]:
        base = storage_root / "viking" / "default" / "user" / "default" / "memories"
        return {
            "overview": read_text(base / ".overview.md"),
            "abstract": read_text(base / ".abstract.md"),
            "entities": read_text(base / "entities" / ".overview.md"),
            "events": read_text(base / "events" / ".overview.md"),
            "preferences": read_text(base / "preferences" / ".overview.md"),
        }

    def read_game_record() -> str:
        referee = workspace_root / "demo__werewolf__referee" / "GAME_RECORD.md"
        return read_text(referee)

    app_fastapi = FastAPI()

    @app_fastapi.get("/", response_class=HTMLResponse)
    def index():
        html = """
<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>狼人杀 Demo 观战</title>
  <style>
    body { font-family: "Press Start 2P", "Menlo", monospace; margin: 0; background: #0b0d12; color: #f2f2f2; }
    header { padding: 14px 24px; background: #121622; border-bottom: 4px solid #252a36; }
    h1 { margin: 0; font-size: 14px; letter-spacing: 1px; }
    .grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; padding: 16px 24px; }
    .card { background: #151a2a; border: 2px solid #30384c; border-radius: 10px; padding: 12px; box-shadow: 0 0 0 2px #0b0d12 inset; }
    .card h2 { margin: 0 0 8px 0; font-size: 11px; color: #9aa4b2; }
    pre { white-space: pre-wrap; word-break: break-word; background: #0f1320; padding: 8px; border-radius: 8px; border: 2px solid #2a3246; max-height: 360px; overflow: auto; font-size: 10px; }
    .arena { position: relative; height: 520px; background: radial-gradient(circle at center, #1a2033 0%, #0b0d12 70%); border: 2px solid #30384c; border-radius: 16px; }
    .table { position: absolute; left: 50%; top: 50%; width: 240px; height: 240px; transform: translate(-50%, -50%); border-radius: 50%; background: #2a3246; box-shadow: 0 0 0 6px #1b2135 inset, 0 0 0 2px #0b0d12; }
    .seat { position: absolute; width: 110px; text-align: center; font-size: 9px; }
    .avatar { width: 68px; height: 68px; margin: 0 auto 6px; border-radius: 8px; border: 2px solid #3f4a66; background: #1d2438; display: grid; place-items: center; box-shadow: 0 0 0 2px #0b0d12 inset; }
    .avatar .role { font-size: 8px; line-height: 1.2; }
    .bubble { position: absolute; background: #ffefc0; color: #1c1c1c; border: 2px solid #3a2e1b; padding: 6px 8px; border-radius: 8px; font-size: 8px; width: 140px; transform: translate(-50%, -100%); }
    .bubble:after { content: ""; position: absolute; left: 20px; bottom: -6px; border-width: 6px 6px 0; border-style: solid; border-color: #3a2e1b transparent transparent; }
    .bubble:before { content: ""; position: absolute; left: 22px; bottom: -4px; border-width: 4px 4px 0; border-style: solid; border-color: #ffefc0 transparent transparent; }
    .players { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .tabs { display: flex; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
    .tab { padding: 4px 8px; border-radius: 6px; background: #202533; cursor: pointer; font-size: 10px; }
    .tab.active { background: #3a4255; }
  </style>
  <script>
    async function fetchStatus() {
      const res = await fetch('/status');
      return res.json();
    }
    function setText(id, text) {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text || '';
    }
    function renderPlayers(players) {
      const container = document.getElementById('players');
      container.innerHTML = '';
      const arena = document.getElementById('arena');
      arena.innerHTML = '<div class="table"></div>';
      let latest = null;
      players.forEach(p => {
        if (!latest || (p.updated_at && p.updated_at > latest.updated_at)) latest = p;
      });

      const seatPositions = [
        { x: 50, y: 2 },
        { x: 75, y: 8 },
        { x: 92, y: 28 },
        { x: 92, y: 58 },
        { x: 75, y: 78 },
        { x: 50, y: 84 },
        { x: 25, y: 78 },
        { x: 8, y: 58 },
        { x: 8, y: 28 },
        { x: 25, y: 8 },
        { x: 50, y: 8 },
        { x: 50, y: 78 },
      ];

      players.forEach((p, i) => {
        const pos = seatPositions[i % seatPositions.length];
        const seat = document.createElement('div');
        seat.className = 'seat';
        seat.style.left = `calc(${pos.x}% - 55px)`;
        seat.style.top = `calc(${pos.y}% - 20px)`;

        const avatar = document.createElement('div');
        avatar.className = 'avatar';
        const role = document.createElement('div');
        role.className = 'role';
        role.textContent = p.role || '未知';
        avatar.appendChild(role);

        const label = document.createElement('div');
        label.textContent = p.id.replace('demo__werewolf__', '').replace('_', ' ');

        seat.appendChild(avatar);
        seat.appendChild(label);
        arena.appendChild(seat);

        if (latest && latest.id === p.id && p.last_speech) {
          const bubble = document.createElement('div');
          bubble.className = 'bubble';
          bubble.textContent = p.last_speech;
          bubble.style.left = '50%';
          bubble.style.top = '0%';
          seat.appendChild(bubble);
        }
      });

      players.forEach((p, idx) => {
        const card = document.createElement('div');
        card.className = 'card';
        const title = document.createElement('h2');
        title.textContent = p.id;
        card.appendChild(title);
        const tabs = document.createElement('div');
        tabs.className = 'tabs';
        const sections = [
          { key: 'game', label: 'GAME.md' },
          { key: 'memory', label: 'MEMORY.md' },
          { key: 'history', label: 'HISTORY.md' },
        ];
        const pre = document.createElement('pre');
        pre.textContent = p.game || '';
        sections.forEach((s, i) => {
          const tab = document.createElement('div');
          tab.className = 'tab' + (i === 0 ? ' active' : '');
          tab.textContent = s.label;
          tab.onclick = () => {
            tabs.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            pre.textContent = p[s.key] || '';
          };
          tabs.appendChild(tab);
        });
        card.appendChild(tabs);
        card.appendChild(pre);
        container.appendChild(card);
      });
    }
    async function refresh() {
      const data = await fetchStatus();
      setText('game_record', data.game_record);
      setText('world_overview', data.world_memory.overview);
      setText('world_entities', data.world_memory.entities);
      setText('world_events', data.world_memory.events);
      setText('world_preferences', data.world_memory.preferences);
      renderPlayers(data.players || []);
    }
    window.onload = () => {
      refresh();
      setInterval(refresh, 2000);
    };
  </script>
</head>
<body>
  <header>
    <h1>狼人杀 Demo 观战 + 记忆可视化</h1>
  </header>
  <div class="grid">
    <div class="card">
      <h2>像素狼人杀桌面</h2>
      <div class="arena" id="arena"></div>
    </div>
    <div class="card">
      <h2>裁判记录 GAME_RECORD.md</h2>
      <pre id="game_record"></pre>
    </div>
    <div class="card">
      <h2>世界记忆（OpenViking User Memory）</h2>
      <pre id="world_overview"></pre>
    </div>
    <div class="card">
      <h2>世界记忆 - 实体</h2>
      <pre id="world_entities"></pre>
    </div>
    <div class="card">
      <h2>世界记忆 - 事件</h2>
      <pre id="world_events"></pre>
    </div>
    <div class="card">
      <h2>世界记忆 - 偏好</h2>
      <pre id="world_preferences"></pre>
    </div>
    <div class="card">
      <h2>玩家视角（GAME.md / MEMORY.md / HISTORY.md）</h2>
      <div class="players" id="players"></div>
    </div>
  </div>
</body>
</html>
"""
        return HTMLResponse(content=html)

    @app_fastapi.get("/status", response_class=JSONResponse)
    def status():
        return JSONResponse(
            {
                "game_record": read_game_record(),
                "players": list_players(),
                "world_memory": read_world_memory(),
            }
        )

    console.print(f"{__logo__} Werewolf UI: http://localhost:{port}")
    uvicorn.run(app_fastapi, host="127.0.0.1", port=port, log_level="warning")


async def _ww_drain_outbound(bus: MessageBus) -> None:
    while True:
        msg = await bus.consume_outbound()
        _ = msg


def _ww_role_alignment(role: str) -> str:
    if role in ("Werewolf", "WhiteWolfKing"):
        return "wolf"
    return "village"


def _ww_check_winner(roster: list[_WerewolfPlayer], alive: dict[int, bool]) -> str | None:
    wolves_alive = 0
    villagers_alive = 0
    specials_alive = 0
    for p in roster:
        if not alive.get(p.seat, False):
            continue
        if _ww_role_alignment(p.role) == "wolf":
            wolves_alive += 1
        elif p.role == "Villager":
            villagers_alive += 1
        else:
            specials_alive += 1
    if wolves_alive == 0:
        return "好人胜利（所有狼人死亡）"
    if villagers_alive == 0 or specials_alive == 0:
        return "狼人胜利（好人阵营瓦解）"
    return None


def _ww_load_roster_from_workspace(workspace_root: Path) -> list[_WerewolfPlayer]:
    roster: list[_WerewolfPlayer] = []
    if not workspace_root.exists():
        return roster
    for item in sorted(workspace_root.iterdir()):
        if not item.is_dir() or not item.name.startswith("demo__werewolf__player_"):
            continue
        game_md = _ww_read_file(item / "GAME.md")
        seat = None
        name = None
        role = None
        for line in game_md.splitlines():
            if line.startswith("座位："):
                val = line.split("座位：", 1)[1].strip().replace("号", "")
                if val.isdigit():
                    seat = int(val) - 1
            elif line.startswith("昵称："):
                name = line.split("昵称：", 1)[1].strip()
            elif line.startswith("身份："):
                role = line.split("身份：", 1)[1].strip()
        if seat is None:
            try:
                seat = int(item.name.split("_")[-1]) - 1
            except Exception:
                seat = len(roster)
        if not name:
            name = f"Bot{seat + 1:02d}"
        if not role:
            role = "Villager"
        chat_id = item.name.split("demo__werewolf__", 1)[-1]
        roster.append(
            _WerewolfPlayer(
                seat=seat,
                name=name,
                role=role,
                session_key=SessionKey(type="demo", channel_id="werewolf", chat_id=chat_id),
            )
        )
    roster.sort(key=lambda p: p.seat)
    return roster


def _ww_compute_alive_from_record(record_path: Path, roster: list[_WerewolfPlayer]) -> dict[int, bool]:
    alive = {p.seat: True for p in roster}
    content = _ww_read_file(record_path)
    for line in content.splitlines():
        m = re.search(r"(昨夜死亡|放逐)：\s*(\d{1,2})号", line)
        if not m:
            continue
        seat = int(m.group(2)) - 1
        if seat in alive:
            alive[seat] = False
    return alive


def _ww_get_next_day_from_record(record_path: Path) -> int:
    content = _ww_read_file(record_path)
    day_nums = [int(m.group(1)) for m in re.finditer(r"第(\d+)天", content)]
    night_nums = [int(m.group(1)) for m in re.finditer(r"第(\d+)晚", content)]
    max_day = max(day_nums) if day_nums else 0
    max_night = max(night_nums) if night_nums else 0
    last = max(max_day, max_night)
    return max(1, last + 1)


async def _ww_run_badge_election(
    agent_loop: AgentLoop,
    rng: random.Random,
    roster: list[_WerewolfPlayer],
    alive: dict[int, bool],
    record_path: Path,
    day: int,
) -> None:
    _ww_append_record(record_path, "## 警长竞选：开始")
    alive_seats = _ww_alive_seats(alive)

    candidates: set[int] = set()
    for seat in alive_seats:
        p = roster[seat]
        header = _ww_player_prompt_header(day=day, phase="警长竞选报名与发言", player=p, roster=roster)
        task = (
            f"{header}\n"
            "任务：\n"
            "1) 决定是否竞选警长（是/否）\n"
            "2) 如果竞选，写一段竞选发言（2-4句）\n"
            "3) 把结果写入 GAME.md 的“操作历史”里，格式示例：\n"
            "## 警长竞选\n"
            "- 是否竞选：是\n"
            "- 竞选发言：...\n"
            "\n"
            "注意：不要在回复里贴发言全文，只把内容写进 GAME.md。\n"
        )
        await _ww_call_player(agent_loop, p, task)

        game_md = _ww_read_player_game_md(agent_loop.sandbox_manager, p.session_key)
        if "是否竞选：是" in game_md:
            candidates.add(seat)

    if not candidates:
        fallback = rng.choice(alive_seats) if alive_seats else None
        if fallback is not None:
            candidates.add(fallback)

    _ww_append_record(
        record_path,
        "候选人：" + "、".join([f"{s + 1}号" for s in sorted(candidates)]) if candidates else "候选人：无",
    )

    votes: dict[int, int] = {}
    for seat in alive_seats:
        p = roster[seat]
        header = _ww_player_prompt_header(day=day, phase="警长竞选投票", player=p, roster=roster)
        cand_list = "、".join([f"{s + 1}号" for s in sorted(candidates)])
        task = (
            f"{header}\n"
            f"候选人：{cand_list}\n"
            "任务：从候选人中投票给 1 人，把你的投票写入 GAME.md：\n"
            "## 警长投票\n"
            "- 投票：X号\n"
        )
        await _ww_call_player(agent_loop, p, task)
        game_md = _ww_read_player_game_md(agent_loop.sandbox_manager, p.session_key)
        vote = _ww_extract_vote_from_md(game_md, title="警长投票")
        if vote is None or (vote - 1) not in candidates:
            vote = rng.choice(sorted(candidates)) + 1
        votes[seat] = vote - 1

    tally: dict[int, float] = {}
    for voter_seat, target_seat in votes.items():
        weight = 1.0
        tally[target_seat] = tally.get(target_seat, 0.0) + weight

    winner_seat = _ww_pick_winner_from_tally(rng, tally)
    if winner_seat is None:
        return
    _ww_append_record(record_path, "警长竞选：结果\n" + _ww_format_tally(tally))
    _ww_append_record(record_path, f"警长当选：{winner_seat + 1}号")

    for p in roster:
        if p.seat == winner_seat:
            ws = agent_loop.sandbox_manager.get_workspace_path(p.session_key)
            game_md_path = ws / "GAME.md"
            game_md = _ww_read_file(game_md_path)
            game_md_path.write_text(game_md + "\n\n# 警长信息\n警长：是\n", encoding="utf-8")
        else:
            ws = agent_loop.sandbox_manager.get_workspace_path(p.session_key)
            game_md_path = ws / "GAME.md"
            game_md = _ww_read_file(game_md_path)
            if "\n# 警长信息\n" not in game_md:
                game_md_path.write_text(game_md + "\n\n# 警长信息\n警长：否\n", encoding="utf-8")


def _ww_read_player_game_md(sandbox_manager: SandboxManager, session_key: SessionKey) -> str:
    ws = sandbox_manager.get_workspace_path(session_key)
    return _ww_read_file(ws / "GAME.md")


def _ww_extract_vote_from_md(game_md: str, title: str) -> int | None:
    if not game_md:
        return None
    idx = game_md.rfind(f"## {title}")
    segment = game_md[idx:] if idx != -1 else game_md
    m = re.search(r"(?:投票|目标|击杀目标|查验目标|守护目标|毒药目标)：\s*(\d{1,2})\s*号", segment)
    if not m:
        n = _ww_parse_last_int(segment)
        return n
    try:
        return int(m.group(1), 10)
    except Exception:
        return None


def _ww_pick_winner_from_tally(rng: random.Random, tally: dict[int, float]) -> int | None:
    if not tally:
        return None
    max_score = max(tally.values())
    top = [seat for seat, score in tally.items() if score == max_score]
    return rng.choice(sorted(top))


def _ww_format_tally(tally: dict[int, float]) -> str:
    lines = []
    for seat, score in sorted(tally.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {seat + 1}号：{score}票")
    return "\n".join(lines)


def _ww_read_badge_holder(
    roster: list[_WerewolfPlayer], alive: dict[int, bool], sandbox_manager: SandboxManager
) -> int | None:
    for p in roster:
        if not alive.get(p.seat, False):
            continue
        game_md = _ww_read_player_game_md(sandbox_manager, p.session_key)
        if re.search(r"^警长：是\s*$", game_md, re.MULTILINE):
            return p.seat
    return None


async def _ww_run_night(
    agent_loop: AgentLoop,
    rng: random.Random,
    roster: list[_WerewolfPlayer],
    alive: dict[int, bool],
    record_path: Path,
    day: int,
    badge_holder: int | None,
) -> int | None:
    _ww_append_record(record_path, f"## 第{day}晚：开始")
    alive_seats = _ww_alive_seats(alive)

    wolves = [p for p in roster if alive.get(p.seat, False) and p.role in ("Werewolf", "WhiteWolfKing")]
    wolf_votes: dict[int, int] = {}
    for idx, wolf in enumerate(sorted(wolves, key=lambda x: x.seat)):
        existing = "、".join([f"{voter + 1}号→{target + 1}号" for voter, target in wolf_votes.items()]) or "无"
        ws = agent_loop.sandbox_manager.get_workspace_path(wolf.session_key)
        game_md_path = ws / "GAME.md"
        game_md = _ww_read_file(game_md_path)
        injected = (
            game_md
            + "\n\n## 裁判提示：狼人投票\n"
            + f"- 已有投票：{existing}\n"
            + "- 你需要选择一个击杀目标（存活玩家，不能是你自己）\n"
        )
        game_md_path.write_text(injected, encoding="utf-8")

        header = _ww_player_prompt_header(day=day, phase="黑夜第一轮-狼人投票", player=wolf, roster=roster)
        task = (
            f"{header}\n"
            "任务：选择击杀目标，写入 GAME.md：\n"
            "## 第{day}晚-狼人投票\n"
            "- 击杀目标：X号\n"
        ).replace("{day}", str(day))
        await _ww_call_player(agent_loop, wolf, task)

        game_md_after = _ww_read_file(game_md_path)
        vote = _ww_extract_vote_from_md(game_md_after, title=f"第{day}晚-狼人投票")
        if vote is None or (vote - 1) not in alive_seats or (vote - 1) == wolf.seat:
            vote = _ww_pick_random_target(rng, alive_seats, exclude={wolf.seat}) + 1
        wolf_votes[wolf.seat] = vote - 1

    wolf_tally: dict[int, float] = {}
    for wolf_seat, target in wolf_votes.items():
        weight = 1.0
        if badge_holder is not None and wolf_seat == badge_holder:
            weight = 1.5
        wolf_tally[target] = wolf_tally.get(target, 0.0) + weight
    wolf_target = _ww_pick_winner_from_tally(rng, wolf_tally)
    if wolf_target is None:
        wolf_target = _ww_pick_random_target(rng, alive_seats)
    _ww_append_record(record_path, f"狼人投票结果：\n{_ww_format_tally(wolf_tally)}\n击杀目标：{wolf_target + 1}号")

    guard_target = await _ww_run_role_target(
        agent_loop, rng, roster, alive, record_path, day, role="Guard", title="守卫守护", forbid_repeat_key=f"第{day-1}晚-守卫守护"
    )
    seer_target = await _ww_run_role_target(agent_loop, rng, roster, alive, record_path, day, role="Seer", title="预言家查验")
    witch_action = await _ww_run_witch(agent_loop, rng, roster, alive, record_path, day, wolf_target)

    protected = guard_target == wolf_target if guard_target is not None else False
    final_dead: int | None = wolf_target
    if protected:
        final_dead = None
    if witch_action.get("save") is True:
        final_dead = None
    poison_target = witch_action.get("poison_target")
    if poison_target is not None and alive.get(poison_target, False):
        if final_dead is None:
            final_dead = poison_target
        else:
            _ww_append_record(record_path, f"女巫毒杀：{poison_target + 1}号（演示版只记录，不额外并发死亡）")

    if seer_target is not None and alive.get(seer_target, False):
        seer = next((p for p in roster if alive.get(p.seat, False) and p.role == "Seer"), None)
        if seer is not None:
            is_wolf = _ww_role_alignment(roster[seer_target].role) == "wolf"
            ws = agent_loop.sandbox_manager.get_workspace_path(seer.session_key)
            game_md_path = ws / "GAME.md"
            game_md = _ww_read_file(game_md_path)
            game_md_path.write_text(
                game_md + f"\n\n## 第{day}晚-查验结果\n- 目标：{seer_target + 1}号\n- 结果：{'狼人' if is_wolf else '好人'}\n",
                encoding="utf-8",
            )

    return final_dead


async def _ww_run_role_target(
    agent_loop: AgentLoop,
    rng: random.Random,
    roster: list[_WerewolfPlayer],
    alive: dict[int, bool],
    record_path: Path,
    day: int,
    role: str,
    title: str,
    forbid_repeat_key: str | None = None,
) -> int | None:
    actor = next((p for p in roster if alive.get(p.seat, False) and p.role == role), None)
    if actor is None:
        return None

    alive_seats = _ww_alive_seats(alive)
    ws = agent_loop.sandbox_manager.get_workspace_path(actor.session_key)
    game_md_path = ws / "GAME.md"
    header = _ww_player_prompt_header(day=day, phase=f"黑夜第二轮-{title}", player=actor, roster=roster)
    extra = ""
    if forbid_repeat_key:
        prev = _ww_extract_vote_from_md(_ww_read_file(game_md_path), title=forbid_repeat_key)
        if prev is not None:
            extra = f"\n限制：不能连续两晚选择 {prev}号。\n"
    task = (
        f"{header}\n"
        f"{extra}"
        f"任务：选择目标（存活玩家，不能是你自己），写入 GAME.md：\n"
        f"## 第{day}晚-{title}\n"
        "- 目标：X号\n"
    )
    await _ww_call_player(agent_loop, actor, task)
    game_md = _ww_read_file(game_md_path)
    target = _ww_extract_vote_from_md(game_md, title=f"第{day}晚-{title}") or _ww_parse_last_int(game_md)
    if target is None:
        target = _ww_pick_random_target(rng, alive_seats, exclude={actor.seat}) + 1
    seat = target - 1
    if seat not in alive_seats or seat == actor.seat:
        seat = _ww_pick_random_target(rng, alive_seats, exclude={actor.seat})
    if forbid_repeat_key:
        prev = _ww_extract_vote_from_md(game_md, title=forbid_repeat_key)
        if prev is not None and prev - 1 == seat:
            seat = _ww_pick_random_target(rng, alive_seats, exclude={actor.seat, seat})
    _ww_append_record(record_path, f"{title}：{seat + 1}号")
    return seat


async def _ww_run_witch(
    agent_loop: AgentLoop,
    rng: random.Random,
    roster: list[_WerewolfPlayer],
    alive: dict[int, bool],
    record_path: Path,
    day: int,
    wolf_target: int,
) -> dict[str, int | bool | None]:
    witch = next((p for p in roster if alive.get(p.seat, False) and p.role == "Witch"), None)
    if witch is None:
        return {"save": None, "poison_target": None}

    alive_seats = _ww_alive_seats(alive)
    ws = agent_loop.sandbox_manager.get_workspace_path(witch.session_key)
    game_md_path = ws / "GAME.md"
    game_md = _ww_read_file(game_md_path)
    game_md_path.write_text(
        game_md + f"\n\n## 裁判提示：女巫信息\n- 今夜被杀：{wolf_target + 1}号\n",
        encoding="utf-8",
    )

    header = _ww_player_prompt_header(day=day, phase="黑夜第二轮-女巫", player=witch, roster=roster)
    task = (
        f"{header}\n"
        f"今夜被杀：{wolf_target + 1}号\n"
        "任务：决定是否用药，并写入 GAME.md：\n"
        f"## 第{day}晚-女巫\n"
        "- 解药：是/否\n"
        "- 毒药：是/否\n"
        "- 毒药目标：X号（如果毒药=是）\n"
    )
    await _ww_call_player(agent_loop, witch, task)
    after = _ww_read_file(game_md_path)

    idx = after.rfind(f"## 第{day}晚-女巫")
    seg = after[idx:] if idx != -1 else after
    save = None
    poison = None
    if re.search(r"解药：\s*是", seg):
        save = True
    if re.search(r"解药：\s*否", seg):
        save = False
    if re.search(r"毒药：\s*是", seg):
        poison = True
    if re.search(r"毒药：\s*否", seg):
        poison = False

    poison_target = None
    m = re.search(r"毒药目标：\s*(\d{1,2})\s*号", seg)
    if m:
        try:
            poison_target = int(m.group(1), 10) - 1
        except Exception:
            poison_target = None
    if poison is True and poison_target is None:
        poison_target = _ww_pick_random_target(rng, alive_seats, exclude={witch.seat}) 

    if poison_target is not None and (poison_target not in alive_seats or poison_target == witch.seat):
        poison_target = None

    _ww_append_record(
        record_path,
        "女巫操作："
        + (f"解药={'用' if save else '不用'}；" if save is not None else "")
        + (f"毒药={'用' if poison else '不用'}" if poison is not None else ""),
    )
    if poison_target is not None:
        _ww_append_record(record_path, f"女巫毒药目标：{poison_target + 1}号")

    return {"save": save, "poison_target": poison_target}


async def _ww_run_day(
    agent_loop: AgentLoop,
    rng: random.Random,
    roster: list[_WerewolfPlayer],
    alive: dict[int, bool],
    record_path: Path,
    day: int,
    badge_holder: int | None,
) -> None:
    _ww_append_record(record_path, f"## 第{day}天：开始")
    alive_seats = _ww_alive_seats(alive)

    for seat in alive_seats:
        p = roster[seat]
        header = _ww_player_prompt_header(day=day, phase="白天发言", player=p, roster=roster)
        task = (
            f"{header}\n"
            "任务：写一段白天发言（2-4句），写入 GAME.md：\n"
            f"## 第{day}天-发言\n"
            "- 发言：...\n"
        )
        await _ww_call_player(agent_loop, p, task)

    _ww_append_record(record_path, f"第{day}天发言：已完成（见各玩家 GAME.md）")

    votes: dict[int, int] = {}
    for seat in alive_seats:
        p = roster[seat]
        header = _ww_player_prompt_header(day=day, phase="白天投票", player=p, roster=roster)
        task = (
            f"{header}\n"
            "任务：投票放逐 1 名存活玩家（不能是你自己），写入 GAME.md：\n"
            f"## 第{day}天-投票\n"
            "- 投票：X号\n"
        )
        await _ww_call_player(agent_loop, p, task)
        game_md = _ww_read_player_game_md(agent_loop.sandbox_manager, p.session_key)
        vote = _ww_extract_vote_from_md(game_md, title=f"第{day}天-投票")
        if vote is None:
            vote = _ww_pick_random_target(rng, alive_seats, exclude={p.seat}) + 1
        target = vote - 1
        if target not in alive_seats or target == p.seat:
            target = _ww_pick_random_target(rng, alive_seats, exclude={p.seat})
        votes[seat] = target

    tally: dict[int, float] = {}
    for voter_seat, target_seat in votes.items():
        weight = 1.0
        if badge_holder is not None and voter_seat == badge_holder:
            weight = 1.5
        tally[target_seat] = tally.get(target_seat, 0.0) + weight

    out_seat = _ww_pick_winner_from_tally(rng, tally)
    if out_seat is None:
        return

    _ww_append_record(record_path, f"第{day}天投票结果：\n{_ww_format_tally(tally)}\n放逐：{out_seat + 1}号")
    alive[out_seat] = False



if __name__ == "__main__":
    app()
