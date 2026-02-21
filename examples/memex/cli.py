#!/usr/bin/env python3
"""
Memex CLI - Personal Knowledge Assistant

Usage:
    python -m memex.cli [--data-path PATH]

Commands:
    /help               Show help
    /add <path>         Add file/directory/URL to knowledge base
    /rm <uri>           Remove resource
    /import <dir>       Import directory
    /ls [uri]           List directory contents
    /tree [uri]         Show directory tree
    /read <uri>         Read full content (L2)
    /abstract <uri>     Show summary (L0)
    /overview <uri>     Show overview (L1)
    /find <query>       Quick semantic search
    /search <query>     Deep search with intent analysis
    /grep <pattern>     Content search
    /glob <pattern>     Pattern matching
    /ask <question>     Ask a question (single turn)
    /chat               Toggle chat mode (multi-turn)
    /clear              Clear chat history
    /stats              Show knowledge base statistics
    /info               Show configuration
    /exit               Exit Memex
"""

# Suppress Pydantic V1 compatibility warning from volcengine SDK
import warnings

warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality")

import argparse
import sys
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

from .config import MemexConfig
from .client import MemexClient
from .commands import (
    BrowseCommands,
    KnowledgeCommands,
    SearchCommands,
    QueryCommands,
    StatsCommands,
)
from .feishu import FeishuCommands


BANNER = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   ███╗   ███╗███████╗███╗   ███╗███████╗██╗  ██╗             ║
║   ████╗ ████║██╔════╝████╗ ████║██╔════╝╚██╗██╔╝             ║
║   ██╔████╔██║█████╗  ██╔████╔██║█████╗   ╚███╔╝              ║
║   ██║╚██╔╝██║██╔══╝  ██║╚██╔╝██║██╔══╝   ██╔██╗              ║
║   ██║ ╚═╝ ██║███████╗██║ ╚═╝ ██║███████╗██╔╝ ██╗             ║
║   ╚═╝     ╚═╝╚══════╝╚═╝     ╚═╝╚══════╝╚═╝  ╚═╝             ║
║                                                              ║
║   Personal Knowledge Assistant powered by OpenViking         ║
║   Type /help for commands, or just ask a question            ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""

HELP_TEXT = """
## Knowledge Management
- `/add <path>`      Add file, directory, or URL
- `/rm <uri>`        Remove resource (use -r for recursive)
- `/import <dir>`    Import entire directory

## Browse
- `/ls [uri]`        List directory contents
- `/tree [uri]`      Show directory tree
- `/read <uri>`      Read full content (L2)
- `/abstract <uri>`  Show summary (L0)
- `/overview <uri>`  Show overview (L1)
- `/stat <uri>`      Show resource metadata

## Search
- `/find <query>`    Quick semantic search
- `/search <query>`  Deep search with intent analysis
- `/grep <pattern>`  Content pattern search
- `/glob <pattern>`  File pattern matching

## Q&A
- `/ask <question>`  Ask a question (single turn)
- `/chat`            Toggle chat mode (multi-turn)
- `/clear`           Clear chat history
- Or just type your question directly!

## Session (Memory)
- `/session`         Show current session info
- `/commit`          End session and extract memories
- `/memories`        Show extracted memories

## Feishu Integration
- `/feishu`          Connect to Feishu MCP server
- `/feishu-login`    Login with your Feishu account (OAuth)
- `/feishu-ls [token]` List files in My Space or folder
- `/feishu-list <query>` Search and list documents in Feishu
- `/feishu-doc <id>` Import Feishu document
- `/feishu-search <query>` Search Feishu documents
- `/feishu-tools`    List available Feishu tools

## System
- `/stats`           Show knowledge base statistics
- `/info`            Show configuration
- `/help`            Show this help
- `/exit`            Exit Memex
"""


class MemexCLI:
    """Memex CLI application."""

    def __init__(self, config: Optional[MemexConfig] = None):
        """Initialize Memex CLI.

        Args:
            config: Memex configuration.
        """
        self.config = config or MemexConfig.from_env()
        self.console = Console()
        self.client: Optional[MemexClient] = None

        # Command handlers (initialized after client)
        self.browse: Optional[BrowseCommands] = None
        self.knowledge: Optional[KnowledgeCommands] = None
        self.search_cmd: Optional[SearchCommands] = None
        self.query: Optional[QueryCommands] = None
        self.stats_cmd: Optional[StatsCommands] = None
        self.feishu: Optional[FeishuCommands] = None

    def initialize(self) -> None:
        """Initialize the client and command handlers."""
        self.console.print("[dim]Initializing Memex...[/dim]")

        self.client = MemexClient(self.config)
        self.client.initialize()

        # Initialize command handlers
        self.browse = BrowseCommands(self.client, self.console)
        self.knowledge = KnowledgeCommands(self.client, self.console)
        self.search_cmd = SearchCommands(self.client, self.console)
        self.query = QueryCommands(self.client, self.console)
        self.stats_cmd = StatsCommands(self.client, self.console)

        # Initialize Feishu commands (optional - may fail if credentials not set)
        try:
            self.feishu = FeishuCommands(self.client, self.console)
        except Exception:
            self.feishu = None
            self.console.print(
                "[dim]Feishu integration not available (set FEISHU_APP_ID and FEISHU_APP_SECRET)[/dim]"
            )

        self.console.print("[green]Memex initialized![/green]")

    def show_banner(self) -> None:
        """Show welcome banner."""
        self.console.print(BANNER, style="cyan")

    def show_help(self) -> None:
        """Show help text."""
        self.console.print(Panel(Markdown(HELP_TEXT), title="Memex Help", border_style="blue"))

    def parse_command(self, user_input: str) -> tuple[str, list[str]]:
        """Parse user input into command and arguments.

        Args:
            user_input: Raw user input.

        Returns:
            Tuple of (command, arguments).
        """
        parts = user_input.strip().split(maxsplit=1)
        if not parts:
            return "", []

        command = parts[0].lower()
        args = parts[1].split() if len(parts) > 1 else []

        # For commands that take a single string argument (like queries)
        if command in ["/ask", "/find", "/search", "/grep"]:
            args = [parts[1]] if len(parts) > 1 else []

        return command, args

    def handle_command(self, user_input: str) -> bool:
        """Handle a command or query.

        Args:
            user_input: User input.

        Returns:
            False if should exit, True otherwise.
        """
        if not user_input.strip():
            return True

        # Check if it's a command
        if user_input.startswith("/"):
            command, args = self.parse_command(user_input)
            return self._dispatch_command(command, args, user_input)
        else:
            # Treat as a question
            self.query.process_input(user_input)
            return True

    def _dispatch_command(self, command: str, args: list[str], raw_input: str) -> bool:
        """Dispatch command to appropriate handler.

        Args:
            command: Command name.
            args: Command arguments.
            raw_input: Original raw input.

        Returns:
            False if should exit, True otherwise.
        """
        # System commands
        if command in ["/exit", "/quit", "/q"]:
            return False
        elif command in ["/help", "/h", "/?"]:
            self.show_help()

        # Knowledge management
        elif command == "/add":
            path = args[0] if args else ""
            target = args[1] if len(args) > 1 else None
            self.knowledge.add(path, target)
        elif command == "/rm":
            uri = args[0] if args else ""
            recursive = "-r" in args or "--recursive" in args
            self.knowledge.rm(uri, recursive)
        elif command == "/import":
            directory = args[0] if args else ""
            target = args[1] if len(args) > 1 else None
            self.knowledge.import_dir(directory, target)
        elif command == "/url":
            url = args[0] if args else ""
            self.knowledge.add_url(url)

        # Browse commands
        elif command == "/ls":
            uri = args[0] if args else None
            self.browse.ls(uri)
        elif command == "/tree":
            uri = args[0] if args else None
            self.browse.tree(uri)
        elif command == "/read":
            uri = args[0] if args else ""
            self.browse.read(uri)
        elif command == "/abstract":
            uri = args[0] if args else ""
            self.browse.abstract(uri)
        elif command == "/overview":
            uri = args[0] if args else ""
            self.browse.overview(uri)
        elif command == "/stat":
            uri = args[0] if args else ""
            self.browse.stat(uri)

        # Search commands
        elif command == "/find":
            # Get the full query after /find
            query = raw_input[len("/find") :].strip()
            self.search_cmd.find(query)
        elif command == "/search":
            query = raw_input[len("/search") :].strip()
            self.search_cmd.search(query)
        elif command == "/grep":
            pattern = args[0] if args else ""
            uri = args[1] if len(args) > 1 else None
            self.search_cmd.grep(uri or self.client.config.default_resource_uri, pattern)
        elif command == "/glob":
            pattern = args[0] if args else ""
            uri = args[1] if len(args) > 1 else None
            self.search_cmd.glob(pattern, uri)

        # Query commands
        elif command == "/ask":
            query = raw_input[len("/ask") :].strip()
            self.query.ask(query)
        elif command == "/chat":
            query = raw_input[len("/chat") :].strip()
            self.query.chat(query)
        elif command == "/clear":
            self.query.clear_history()

        # Session commands
        elif command == "/session":
            self.query.show_session_info()
        elif command == "/commit":
            self.query.commit_session()
        elif command == "/memories":
            self.query.show_memories()

        # Stats commands
        elif command == "/stats":
            self.stats_cmd.stats()
        elif command == "/info":
            self.stats_cmd.info()

        # Feishu commands
        elif command == "/feishu":
            if self.feishu:
                self.feishu.connect()
            else:
                self.console.print(
                    "[red]Feishu not available. Set FEISHU_APP_ID and FEISHU_APP_SECRET.[/red]"
                )
        elif command == "/feishu-login":
            if self.feishu:
                self.feishu.login()
            else:
                self.console.print("[red]Feishu not available.[/red]")
        elif command == "/feishu-ls":
            if self.feishu:
                token = raw_input[len("/feishu-ls") :].strip() or None
                self.feishu.list_directory(token)
            else:
                self.console.print("[red]Feishu not available.[/red]")
        elif command == "/feishu-doc":
            if self.feishu:
                doc_id = args[0] if args else ""
                target = args[1] if len(args) > 1 else None
                self.feishu.import_document(doc_id, target)
            else:
                self.console.print("[red]Feishu not available.[/red]")
        elif command == "/feishu-search":
            if self.feishu:
                query = raw_input[len("/feishu-search") :].strip()
                self.feishu.search_and_import(query)
            else:
                self.console.print("[red]Feishu not available.[/red]")
        elif command == "/feishu-tools":
            if self.feishu:
                self.feishu.list_tools()
            else:
                self.console.print("[red]Feishu not available.[/red]")
        elif command == "/feishu-list":
            if self.feishu:
                query = raw_input[len("/feishu-list") :].strip() or None
                self.feishu.list_files(query)
            else:
                self.console.print("[red]Feishu not available.[/red]")

        else:
            self.console.print(f"[red]Unknown command: {command}[/red]")
            self.console.print("[dim]Type /help for available commands[/dim]")

        return True

    def run(self) -> None:
        """Run the CLI main loop."""
        self.show_banner()

        try:
            self.initialize()
        except Exception as e:
            self.console.print(f"[red]Failed to initialize: {e}[/red]")
            return

        # Create prompt session with history
        session = PromptSession(
            history=FileHistory(".memex_history"),
            auto_suggest=AutoSuggestFromHistory(),
        )

        # Main loop
        while True:
            try:
                # Show chat mode indicator
                if self.query.chat_mode:
                    prompt = "[chat] memex> "
                else:
                    prompt = "memex> "

                user_input = session.prompt(prompt)

                if not self.handle_command(user_input):
                    break

            except KeyboardInterrupt:
                self.console.print("\n[dim]Use /exit to quit[/dim]")
                continue
            except EOFError:
                break
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/red]")

        # Cleanup
        self.console.print("\n[cyan]Goodbye! 👋[/cyan]")
        self._cleanup()

    def _cleanup(self) -> None:
        """Clean up resources on exit."""
        # Close Feishu connection if active
        if self.feishu:
            try:
                self.feishu.disconnect()
            except Exception:
                pass

        # Close OpenViking client
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

        # Kill any remaining AGFS processes for this data path
        import subprocess

        try:
            subprocess.run(
                ["pkill", "-f", "agfs-server"],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Memex - Personal Knowledge Assistant")
    parser.add_argument(
        "--data-path",
        default="./memex_data",
        help="Path to store Memex data (default: ./memex_data)",
    )
    parser.add_argument(
        "--config-path",
        default="./ov.conf",
        help="Path to OpenViking config file (default: ./ov.conf)",
    )
    parser.add_argument("--user", default="default", help="User name (default: default)")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging (default: off)",
    )

    args = parser.parse_args()

    import logging

    if args.verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
    else:
        logging.basicConfig(level=logging.WARNING)
        # Suppress openviking/vikingdb loggers
        for name, logger in list(logging.Logger.manager.loggerDict.items()):
            if isinstance(logger, logging.Logger) and name.startswith(("openviking", "vikingdb")):
                logger.setLevel(logging.WARNING)

    config = MemexConfig(
        data_path=args.data_path,
        config_path=args.config_path,
    )

    cli = MemexCLI(config)
    cli.run()


if __name__ == "__main__":
    main()
