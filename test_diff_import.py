#!/usr/bin/env python3
"""Test script to debug diff-match-patch import issue."""

import sys

print("=== Testing diff-match-patch import ===")
print(f"Python executable: {sys.executable}")
print()

# Test 1: Direct import
print("Test 1: Direct import")
try:
    import diff_match_patch
    print(f"  ✓ diff_match_patch module imported: {diff_match_patch}")
    print(f"  ✓ Module location: {diff_match_patch.__file__}")
except Exception as e:
    print(f"  ✗ {type(e).__name__}: {e}")

print()

# Test 2: The exact pattern used in the test file
print("Test 2: Import pattern from test file")
try:
    from diff_match_patch import diff_match_patch
    print(f"  ✓ diff_match_patch class imported")
    dmp = diff_match_patch()
    print(f"  ✓ diff_match_patch instance created")
except Exception as e:
    print(f"  ✗ {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

print()

# Test 3: Check what's in the module
print("Test 3: Module contents")
try:
    import diff_match_patch
    print(f"  dir(diff_match_patch): {[x for x in dir(diff_match_patch) if not x.startswith('_')]}")
except Exception as e:
    print(f"  ✗ {type(e).__name__}: {e}")
