#!/usr/bin/env python3
"""
Resource Manager - Shared utilities for adding resources to OpenViking
"""

from pathlib import Path
from typing import Optional

from openviking_sdk import SyncHTTPClient
from rich.console import Console


def create_client(server_url: str = "http://127.0.0.1:1933") -> SyncHTTPClient:
    """
    Create and initialize OpenViking client

    Args:
        server_url: OpenViking HTTP server URL

    Returns:
        Initialized HTTP client
    """
    client = SyncHTTPClient(url=server_url)
    client.initialize()

    return client


def add_resource(
    client: SyncHTTPClient,
    resource_path: str,
    console: Optional[Console] = None,
    show_output: bool = True,
) -> bool:
    """
    Add a resource to OpenViking database

    Args:
        client: Initialized HTTP client
        resource_path: Path to file/directory or URL
        console: Rich Console for output (creates new if None)
        show_output: Whether to print status messages

    Returns:
        True if successful, False otherwise
    """
    if console is None:
        console = Console()

    try:
        if show_output:
            console.print(f"📂 Adding resource: {resource_path}")

        # Validate file path (if not URL)
        if not resource_path.startswith("http"):
            path = Path(resource_path).expanduser()
            if not path.exists():
                if show_output:
                    console.print(f"❌ Error: File not found: {path}", style="red")
                return False

        # Add resource
        result = client.add_resource(path=resource_path)

        # Check result
        if result and "root_uri" in result:
            root_uri = result["root_uri"]
            if show_output:
                console.print(f"✓ Resource added: {root_uri}")

            # Wait for processing
            if show_output:
                console.print("⏳ Processing and indexing...")
            client.wait_processed()

            if show_output:
                console.print("✓ Processing complete!")
                console.print("🎉 Resource is now searchable!", style="bold green")

            return True

        elif result and result.get("status") == "error":
            if show_output:
                console.print("⚠️  Resource had parsing issues:", style="yellow")
                if "errors" in result:
                    for error in result["errors"][:3]:
                        console.print(f"  - {error}")
                console.print("💡 Some content may still be searchable.")
            return False

        else:
            if show_output:
                console.print("❌ Failed to add resource", style="red")
            return False

    except Exception as e:
        if show_output:
            console.print(f"❌ Error: {e}", style="red")
        return False
