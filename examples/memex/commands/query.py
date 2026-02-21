"""
Query commands for Memex - ask, chat with RAG.
"""

from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..client import MemexClient
from ..rag.recipe import MemexRecipe


class QueryCommands:
    """Query commands for RAG-based Q&A."""

    def __init__(self, client: MemexClient, console: Console):
        self.client = client
        self.console = console
        self._recipe: Optional[MemexRecipe] = None
        self._chat_mode = False

    @property
    def recipe(self) -> MemexRecipe:
        if self._recipe is None:
            self._recipe = MemexRecipe(self.client)
            self._recipe.start_session()
        return self._recipe

    def ask(self, query: str, target_uri: Optional[str] = None) -> None:
        if not query:
            self.console.print("[red]Usage: /ask <question>[/red]")
            return

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=self.console,
            ) as progress:
                task = progress.add_task("Searching knowledge base...", total=None)

                response = self.recipe.query(
                    user_query=query,
                    target_uri=target_uri,
                    use_chat_history=False,
                )

                progress.update(task, description="Done!")

            self.console.print()
            self.console.print(
                Panel(
                    Markdown(response),
                    title="[bold cyan]Memex[/bold cyan]",
                    border_style="cyan",
                )
            )

        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

    def chat(self, query: str, target_uri: Optional[str] = None) -> None:
        if not query:
            self._chat_mode = not self._chat_mode
            if self._chat_mode:
                self.console.print(
                    "[green]Chat mode enabled. Type your questions directly.[/green]"
                )
                self.console.print(
                    "[dim]Use /clear to clear history, /exit to exit chat mode.[/dim]"
                )
            else:
                self.console.print("[yellow]Chat mode disabled.[/yellow]")
            return

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=self.console,
            ) as progress:
                task = progress.add_task("Thinking...", total=None)

                response = self.recipe.query(
                    user_query=query,
                    target_uri=target_uri,
                    use_chat_history=True,
                )

                progress.update(task, description="Done!")

            self.console.print()
            self.console.print(
                Panel(
                    Markdown(response),
                    title="[bold cyan]Memex[/bold cyan]",
                    border_style="cyan",
                )
            )

        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

    def clear_history(self) -> None:
        self.recipe.clear_history()
        self.console.print("[green]Chat history cleared.[/green]")

    @property
    def chat_mode(self) -> bool:
        return self._chat_mode

    @chat_mode.setter
    def chat_mode(self, value: bool) -> None:
        self._chat_mode = value

    def process_input(self, user_input: str, target_uri: Optional[str] = None) -> None:
        if self._chat_mode:
            self.chat(user_input, target_uri)
        else:
            self.ask(user_input, target_uri)

    def show_session_info(self) -> None:
        session = self.recipe.session
        if not session:
            self.console.print("[yellow]No active session.[/yellow]")
            return

        session_id = self.recipe.session_id
        msg_count = len(session.messages) if hasattr(session, "messages") else 0

        self.console.print(
            Panel(
                f"Session ID: {session_id}\n"
                f"Messages: {msg_count}\n"
                f"Chat history: {len(self.recipe.chat_history)} turns",
                title="[bold cyan]Session Info[/bold cyan]",
                border_style="cyan",
            )
        )

    def commit_session(self) -> None:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
        ) as progress:
            task = progress.add_task("Extracting memories...", total=None)

            result = self.recipe.end_session()

            progress.update(task, description="Done!")

        if result.get("status") == "no_session":
            self.console.print("[yellow]No active session to commit.[/yellow]")
        elif result.get("status") == "error":
            self.console.print(f"[red]Error: {result.get('error')}[/red]")
        else:
            memories = result.get("memories_extracted", 0)
            self.console.print(
                Panel(
                    f"Session committed!\n"
                    f"Memories extracted: {memories}\n"
                    f"Status: {result.get('status', 'unknown')}",
                    title="[bold green]Session Committed[/bold green]",
                    border_style="green",
                )
            )
            self._recipe.start_session()

    def show_memories(self) -> None:
        try:
            user_memories = self.client.ls("viking://user/memories/")
            agent_memories = self.client.ls("viking://agent/memories/")

            output = "## User Memories\n"
            for item in user_memories:
                name = item.get("name", str(item))
                output += f"- {name}\n"

            output += "\n## Agent Memories\n"
            for item in agent_memories:
                name = item.get("name", str(item))
                output += f"- {name}\n"

            self.console.print(
                Panel(
                    Markdown(output),
                    title="[bold cyan]Extracted Memories[/bold cyan]",
                    border_style="cyan",
                )
            )
        except Exception as e:
            self.console.print(f"[red]Error listing memories: {e}[/red]")
