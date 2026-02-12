#!/usr/bin/env python3
"""
Resource Manager - Shared utilities for adding resources to OpenViking
"""

import json
from pathlib import Path
from typing import Optional

from rich.console import Console

import openviking as ov
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


def create_client(config_path: str = "./ov.conf", data_path: str = "./data") -> ov.SyncOpenViking:
    """
    Create and initialize OpenViking client

    Args:
        config_path: Path to config file
        data_path: Path to data directory

    Returns:
        Initialized SyncOpenViking client
    """
    with open(config_path, "r") as f:
        config_dict = json.load(f)

    config = OpenVikingConfig.from_dict(config_dict)
    client = ov.SyncOpenViking(path=data_path, config=config)
    client.initialize()

    return client


def add_resource(
    client: ov.SyncOpenViking,
    resource_path: str,
    console: Optional[Console] = None,
    show_output: bool = True,
) -> bool:
    """
    Add a resource to OpenViking database

    Args:
        client: Initialized SyncOpenViking client
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
