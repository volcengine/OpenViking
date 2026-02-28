"""CLI commands for vikingbot."""

import asyncio
import json
import os
import signal
from multiprocessing.spawn import prepare
from pathlib import Path
import select
import sys
from xml.etree.ElementPath import prepare_self
from loguru import logger
import typer
from jinja2.filters import prepare_map
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from vikingbot.config.loader import load_config, ensure_config, get_data_dir, get_config_path
from vikingbot.bus.queue import MessageBus
from vikingbot.agent.loop import AgentLoop

from vikingbot.session.manager import SessionManager
from vikingbot.cron.service import CronService
from vikingbot.cron.types import CronJob
from vikingbot.heartbeat.service import HeartbeatService
from vikingbot import __version__, __logo__
from vikingbot.config.schema import SessionKey

# Create sandbox manager
from vikingbot.sandbox.manager import SandboxManager
from vikingbot.utils.helpers import get_source_workspace_path
from vikingbot.channels.manager import ChannelManager


app = typer.Typer(
    name="vikingbot",
    help=f"{__logo__} vikingbot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

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

    history_file = Path.home() / ".vikingbot" / "history" / "cli_history"
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
                HTML("<b fg='ansiblue'>You:</b> "),
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


def _make_provider(config):
    """Create LiteLLMProvider from config. Allows starting without API key."""
    from vikingbot.providers.litellm_provider import LiteLLMProvider

    p = config.get_provider()
    model = config.agents.defaults.model
    api_key = p.api_key if p else None
    api_base = config.get_api_base()
    provider_name = config.get_provider_name()

    if not (api_key) and not model.startswith("bedrock/"):
        console.print("[yellow]Warning: No API key configured.[/yellow]")
        console.print("You can configure providers later in the Console UI.")

    return LiteLLMProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=provider_name,
    )


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    console_port: int = typer.Option(18791, "--console-port", help="Console web UI port"),
    enable_console: bool = typer.Option(
        True, "--console/--no-console", help="Enable console web UI"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the vikingbot gateway."""

    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    bus = MessageBus()
    config = ensure_config()
    session_manager = SessionManager(config.workspace_path)

    cron = prepare_cron(bus)
    channels = prepare_channel(config, bus)
    agent_loop = prepare_agent_loop(config, bus, session_manager, cron)
    heartbeat = prepare_heartbeat(config, agent_loop, session_manager)

    async def run():
        tasks = []
        tasks.append(cron.start())
        tasks.append(heartbeat.start())
        tasks.append(channels.start_all())
        tasks.append(agent_loop.run())
        if enable_console:
            tasks.append(start_console(console_port))

        await asyncio.gather(*tasks)

    asyncio.run(run())


def prepare_agent_loop(config, bus, session_manager, cron):
    sandbox_parent_path = config.workspace_path
    source_workspace_path = get_source_workspace_path()
    sandbox_manager = SandboxManager(config, sandbox_parent_path, source_workspace_path)
    console.print(
        f"[green]✓[/green] Sandbox: enabled (backend={config.sandbox.backend}, mode={config.sandbox.mode})"
    )
    provider = _make_provider(config)
    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        brave_api_key=config.tools.web.search.api_key or None,
        exa_api_key=None,
        gen_image_model=config.agents.defaults.gen_image_model,
        exec_config=config.tools.exec,
        cron_service=cron,
        session_manager=session_manager,
        sandbox_manager=sandbox_manager,
        config=config,
    )
    # Set the agent reference in cron if it uses the holder pattern
    if hasattr(cron, '_agent_holder'):
        cron._agent_holder['agent'] = agent
    return agent


def prepare_cron(bus) -> CronService:
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
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    return cron


def prepare_channel(config, bus):

    channels = ChannelManager(config, bus)
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
    try:
        import subprocess
        import sys
        import os

        def start_gradio():
            script_path = os.path.join(
                os.path.dirname(__file__), "..", "console", "console_gradio_simple.py"
            )
            subprocess.Popen([sys.executable, script_path, str(console_port)])

        start_gradio()
    except Exception as e:
        console.print(f"[yellow]Warning: Gradio not available ({e})[/yellow]")


# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli__default__direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(
        True, "--markdown/--no-markdown", help="Render assistant output as Markdown"
    ),
    logs: bool = typer.Option(
        False, "--logs/--no-logs", help="Show vikingbot runtime logs during chat"
    ),
):
    """Interact with the agent directly."""
    if logs:
        logger.enable("vikingbot")
    else:
        logger.disable("vikingbot")

    session_key = SessionKey.from_safe_name(session_id)

    bus = MessageBus()
    config = ensure_config()
    session_manager = SessionManager(config.workspace_path)

    cron = prepare_cron(bus)
    agent_loop = prepare_agent_loop(config, bus, session_manager, cron)

    # Show spinner when logs are off (no output to miss); skip when logs are on
    def _thinking_ctx():
        if logs:
            from contextlib import nullcontext

            return nullcontext()
        # Animated spinner is safe to use with prompt_toolkit input handling
        return console.status("[dim]vikingbot is thinking...[/dim]", spinner="dots")

    if message:
        # Single message mode
        async def run_once():
            with _thinking_ctx():
                response = await agent_loop.process_direct(message, session_key=session_key)
            _print_agent_response(response, render_markdown=markdown)

        asyncio.run(run_once())
    else:
        # Interactive mode
        _init_prompt_session()
        console.print(
            f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n"
        )

        def _exit_on_sigint(signum, frame):
            _restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _exit_on_sigint)

        async def run_interactive():
            while True:
                try:
                    _flush_pending_tty_input()
                    user_input = await _read_interactive_input_async()
                    command = user_input.strip()
                    if not command:
                        continue

                    if _is_exit_command(command):
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break

                    with _thinking_ctx():
                        response = await agent_loop.process_direct(
                            user_input, session_key=session_key
                        )
                    _print_agent_response(response, render_markdown=markdown)
                except KeyboardInterrupt:
                    _restore_terminal()
                    console.print("\nGoodbye!")
                    break
                except EOFError:
                    _restore_terminal()
                    console.print("\nGoodbye!")
                    break

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from vikingbot.config.loader import load_config
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
    user_bridge = Path.home() / ".vikingbot" / "bridge"

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
        console.print("Try reinstalling: pip install --force-reinstall vikingbot")
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
    from vikingbot.config.loader import load_config
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

    import time

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

    session_key = SessionKey.from_safe_name()

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
        console.print(f"[green]✓[/green] Job executed")
    else:
        console.print(f"[red]Failed to run job {job_id}[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show vikingbot status."""
    from vikingbot.config.loader import load_config, get_config_path

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

        console.print(f"Model: {config.agents.defaults.model}")

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


@app.command()
def tui(
    console_port: int = typer.Option(18791, "--console-port", help="Console web UI port"),
    enable_console: bool = typer.Option(
        True, "--console/--no-console", help="Enable console web UI"
    ),
):
    """Launch vikingbot TUI interface interface."""
    """Interact with the agent directly."""
    logger.enable("vikingbot")
    if enable_console:
        console.print(f"[green]✓[/green] Console: http://localhost:{console_port} ")

    bus = MessageBus()
    config = ensure_config()
    session_manager = SessionManager(config.workspace_path)

    cron = prepare_cron(bus)
    agent_loop = prepare_agent_loop(config, bus, session_manager, cron)

    async def run():
        tasks = []
        from vikingbot.tui.app import run_tui

        tasks.append(run_tui(agent_loop, bus, config))
        await asyncio.gather(*tasks)

    asyncio.run(run())


if __name__ == "__main__":
    app()
