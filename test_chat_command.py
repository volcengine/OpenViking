#!/usr/bin/env python3
"""Test script to verify the ov chat command implementation."""

import sys
from pathlib import Path

# Add the root directory to Python path
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

from openviking_cli.cli.commands.chat import _check_vikingbot


def test_vikingbot_detection():
    """Test that vikingbot detection works."""
    print("Testing vikingbot detection...")
    has_vikingbot = _check_vikingbot()
    print(f"vikingbot available: {has_vikingbot}")

    # Also check via import
    try:
        import vikingbot
        print(f"Direct import successful: vikingbot {vikingbot.__version__}")
    except ImportError:
        print("Direct import: vikingbot not found")

    return has_vikingbot


if __name__ == "__main__":
    test_vikingbot_detection()
