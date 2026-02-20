# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Video parser - Future implementation.

Planned Features:
1. Key frame extraction at regular intervals
2. Audio track transcription using ASR
3. VLM-based scene description for key frames
4. Video metadata extraction (duration, resolution, codec)
5. Generate structured ResourceNode combining visual and audio

Example workflow:
    1. Load video file
    2. Extract metadata (duration, resolution, fps)
    3. Extract audio track → transcribe using AudioParser
    4. Extract key frames at specified intervals
    5. For each frame: generate VLM description
    6. Create ResourceNode tree:
       - Root: video metadata
       - Children: timeline nodes (each with frame + transcript)
    7. Return ParseResult

Supported formats: MP4, AVI, MOV, MKV, WEBM
"""

from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import NodeType, ParseResult, ResourceNode
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.constants import VIDEO_EXTENSIONS
from openviking_cli.utils.config.parser_config import VideoConfig


class VideoParser(BaseParser):
    """
    Video parser for video files.
    """

    def __init__(self, config: Optional[VideoConfig] = None, **kwargs):
        """
        Initialize VideoParser.

        Args:
            config: Video parsing configuration
            **kwargs: Additional configuration parameters
        """
        self.config = config or VideoConfig()

    @property
    def supported_extensions(self) -> List[str]:
        """Return supported video file extensions."""
        return VIDEO_EXTENSIONS

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse video file - only copy original file and extract basic metadata, no content understanding.

        Args:
            source: Video file path
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with video content

        Raises:
            FileNotFoundError: If source file does not exist
            IOError: If video processing fails
        """
        from openviking.storage.viking_fs import get_viking_fs

        # Convert to Path object
        file_path = Path(source) if isinstance(source, str) else source
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found: {source}")

        viking_fs = get_viking_fs()
        temp_uri = viking_fs.create_temp_uri()

        # Phase 1: Generate temporary files
        video_bytes = file_path.read_bytes()
        ext = file_path.suffix

        from openviking_cli.utils.uri import VikingURI

        # Sanitize original filename (replace spaces with underscores)
        original_filename = file_path.name.replace(" ", "_")
        # Root directory name: filename stem + _ + extension (without dot)
        stem = file_path.stem.replace(" ", "_")
        ext_no_dot = ext[1:] if ext else ""
        root_dir_name = VikingURI.sanitize_segment(f"{stem}_{ext_no_dot}")
        root_dir_uri = f"{temp_uri}/{root_dir_name}"
        await viking_fs.mkdir(root_dir_uri)

        # 1.1 Save original video with original filename (sanitized)
        await viking_fs.write_file_bytes(f"{root_dir_uri}/{original_filename}", video_bytes)

        # 1.2 Validate video file using magic bytes
        # Define magic bytes for supported video formats
        video_magic_bytes = {
            ".mp4": [b"\x00\x00\x00", b"ftyp"],
            ".avi": [b"RIFF"],
            ".mov": [b"\x00\x00\x00", b"ftyp"],
            ".mkv": [b"\x1a\x45\xdf\xa3"],
            ".webm": [b"\x1a\x45\xdf\xa3"],
            ".flv": [b"FLV"],
            ".wmv": [b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"],
        }

        # Check magic bytes
        valid = False
        ext_lower = ext.lower()
        magic_list = video_magic_bytes.get(ext_lower, [])
        for magic in magic_list:
            if len(video_bytes) >= len(magic) and video_bytes.startswith(magic):
                valid = True
                break

        if not valid:
            raise ValueError(
                f"Invalid video file: {file_path}. File signature does not match expected format {ext_lower}"
            )

        # Extract video metadata (placeholder)
        duration = 0
        width = 0
        height = 0
        fps = 0
        format_str = ext[1:].upper()

        # Create ResourceNode - metadata only, no content understanding yet
        root_node = ResourceNode(
            type=NodeType.ROOT,
            title=file_path.stem,
            level=0,
            detail_file=None,
            content_path=None,
            children=[],
            meta={
                "duration": duration,
                "width": width,
                "height": height,
                "fps": fps,
                "format": format_str.lower(),
                "content_type": "video",
                "source_title": file_path.stem,
                "semantic_name": file_path.stem,
                "original_filename": original_filename,
            },
        )

        # Phase 3: Build directory structure (handled by TreeBuilder)
        return ParseResult(
            root=root_node,
            source_path=str(file_path),
            temp_dir_path=temp_uri,
            source_format="video",
            parser_name="VideoParser",
            meta={"content_type": "video", "format": format_str.lower()},
        )

    async def _generate_video_description(self, file_path: Path, config: VideoConfig) -> str:
        """
        Generate video description using key frames and audio transcription.

        Args:
            file_path: Video file path
            config: Video parsing configuration

        Returns:
            Video description in markdown format

        TODO: Integrate with actual video processing libraries
        """
        # Fallback implementation - returns basic placeholder
        return "Video description (video processing integration pending)\n\nThis is a video. Video processing feature has not yet integrated external libraries."

    async def _generate_semantic_info(
        self, node: ResourceNode, description: str, viking_fs, has_key_frames: bool
    ):
        """
        Phase 2: Generate abstract and overview.

        Args:
            node: ResourceNode to update
            description: Video description
            viking_fs: VikingFS instance
            has_key_frames: Whether key frames directory exists
        """
        # Generate abstract (short summary, < 100 tokens)
        abstract = description[:200] if len(description) > 200 else description

        # Generate overview (content summary + file list + usage instructions)
        overview_parts = [
            "## Content Summary\n",
            description,
            "\n\n## Available Files\n",
            f"- {node.meta['original_filename']}: Original video file ({node.meta['duration']}s, {node.meta['width']}x{node.meta['height']}, {node.meta['fps']}fps, {node.meta['format'].upper()} format)\n",
        ]

        if has_key_frames:
            overview_parts.append("- keyframes/: Directory containing extracted key frames\n")

        overview_parts.append("\n## Usage\n")
        overview_parts.append("### Play Video\n")
        overview_parts.append("```python\n")
        overview_parts.append("video_bytes = await video_resource.play()\n")
        overview_parts.append("# Returns: Video file binary data\n")
        overview_parts.append("# Purpose: Play or save the video\n")
        overview_parts.append("```\n\n")

        if has_key_frames:
            overview_parts.append("### Get Key Frames\n")
            overview_parts.append("```python\n")
            overview_parts.append("keyframes = await video_resource.keyframes()\n")
            overview_parts.append("# Returns: List of key frame resources\n")
            overview_parts.append("# Purpose: Analyze video scenes\n")
            overview_parts.append("```\n\n")

        overview_parts.append("### Get Video Metadata\n")
        overview_parts.append("```python\n")
        overview_parts.append(
            f"duration = video_resource.get_duration()  # {node.meta['duration']}s\n"
        )
        overview_parts.append(
            f"resolution = video_resource.get_resolution()  # ({node.meta['width']}, {node.meta['height']})\n"
        )
        overview_parts.append(f"fps = video_resource.get_fps()  # {node.meta['fps']}\n")
        overview_parts.append(f'format = video_resource.get_format()  # "{node.meta["format"]}"\n')
        overview_parts.append("```\n")

        overview = "".join(overview_parts)

        # Store in node meta
        node.meta["abstract"] = abstract
        node.meta["overview"] = overview

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse video from content string - Not yet implemented.

        Args:
            content: Video content (base64 or binary string)
            source_path: Optional source path for metadata
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with video content

        Raises:
            NotImplementedError: This feature is not yet implemented
        """
        raise NotImplementedError("Video parsing from content not yet implemented")
