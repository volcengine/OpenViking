#!/usr/bin/env python3
"""Integration test for ov chat command."""

import subprocess
import sys
from pathlib import Path

# Add root to path
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))


def test_chat_command_exists():
    """Test that chat command is registered."""
    print("Testing chat command registration...")
    result = subprocess.run(
        [sys.executable, "-m", "openviking_cli.cli.main", "--help"],
        capture_output=True,
        text=True,
    )
    print("Exit code:", result.returncode)
    print("\nSTDOUT:")
    print(result.stdout)
    if result.stderr:
        print("\nSTDERR:")
        print(result.stderr)

    # Check if chat is in the help output
    if "chat" in result.stdout:
        print("\n✓ SUCCESS: chat command found in help!")
        return True
    else:
        print("\n✗ FAILED: chat command not found in help")
        return False


def test_chat_help():
    """Test that chat --help shows correct parameters."""
    print("\n\nTesting chat --help...")
    result = subprocess.run(
        [sys.executable, "-m", "openviking_cli.cli.main", "chat", "--help"],
        capture_output=True,
        text=True,
    )
    print("Exit code:", result.returncode)
    print("\nSTDOUT:")
    print(result.stdout)
    if result.stderr:
        print("\nSTDERR:")
        print(result.stderr)

    # Check for expected parameters
    expected_params = ["--message", "-m", "--session", "-s", "--markdown", "--logs"]
    found = all(p in result.stdout for p in expected_params)
    if found:
        print("\n✓ SUCCESS: All expected parameters found!")
    else:
        print("\n✗ FAILED: Some parameters missing")
    return found


if __name__ == "__main__":
    print("=" * 60)
    print("Testing ov chat command integration")
    print("=" * 60)
    print()

    success1 = test_chat_command_exists()
    success2 = test_chat_help()

    print("\n" + "=" * 60)
    if success1 and success2:
        print("✓ All tests passed!")
        sys.exit(0)
    else:
        print("✗ Some tests failed!")
        sys.exit(1)
