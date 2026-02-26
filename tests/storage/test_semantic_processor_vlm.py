from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.storage.queuefs.semantic_processor import SemanticProcessor


@pytest.mark.asyncio
async def test_semantic_processor_image_description():
    """Test that image files trigger VLM vision capabilities"""
    processor = SemanticProcessor()

    # Mock VikingFS to return bytes that are not valid utf-8 (simulating image binary)
    mock_fs = MagicMock()
    mock_fs.read_file = AsyncMock(return_value=b"\xff\xff\xff\xff")

    # Mock VLM Config
    mock_vlm_config = MagicMock()
    mock_vlm_config.is_available.return_value = True

    # Mock VLM Instance with vision capability
    mock_vlm_instance = MagicMock()
    mock_vlm_instance.get_vision_completion_async = AsyncMock(
        return_value="A detailed description of the image."
    )
    mock_vlm_config.get_vlm_instance.return_value = mock_vlm_instance

    with (
        patch("openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=mock_fs),
        patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config"
        ) as mock_get_config,
    ):
        mock_get_config.return_value.vlm = mock_vlm_config

        # Test with png extension to trigger vision path
        result = await processor._generate_single_file_summary("test_image.png")

        assert result["name"] == "test_image.png"
        assert result["summary"] == "A detailed description of the image."

        # Verify vision method was called
        mock_vlm_instance.get_vision_completion_async.assert_called_once()
        # Verify the prompt is vision-specific
        args = mock_vlm_instance.get_vision_completion_async.call_args
        assert "image" in args[0][0].lower()


@pytest.mark.asyncio
async def test_semantic_processor_binary_file_fallback():
    """Test fallback when VLM is not available for binary files"""
    processor = SemanticProcessor()

    mock_fs = MagicMock()
    mock_fs.read_file = AsyncMock(return_value=b"\xff\xff")

    mock_vlm_config = MagicMock()
    mock_vlm_config.is_available.return_value = False

    with (
        patch("openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=mock_fs),
        patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config"
        ) as mock_get_config,
    ):
        mock_get_config.return_value.vlm = mock_vlm_config

        result = await processor._generate_single_file_summary("unknown_file.bin")

        assert "[Binary file: unknown_file.bin]" in result["summary"]
