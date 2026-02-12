#!/usr/bin/env python3
"""
Chat with Memory - Multi-turn conversation with persistent memory using OpenViking Session API
"""

import json
import os
import signal
import sys

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threading

import common.boring_logging_config  # noqa: F401
from common.recipe import Recipe
from prompt_toolkit import prompt
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style
from rich.live import Live
from rich.spinner import Spinner

from openviking import SyncOpenViking
from openviking.message import TextPart
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

console = Console()
PANEL_WIDTH = 78


def show_loading_with_spinner(message: str, target_func, *args, **kwargs):
    """Show a loading spinner while a function executes"""
    spinner = Spinner("dots", text=message)
    result = None
    exception = None

    def run_target():
        nonlocal result, exception
        try:
            result = target_func(*args, **kwargs)
        except Exception as e:
            exception = e

    thread = threading.Thread(target=run_target)
    thread.start()

    with Live(spinner, console=console, refresh_per_second=10, transient=True):
        thread.join()

    console.print()

    if exception:
        raise exception

    return result


class ChatREPL:
    """Interactive chat REPL with OpenViking Session API for persistent memory"""

    def __init__(
        self,
        config_path: str = "./ov.conf",
        data_path: str = "./data",
        session_id: str = "chat-interactive",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_k: int = 5,
        score_threshold: float = 0.2,
    ):
        """Initialize chat REPL"""
        self.config_path = config_path
        self.data_path = data_path
        self.session_id = session_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_k = top_k
        self.score_threshold = score_threshold

        # OpenViking client and session (initialized in run())
        self.client: SyncOpenViking = None
        self.session = None
        self.recipe: Recipe = None
        self.should_exit = False
        self._session_committed = False  # Track if session was already committed

        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle Ctrl-C gracefully"""
        console.print("\n")

        # Commit session before exit
        if self.session and not self._session_committed:
            console.print("[dim]💾 Saving session...[/dim]")
            try:
                commit_result = self.session.commit()
                self._session_committed = True  # Mark as committed
                memories = commit_result.get("memories_extracted", 0)
                if memories > 0:
                    console.print(f"[dim]✨ Extracted {memories} memories[/dim]")
            except Exception as e:
                console.print(f"[dim red]⚠️  Error saving session: {e}[/dim red]")

        console.print(Panel("👋 Goodbye!", style="bold yellow", padding=(0, 1), width=PANEL_WIDTH))
        self.should_exit = True
        sys.exit(0)

    def _show_welcome(self):
        """Display welcome banner"""
        console.clear()
        welcome_text = Text()
        welcome_text.append("🚀 OpenViking Chat with Memory\n\n", style="bold cyan")
        welcome_text.append("Multi-round conversation with persistent memory\n", style="white")
        welcome_text.append("Type ", style="dim")
        welcome_text.append("/help", style="bold yellow")
        welcome_text.append(" for commands or ", style="dim")
        welcome_text.append("/exit", style="bold yellow")
        welcome_text.append(" to quit", style="dim")

        console.print(Panel(welcome_text, style="bold", padding=(1, 2), width=PANEL_WIDTH))
        console.print()

    def _show_help(self):
        """Display help message"""
        help_text = Text()
        help_text.append("Available Commands:\n\n", style="bold cyan")
        help_text.append("/help", style="bold yellow")
        help_text.append("                - Show this help message\n", style="white")
        help_text.append("/clear", style="bold yellow")
        help_text.append("               - Clear screen (keeps history)\n", style="white")
        help_text.append("/time <question>", style="bold yellow")
        help_text.append("     - Ask question and show performance timing\n", style="white")
        help_text.append("/add_resource <path>", style="bold yellow")
        help_text.append(" - Add file/URL to database\n", style="white")
        help_text.append("/exit or /quit", style="bold yellow")
        help_text.append("       - Exit chat\n", style="white")
        help_text.append("\nKeyboard Shortcuts:\n\n", style="bold cyan")
        help_text.append("Ctrl-C", style="bold yellow")
        help_text.append("  - Exit gracefully\n", style="white")
        help_text.append("Ctrl-D", style="bold yellow")
        help_text.append("  - Exit\n", style="white")
        help_text.append("↑/↓", style="bold yellow")
        help_text.append("     - Navigate input history", style="white")

        console.print(
            Panel(help_text, title="Help", style="bold green", padding=(1, 2), width=PANEL_WIDTH)
        )
        console.print()

    def handle_command(self, cmd: str) -> bool:
        """
        Handle slash commands

        Args:
            cmd: Command string (e.g., "/help")

        Returns:
            True if should exit, False otherwise
        """
        cmd_lower = cmd.strip().lower()

        if cmd_lower in ["/exit", "/quit"]:
            console.print(
                Panel("👋 Goodbye!", style="bold yellow", padding=(0, 1), width=PANEL_WIDTH)
            )
            return True
        elif cmd_lower == "/help":
            self._show_help()
        elif cmd_lower == "/clear":
            console.clear()
            self._show_welcome()
        elif cmd.strip().startswith("/time"):
            # Extract question from command
            question = cmd.strip()[5:].strip()  # Remove "/time" prefix

            if not question:
                console.print("Usage: /time <your question>", style="yellow")
                console.print("Example: /time what is prompt engineering?", style="dim")
                console.print()
            else:
                self.ask_question(question, show_timing=True)
        elif cmd.strip().startswith("/add_resource"):
            # Extract resource path from command
            resource_path = cmd.strip()[13:].strip()  # Remove "/add_resource" prefix

            if not resource_path:
                console.print("Usage: /add_resource <path/to/file or URL>", style="yellow")
                console.print("Examples:", style="dim")
                console.print("  /add_resource ~/Downloads/paper.pdf", style="dim")
                console.print("  /add_resource https://example.com/doc.md", style="dim")
                console.print()
            else:
                # Import at usage time to avoid circular imports
                import os
                import sys
                from pathlib import Path

                sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                from common.resource_manager import add_resource

                # Expand user path
                if not resource_path.startswith("http"):
                    resource_path = str(Path(resource_path).expanduser())

                # Add resource with spinner
                success = show_loading_with_spinner(
                    "Adding resource...",
                    add_resource,
                    client=self.client,
                    resource_path=resource_path,
                    console=console,
                    show_output=True,
                )

                if success:
                    console.print()
                    console.print(
                        "💡 You can now ask questions about this resource!", style="dim green"
                    )

                console.print()
        else:
            console.print(f"Unknown command: {cmd}", style="red")
            console.print("Type /help for available commands", style="dim")
            console.print()

        return False

    def ask_question(self, question: str, show_timing: bool = False) -> bool:
        """Ask a question and display of answer"""

        # Record user message to session
        self.session.add_message("user", [TextPart(question)])

        try:
            # Convert session messages to chat history format for Recipe
            chat_history = []
            for msg in self.session.messages:
                if msg.role in ["user", "assistant"]:
                    content = msg.content if hasattr(msg, "content") else ""
                    chat_history.append({"role": msg.role, "content": content})

            result = show_loading_with_spinner(
                "Thinking...",
                self.recipe.query,
                user_query=question,
                search_top_k=self.top_k,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                score_threshold=self.score_threshold,
                chat_history=chat_history,
            )

            # Record assistant message to session
            self.session.add_message("assistant", [TextPart(result["answer"])])

            answer_text = Text(result["answer"], style="white")
            console.print(
                Panel(
                    answer_text,
                    title="💡 Answer",
                    style="bold bright_cyan",
                    padding=(1, 1),
                    width=PANEL_WIDTH,
                )
            )
            console.print()

            if result["context"]:
                from rich import box
                from rich.table import Table

                sources_table = Table(
                    title=f"📚 Sources ({len(result['context'])} documents)",
                    box=box.ROUNDED,
                    show_header=True,
                    header_style="bold magenta",
                    title_style="bold magenta",
                )
                sources_table.add_column("#", style="cyan", width=4)
                sources_table.add_column("File", style="bold white")
                sources_table.add_column("Relevance", style="green", justify="right")

                for i, ctx in enumerate(result["context"], 1):
                    uri_parts = ctx["uri"].split("/")
                    filename = uri_parts[-1] if uri_parts else ctx["uri"]
                    score_text = Text(f"{ctx['score']:.4f}", style="bold green")
                    sources_table.add_row(str(i), filename, score_text)

                console.print(sources_table)
                console.print()

            # Display timing panel if requested
            if show_timing and "timings" in result:
                from rich.table import Table

                timings = result["timings"]

                timing_table = Table(show_header=False, box=None, padding=(0, 2))
                timing_table.add_column("Metric", style="cyan")
                timing_table.add_column("Time", style="bold green", justify="right")

                timing_table.add_row("Search", f"{timings['search_time']:.3f}s")
                timing_table.add_row("LLM Generation", f"{timings['llm_time']:.3f}s")
                timing_table.add_row("Total", f"{timings['total_time']:.3f}s")

                console.print(
                    Panel(
                        timing_table,
                        title="⏱️  Performance",
                        style="bold blue",
                        padding=(0, 1),
                        width=PANEL_WIDTH,
                    )
                )
                console.print()

            return True

        except Exception as e:
            console.print(
                Panel(f"❌ Error: {e}", style="bold red", padding=(0, 1), width=PANEL_WIDTH)
            )
            console.print()
            return False

    def run(self):
        """Main REPL loop"""
        # Initialize OpenViking client
        try:
            with open(self.config_path, "r") as f:
                config_dict = json.load(f)
            config = OpenVikingConfig.from_dict(config_dict)

            self.client = SyncOpenViking(path=self.data_path, config=config)
            self.client.initialize()

            # Create/load session
            self.session = self.client.session(session_id=self.session_id)
            self.session.load()

            # Initialize recipe (same as before)
            self.recipe = Recipe(config_path=self.config_path, data_path=self.data_path)

        except Exception as e:
            console.print(Panel(f"❌ Error initializing: {e}", style="bold red", padding=(0, 1)))
            return

        # Show session info if continuing
        if self.session.messages:
            msg_count = len(self.session.messages)
            turn_count = len([m for m in self.session.messages if m.role == "user"])
            console.print(
                f"[dim]📝 Continuing from previous session: {turn_count} turns, {msg_count} messages[/dim]\n"
            )

        self._show_welcome()

        try:
            while not self.should_exit:
                try:
                    user_input = prompt(
                        HTML("<style fg='cyan'>You:</style> "), style=Style.from_dict({"": ""})
                    ).strip()

                    if not user_input:
                        continue

                    if user_input.startswith("/"):
                        if self.handle_command(user_input):
                            break
                        continue

                    self.ask_question(user_input)

                except (EOFError, KeyboardInterrupt):
                    console.print("\n")
                    console.print(
                        Panel("👋 Goodbye!", style="bold yellow", padding=(0, 1), width=PANEL_WIDTH)
                    )
                    break

        finally:
            # Commit session before cleanup (only if not already committed by signal handler)
            if self.session and not self._session_committed:
                console.print("\n[dim]💾 Saving session...[/dim]")
                try:
                    commit_result = self.session.commit()
                    self._session_committed = True
                    memories = commit_result.get("memories_extracted", 0)
                    if memories > 0:
                        console.print(f"[dim]{memories} memories processing... [/dim]")
                        self.client.wait_processed()  # critical, waiting to process memory embedding, timeout=inf
                        console.print(f"[dim green]✨ Extracted {memories} memories[/dim green]")
                except Exception as e:
                    console.print(f"[dim red]⚠️  Error saving session: {e}[/dim red]")

            # Cleanup resources
            if self.recipe:
                self.recipe.close()
            if self.client:
                self.client.close()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Multi-turn chat with persistent memory using OpenViking Session API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start chat with default session
  uv run chatmem.py

  # Use custom session ID
  uv run chatmem.py --session-id my-project

  # Enable debug logging
  OV_DEBUG=1 uv run chatmem.py
        """,
    )

    parser.add_argument("--config", type=str, default="./ov.conf", help="Path to config file")
    parser.add_argument("--data", type=str, default="./data", help="Path to data directory")
    parser.add_argument(
        "--session-id",
        type=str,
        default="chat-interactive",
        help="Session ID for memory (default: chat-interactive)",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Number of search results")
    parser.add_argument("--temperature", type=float, default=0.7, help="LLM temperature 0.0-1.0")
    parser.add_argument("--max-tokens", type=int, default=2048, help="Max tokens to generate")
    parser.add_argument("--score-threshold", type=float, default=0.2, help="Min relevance score")

    args = parser.parse_args()

    if not 0.0 <= args.temperature <= 1.0:
        console.print("❌ Temperature must be between 0.0 and 1.0", style="bold red")
        sys.exit(1)

    if args.top_k < 1:
        console.print("❌ top-k must be at least 1", style="bold red")
        sys.exit(1)

    if not 0.0 <= args.score_threshold <= 1.0:
        console.print("❌ score-threshold must be between 0.0 and 1.0", style="bold red")
        sys.exit(1)

    repl = ChatREPL(
        config_path=args.config,
        data_path=args.data,
        session_id=args.session_id,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        top_k=args.top_k,
        score_threshold=args.score_threshold,
    )

    repl.run()


if __name__ == "__main__":
    main()
