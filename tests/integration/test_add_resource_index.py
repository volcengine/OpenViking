import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from openviking.async_client import AsyncOpenViking
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.fixture
def test_config(tmp_path):
    """Create a temporary config file."""
    config_path = tmp_path / "ov.conf"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    config_content = {
        "storage": {
            "workspace": str(workspace),
            "agfs": {"backend": "local", "port": 1833},
            "vectordb": {"backend": "local"},
        },
        "embedding": {
            "dense": {"provider": "openai", "api_key": "fake", "model": "text-embedding-3-small"}
        },
        "vlm": {"provider": "openai", "api_key": "fake", "model": "gpt-4-vision-preview"},
    }
    config_path.write_text(json.dumps(config_content))
    return config_path


@pytest.fixture
async def client(test_config, tmp_path):
    """Initialize AsyncOpenViking client with mocks."""

    # Set config env var
    os.environ["OPENVIKING_CONFIG_FILE"] = str(test_config)

    # Reset Singletons
    OpenVikingConfigSingleton._instance = None
    await AsyncOpenViking.reset()

    mock_agfs = MockLocalAGFS(root_path=tmp_path / "mock_agfs_root")

    # Mock LLM/VLM services AND AGFS
    with (
        patch("openviking.utils.summarizer.Summarizer.summarize") as mock_summarize,
        patch("openviking.utils.index_builder.IndexBuilder.build_index") as mock_build_index,
        patch("openviking.utils.agfs_utils.create_agfs_client", return_value=mock_agfs),
        patch("openviking.agfs_manager.AGFSManager.start"),
        patch("openviking.agfs_manager.AGFSManager.stop"),
    ):
        # Make mocks return success
        mock_summarize.return_value = {"status": "success"}
        mock_build_index.return_value = {"status": "success"}

        client = AsyncOpenViking(path=str(test_config.parent))
        await client.initialize()

        yield client

        await client.close()

        # Cleanup
        OpenVikingConfigSingleton._instance = None
        if "OPENVIKING_CONFIG_FILE" in os.environ:
            del os.environ["OPENVIKING_CONFIG_FILE"]


@pytest.mark.asyncio
async def test_add_resource_indexing_logic(test_config, tmp_path):
    """
    Integration-like test for add_resource indexing logic.
    Uses Mock AGFS but tests the client logic.
    """
    # Set config env var
    os.environ["OPENVIKING_CONFIG_FILE"] = str(test_config)
    OpenVikingConfigSingleton._instance = None
    await AsyncOpenViking.reset()

    # Create dummy resource
    resource_file = tmp_path / "test_doc.md"
    resource_file.write_text("# Test Document\n\nThis is a test document.", encoding="utf-8")

    mock_agfs = MockLocalAGFS(root_path=tmp_path / "mock_agfs_root")

    # Patch the Summarizer and IndexBuilder to verify calls
    with (
        patch(
            "openviking.utils.summarizer.Summarizer.summarize", new_callable=AsyncMock
        ) as mock_summarize,
        patch("openviking.utils.agfs_utils.create_agfs_client", return_value=mock_agfs),
        patch("openviking.agfs_manager.AGFSManager.start"),
        patch("openviking.agfs_manager.AGFSManager.stop"),
    ):
        mock_summarize.return_value = {"status": "success"}

        client = AsyncOpenViking(path=str(test_config.parent))
        await client.initialize()

        try:
            # 1. Test with build_index=True
            await client.add_resource(path=str(resource_file), build_index=True, wait=True)

            # Verify summarizer called with skip_vectorization=False
            assert mock_summarize.call_count == 1
            call_kwargs = mock_summarize.call_args.kwargs
            assert call_kwargs.get("skip_vectorization") is False

            mock_summarize.reset_mock()

            # 2. Test with build_index=False, summarize=True
            await client.add_resource(
                path=str(resource_file), build_index=False, summarize=True, wait=True
            )

            # Verify summarizer called with skip_vectorization=True
            assert mock_summarize.call_count == 1
            call_kwargs = mock_summarize.call_args.kwargs
            assert call_kwargs.get("skip_vectorization") is True

            mock_summarize.reset_mock()

            # 3. Test with build_index=False, summarize=False
            await client.add_resource(
                path=str(resource_file), build_index=False, summarize=False, wait=True
            )

            # Verify summarizer NOT called
            mock_summarize.assert_not_called()

        finally:
            await client.close()
            OpenVikingConfigSingleton._instance = None
            if "OPENVIKING_CONFIG_FILE" in os.environ:
                del os.environ["OPENVIKING_CONFIG_FILE"]
