"""
Search commands for Memex - find, search, grep.
"""

from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..client import MemexClient


class SearchCommands:
    """Search commands for finding content in the knowledge base."""

    def __init__(self, client: MemexClient, console: Console):
        """Initialize search commands.

        Args:
            client: Memex client instance.
            console: Rich console for output.
        """
        self.client = client
        self.console = console

    def find(
        self,
        query: str,
        target_uri: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> None:
        """Quick semantic search.

        Args:
            query: Search query.
            target_uri: URI to search in.
            top_k: Number of results.
        """
        if not query:
            self.console.print("[red]Usage: /find <query> [uri][/red]")
            return

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=self.console,
            ) as progress:
                task = progress.add_task("Searching...", total=None)

                results = self.client.find(
                    query=query,
                    target_uri=target_uri,
                    top_k=top_k,
                )

                progress.update(task, description="Done!")

            self._display_results(results, query, "Find Results")

        except Exception as e:
            self.console.print(f"[red]Error searching: {e}[/red]")

    def search(
        self,
        query: str,
        target_uri: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> None:
        """Deep semantic search with intent analysis.

        Args:
            query: Search query.
            target_uri: URI to search in.
            top_k: Number of results.
        """
        if not query:
            self.console.print("[red]Usage: /search <query> [uri][/red]")
            return

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=self.console,
            ) as progress:
                task = progress.add_task("Analyzing intent and searching...", total=None)

                results = self.client.search(
                    query=query,
                    target_uri=target_uri,
                    top_k=top_k,
                )

                progress.update(task, description="Done!")

            self._display_results(results, query, "Search Results (Deep)")

        except Exception as e:
            self.console.print(f"[red]Error searching: {e}[/red]")

    def grep(self, uri: str, pattern: str) -> None:
        """Search content within resources using pattern.

        Args:
            uri: URI to search in.
            pattern: Pattern to search for.
        """
        if not pattern:
            self.console.print("[red]Usage: /grep <pattern> [uri][/red]")
            return

        uri = uri or self.client.config.default_resource_uri

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=self.console,
            ) as progress:
                task = progress.add_task(f"Searching for '{pattern}'...", total=None)

                results = self.client.grep(uri=uri, pattern=pattern)

                progress.update(task, description="Done!")

            if not results:
                self.console.print(f"[dim]No matches found for '{pattern}' in {uri}[/dim]")
                return

            table = Table(title=f"Grep Results: '{pattern}'", show_header=True)
            table.add_column("URI", style="cyan")
            table.add_column("Match", style="white")

            for result in results[:20]:  # Limit to 20 results
                result_uri = result.get("uri", "unknown")
                match = result.get("match", result.get("content", ""))
                # Truncate long matches
                if len(match) > 100:
                    match = match[:100] + "..."
                table.add_row(result_uri, match)

            self.console.print(table)

            if len(results) > 20:
                self.console.print(f"[dim]... and {len(results) - 20} more matches[/dim]")

        except Exception as e:
            self.console.print(f"[red]Error grepping: {e}[/red]")

    def glob(self, pattern: str, uri: Optional[str] = None) -> None:
        """Find resources matching a glob pattern.

        Args:
            pattern: Glob pattern (e.g., *.md, **/*.py).
            uri: Base URI to search in.
        """
        if not pattern:
            self.console.print("[red]Usage: /glob <pattern> [uri][/red]")
            return

        try:
            results = self.client.glob(pattern=pattern, uri=uri)

            if not results:
                self.console.print(f"[dim]No matches found for '{pattern}'[/dim]")
                return

            self.console.print(
                Panel("\n".join(results[:50]), title=f"Glob: {pattern}", border_style="blue")
            )

            if len(results) > 50:
                self.console.print(f"[dim]... and {len(results) - 50} more matches[/dim]")

        except Exception as e:
            self.console.print(f"[red]Error globbing: {e}[/red]")

    def _display_results(self, results, query: str, title: str) -> None:
        """Display search results.

        Args:
            results: Search results object or list.
            query: Original query.
            title: Title for the results panel.
        """
        # Extract resources from results
        resources = []
        if hasattr(results, "resources"):
            resources = results.resources
        elif isinstance(results, list):
            resources = results

        if not resources:
            self.console.print(f"[dim]No results found for: {query}[/dim]")
            return

        table = Table(title=title, show_header=True)
        table.add_column("#", style="dim", width=3)
        table.add_column("URI", style="cyan")
        table.add_column("Score", style="green", justify="right")
        table.add_column("Preview", style="white", max_width=50)

        for i, r in enumerate(resources[:10], 1):
            if hasattr(r, "uri"):
                uri = r.uri
                score = f"{r.score:.3f}" if hasattr(r, "score") else "-"
                content = r.content if hasattr(r, "content") else ""
            elif isinstance(r, dict):
                uri = r.get("uri", "unknown")
                score = f"{r.get('score', 0):.3f}"
                content = r.get("content", "")
            else:
                uri = str(r)
                score = "-"
                content = ""

            # Truncate content for preview
            preview = content[:100] + "..." if len(content) > 100 else content
            preview = preview.replace("\n", " ")

            table.add_row(str(i), uri, score, preview)

        self.console.print(table)

        if len(resources) > 10:
            self.console.print(f"[dim]Showing top 10 of {len(resources)} results[/dim]")
