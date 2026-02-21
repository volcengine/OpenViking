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
        self._request_id = 0
        self._user_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._user_token_expires_at: Optional[float] = None

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
        """Start the MCP server process and perform protocol handshake.

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

            # MCP protocol handshake: initialize
            init_response = self._send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "memex", "version": "0.1.0"},
                },
            )

            if "error" in init_response:
                self.console.print(f"[red]MCP initialize failed: {init_response['error']}[/red]")
                self.stop()
                return False

            # Send initialized notification (no id, no response expected)
            notification = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
            self._process.stdin.write(json.dumps(notification) + "\n")
            self._process.stdin.flush()

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
        if not self._process:
            raise RuntimeError("Feishu MCP server not running. Call start() first.")

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        try:
            # Send request
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()

            # Read response (skip notification lines until we get our response)
            while True:
                response_line = self._process.stdout.readline()
                if not response_line:
                    raise RuntimeError("No response from MCP server")
                response = json.loads(response_line)
                # Skip notifications (no "id" field)
                if "id" in response:
                    return response

        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON from MCP server: {e}")
        except Exception as e:
            raise RuntimeError(f"Error communicating with MCP server: {e}")

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call.
            arguments: Tool arguments.

        Returns:
            Parsed tool result (dict from the Feishu API JSON response).
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

        result = response.get("result", {})

        # MCP tools/call returns content as: [{"type": "text", "text": "...json..."}]
        # Extract and parse the actual API response from the text field.
        content = result.get("content", [])
        if isinstance(content, list) and content:
            text = content[0].get("text", "")
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"content": text}

        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """List available tools.

        Returns:
            List of tool definitions.
        """
        response = self._send_request("tools/list", {})
        return response.get("result", {}).get("tools", [])

    # ==================== High-level API ====================
    #
    # Tool names use underscores (e.g. docx_v1_document_rawContent).
    # Arguments must be nested under path/data/params per the tool's inputSchema.

    def _get_tenant_token(self) -> str:
        """Get tenant access token via REST API."""
        import urllib.request

        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=json.dumps(
                {"app_id": self.config.app_id, "app_secret": self.config.app_secret}
            ).encode(),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to get tenant token: {data.get('msg')}")
        return data["tenant_access_token"]

    def _get_app_access_token(self) -> str:
        """Get app access token (needed for OAuth token exchange)."""
        import urllib.request

        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
            data=json.dumps(
                {"app_id": self.config.app_id, "app_secret": self.config.app_secret}
            ).encode(),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to get app token: {data.get('msg')}")
        return data["app_access_token"]

    def _get_user_token(self) -> str:
        """Get user access token via OAuth authorization code flow.

        Opens browser for user login, starts a local HTTP server to receive
        the callback, then exchanges the code for a user access token.

        Returns:
            User access token string.
        """
        # Return cached token if still valid
        if (
            self._user_token
            and self._user_token_expires_at
            and time.time() < self._user_token_expires_at - 60
        ):
            return self._user_token

        # Try refresh first
        if self._refresh_token:
            try:
                return self._refresh_user_token()
            except Exception:
                pass  # Fall through to full OAuth flow

        import http.server
        import secrets
        import urllib.parse
        import urllib.request
        import webbrowser

        port = 8089
        state = secrets.token_urlsafe(16)
        redirect_uri = f"http://localhost:{port}/callback"
        auth_code = None

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                nonlocal auth_code
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)

                if parsed.path == "/callback" and "code" in params:
                    recv_state = params.get("state", [None])[0]
                    if recv_state != state:
                        self.send_response(400)
                        self.end_headers()
                        self.wfile.write(b"State mismatch")
                        return
                    auth_code = params["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write("✅ 授权成功！请返回 Memex 终端继续操作。".encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass  # Suppress HTTP logs

        # Build authorization URL
        auth_url = (
            "https://passport.feishu.cn/suite/passport/oauth/authorize?"
            + urllib.parse.urlencode(
                {
                    "app_id": self.config.app_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "state": state,
                }
            )
        )

        self.console.print(f"\n[bold cyan]请在浏览器中登录飞书并授权...[/bold cyan]")
        self.console.print(f"[dim]如果浏览器没有自动打开，请手动访问：[/dim]")
        self.console.print(f"[dim]{auth_url}[/dim]\n")

        webbrowser.open(auth_url)

        # Start local server in a separate thread
        server = http.server.HTTPServer(("localhost", port), CallbackHandler)
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()

        try:
            # Wait for callback OR manual input
            self.console.print("[yellow]Waiting for authorization...[/yellow]")
            self.console.print(
                "[dim]Tip: If you are in a headless environment (SSH), open the URL above locally.\n"
                "After authorization, the browser will redirect to localhost (which may fail).\n"
                "Copy the full redirect URL (or just the 'code' parameter) and paste it here:[/dim]"
            )

            # Poll for auth_code from server, or accept manual input
            # We can't easily do blocking input() and server check simultaneously in a clean way
            # without complex async or select.
            # Simplified approach: Use a loop with non-blocking input check? No, python input is blocking.
            # Better: Ask for input, but shutdown server if input provided.
            # But we want to support AUTO callback too.

            # Pragmatic approach:
            # We assume if the user is typing, they want to paste.
            # But input() blocks. So the server thread handles the auto-callback.
            # We block on input(). If the server gets a code, we need a way to interrupt input() - hard.

            # Alternative: Just ask for input with a prompt. If the server receives a request,
            # it sets auth_code. We can't easily break the input() prompt though.

            # Compromise: We just use input() as the primary wait mechanism.
            # If the server gets a hit, it can print a message, but main thread is stuck in input().
            # Actually, standard `input()` is hard to interrupt.

            # Let's use a loop that checks auth_code every second, and uses a non-blocking input method
            # if available, or just rely on the user pressing Enter to paste code.

            # SIMPLER: Just ask user to paste code IF auto-redirect fails.
            # But the user experience is best if it "just works".

            # Let's try this:
            # We print the prompt. If the user authorizes via browser callback,
            # the server thread sets `auth_code`.
            # We can't interrupt `input()`.
            # So we will NOT use `input()`. instead we wait for a bit, then ask user?
            # No, that's annoying.

            # Solution: We run the server. We also prompt "Paste code here (or wait for auto-redirect): "
            # This blocks.
            # If the server receives a code, it can print "Received!" but we are still stuck in input().
            # This is acceptable for a CLI. The user can just hit Enter if auto-redirect happened.

            self.console.print(
                "\nPaste the code (or full URL) here if auto-redirect fails, then press Enter:"
            )
            # Use a timed input or just standard input? Standard input blocks.
            # We can use a loop with select on sys.stdin (Unix only) or just accept that input() blocks.
            # Since we are on Linux, we could use select.

            import sys
            import select

            start_time = time.time()
            while auth_code is None:
                # Check for timeout
                if time.time() - start_time > 120:
                    raise RuntimeError("Authorization timed out")

                # Check if stdin has data
                if sys.stdin in select.select([sys.stdin], [], [], 0.5)[0]:
                    line = sys.stdin.readline().strip()
                    if line:
                        # Extract code from URL or use raw code
                        if "code=" in line:
                            try:
                                parsed = urllib.parse.urlparse(line)
                                params = urllib.parse.parse_qs(parsed.query)
                                if "code" in params:
                                    auth_code = params["code"][0]
                            except Exception:
                                pass

                        if not auth_code:
                            # Assume raw code
                            auth_code = line
                        break

                # Loop continues, server thread is running in background

        finally:
            server.shutdown()
            server.server_close()

        if not auth_code:
            raise RuntimeError("Authorization failed or cancelled")

        self.console.print("[green]Processing authorization code...[/green]")

        # Exchange code for user access token
        app_token = self._get_app_access_token()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token",
            data=json.dumps({"grant_type": "authorization_code", "code": auth_code}).encode(),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {app_token}",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())

        if result.get("code") != 0:
            raise RuntimeError(f"Failed to get user token: {result.get('msg')}")

        token_data = result["data"]
        self._user_token = token_data["access_token"]
        self._refresh_token = token_data.get("refresh_token")
        self._user_token_expires_at = time.time() + token_data.get("expires_in", 7200)

        user_name = token_data.get("name", "")
        self.console.print(
            f"[green]✓ 已获取用户 token{' (' + user_name + ')' if user_name else ''}[/green]"
        )
        return self._user_token

    def _refresh_user_token(self) -> str:
        """Refresh an expired user access token."""
        import urllib.request

        app_token = self._get_app_access_token()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/authen/v1/oidc/refresh_access_token",
            data=json.dumps(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                }
            ).encode(),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {app_token}",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())

        if result.get("code") != 0:
            self._refresh_token = None
            raise RuntimeError(f"Token refresh failed: {result.get('msg')}")

        token_data = result["data"]
        self._user_token = token_data["access_token"]
        self._refresh_token = token_data.get("refresh_token", self._refresh_token)
        self._user_token_expires_at = time.time() + token_data.get("expires_in", 7200)
        return self._user_token

    def list_user_files(
        self, folder_token: Optional[str] = None, page_size: int = 50
    ) -> list[dict[str, Any]]:
        """List files in user's drive (My Space).

        Args:
            folder_token: Folder token. None for root folder.
            page_size: Number of items.

        Returns:
            List of file objects.
        """
        token = self._get_access_token()

        # If no folder_token, try to get root folder token first
        if not folder_token:
            try:
                # Try v2 explorer API to get root folder meta
                root_req = urllib.request.Request(
                    "https://open.feishu.cn/open-apis/drive/explorer/v2/root_folder/meta",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                )
                root_resp = urllib.request.urlopen(root_req, timeout=10)
                root_res = json.loads(root_resp.read())
                if root_res.get("code") == 0:
                    folder_token = root_res["data"]["token"]
            except Exception:
                pass  # Fallback to listing without token

        params = {"page_size": str(page_size)}
        if folder_token:
            params["folder_token"] = folder_token

        query = urllib.parse.urlencode(params)
        req = urllib.request.Request(
            f"https://open.feishu.cn/open-apis/drive/v1/files?{query}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())

        if result.get("code") != 0:
            raise RuntimeError(
                f"Failed to list files: {result.get('msg')} (code={result.get('code')})"
            )

        data = result.get("data", {})
        return data.get("files", [])

    def _get_access_token(self) -> str:
        """Get the appropriate access token based on auth mode.

        Returns user token if available (via OAuth), otherwise tenant token.
        """
        if self._user_token:
            # Check expiry, refresh if needed
            if self._user_token_expires_at and time.time() >= self._user_token_expires_at - 60:
                try:
                    return self._refresh_user_token()
                except Exception:
                    self._user_token = None
            else:
                return self._user_token
        return self._get_tenant_token()

    def read_document(self, document_id: str) -> str:
        """Read a Feishu document.

        Args:
            document_id: Document ID (from URL or API).

        Returns:
            Document content as text.
        """
        result = self.call_tool(
            "docx_v1_document_rawContent",
            {"path": {"document_id": document_id}},
        )
        # Feishu API response: {"code": 0, "data": {"content": "..."}}
        data = result.get("data", result)
        content = data.get("content", "")
        if isinstance(content, str):
            return content
        return str(content)

    def search_documents(self, query: str, count: int = 10) -> list[dict[str, Any]]:
        """Search Feishu documents via REST API.

        Uses user token if available (can see user's own docs),
        falls back to tenant token (only sees shared/template docs).

        Args:
            query: Search query.
            count: Maximum number of results.

        Returns:
            List of search results.
        """
        import urllib.request

        token = self._get_access_token()
        body = json.dumps(
            {
                "search_key": query,
                "count": count,
                "offset": 0,
            }
        ).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/suite/docs-api/search/object",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        data = result.get("data", {})
        return data.get("docs_entities", [])

    def search_wiki(
        self, query: str, space_id: Optional[str] = None, page_size: int = 20
    ) -> list[dict[str, Any]]:
        """Search wiki nodes.

        Args:
            query: Search query.
            space_id: Optional wiki space ID to search within.
            page_size: Number of results.

        Returns:
            List of wiki nodes.
        """
        data: dict[str, Any] = {"query": query}
        if space_id:
            data["space_id"] = space_id
        result = self.call_tool(
            "wiki_v1_node_search",
            {"data": data, "params": {"page_size": page_size}},
        )
        resp_data = result.get("data", result)
        return resp_data.get("items", resp_data.get("nodes", []))

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
        params: dict[str, Any] = {
            "page_size": page_size,
        }
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time

        result = self.call_tool(
            "im_v1_message_list",
            {
                "path": {"container_id_type": "chat", "container_id": chat_id},
                "params": params,
            },
        )
        data = result.get("data", result)
        return data.get("items", [])

    def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        """Get chat information.

        Args:
            chat_id: Chat ID.

        Returns:
            Chat information.
        """
        result = self.call_tool(
            "im_v1_chat_list",
            {"params": {"page_size": 1}},
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
        from .client import MemexClient

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

    def login(self) -> None:
        """Login to Feishu with user OAuth to access personal documents."""
        try:
            self.feishu._get_user_token()
        except Exception as e:
            self.console.print(f"[red]Login failed: {e}[/red]")

    def list_directory(self, folder_token: Optional[str] = None) -> None:
        """List files in a directory (My Space or specific folder).

        Args:
            folder_token: Folder token. None for root.
        """
        try:
            # Note: This uses REST API, doesn't need MCP server running, but needs user token
            # If no user token, it might fail or return empty for tenant
            label = f"folder '{folder_token}'" if folder_token else "My Space (root)"
            self.console.print(f"[dim]Listing {label}...[/dim]")

            files = self.feishu.list_user_files(folder_token)

            if not files:
                self.console.print(f"[dim]No files found in {label}[/dim]")
                return

            from rich.table import Table

            table = Table(title=f"Feishu Files ({label})", show_header=True)
            table.add_column("#", style="dim", width=3)
            table.add_column("Name", style="cyan")
            table.add_column("Type", style="yellow", width=10)
            table.add_column("Token", style="dim")

            for i, f in enumerate(files, 1):
                name = f.get("name", "Untitled")
                file_type = f.get("type", "unknown")
                token = f.get("token", "")
                table.add_row(str(i), name, file_type, token)

            self.console.print(table)
            self.console.print(
                "[dim]Use /feishu-doc <token> to import, "
                "or /feishu-ls <token> to browse subfolder[/dim]"
            )

        except Exception as e:
            self.console.print(f"[red]Error listing files: {e}[/red]")
            if "99991663" in str(e) or "access token" in str(e).lower():
                self.console.print(
                    "[yellow]Tip: Use /feishu-login first to access your personal files[/yellow]"
                )

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

    def list_files(self, query: Optional[str] = None) -> None:
        """Search and list Feishu documents.

        Args:
            query: Search query. If empty, searches with a broad query.
        """
        try:
            if not query:
                self.console.print("[red]Usage: /feishu-list <query>[/red]")
                self.console.print("[dim]Example: /feishu-list 项目周报[/dim]")
                return
            search_query = query
            self.console.print(
                f"[dim]Searching documents for '{search_query.strip() or '*'}'...[/dim]"
            )

            results = self.feishu.search_documents(search_query, count=20)

            if not results:
                self.console.print("[dim]No documents found[/dim]")
                return

            from rich.table import Table

            table = Table(title="Feishu Documents", show_header=True)
            table.add_column("#", style="dim", width=3)
            table.add_column("Title", style="cyan")
            table.add_column("Type", style="yellow", width=10)
            table.add_column("Token", style="dim")

            for i, doc in enumerate(results, 1):
                title = doc.get("title", "Untitled")
                doc_type = doc.get("docs_type", "")
                doc_id = doc.get("docs_token", "")
                table.add_row(str(i), title, doc_type, doc_id)

            self.console.print(table)
            self.console.print("[dim]Use /feishu-doc <token> to import a document[/dim]")

        except Exception as e:
            self.console.print(f"[red]Error listing files: {e}[/red]")
