#!/usr/bin/env python3
"""Integration test for ov chat command."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.bot

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT_ROOT = REPO_ROOT / "bot"


def _cli_env() -> dict[str, str]:
    """Run the standalone bot CLI without inheriting a developer config."""
    env = os.environ.copy()
    env.pop("OPENVIKING_CONFIG_FILE", None)
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (str(REPO_ROOT), str(BOT_ROOT), env.get("PYTHONPATH", "")) if path
    )
    return env


def test_chat_command_exists():
    """Test that chat command is registered."""
    print("Testing chat command registration...")
    result = subprocess.run(
        [sys.executable, "-m", "vikingbot", "--help"],
        capture_output=True,
        text=True,
        env=_cli_env(),
    )
    print("Exit code:", result.returncode)
    print("\nSTDOUT:")
    print(result.stdout)
    if result.stderr:
        print("\nSTDERR:")
        print(result.stderr)

    # Check if chat is in the help output.  Assertions keep this a real pytest
    # test instead of returning a value that pytest cannot interpret.
    assert "chat" in result.stdout, "chat command not found in help"
    print("\n✓ SUCCESS: chat command found in help!")


def test_chat_help():
    """Test that chat --help shows correct parameters."""
    print("\n\nTesting chat --help...")
    result = subprocess.run(
        [sys.executable, "-m", "vikingbot", "chat", "--help"],
        capture_output=True,
        text=True,
        env=_cli_env(),
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
    assert found, f"missing chat parameters: {[p for p in expected_params if p not in result.stdout]}"
    print("\n✓ SUCCESS: All expected parameters found!")


if __name__ == "__main__":
    print("=" * 60)
    print("Testing ov chat command integration")
    print("=" * 60)
    print()

    success1 = test_chat_command_exists()
    success2 = test_chat_help()

    print("\n" + "=" * 60)
    if success1 is not False and success2 is not False:
        print("✓ All tests passed!")
        sys.exit(0)
    else:
        print("✗ Some tests failed!")
        sys.exit(1)
