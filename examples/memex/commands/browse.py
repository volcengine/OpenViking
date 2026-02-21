"""
Browse commands for Memex - ls, tree, read, abstract, overview.
"""

from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree
from rich.table import Table
from rich.markdown import Markdown

from ..client import MemexClient


class BrowseCommands:
    """Browse commands for navigating the knowledge base."""

    def __init__(self, client: MemexClient, console: Console):
        """Initialize browse commands.

        Args:
            client: Memex client instance.
            console: Rich console for output.
        """
        self.client = client
        self.console = console

    def ls(self, uri: Optional[str] = None) -> None:
        """List contents of a directory.

        Args:
            uri: URI to list. Defaults to viking://resources/.
        """
        uri = uri or self.client.config.default_resource_uri

        try:
            items = self.client.ls(uri)

            if not items:
                self.console.print(f"[dim]Empty directory: {uri}[/dim]")
                return

            table = Table(title=f"Contents of {uri}", show_header=True)
            table.add_column("Name", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Size", style="yellow", justify="right")

            for item in items:
                name = item.get("name", "unknown")
                item_type = item.get("type", "unknown")
                size = item.get("size", "-")

                # Format type with icon
                if item_type == "directory":
                    type_display = "📁 dir"
                elif item_type == "file":
                    type_display = "📄 file"
                else:
                    type_display = f"📦 {item_type}"

                table.add_row(name, type_display, str(size))

            self.console.print(table)

        except Exception as e:
            self.console.print(f"[red]Error listing {uri}: {e}[/red]")

    def tree(self, uri: Optional[str] = None) -> None:
        """Display directory tree.

        Args:
            uri: URI to show tree for. Defaults to viking://resources/.
        """
        uri = uri or self.client.config.default_resource_uri

        try:
            tree_result = self.client.tree(uri)

            # Handle different return types
            if isinstance(tree_result, str):
                tree_str = tree_result
            elif isinstance(tree_result, list):
                # Build tree from list of items
                lines = []
                for item in tree_result:
                    if isinstance(item, dict):
                        name = item.get("name", "unknown")
                        is_dir = item.get("isDir", False)
                        prefix = "📁 " if is_dir else "📄 "
                        lines.append(f"{prefix}{name}")
                    else:
                        lines.append(str(item))
                tree_str = "\n".join(lines)
            else:
                tree_str = str(tree_result) if tree_result else ""

            if tree_str:
                self.console.print(Panel(tree_str, title=f"Tree: {uri}", border_style="blue"))
            else:
                self.console.print(f"[dim]Empty or not found: {uri}[/dim]")

        except Exception as e:
            self.console.print(f"[red]Error getting tree for {uri}: {e}[/red]")

    def read(self, uri: str) -> None:
        """Read full content of a resource (L2).

        Args:
            uri: URI of the resource to read.
        """
        if not uri:
            self.console.print("[red]Usage: /read <uri>[/red]")
            return

        try:
            content = self.client.read(uri)

            if content:
                # Try to render as markdown if it looks like markdown
                if uri.endswith(".md") or "```" in content or content.startswith("#"):
                    self.console.print(
                        Panel(Markdown(content), title=f"📄 {uri}", border_style="green")
                    )
                else:
                    self.console.print(Panel(content, title=f"📄 {uri}", border_style="green"))
            else:
                self.console.print(f"[dim]Empty content: {uri}[/dim]")

        except Exception as e:
            self.console.print(f"[red]Error reading {uri}: {e}[/red]")

    def abstract(self, uri: str) -> None:
        """Show abstract/summary of a resource (L0).

        Args:
            uri: URI of the resource.
        """
        if not uri:
            self.console.print("[red]Usage: /abstract <uri>[/red]")
            return

        # Remove trailing slash if present
        uri = uri.rstrip("/")

        try:
            content = self.client.abstract(uri)

            if content:
                self.console.print(
                    Panel(
                        content,
                        title=f"📝 Abstract: {uri}",
                        subtitle="[dim]L0 - Quick Summary[/dim]",
                        border_style="cyan",
                    )
                )
            else:
                self.console.print(f"[dim]No abstract available: {uri}[/dim]")

        except Exception as e:
            self.console.print(f"[red]Error getting abstract for {uri}: {e}[/red]")

    def overview(self, uri: str) -> None:
        """Show overview of a resource (L1).

        Args:
            uri: URI of the resource.
        """
        if not uri:
            self.console.print("[red]Usage: /overview <uri>[/red]")
            return

        # Remove trailing slash if present
        uri = uri.rstrip("/")

        try:
            content = self.client.overview(uri)

            if content:
                # Try to render as markdown
                self.console.print(
                    Panel(
                        Markdown(content)
                        if "```" in content or content.startswith("#")
                        else content,
                        title=f"📋 Overview: {uri}",
                        subtitle="[dim]L1 - Detailed Summary[/dim]",
                        border_style="yellow",
                    )
                )
            else:
                self.console.print(f"[dim]No overview available: {uri}[/dim]")

        except Exception as e:
            self.console.print(f"[red]Error getting overview for {uri}: {e}[/red]")

    def stat(self, uri: str) -> None:
        """Show metadata about a resource.

        Args:
            uri: URI of the resource.
        """
        if not uri:
            self.console.print("[red]Usage: /stat <uri>[/red]")
            return

        try:
            metadata = self.client.stat(uri)

            if metadata:
                table = Table(title=f"Metadata: {uri}", show_header=True)
                table.add_column("Property", style="cyan")
                table.add_column("Value", style="white")

                for key, value in metadata.items():
                    table.add_row(str(key), str(value))

                self.console.print(table)
            else:
                self.console.print(f"[dim]No metadata available: {uri}[/dim]")

        except Exception as e:
            self.console.print(f"[red]Error getting metadata for {uri}: {e}[/red]")
