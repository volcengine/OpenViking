"""
Stats commands for Memex - knowledge base statistics.
"""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from ..client import MemexClient


class StatsCommands:
    """Statistics commands for the knowledge base."""

    def __init__(self, client: MemexClient, console: Console):
        """Initialize stats commands.

        Args:
            client: Memex client instance.
            console: Rich console for output.
        """
        self.client = client
        self.console = console

    def stats(self) -> None:
        """Display knowledge base statistics."""
        try:
            stats = self.client.get_stats()

            # Create main stats panel
            table = Table(title="Knowledge Base Statistics", show_header=True)
            table.add_column("Category", style="cyan")
            table.add_column("Metric", style="white")
            table.add_column("Value", style="green", justify="right")

            # Resources
            table.add_row("📚 Resources", "Total", str(stats["resources"]["count"]))
            for type_name, count in stats["resources"]["types"].items():
                table.add_row("", f"  {type_name}", str(count))

            # User
            table.add_row("👤 User", "Memories", str(stats["user"]["memories"]))

            # Agent
            table.add_row("🤖 Agent", "Skills", str(stats["agent"]["skills"]))
            table.add_row("", "Memories", str(stats["agent"]["memories"]))

            self.console.print(table)

        except Exception as e:
            self.console.print(f"[red]Error getting stats: {e}[/red]")

    def info(self) -> None:
        """Display system information."""
        config = self.client.config

        # Get VLM config for display
        try:
            vlm_config = config.get_vlm_config()
            llm_backend = vlm_config.get("backend", "unknown")
            llm_model = vlm_config.get("model", "unknown")
        except Exception:
            llm_backend = "not configured"
            llm_model = "not configured"

        info_text = f"""[cyan]Data Path:[/cyan] {config.data_path}
[cyan]Config Path:[/cyan] {config.config_path}
[cyan]User:[/cyan] {config.default_user}
[cyan]LLM Backend:[/cyan] {llm_backend}
[cyan]LLM Model:[/cyan] {llm_model}
[cyan]Search Top-K:[/cyan] {config.search_top_k}
[cyan]Score Threshold:[/cyan] {config.search_score_threshold}"""

        self.console.print(Panel(info_text, title="Memex Configuration", border_style="blue"))
