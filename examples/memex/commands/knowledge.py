"""
Knowledge management commands for Memex - add, rm, import.
"""

import os
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel

from ..client import MemexClient


class KnowledgeCommands:
    """Knowledge management commands for adding and removing resources."""

    def __init__(self, client: MemexClient, console: Console):
        """Initialize knowledge commands.

        Args:
            client: Memex client instance.
            console: Rich console for output.
        """
        self.client = client
        self.console = console

    def add(
        self,
        path: str,
        target: Optional[str] = None,
        reason: Optional[str] = None,
        instruction: Optional[str] = None,
        wait: bool = True,
    ) -> None:
        if not path:
            self.console.print("[red]Usage: /add <path> [target] [reason][/red]")
            return

        if path.startswith("~"):
            path = os.path.expanduser(path)

        if not path.startswith(("http://", "https://")) and not os.path.exists(path):
            self.console.print(f"[red]Path not found: {path}[/red]")
            return

        is_dir = os.path.isdir(path)
        if is_dir:
            self._add_directory(path, target, reason, instruction, wait)
        else:
            self._add_file(path, target, reason, instruction, wait)

    def _add_file(
        self,
        path: str,
        target: Optional[str] = None,
        reason: Optional[str] = None,
        instruction: Optional[str] = None,
        wait: bool = True,
    ) -> None:
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=self.console,
            ) as progress:
                task = progress.add_task(f"Adding {path}...", total=None)

                result = self.client.add_resource(
                    path=path,
                    target=target,
                    reason=reason,
                    instruction=instruction,
                )

                status = result.get("status", "unknown")
                errors = result.get("errors", [])
                root_uri = result.get("root_uri", "unknown")

                if status == "error" or errors:
                    error_msg = errors[0] if errors else "Unknown error"
                    self.console.print(f"[red]Error: {error_msg}[/red]")
                    return

                progress.update(task, description=f"Added to {root_uri}")

                if wait:
                    progress.update(task, description="Processing...")
                    self.client.wait_processed(timeout=120)
                    progress.update(task, description="Done!")

            self.console.print(
                Panel(
                    f"[green]✓[/green] Added: {path}\n[cyan]URI:[/cyan] {root_uri}",
                    title="Resource Added",
                    border_style="green",
                )
            )

        except Exception as e:
            self.console.print(f"[red]Error adding resource: {e}[/red]")

    def _add_directory(
        self,
        directory: str,
        target: Optional[str] = None,
        reason: Optional[str] = None,
        instruction: Optional[str] = None,
        wait: bool = True,
    ) -> None:
        supported_extensions = {
            ".txt",
            ".md",
            ".markdown",
            ".rst",
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".java",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".go",
            ".rs",
            ".rb",
            ".php",
            ".swift",
            ".kt",
            ".scala",
            ".sh",
            ".bash",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".xml",
            ".html",
            ".css",
            ".scss",
            ".pdf",
            ".doc",
            ".docx",
        }

        files_to_add = []
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for file in files:
                if file.startswith("."):
                    continue
                ext = os.path.splitext(file)[1].lower()
                if ext in supported_extensions:
                    files_to_add.append(os.path.join(root, file))

        if not files_to_add:
            self.console.print(f"[yellow]No supported files found in {directory}[/yellow]")
            return

        self.console.print(f"[dim]Found {len(files_to_add)} files to add...[/dim]")

        success_count = 0
        error_count = 0
        added_uris = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
        ) as progress:
            task = progress.add_task(f"Adding files from {directory}...", total=len(files_to_add))

            for i, file_path in enumerate(files_to_add):
                progress.update(
                    task,
                    description=f"Adding ({i + 1}/{len(files_to_add)}): {os.path.basename(file_path)}",
                )

                try:
                    result = self.client.add_resource(
                        path=file_path,
                        target=target,
                        reason=reason or f"Imported from {directory}",
                        instruction=instruction,
                    )

                    status = result.get("status", "unknown")
                    errors = result.get("errors", [])

                    if status == "error" or errors:
                        error_count += 1
                    else:
                        success_count += 1
                        root_uri = result.get("root_uri", "")
                        if root_uri:
                            added_uris.append(root_uri)

                except Exception:
                    error_count += 1

                progress.advance(task)

            if wait and success_count > 0:
                progress.update(task, description="Processing...")
                self.client.wait_processed(timeout=300)

            progress.update(task, description="Done!")

        self.console.print(
            Panel(
                f"[green]✓[/green] Added {success_count} files from {directory}\n"
                f"[red]✗[/red] Failed: {error_count} files",
                title="Directory Import Complete",
                border_style="green" if error_count == 0 else "yellow",
            )
        )

    def rm(self, uri: str, recursive: bool = False) -> None:
        """Remove a resource from the knowledge base.

        Args:
            uri: URI of the resource to remove.
            recursive: Whether to remove recursively.
        """
        if not uri:
            self.console.print("[red]Usage: /rm <uri> [-r][/red]")
            return

        try:
            self.client.remove(uri=uri, recursive=recursive)
            self.console.print(f"[green]✓[/green] Removed: {uri}")

        except Exception as e:
            self.console.print(f"[red]Error removing {uri}: {e}[/red]")

    def import_dir(
        self,
        directory: str,
        target: Optional[str] = None,
        reason: Optional[str] = None,
        wait: bool = True,
    ) -> None:
        if not directory:
            self.console.print("[red]Usage: /import <directory> [target][/red]")
            return

        if directory.startswith("~"):
            directory = os.path.expanduser(directory)

        if not os.path.isdir(directory):
            self.console.print(f"[red]Not a directory: {directory}[/red]")
            return

        self._add_directory(directory, target, reason, wait=wait)

    def add_url(
        self,
        url: str,
        target: Optional[str] = None,
        reason: Optional[str] = None,
        wait: bool = True,
    ) -> None:
        """Add a URL resource to the knowledge base.

        Args:
            url: URL to add.
            target: Target URI in viking://.
            reason: Reason for adding.
            wait: Whether to wait for processing.
        """
        if not url:
            self.console.print("[red]Usage: /url <url> [target][/red]")
            return

        if not url.startswith(("http://", "https://")):
            self.console.print("[red]Invalid URL. Must start with http:// or https://[/red]")
            return

        self.add(path=url, target=target, reason=reason, wait=wait)
