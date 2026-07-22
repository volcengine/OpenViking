# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Constants for media parsers."""

# Image extensions supported by ImageParser
IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tiff", ".tif", ".ico", ".dib", ".icns", ".sgi", ".jp2"]

# Audio extensions supported by AudioParser
AUDIO_EXTENSIONS = [".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".opus", ".ac3"]

# Video extensions supported by VideoParser
# Note: ".ts" (MPEG-2 Transport Stream) is intentionally excluded because it
# collides with TypeScript source files (.ts in CODE_EXTENSIONS). Users who
# need to import .ts video files can pass source_format="video" explicitly.
VIDEO_EXTENSIONS = [".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"]

# All media extensions combined
MEDIA_EXTENSIONS = set(IMAGE_EXTENSIONS + AUDIO_EXTENSIONS + VIDEO_EXTENSIONS)
