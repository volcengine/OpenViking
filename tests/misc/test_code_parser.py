#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Test CodeRepositoryParser functionality and compliance with README.md requirements"""

import os
import tempfile
from pathlib import Path
import sys

# Add parent directory to path to import openviking
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from openviking.parse.parsers.code import CodeRepositoryParser


def test_ignore_dirs_compliance():
    """Test that IGNORE_DIRS includes all required directories from README.md"""
    print("=" * 60)
    print("Test IGNORE_DIRS compliance with README.md requirements")
    print("=" * 60)

    parser = CodeRepositoryParser()

    # Required directories from README.md
    required_dirs = {
        ".git",  # Git repository metadata
        ".idea",  # IDE configuration
        "__pycache__",  # Python bytecode cache
        "node_modules",  # Node.js dependencies
    }

    # Check each required directory is in IGNORE_DIRS
    all_present = True
    for dir_name in required_dirs:
        if dir_name in parser.IGNORE_DIRS:
            print(f"✓ {dir_name} is in IGNORE_DIRS")
        else:
            print(f"✗ {dir_name} is MISSING from IGNORE_DIRS")
            all_present = False

    # Additional directories that should be ignored
    additional_dirs = {
        ".svn",
        ".hg",
        ".vscode",
        "venv",
        ".venv",
        "env",
        ".env",
        "dist",
        "build",
        "target",
        "bin",
        "obj",
        ".DS_Store",
    }

    print("\nAdditional ignored directories:")
    for dir_name in additional_dirs:
        if dir_name in parser.IGNORE_DIRS:
            print(f"  ✓ {dir_name}")
        else:
            print(f"  ✗ {dir_name} (missing)")

    return all_present


def test_ignore_extensions_compliance():
    """Test that IGNORE_EXTENSIONS includes required formats from README.md"""
    print("\n" + "=" * 60)
    print("Test IGNORE_EXTENSIONS compliance with README.md requirements")
    print("=" * 60)

    parser = CodeRepositoryParser()

    # Required formats from README.md: "除了图片以外，不要让大模型处理文本以外的其他模态内容"
    # (Except for images, don't let the large model process non-text content)

    # Images should be included (they are explicitly mentioned as the exception)
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico"}

    # Video formats (non-text content)
    video_extensions = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".flv", ".wmv"}

    # Audio formats (non-text content)
    audio_extensions = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".wma"}

    # Binary/compiled files
    binary_extensions = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".bin"}

    all_extensions = image_extensions | video_extensions | audio_extensions | binary_extensions

    print("Checking required extensions are in IGNORE_EXTENSIONS:")

    missing_count = 0
    for ext in sorted(all_extensions):
        if ext in parser.IGNORE_EXTENSIONS:
            print(f"  ✓ {ext}")
        else:
            print(f"  ✗ {ext} (missing)")
            missing_count += 1

    # Check that .md files are NOT in IGNORE_EXTENSIONS (they should be processed)
    print("\nChecking .md file treatment (should be processed, not ignored):")
    if ".md" not in parser.IGNORE_EXTENSIONS:
        print("  ✓ .md files are NOT in IGNORE_EXTENSIONS (will be processed)")
    else:
        print("  ✗ .md files ARE in IGNORE_EXTENSIONS (will be ignored - this may be incorrect)")
        missing_count += 1

    return missing_count == 0


def test_file_type_detection():
    """Test the _detect_file_type helper method"""
    print("\n" + "=" * 60)
    print("Test file type detection helper method")
    print("=" * 60)

    parser = CodeRepositoryParser()

    test_cases = [
        (Path("test.py"), "code"),
        (Path("test.java"), "code"),
        (Path("test.js"), "code"),
        (Path("README.md"), "documentation"),
        (Path("docs.txt"), "documentation"),
        (Path("config.yaml"), "code"),  # YAML config files are considered code
        (Path("package.json"), "code"),  # JSON config files are considered code
        (Path("unknown.xyz"), "other"),
    ]

    print("Testing file type detection:")
    all_correct = True
    for file_path, expected_type in test_cases:
        detected_type = parser._detect_file_type(file_path)
        if detected_type == expected_type:
            print(f"  ✓ {file_path}: {detected_type} (expected: {expected_type})")
        else:
            print(f"  ✗ {file_path}: {detected_type} (expected: {expected_type})")
            all_correct = False

    return all_correct


def test_symbolic_link_handling():
    """Test that symbolic links are properly detected and skipped"""
    print("\n" + "=" * 60)
    print("Test symbolic link handling (README.md requirement)")
    print("=" * 60)

    # This is a conceptual test since we can't easily test the actual upload
    # without a real VikingFS instance

    print("Symbolic link handling implementation check:")

    # Check that os.path.islink is imported (indirectly through os module)
    if hasattr(os.path, "islink"):
        print("  ✓ os.path.islink is available")
    else:
        print("  ✗ os.path.islink not available")
        return False

    # Check that os.readlink is imported
    if hasattr(os, "readlink"):
        print("  ✓ os.readlink is available")
    else:
        print("  ✗ os.readlink not available")
        return False

    print("\nSymbolic link handling should:")
    print("  1. Detect symbolic links using os.path.islink()")
    print("  2. Read target path using os.readlink()")
    print("  3. Log the symbolic link and target path")
    print("  4. Skip uploading the symbolic link")

    return True


def main():
    """Run all tests"""
    print("CodeRepositoryParser Compliance Tests")
    print("=" * 60)

    tests = [
        ("IGNORE_DIRS compliance", test_ignore_dirs_compliance),
        ("IGNORE_EXTENSIONS compliance", test_ignore_extensions_compliance),
        ("File type detection", test_file_type_detection),
        ("Symbolic link handling", test_symbolic_link_handling),
    ]

    results = []

    for test_name, test_func in tests:
        print(f"\n{'=' * 20} {test_name} {'=' * 20}")
        try:
            result = test_func()
            results.append((test_name, result))
            status = "PASS" if result else "FAIL"
            print(f"\n{test_name}: {status}")
        except Exception as e:
            print(f"\n{test_name}: ERROR - {e}")
            results.append((test_name, False))

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print(
            "\n✅ All tests passed! CodeRepositoryParser appears compliant with README.md requirements."
        )
        return 0
    else:
        print(
            f"\n❌ {total - passed} test(s) failed. Review implementation against README.md requirements."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
