# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Global test fixtures"""

import asyncio
import shutil
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio

from openviking import AsyncOpenViking

# Test data root directory
TEST_ROOT = Path(__file__).parent
TEST_TMP_DIR = TEST_ROOT / ".tmp"


@pytest.fixture(scope="session")
def event_loop():
    """Create session-level event loop"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def temp_dir() -> Generator[Path, None, None]:
    """Create temp directory, auto-cleanup before and after test"""
    shutil.rmtree(TEST_TMP_DIR, ignore_errors=True)
    TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)
    yield TEST_TMP_DIR
    shutil.rmtree(TEST_TMP_DIR, ignore_errors=True)


@pytest.fixture(scope="function")
def test_data_dir(temp_dir: Path) -> Path:
    """Create test data directory"""
    data_dir = temp_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture(scope="function")
def sample_text_file(temp_dir: Path) -> Path:
    """Create sample text file"""
    file_path = temp_dir / "sample.txt"
    file_path.write_text("This is a sample text file for testing OpenViking.")
    return file_path


@pytest.fixture(scope="function")
def sample_markdown_file(temp_dir: Path) -> Path:
    """Create sample Markdown file"""
    file_path = temp_dir / "sample.md"
    file_path.write_text(
        """# Sample Document

## Introduction
This is a sample markdown document for testing OpenViking.

## Features
- Feature 1: Resource management
- Feature 2: Semantic search
- Feature 3: Session management

## Usage
Use this document to test various OpenViking functionalities.
"""
    )
    return file_path


@pytest.fixture(scope="function")
def sample_skill_file(temp_dir: Path) -> Path:
    """Create sample skill file in SKILL.md format"""
    file_path = temp_dir / "sample_skill.md"
    file_path.write_text(
        """---
name: sample-skill
description: A sample skill for testing OpenViking skill management
tags:
  - test
  - sample
---

# Sample Skill

## Description
A sample skill for testing OpenViking skill management.

## Usage
Use this skill when you need to test skill functionality.

## Instructions
1. Step one: Initialize the skill
2. Step two: Execute the skill
3. Step three: Verify the result
"""
    )
    return file_path


@pytest.fixture(scope="function")
def sample_directory(temp_dir: Path) -> Path:
    """Create sample directory with multiple files"""
    dir_path = temp_dir / "sample_dir"
    dir_path.mkdir(parents=True, exist_ok=True)

    (dir_path / "file1.txt").write_text("Content of file 1 for testing.")
    (dir_path / "file2.md").write_text("# File 2\nContent of file 2 for testing.")

    subdir = dir_path / "subdir"
    subdir.mkdir()
    (subdir / "file3.txt").write_text("Content of file 3 in subdir for testing.")

    return dir_path


@pytest.fixture(scope="function")
def sample_files(temp_dir: Path) -> list[Path]:
    """Create multiple sample files for batch testing"""
    files = []
    for i in range(3):
        file_path = temp_dir / f"batch_file_{i}.md"
        file_path.write_text(
            f"""# Batch File {i}

## Content
This is batch file number {i} for testing batch operations.

## Keywords
- batch
- test
- file{i}
"""
        )
        files.append(file_path)
    return files


# ============ Client Fixtures ============


@pytest_asyncio.fixture(scope="function")
async def client(test_data_dir: Path) -> AsyncGenerator[AsyncOpenViking, None]:
    """Create initialized OpenViking client"""
    await AsyncOpenViking.reset()

    client = AsyncOpenViking(path=str(test_data_dir))
    await client.initialize()

    yield client

    await client.close()
    await AsyncOpenViking.reset()


@pytest_asyncio.fixture(scope="function")
async def uninitialized_client(test_data_dir: Path) -> AsyncGenerator[AsyncOpenViking, None]:
    """Create uninitialized OpenViking client (for testing initialization flow)"""
    await AsyncOpenViking.reset()

    client = AsyncOpenViking(path=str(test_data_dir))

    yield client

    try:
        await client.close()
    except Exception:
        pass
    await AsyncOpenViking.reset()


@pytest_asyncio.fixture(scope="function")
async def client_with_resource_sync(
    client: AsyncOpenViking, sample_markdown_file: Path
) -> AsyncGenerator[tuple[AsyncOpenViking, str], None]:
    """Create client with resource (sync mode, wait for vectorization)"""
    result = await client.add_resource(
        path=str(sample_markdown_file), reason="Test resource", wait=True
    )
    uri = result.get("root_uri", "")

    yield client, uri


@pytest_asyncio.fixture(scope="function")
async def client_with_resource(
    client: AsyncOpenViking, sample_markdown_file: Path
) -> AsyncGenerator[tuple[AsyncOpenViking, str], None]:
    """Create client with resource (async mode, no wait for vectorization)"""
    result = await client.add_resource(path=str(sample_markdown_file), reason="Test resource")
    uri = result.get("root_uri", "")
    yield client, uri
