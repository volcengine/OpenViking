# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Media parsers must honor a caller-supplied resource_name / source_name for
the resource's internal filename, URI and title, instead of leaking the temp
upload id (upload_<uuid>) — matching the markdown parser. Regression test for
#2382.

The name-resolution logic is shared by the image / audio / video parsers via
``resolve_media_names``; it is unit-tested directly here (covering all three),
plus one image-parser integration test wiring it end-to-end.
"""

import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from openviking.parse.parsers.media.image import ImageParser
from openviking.parse.parsers.media.naming import resolve_media_names
from openviking_cli.utils.config.parser_config import ImageConfig


def _png_bytes(width: int = 10, height: int = 10) -> bytes:
    img = Image.new("RGB", (width, height), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fake_viking_fs() -> MagicMock:
    fs = MagicMock()
    fs.create_temp_uri = MagicMock(return_value="viking://temp/abc123")
    fs.mkdir = AsyncMock()
    fs.write_file_bytes = AsyncMock()
    return fs


# --- resolve_media_names (shared by image / audio / video parsers) -----------


def test_resolve_uses_resource_name():
    fp = Path("upload_0123456789abcdef.png")
    assert resolve_media_names(fp, ".png", resource_name="vacation") == (
        "vacation",
        "vacation",
        "vacation.png",
    )


def test_resolve_resource_name_filename_is_not_double_extended():
    # A filename-like resource_name is reduced to its stem ("My Holiday.png" ->
    # "My_Holiday.png", not "My_Holiday.png.png").
    fp = Path("upload_x.png")
    assert resolve_media_names(fp, ".png", resource_name="My Holiday.png") == (
        "My Holiday",
        "My_Holiday",
        "My_Holiday.png",
    )


def test_resolve_source_name_stem_when_no_resource_name():
    fp = Path("upload_x.png")
    assert resolve_media_names(fp, ".png", source_name="My Holiday.png") == (
        "My Holiday",
        "My_Holiday",
        "My_Holiday.png",
    )


def test_resolve_preserves_non_media_dotted_name():
    # A name that merely contains a dot (not a media extension) is kept intact —
    # only a real media extension is stripped, so "meeting.v1" is not truncated.
    fp = Path("upload_x.png")
    assert resolve_media_names(fp, ".png", resource_name="meeting.v1") == (
        "meeting.v1",
        "meeting.v1",
        "meeting.v1.png",
    )


def test_resolve_empty_resource_name_falls_through_to_source_name():
    fp = Path("upload_x.png")
    assert resolve_media_names(fp, ".png", resource_name="", source_name="clip.png") == (
        "clip",
        "clip",
        "clip.png",
    )


def test_resolve_falls_back_to_temp_name_when_no_caller_name():
    fp = Path("upload_0123456789abcdef.png")
    assert resolve_media_names(fp, ".png") == (
        "upload_0123456789abcdef",
        "upload_0123456789abcdef",
        "upload_0123456789abcdef.png",
    )


def test_resolve_fallback_is_byte_identical_to_legacy_for_spaces():
    # Legacy behavior replaced spaces anywhere in the basename via
    # name.replace(" ", "_") — including a space inside the extension. The
    # no-kwargs fallback must reproduce that exactly.
    fp = Path("my clip. mp4")
    display, stem, original = resolve_media_names(fp, fp.suffix)
    assert original == "my_clip._mp4"
    assert original == fp.name.replace(" ", "_")  # exact legacy expression
    assert (display, stem) == ("my clip", "my_clip")


# --- image parser integration (wires resolve_media_names end-to-end) ---------


@pytest.mark.asyncio
async def test_image_parser_honors_resource_name(tmp_path):
    upload = tmp_path / "upload_0123456789abcdef.png"
    upload.write_bytes(_png_bytes())

    parser = ImageParser(config=ImageConfig())
    fake_fs = _fake_viking_fs()
    with patch("openviking.parse.parsers.media.image.get_viking_fs", return_value=fake_fs):
        result = await parser.parse(str(upload), resource_name="vacation")

    assert result.root.meta["original_filename"] == "vacation.png"
    assert result.root.meta["semantic_name"] == "vacation"
    assert result.root.meta["source_title"] == "vacation"
    assert result.root.title == "vacation"

    written = [call.args[0] for call in fake_fs.write_file_bytes.await_args_list]
    assert any(p.endswith("/vacation.png") for p in written)
    assert not any("upload_0123456789abcdef" in p for p in written)


@pytest.mark.asyncio
async def test_image_parser_falls_back_to_temp_name(tmp_path):
    upload = tmp_path / "upload_0123456789abcdef.png"
    upload.write_bytes(_png_bytes())

    parser = ImageParser(config=ImageConfig())
    fake_fs = _fake_viking_fs()
    with patch("openviking.parse.parsers.media.image.get_viking_fs", return_value=fake_fs):
        result = await parser.parse(str(upload))

    assert result.root.meta["original_filename"] == "upload_0123456789abcdef.png"
