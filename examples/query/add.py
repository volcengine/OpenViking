#!/usr/bin/env python3
"""
Add Resource - CLI tool to add documents to OpenViking database
"""

import argparse
import json
import sys
from pathlib import Path

import openviking as ov
from openviking.utils.config.open_viking_config import OpenVikingConfig


def add_resource(resource_path: str, config_path: str = "./ov.conf", data_path: str = "./data"):
    """
    Add a resource to OpenViking database

    Args:
        resource_path: Path to file, directory, or URL
        config_path: Path to config file
        data_path: Path to data directory
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

        print(f"📂 Adding resource: {resource_path}")

        # Check if it's a file and exists
        if not resource_path.startswith("http"):
            path = Path(resource_path).expanduser()
            if not path.exists():
                print(f"❌ Error: File not found: {path}")
                return False

        result = client.add_resource(path=resource_path)

        # Check result
        if result and "root_uri" in result:
            root_uri = result["root_uri"]
            print(f"✓ Resource added: {root_uri}\n")

            # Wait for processing
            print("⏳ Processing and indexing...")
            client.wait_processed(timeout=300)
            print("✓ Processing complete!\n")

            print("🎉 Resource is now searchable in the database!")
            return True

        elif result and result.get("status") == "error":
            print("\n⚠️  Resource had parsing issues:")
            if "errors" in result:
                for error in result["errors"][:3]:
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

    args = parser.parse_args()

    # Expand user paths
    resource_path = (
        str(Path(args.resource).expanduser())
        if not args.resource.startswith("http")
        else args.resource
    )

    # Add the resource
    success = add_resource(resource_path, args.config, args.data)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
