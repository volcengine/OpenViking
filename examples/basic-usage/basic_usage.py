#!/usr/bin/env python3
"""
OpenViking Basic Usage Example

This script demonstrates the core features of OpenViking:
1. Initialization (embedded mode and HTTP client mode)
2. Adding resources (URLs, files, directories)
3. Browsing the virtual filesystem
4. Semantic search and retrieval
5. Tiered context loading (L0/L1/L2)
6. Session management for memory

Requirements:
- pip install openviking --upgrade
- Configuration file at ~/.openviking/ov.conf
"""

import os
import sys
import time

# Add parent directory to path for local development
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main():
    print("=" * 60)
    print("OpenViking Basic Usage Example")
    print("=" * 60)
    print()

    # ============================================================
    # 1. Initialization
    # ============================================================
    print("1. Initializing OpenViking...")
    print("-" * 40)

    try:
        import openviking as ov
    except ImportError as e:
        print(f"   Error: Failed to import openviking: {e}")
        print("   Please install: pip install openviking --upgrade")
        sys.exit(1)

    # Embedded mode (local development)
    # Option A: Embedded mode with local path
    client = ov.OpenViking(path="./data")

    # Option B: HTTP client mode (connect to remote server)
    # client = ov.SyncHTTPClient(url="http://localhost:1933")

    try:
        client.initialize()
        print("   Client initialized successfully")

        # Check health status
        if client.is_healthy():
            print("   Status: healthy")
        else:
            print("   Warning: Some components may not be healthy")

    except Exception as e:
        print(f"   Error during initialization: {e}")
        print("   Make sure you have configured ~/.openviking/ov.conf")
        sys.exit(1)

    print()

    # ============================================================
    # 2. Adding Resources
    # ============================================================
    print("2. Adding a resource (URL)...")
    print("-" * 40)

    try:
        # Add a URL resource
        result = client.add_resource(
            path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md",
            wait=False  # Non-blocking, process in background
        )

        root_uri = result.get("root_uri", "")
        print(f"   Root URI: {root_uri}")

        # Get the file count
        files = client.ls(root_uri)
        print(f"   Files indexed: {len(files)}")

    except Exception as e:
        print(f"   Error adding resource: {e}")
        root_uri = ""

    print()

    # ============================================================
    # 3. Browsing the Virtual Filesystem
    # ============================================================
    print("3. Browsing the virtual filesystem...")
    print("-" * 40)

    if root_uri:
        try:
            # List directory contents
            print(f"   Directory listing:")
            files = client.ls(root_uri, simple=True)
            for f in files[:5]:  # Show first 5 files
                print(f"   - {f}")

            # Show tree structure
            print(f"\n   Tree view:")
            tree = client.tree(root_uri, level_limit=2)
            print_tree(tree, indent="   ")

        except Exception as e:
            print(f"   Error browsing filesystem: {e}")

    print()

    # ============================================================
    # 4. Waiting for Semantic Processing
    # ============================================================
    print("4. Waiting for semantic processing...")
    print("-" * 40)

    try:
        # Wait for all async operations to complete
        status = client.wait_processed(timeout=60)
        print(f"   Processing complete: {status}")
    except Exception as e:
        print(f"   Note: {e}")
        print("   Continuing without waiting...")

    print()

    # ============================================================
    # 5. Tiered Context Loading
    # ============================================================
    print("5. Tiered Context Loading (L0/L1/L2):")
    print("-" * 40)

    if root_uri:
        try:
            # L0: Abstract (quick summary ~100 tokens)
            print("   L0 (Abstract):")
            abstract = client.abstract(root_uri)
            if abstract:
                # Show first 200 characters
                preview = abstract[:200] + "..." if len(abstract) > 200 else abstract
                print(f"   {preview}")
            else:
                print("   (Not available yet)")

            print()

            # L1: Overview (key points ~2k tokens)
            print("   L1 (Overview):")
            overview = client.overview(root_uri)
            if overview:
                preview = overview[:300] + "..." if len(overview) > 300 else overview
                print(f"   {preview}")
            else:
                print("   (Not available yet)")

            print()

            # L2: Full content (read first 500 chars)
            print("   L2 (Full content - first 500 chars):")
            content = client.read(root_uri)
            if content:
                preview = content[:500] + "..." if len(content) > 500 else content
                print(f"   {preview}")
            else:
                print("   (Not available)")

        except Exception as e:
            print(f"   Error loading context: {e}")

    print()

    # ============================================================
    # 6. Semantic Search
    # ============================================================
    print("6. Semantic Search:")
    print("-" * 40)

    if root_uri:
        try:
            query = "what is openviking"
            print(f"   Query: '{query}'")
            print("   Results:")

            results = client.find(
                query=query,
                target_uri=root_uri,
                limit=5
            )

            if hasattr(results, 'resources') and results.resources:
                for r in results.resources:
                    print(f"   - {r.uri}")
                    print(f"     Score: {r.score:.4f}")
            else:
                print("   No results found")

        except Exception as e:
            print(f"   Error during search: {e}")

    print()

    # ============================================================
    # 7. Content Search (grep)
    # ============================================================
    print("7. Content search (grep):")
    print("-" * 40)

    if root_uri:
        try:
            pattern = "Agent"
            print(f"   Pattern: '{pattern}'")

            result = client.grep(root_uri, pattern, case_insensitive=True)

            matches = result.get("matches", [])
            print(f"   Found {len(matches)} matches")

            # Show first 3 matches
            for match in matches[:3]:
                print(f"   - {match.get('uri', 'N/A')}: {match.get('count', 0)} occurrences")

        except Exception as e:
            print(f"   Error during grep: {e}")

    print()

    # ============================================================
    # 8. Session Management (Optional Demo)
    # ============================================================
    print("8. Session Management (Demo):")
    print("-" * 40)

    try:
        # Create a new session
        session_info = client.create_session()
        session_id = session_info.get("session_id", "")
        print(f"   Created session: {session_id}")

        # Add a conversation turn
        client.add_message(session_id, "user", "I prefer Python for data science projects")
        client.add_message(session_id, "assistant", "Understood! I'll use Python for your data science work.")

        print("   Added conversation turn")

        # Note: In a real application, you would commit the session at the end
        # to extract long-term memories. Here we just demonstrate the API.
        # client.commit_session(session_id)

        print("   (Session would be committed at conversation end)")

    except Exception as e:
        print(f"   Note: Session demo skipped - {e}")

    print()

    # ============================================================
    # 9. Cleanup
    # ============================================================
    print("8. Closing OpenViking...")
    print("-" * 40)

    client.close()
    print("   Done!")
    print()
    print("=" * 60)
    print("Example completed successfully!")
    print("=" * 60)


def print_tree(tree: dict, indent: str = ""):
    """Helper function to print tree structure."""
    if not tree:
        return

    name = tree.get("name", "?")
    children = tree.get("children", [])

    print(f"{indent}{name}/")

    for child in children[:5]:  # Limit to first 5 children
        if child.get("is_dir"):
            print_tree(child, indent + "  ")
        else:
            print(f"{indent}  {child.get('name', '?')}")


if __name__ == "__main__":
    main()