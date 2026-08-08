# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Global test fixtures"""

import asyncio
import shutil
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import set_task_tracker
from openviking.storage import viking_fs as viking_fs_module
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.utils.mock_agfs import MockLocalAGFS


# ── Workaround: local .so may lack AGFS_Grep symbol (new in latest source) ──
def _patch_agfs_grep_if_missing():
    """Wrap _setup_functions to catch missing AGFS_Grep and skip its binding."""
    try:
        from openviking.pyagfs.binding_client import BindingLib

        _orig_setup = BindingLib._setup_functions

        def _safe_setup(self):
            try:
                _orig_setup(self)
            except AttributeError as e:
                if "AGFS_Grep" not in str(e):
                    raise
                # Re-implement _setup_functions but skip AGFS_Grep lines.
                # We do this by temporarily removing the Grep lines from the
                # source, but since we can't edit .so, we monkey-patch the lib
                # object's __getattr__ to not fail on AGFS_Grep.
                import ctypes

                class _GrepStub:
                    """Fake ctypes function descriptor for AGFS_Grep."""

                    argtypes = [
                        ctypes.c_int64,
                        ctypes.c_char_p,
                        ctypes.c_char_p,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_int,
                    ]
                    restype = ctypes.c_char_p

                    def __call__(self, *args):
                        return b'{"error":"AGFS_Grep not available in this .so version"}'

                # Patch at the CDLL instance level by overriding __getattr__
                orig_class = type(self.lib)
                orig_getattr = orig_class.__getattr__

                def patched_getattr(cdll_self, name):
                    if name == "AGFS_Grep":
                        return _GrepStub()
                    return orig_getattr(cdll_self, name)

                orig_class.__getattr__ = patched_getattr
                try:
                    _orig_setup(self)
                finally:
                    orig_class.__getattr__ = orig_getattr

        BindingLib._setup_functions = _safe_setup
    except Exception:
        pass


_patch_agfs_grep_if_missing()

# Test data root directory
PROJECT_ROOT = Path(__file__).parent.parent
TEST_TMP_DIR = PROJECT_ROOT / "test_data" / "tmp"


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


# ============ Service Fixtures ============


@pytest_asyncio.fixture(scope="function")
async def service(
    test_data_dir: Path,
    monkeypatch,
) -> AsyncGenerator[OpenVikingService, None]:
    """Create an initialized service for domain-level tests."""

    previous_viking_fs = viking_fs_module._instance

    class FakeEmbedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__(model_name="test-fake-embedder")

        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            return EmbedResult(dense_vector=[0.1] * 1024)

        def get_dimension(self) -> int:
            return 1024

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda self: FakeEmbedder())
    mock_agfs = MockLocalAGFS(root_path=test_data_dir / "mock_agfs_root")
    monkeypatch.setattr(
        "openviking.utils.agfs_utils.create_agfs_client",
        lambda *args, **kwargs: mock_agfs,
    )
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "storage": {
                "workspace": str(test_data_dir),
                "agfs": {"backend": "local"},
                "vectordb": {"backend": "local"},
            },
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "test-embedder",
                    "api_key": "test-key",
                    "dimension": 1024,
                }
            },
        }
    )
    instance = OpenVikingService(
        path=str(test_data_dir),
        user=UserIdentifier.the_default_user(),
    )
    try:
        await instance.initialize()
        yield instance
    finally:
        await instance.close()
        set_task_tracker(None)
        viking_fs_module._instance = previous_viking_fs
        OpenVikingConfigSingleton.reset_instance()


@pytest.fixture(scope="function")
def request_context() -> RequestContext:
    return RequestContext(
        user=UserIdentifier.the_default_user(),
        role=Role.USER,
    )
