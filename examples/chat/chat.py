#!/usr/bin/env python3
"""
Chat - Multi-turn conversation interface for OpenViking
"""

import sys
import signal
from typing import List, Dict, Any
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from recipe import Recipe
from rich.live import Live
from rich.spinner import Spinner
import threading

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


class ChatSession:
    """Manages in-memory conversation history"""

    def __init__(self):
        """Initialize empty conversation history"""
        self.history: List[Dict[str, Any]] = []

    def add_turn(self, question: str, answer: str, sources: List[Dict[str, Any]]) -> None:
        """
        Add a Q&A turn to history

        Args:
            question: User's question
            answer: Assistant's answer
            sources: List of source documents used
        """
        self.history.append(
            {
                "question": question,
                "answer": answer,
                "sources": sources,
                "turn": len(self.history) + 1,
            }
        )

    def clear(self) -> None:
        """Clear all conversation history"""
        self.history.clear()

    def get_turn_count(self) -> int:
        """Get number of turns in conversation"""
        return len(self.history)


class ChatREPL:
    """Interactive chat REPL"""

    def __init__(
        self,
        config_path: str = "./ov.conf",
        data_path: str = "./data",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_k: int = 5,
        score_threshold: float = 0.2,
    ):
        """Initialize chat REPL"""
        self.config_path = config_path
        self.data_path = data_path
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_k = top_k
        self.score_threshold = score_threshold

        self.recipe = None
        self.session = ChatSession()
        self.should_exit = False

        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle Ctrl-C gracefully"""
        console.print("\n")
        console.print(Panel("👋 Goodbye!", style="bold yellow", padding=(0, 1), width=PANEL_WIDTH))
        self.should_exit = True
        sys.exit(0)

    def run(self):
        """Main REPL loop"""
        pass  # To be implemented in Task 6

    def _show_welcome(self):
        """Display welcome banner"""
        console.clear()
        welcome_text = Text()
        welcome_text.append("🚀 OpenViking Chat\n\n", style="bold cyan")
        welcome_text.append("Multi-turn conversation powered by by RAG\n", style="white")
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
        help_text.append("   - Show this help message\n", style="white")
        help_text.append("/clear", style="bold yellow")
        help_text.append("  - Clear screen (keeps history)\n", style="white")
        help_text.append("/exit", style="bold yellow")
        help_text.append("   - Exit chat\n", style="white")
        help_text.append("/quit", style="bold yellow")
        help_text.append("   - Exit chat\n", style="white")
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
        cmd = cmd.strip().lower()

        if cmd in ["/exit", "/quit"]:
            console.print(
                Panel("👋 Goodbye!", style="bold yellow", padding=(0, 1), width=PANEL_WIDTH)
            )
            return True
        elif cmd == "/help":
            self._show_help()
        elif cmd == "/clear":
            console.clear()
            self._show_welcome()
        else:
            console.print(f"Unknown command: {cmd}", style="red")
            console.print("Type /help for available commands", style="dim")
            console.print()

        return False

    def ask_question(self, question: str) -> bool:
        """Ask a question and display the answer"""
        try:
            result = show_loading_with_spinner(
                "Thinking...",
                self.recipe.query,
                user_query=question,
                search_top_k=self.top_k,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                score_threshold=self.score_threshold,
            )

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
                from rich.table import Table
                from rich import box

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

            self.session.add_turn(question, result["answer"], result["context"])

            return True

        except Exception as e:
            console.print(
                Panel(f"❌ Error: {e}", style="bold red", padding=(0, 1), width=PANEL_WIDTH)
            )
            console.print()
            return False
