#!/usr/bin/env python3
"""
Add Resource - CLI tool to add documents to OpenViking database
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from rich import box
from rich.console import Console
from rich.table import Table

import openviking as ov
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

console = Console()


# ── Table helpers ──────────────────────────────────────────────────


def _print_directory_summary(meta: Dict[str, Any], errors: List[str]) -> None:
    """Print a rich-table summary for a directory import."""
    processed: List[Dict[str, str]] = meta.get("processed_files", [])
    failed: List[Dict[str, str]] = meta.get("failed_files", [])
    unsupported: List[Dict[str, str]] = meta.get("unsupported_files", [])
    skipped: List[Dict[str, str]] = meta.get("skipped_files", [])

    n_total = len(processed) + len(failed) + len(unsupported) + len(skipped)

    if n_total == 0:
        console.print("  (no files found)", style="dim")
        return

    # Build a single combined table (ROUNDED box style, same as query.py)
    table = Table(
        title=f"Directory Import  ({n_total} files)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        title_style="bold magenta",
    )
    table.add_column("#", style="cyan", width=4)
    table.add_column("Status", no_wrap=True)
    table.add_column("File", style="bold white", no_wrap=True)
    table.add_column("Detail")

    # Match failed files to their warning messages
    fail_reasons: Dict[str, str] = {}
    for err in errors:
        for f in failed:
            if f["path"] in err:
                fail_reasons[f["path"]] = err
                break

    idx = 0
    for f in processed:
        idx += 1
        table.add_row(
            str(idx),
            "[green]processed[/green]",
            f["path"],
            f"[dim]{f.get('parser', '')}[/dim]",
        )

    for f in failed:
        idx += 1
        reason = fail_reasons.get(f["path"], "parse error")
        table.add_row(
            str(idx),
            "[red]failed[/red]",
            f["path"],
            f"[red]{reason}[/red]",
        )

    for f in unsupported:
        idx += 1
        table.add_row(
            str(idx),
            "[yellow]unsupported[/yellow]",
            f["path"],
            "",
        )

    for f in skipped:
        idx += 1
        status = f.get("status", "skip")
        table.add_row(
            str(idx),
            f"[dim]{status}[/dim]",
            f"[dim]{f['path']}[/dim]",
            "",
        )

    console.print()
    console.print(table)


# ── Main logic ─────────────────────────────────────────────────────


def add_resource(
    resource_path: str,
    config_path: str = "./ov.conf",
    data_path: str = "./data",
    **kwargs,
):
    """
    Add a resource to OpenViking database

    Args:
        resource_path: Path to file, directory, or URL
        config_path: Path to config file
        data_path: Path to data directory
        **kwargs: Extra options forwarded to ``add_resource`` (e.g.
            ``include``, ``exclude``, ``ignore_dirs``).
    """
    # Load config
    print(f"📋 Loading config from: {config_path}")
    with open(config_path, "r") as f:
        config_dict = json.load(f)

    config = OpenVikingConfig.from_dict(config_dict)
    client = ov.SyncOpenViking(path=data_path, config=config)

    try:
        print("🚀 Initializing OpenViking...")
        client.initialize()
        print("✓ Initialized\n")

        # Check if it's a local path and exists
        is_local = not resource_path.startswith("http")
        is_directory = False
        if is_local:
            path = Path(resource_path).expanduser()
            if not path.exists():
                print(f"❌ Error: Path not found: {path}")
                return False
            is_directory = path.is_dir()

        if is_directory:
            print(f"📂 Adding directory: {resource_path}")
        else:
            print(f"📄 Adding resource: {resource_path}")

        result = client.add_resource(path=resource_path, **kwargs)

        # Check result
        if result and "root_uri" in result:
            root_uri = result["root_uri"]
            meta = result.get("meta", {})
            errors = result.get("errors", [])
            print(f"✓ Resource added: {root_uri}")

            # Show directory-specific table
            if is_directory:
                _print_directory_summary(meta, errors)

            # Wait for processing
            print("\n⏳ Processing and indexing...")
            client.wait_processed(timeout=600 if is_directory else 300)
            print("✓ Processing complete!\n")

            print("🎉 Resource is now searchable in the database!")
            return True

        elif result and result.get("status") == "error":
            print("\n⚠️  Resource had parsing issues:")
            if "errors" in result:
                for error in result["errors"][:5]:
                    print(f"  - {error}")
            print("\n💡 Some content may still be searchable.")
            return False

        else:
            print("❌ Failed to add resource")
            return False

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False

    finally:
        client.close()
        print("\n✓ Done")


def main():
    parser = argparse.ArgumentParser(
        description="Add documents, PDFs, or URLs to OpenViking database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Add a PDF file
  uv run add_resource.py ~/Downloads/document.pdf

  # Add a URL
  uv run add_resource.py https://example.com/README.md

  # Add with custom config and data paths
  uv run add_resource.py document.pdf --config ./my.conf --data ./mydata

  # Add a directory
  uv run add_resource.py ~/Documents/research/

  # Enable debug logging
  OV_DEBUG=1 uv run add_resource.py document.pdf

Notes:
  - Supported formats: PDF, Markdown, Text, HTML, and more
  - URLs are automatically downloaded and processed
  - Large files may take several minutes to process
  - The resource becomes searchable after processing completes
        """,
    )

    parser.add_argument(
        "resource", type=str, help="Path to file/directory or URL to add to the database"
    )

    parser.add_argument(
        "--config", type=str, default="./ov.conf", help="Path to config file (default: ./ov.conf)"
    )

    parser.add_argument(
        "--data", type=str, default="./data", help="Path to data directory (default: ./data)"
    )

    # Directory-specific options
    dir_group = parser.add_argument_group("directory options")
    dir_group.add_argument(
        "--include",
        type=str,
        action="append",
        default=None,
        help="Glob pattern for files to include (can be repeated, e.g. --include '*.md')",
    )
    dir_group.add_argument(
        "--exclude",
        type=str,
        action="append",
        default=None,
        help="Glob pattern for files to exclude (can be repeated, e.g. --exclude 'test_*')",
    )
    dir_group.add_argument(
        "--ignore-dirs",
        type=str,
        action="append",
        default=None,
        dest="ignore_dirs",
        help="Directory names to skip (can be repeated, e.g. --ignore-dirs node_modules)",
    )

    args = parser.parse_args()

    # Expand user paths
    resource_path = (
        str(Path(args.resource).expanduser())
        if not args.resource.startswith("http")
        else args.resource
    )

    # Build kwargs for directory options
    # scan_directory expects include/exclude as comma-separated strings,
    # and ignore_dirs as a Set[str].
    dir_kwargs = {}
    if args.include:
        dir_kwargs["include"] = ",".join(args.include)
    if args.exclude:
        dir_kwargs["exclude"] = ",".join(args.exclude)
    if args.ignore_dirs:
        dir_kwargs["ignore_dirs"] = set(args.ignore_dirs)

    # Add the resource
    success = add_resource(resource_path, args.config, args.data, **dir_kwargs)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
