"""
Feishu/Lark MCP integration for Memex.

This module provides integration with Feishu (Lark) through the official MCP server.
It allows importing documents, messages, and other content from Feishu into Memex.

Requirements:
- Node.js (for npx)
- Feishu App credentials (app_id, app_secret)

Usage:
    from memex.feishu import FeishuMCP

    feishu = FeishuMCP(app_id="cli_xxx", app_secret="xxx")
    feishu.start()

    # Read a document
    content = feishu.read_document(document_id="xxx")

    # Search documents
    results = feishu.search_documents(query="keyword")

    feishu.stop()
"""

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from rich.console import Console


@dataclass
class FeishuConfig:
    """Feishu MCP configuration."""

    app_id: str
    app_secret: str
    auth_mode: str = "tenant"  # "tenant", "user", or "auto"
    tools: list[str] | None = None  # Specific tools to enable, None for all

    @classmethod
    def from_env(cls) -> "FeishuConfig":
        """Create config from environment variables."""
        app_id = os.getenv("FEISHU_APP_ID") or os.getenv("LARK_APP_ID")
        app_secret = os.getenv("FEISHU_APP_SECRET") or os.getenv("LARK_APP_SECRET")

        if not app_id or not app_secret:
            raise ValueError(
                "Feishu credentials not found. Set FEISHU_APP_ID and FEISHU_APP_SECRET environment variables."
            )

        return cls(
            app_id=app_id,
            app_secret=app_secret,
            auth_mode=os.getenv("FEISHU_AUTH_MODE", "tenant"),
        )


class FeishuMCPClient:
    """
    Feishu MCP Client - communicates with lark-openapi-mcp server.

    This client manages the MCP server process and provides methods to call
    Feishu APIs through the MCP protocol.
    """

    def __init__(self, config: Optional[FeishuConfig] = None, console: Optional[Console] = None):
        """Initialize Feishu MCP client.

        Args:
            config: Feishu configuration. If None, loads from environment.
            console: Rich console for output.
        """
        self.config = config or FeishuConfig.from_env()
        self.console = console or Console()
        self._process: Optional[subprocess.Popen] = None
        self._running = False

    def _build_command(self) -> list[str]:
        """Build the npx command to start the MCP server."""
        cmd = [
            "npx",
            "-y",
            "@larksuiteoapi/lark-mcp",
            "mcp",
            "-a",
            self.config.app_id,
            "-s",
            self.config.app_secret,
        ]

        # Add auth mode
        if self.config.auth_mode != "tenant":
            cmd.extend(["--auth-mode", self.config.auth_mode])

        # Add specific tools if configured
        if self.config.tools:
            for tool in self.config.tools:
                cmd.extend(["-t", tool])

        return cmd

    def start(self) -> bool:
        """Start the MCP server process.

        Returns:
            True if started successfully.
        """
        if self._running:
            self.console.print("[yellow]Feishu MCP server already running[/yellow]")
            return True

        try:
            cmd = self._build_command()
            self.console.print(f"[dim]Starting Feishu MCP server...[/dim]")

            # Start the process with stdio transport
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            # Give it a moment to start
            time.sleep(2)

            # Check if process is still running
            if self._process.poll() is not None:
                stderr = self._process.stderr.read() if self._process.stderr else ""
                self.console.print(f"[red]Failed to start Feishu MCP server: {stderr}[/red]")
                return False

            self._running = True
            self.console.print("[green]Feishu MCP server started[/green]")
            return True

        except FileNotFoundError:
            self.console.print("[red]npx not found. Please install Node.js.[/red]")
            return False
        except Exception as e:
            self.console.print(f"[red]Error starting Feishu MCP server: {e}[/red]")
            return False

    def stop(self) -> None:
        """Stop the MCP server process."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        self._running = False
        self.console.print("[dim]Feishu MCP server stopped[/dim]")

    def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request to the MCP server.

        Args:
            method: MCP method name.
            params: Method parameters.

        Returns:
            Response from the server.
        """
        if not self._running or not self._process:
            raise RuntimeError("Feishu MCP server not running. Call start() first.")

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }

        try:
            # Send request
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()

            # Read response
            response_line = self._process.stdout.readline()
            if response_line:
                return json.loads(response_line)
            else:
                raise RuntimeError("No response from MCP server")

        except Exception as e:
            raise RuntimeError(f"Error communicating with MCP server: {e}")

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call.
            arguments: Tool arguments.

        Returns:
            Tool result.
        """
        response = self._send_request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
            },
        )

        if "error" in response:
            raise RuntimeError(f"Tool error: {response['error']}")

        return response.get("result", {})

    def list_tools(self) -> list[dict[str, Any]]:
        """List available tools.

        Returns:
            List of tool definitions.
        """
        response = self._send_request("tools/list", {})
        return response.get("result", {}).get("tools", [])

    # ==================== High-level API ====================

    def read_document(self, document_id: str) -> str:
        """Read a Feishu document.

        Args:
            document_id: Document ID (from URL or API).

        Returns:
            Document content as text.
        """
        result = self.call_tool(
            "docx.v1.document.rawContent",
            {
                "document_id": document_id,
            },
        )
        return result.get("content", "")

    def search_documents(self, query: str, count: int = 10) -> list[dict[str, Any]]:
        """Search Feishu documents.

        Args:
            query: Search query.
            count: Maximum number of results.

        Returns:
            List of search results.
        """
        result = self.call_tool(
            "docx.builtin.search",
            {
                "query": query,
                "count": count,
            },
        )
        return result.get("items", [])

    def list_messages(
        self,
        chat_id: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """List messages from a chat.

        Args:
            chat_id: Chat ID.
            start_time: Start time (Unix timestamp string).
            end_time: End time (Unix timestamp string).
            page_size: Number of messages per page.

        Returns:
            List of messages.
        """
        params = {
            "container_id_type": "chat",
            "container_id": chat_id,
            "page_size": page_size,
        }
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time

        result = self.call_tool("im.v1.message.list", params)
        return result.get("items", [])

    def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        """Get chat information.

        Args:
            chat_id: Chat ID.

        Returns:
            Chat information.
        """
        result = self.call_tool(
            "im.v1.chat.get",
            {
                "chat_id": chat_id,
            },
        )
        return result

    @property
    def is_running(self) -> bool:
        """Check if the MCP server is running."""
        return self._running


class FeishuCommands:
    """Feishu commands for Memex CLI."""

    def __init__(self, client: "MemexClient", console: Console):
        """Initialize Feishu commands.

        Args:
            client: Memex client instance.
            console: Rich console for output.
        """
        from client import MemexClient

        self.memex_client = client
        self.console = console
        self._feishu: Optional[FeishuMCPClient] = None

    @property
    def feishu(self) -> FeishuMCPClient:
        """Get or create Feishu MCP client."""
        if self._feishu is None:
            try:
                self._feishu = FeishuMCPClient(console=self.console)
            except ValueError as e:
                self.console.print(f"[red]{e}[/red]")
                raise
        return self._feishu

    def connect(self) -> None:
        """Connect to Feishu MCP server."""
        try:
            if self.feishu.start():
                self.console.print("[green]Connected to Feishu[/green]")
            else:
                self.console.print("[red]Failed to connect to Feishu[/red]")
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

    def disconnect(self) -> None:
        """Disconnect from Feishu MCP server."""
        if self._feishu:
            self._feishu.stop()
            self._feishu = None
            self.console.print("[dim]Disconnected from Feishu[/dim]")

    def import_document(self, document_id: str, target: Optional[str] = None) -> None:
        """Import a Feishu document into Memex.

        Args:
            document_id: Feishu document ID.
            target: Target URI in Memex.
        """
        if not document_id:
            self.console.print("[red]Usage: /feishu-doc <document_id>[/red]")
            return

        try:
            if not self.feishu.is_running:
                self.connect()

            self.console.print(f"[dim]Fetching document {document_id}...[/dim]")
            content = self.feishu.read_document(document_id)

            if content:
                # Save to a temporary file and add to Memex
                import tempfile

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".md", delete=False, prefix=f"feishu_{document_id}_"
                ) as f:
                    f.write(content)
                    temp_path = f.name

                # Add to Memex
                target = target or "viking://resources/feishu/documents/"
                self.memex_client.add_resource(
                    path=temp_path,
                    target=target,
                    reason=f"Imported from Feishu document {document_id}",
                )

                # Clean up temp file
                os.unlink(temp_path)

                self.console.print(f"[green]✓ Imported document {document_id}[/green]")
            else:
                self.console.print(f"[yellow]Document {document_id} is empty[/yellow]")

        except Exception as e:
            self.console.print(f"[red]Error importing document: {e}[/red]")

    def search_and_import(self, query: str, count: int = 5) -> None:
        """Search Feishu documents and optionally import them.

        Args:
            query: Search query.
            count: Maximum number of results.
        """
        if not query:
            self.console.print("[red]Usage: /feishu-search <query>[/red]")
            return

        try:
            if not self.feishu.is_running:
                self.connect()

            self.console.print(f"[dim]Searching for '{query}'...[/dim]")
            results = self.feishu.search_documents(query, count)

            if not results:
                self.console.print(f"[dim]No documents found for '{query}'[/dim]")
                return

            from rich.table import Table

            table = Table(title=f"Feishu Documents: '{query}'", show_header=True)
            table.add_column("#", style="dim", width=3)
            table.add_column("Title", style="cyan")
            table.add_column("ID", style="dim")

            for i, doc in enumerate(results, 1):
                title = doc.get("title", "Untitled")
                doc_id = doc.get("id", "unknown")
                table.add_row(str(i), title, doc_id)

            self.console.print(table)
            self.console.print("[dim]Use /feishu-doc <id> to import a document[/dim]")

        except Exception as e:
            self.console.print(f"[red]Error searching: {e}[/red]")

    def list_tools(self) -> None:
        """List available Feishu MCP tools."""
        try:
            if not self.feishu.is_running:
                self.connect()

            tools = self.feishu.list_tools()

            from rich.table import Table

            table = Table(title="Available Feishu Tools", show_header=True)
            table.add_column("Tool", style="cyan")
            table.add_column("Description", style="white")

            for tool in tools[:20]:  # Limit display
                name = tool.get("name", "unknown")
                desc = tool.get("description", "")[:60]
                table.add_row(name, desc)

            self.console.print(table)

            if len(tools) > 20:
                self.console.print(f"[dim]... and {len(tools) - 20} more tools[/dim]")

        except Exception as e:
            self.console.print(f"[red]Error listing tools: {e}[/red]")
