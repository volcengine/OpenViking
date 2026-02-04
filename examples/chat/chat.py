#!/usr/bin/env python3
"""
Chat - Multi-turn conversation interface for OpenViking
"""

import os
import signal
import sys
from typing import Any, Dict, List

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threading

from common.recipe import Recipe
from prompt_toolkit import prompt
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style
from rich.live import Live
from rich.spinner import Spinner

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

    def get_chat_history(self) -> List[Dict[str, str]]:
        """
        Get conversation history in OpenAI chat completion format

        Returns:
            List of message dicts with 'role' and 'content' keys
            Format: [{"role": "user", "content": "..."},
                      {"role": "assistant", "content": "..."}]
        """
        history = []
        for turn in self.history:
            history.append({"role": "user", "content": turn["question"]})
            history.append({"role": "assistant", "content": turn["answer"]})
        return history


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

    def _show_welcome(self):
        """Display welcome banner"""
        console.clear()
        welcome_text = Text()
        welcome_text.append("🚀 OpenViking Chat\n\n", style="bold cyan")
        welcome_text.append("Multi-round conversation\n", style="white")
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
        """Ask a question and display of answer"""
        try:
            chat_history = self.session.get_chat_history()
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

            self.session.add_turn(question, result["answer"], result["context"])

            return True

        except Exception as e:
            console.print(
                Panel(f"❌ Error: {e}", style="bold red", padding=(0, 1), width=PANEL_WIDTH)
            )
            console.print()
            return False

    def run(self):
        """Main REPL loop"""
        try:
            self.recipe = Recipe(config_path=self.config_path, data_path=self.data_path)
        except Exception as e:
            console.print(Panel(f"❌ Error initializing: {e}", style="bold red", padding=(0, 1)))
            return

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
            if self.recipe:
                self.recipe.close()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Multi-turn chat with OpenViking RAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start chat with default settings
  uv run chat.py

  # Adjust creativity
  uv run chat.py --temperature 0.9

  # Use more context
  uv run chat.py --top-k 10

  # Enable debug logging
  OV:DEBUG=1 uv run chat.py
        """,
    )

    parser.add_argument("--config", type=str, default="./ov.conf", help="Path to config file")
    parser.add_argument("--data", type=str, default="./data", help="Path to data directory")
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
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        top_k=args.top_k,
        score_threshold=args.score_threshold,
    )

    repl.run()


if __name__ == "__main__":
    main()
