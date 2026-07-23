from contextlib import asynccontextmanager
from unittest.mock import patch

from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.markdown import MarkdownParser


class _RecordingTempVikingFS:
    def __init__(self):
        self.handle = object()
        self.locked_uris = []
        self.writes = []
        self.dirs = []

    def create_temp_uri(self):
        return "viking://temp/request-123"

    @asynccontextmanager
    async def lock_temp_tree(self, uri, ctx=None):
        self.locked_uris.append(uri)
        yield self.handle

    async def mkdir(self, uri, exist_ok=False, **kwargs):
        self.dirs.append(uri)

    async def write_file(self, uri, content, lock_handle=None, **kwargs):
        self.writes.append(("text", uri, lock_handle))

    async def write_file_bytes(self, uri, content, lock_handle=None, **kwargs):
        self.writes.append(("bytes", uri, lock_handle))

    async def glob(self, pattern, uri="", **kwargs):
        return {"matches": []}


async def test_parse_content_locks_generated_temp_tree_and_reuses_handle():
    fs = _RecordingTempVikingFS()

    with patch.object(BaseParser, "_get_viking_fs", return_value=fs):
        result = await MarkdownParser().parse_content(
            "# title\n\nbody",
            source_name="take-meta.md",
        )

    assert result.temp_dir_path == "viking://temp/request-123"
    assert fs.locked_uris == ["viking://temp/request-123"]
    assert fs.writes
    assert all(lock_handle is fs.handle for _, _, lock_handle in fs.writes)


async def test_apply_layout_passes_handle_to_image_ingestion():
    fs = _RecordingTempVikingFS()
    parser = MarkdownParser()
    layout = await parser._compute_layout(
        "# title\n\nbody",
        "viking://temp/request-123",
        source_name="take-meta.md",
    )
    seen = []

    async def record_images(root_dir, base_dir, allowed_media_dirs, lock_handle=None):
        seen.append(lock_handle)

    parser._ingest_local_images = record_images
    with patch.object(BaseParser, "_get_viking_fs", return_value=fs):
        await parser._apply_layout(layout, lock_handle=fs.handle)

    assert seen == [fs.handle]
    assert fs.writes
    assert all(lock_handle is fs.handle for _, _, lock_handle in fs.writes)
