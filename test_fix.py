#!/usr/bin/env python3
"""Test the fix."""

from dataclasses import dataclass
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


@dataclass
class MemoryFileContent(BaseModel):
    uri: Optional[str] = None
    plain_content: str
    memory_fields: Dict


def parse_memory_file_with_fields(content: str) -> Dict[str, Any]:
    """Mock function that returns dict."""
    return {"content": content, "field1": "value1"}


# Test the old (broken) code
print("Testing old code...")
content = "test content"
try:
    old_content = parse_memory_file_with_fields(content)
    old_content.uri = "test_uri"
    print("ERROR: Should have failed because dict doesn't have .uri attribute!")
except AttributeError as e:
    print(f"✓ Correctly got AttributeError: {e}")


# Test the fixed code
print("\nTesting fixed code...")
try:
    parsed = parse_memory_file_with_fields(content)
    old_content = MemoryFileContent(
        uri="test_uri",
        plain_content=parsed.get("content", ""),
        memory_fields=parsed
    )
    print(f"✓ Successfully created MemoryFileContent!")
    print(f"  uri: {old_content.uri}")
    print(f"  plain_content: {old_content.plain_content}")
    print(f"  memory_fields: {old_content.memory_fields}")
except Exception as e:
    print(f"ERROR: Failed with exception: {e}")
