# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Whiteboard fidelity and failure handling through native Feishu imports."""

import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from lark_oapi.api.docx.v1 import ListDocumentBlockResponse
from PIL import Image

from openviking.parse.accessors import feishu_accessor as module
from openviking.parse.accessors.feishu_accessor import FeishuAccessor
from openviking_cli.exceptions import InvalidArgumentError, OpenVikingError
from openviking_cli.utils.config.parser_config import FeishuConfig

SOURCE = "https://example.feishu.cn/docx/document"
NODES = [
    {"id": "node-a", "type": "rect", "text": {"text": "Review request"}},
    {"id": "node-b", "type": "rect", "text": {"text": "Publish change"}},
    {
        "id": "edge",
        "type": "connector",
        "connector": {"captions": {"data": [{"text": "Approved by owner"}]}},
    },
    {
        "id": "diagram",
        "type": "section",
        "syntax": {"syntax_type": 2, "code": "graph LR\nA-->B"},
    },
]


def _paragraph(block_id, text):
    return {
        "block_id": block_id,
        "block_type": 2,
        "parent_id": "document",
        "text": {"elements": [{"text_run": {"content": text}}]},
    }


def _board(block_id="board-block", token="board-token"):
    return {
        "block_id": block_id,
        "block_type": 43,
        "parent_id": "document",
        "board": {"token": token},
    }


def _response(payload=None, *, content=None, code=0, content_type="application/json"):
    return SimpleNamespace(
        code=code,
        msg="missing board:whiteboard:node:read" if code else "",
        success=lambda: code == 0,
        raw=SimpleNamespace(
            content=content if content is not None else json.dumps(payload).encode(),
            headers={"content-type": content_type},
            status_code=403 if code else 200,
        ),
    )


@pytest.fixture
def whiteboard_import(monkeypatch, tmp_path):
    image_buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image_buffer, format="PNG")
    preview = image_buffer.getvalue()
    blocks = [
        {"block_id": "document", "block_type": 1, "page": {}},
        _paragraph("before", "Before the board"),
        _board(),
        _paragraph("after", "After the board"),
    ]
    list_blocks = MagicMock(
        side_effect=lambda *_: ListDocumentBlockResponse(
            {"code": 0, "data": {"items": blocks, "has_more": False}}
        )
    )
    read_nodes = MagicMock(return_value=_response({"code": 0, "data": {"nodes": NODES}}))
    read_preview = MagicMock(return_value=_response(content=preview, content_type="image/png"))
    client = SimpleNamespace(
        docx=SimpleNamespace(v1=SimpleNamespace(document_block=SimpleNamespace(list=list_blocks))),
        request=read_nodes,
        board=SimpleNamespace(
            v1=SimpleNamespace(whiteboard=SimpleNamespace(download_as_image=read_preview))
        ),
    )
    identity_modes = []

    def get_client(self, *, use_user_token=False):
        identity_modes.append(use_user_token)
        return client

    monkeypatch.setattr(FeishuAccessor, "_get_client", get_client)
    monkeypatch.setattr(module, "get_parser_download_temp_dir", lambda: str(tmp_path))
    accessor = FeishuAccessor()

    def run(*, token="user-token", download_images=True):
        return asyncio.run(
            accessor.access(
                SOURCE,
                feishu_access_token=token,
                feishu_config=FeishuConfig(
                    app_id="test-app", app_secret="test-secret", download_images=download_images
                ),
            )
        )

    return SimpleNamespace(
        run=run,
        blocks=blocks,
        nodes=read_nodes,
        preview=read_preview,
        preview_bytes=preview,
        identity_modes=identity_modes,
        tmp_path=tmp_path,
    )


@pytest.mark.parametrize("token", [None, "user-token"])
def test_import_preserves_whiteboard_text_caption_syntax_and_preview(whiteboard_import, token):
    fixture = whiteboard_import
    resource = fixture.run(token=token)
    try:
        content = resource.path.read_text()
        for text in [
            "Before the board",
            "Review request",
            "Publish change",
            "Approved by owner",
            "graph LR\nA-->B",
            "After the board",
        ]:
            assert text in content
        assert "https://example.feishu.cn/docx/document#board-block" in content
        assert "feishu://whiteboard/" not in content
        images = list((resource.path.parent / "images").iterdir())
        assert len(images) == 1
        assert images[0].read_bytes() == fixture.preview_bytes
        assert images[0].relative_to(resource.path.parent).as_posix() in content
        assert fixture.nodes.call_count == fixture.preview.call_count == 1
        assert all(mode is bool(token) for mode in fixture.identity_modes)
        assert fixture.nodes.call_args.args[0].uri.endswith("/board-token/nodes")
        if token:
            assert fixture.nodes.call_args.args[1].user_access_token == token
            assert fixture.preview.call_args.args[1].user_access_token == token
    finally:
        cleanup_path = resource.path.parent
        resource.cleanup()
    assert not cleanup_path.exists()


def test_board_only_document_is_not_reduced_to_its_title(whiteboard_import):
    fixture = whiteboard_import
    fixture.blocks[:] = [fixture.blocks[0], _board()]
    resource = fixture.run()
    try:
        assert "Approved by owner" in resource.path.read_text()
        assert list((resource.path.parent / "images").glob("*"))
    finally:
        resource.cleanup()


def test_download_images_false_keeps_board_text_and_source_link(whiteboard_import):
    fixture = whiteboard_import
    resource = fixture.run(download_images=False)
    try:
        content = resource.path.read_text()
        assert "Review request" in content and "Approved by owner" in content
        assert "https://example.feishu.cn/docx/document#board-block" in content
        assert "feishu://whiteboard/" not in content
        fixture.preview.assert_not_called()
    finally:
        resource.cleanup()


def test_duplicate_board_reads_are_deduplicated_only_within_one_import(whiteboard_import):
    fixture = whiteboard_import
    fixture.blocks.insert(3, _board("second-board-block"))
    for _ in range(2):
        resource = fixture.run()
        try:
            content = resource.path.read_text()
            assert "#board-block" in content and "#second-board-block" in content
            assert len(list((resource.path.parent / "images").iterdir())) == 1
        finally:
            resource.cleanup()
    assert fixture.nodes.call_count == fixture.preview.call_count == 2


def test_reused_accessor_does_not_share_board_content_between_users(whiteboard_import):
    fixture = whiteboard_import
    first = fixture.run(token="first-user")
    first.cleanup()
    fixture.nodes.return_value = _response(
        {"data": {"nodes": [{"text": {"text": "Second user's updated board"}}]}}
    )
    second = fixture.run(token="second-user")
    try:
        content = second.path.read_text()
        assert "Second user's updated board" in content
        assert "Review request" not in content
        assert fixture.nodes.call_count == fixture.preview.call_count == 2
        assert fixture.nodes.call_args.args[1].user_access_token == "second-user"
    finally:
        second.cleanup()


def test_board_attribute_fallback_handles_unmapped_block_types(whiteboard_import):
    fixture = whiteboard_import
    fixture.blocks[2]["block_type"] = 999
    resource = fixture.run()
    try:
        assert "Review request" in resource.path.read_text()
        assert list((resource.path.parent / "images").iterdir())
    finally:
        resource.cleanup()


def test_plantuml_source_with_backticks_survives_raw_node_parsing(whiteboard_import):
    fixture = whiteboard_import
    code = '@startuml\nA -> B: "```"\n@enduml'
    fixture.nodes.return_value = _response(
        {"data": {"nodes": [{"syntax": {"syntax_type": 1, "code": code}}]}}
    )
    resource = fixture.run()
    try:
        assert f"````plantuml\n{code}\n````" in resource.path.read_text()
    finally:
        resource.cleanup()


@pytest.mark.parametrize("stage", ["nodes", "preview"])
def test_missing_board_permissions_fail_import_explicitly(whiteboard_import, stage):
    fixture = whiteboard_import
    getattr(fixture, stage).return_value = _response({}, code=99991672)
    with pytest.raises(OpenVikingError) as error:
        fixture.run()
    assert error.value.code == "FAILED_PRECONDITION"
    assert "board:whiteboard:node:read" in str(error.value)
    assert "Bitable" not in str(error.value)
    assert not list(fixture.tmp_path.iterdir())


def test_board_timeout_is_not_reported_as_a_successful_import(whiteboard_import):
    fixture = whiteboard_import
    fixture.nodes.side_effect = TimeoutError("board request timed out")
    with pytest.raises(TimeoutError, match="timed out"):
        fixture.run()
    fixture.preview.assert_not_called()


@pytest.mark.parametrize("bad_payload", [{"data": {}}, {"data": {"nodes": ["invalid"]}}])
def test_malformed_board_response_fails_import(whiteboard_import, bad_payload):
    fixture = whiteboard_import
    fixture.nodes.return_value = _response(bad_payload)
    with pytest.raises(OpenVikingError, match="whiteboard"):
        fixture.run()


@pytest.mark.parametrize(
    "limit,value",
    [("_MAX_WHITEBOARDS", 0), ("_MAX_WHITEBOARD_NODES", 1), ("_MAX_WHITEBOARD_JSON_BYTES", 1)],
)
def test_board_processing_limits_fail_instead_of_truncating(
    whiteboard_import, monkeypatch, limit, value
):
    fixture = whiteboard_import
    monkeypatch.setattr(module, limit, value)
    with pytest.raises(InvalidArgumentError, match="whiteboard"):
        fixture.run()
    fixture.preview.assert_not_called()


@pytest.mark.parametrize("limit", ["_MAX_WHITEBOARD_PREVIEW_BYTES", "_MAX_WHITEBOARD_MEDIA_BYTES"])
def test_preview_byte_limits_fail_before_materialization(whiteboard_import, monkeypatch, limit):
    fixture = whiteboard_import
    monkeypatch.setattr(module, limit, len(fixture.preview_bytes) - 1)
    with pytest.raises(InvalidArgumentError, match="whiteboard"):
        fixture.run()
    assert not list(fixture.tmp_path.iterdir())


def test_invalid_preview_cannot_be_silently_dropped_downstream(whiteboard_import):
    fixture = whiteboard_import
    fixture.preview.return_value = _response(content=b"not an image", content_type="image/png")
    with pytest.raises(InvalidArgumentError, match="whiteboard"):
        fixture.run()


def test_unrecognized_node_payload_keeps_visible_text_and_preview(whiteboard_import):
    fixture = whiteboard_import
    fixture.nodes.return_value = _response(
        {
            "data": {
                "nodes": [{"id": "future", "type": "new-kind", "body": {"text": "New node text"}}]
            }
        }
    )
    resource = fixture.run()
    try:
        assert "New node text" in resource.path.read_text()
        assert list((resource.path.parent / "images").iterdir())
    finally:
        resource.cleanup()


@pytest.mark.parametrize("kind", ["nodes", "json", "media"])
def test_limits_cover_all_distinct_boards_in_one_import(whiteboard_import, monkeypatch, kind):
    fixture = whiteboard_import
    fixture.blocks.insert(3, _board("other-block", "other-board"))
    limit, value = {
        "nodes": ("_MAX_WHITEBOARD_NODES", len(NODES)),
        "json": ("_MAX_WHITEBOARD_JSON_BYTES", len(fixture.nodes.return_value.raw.content)),
        "media": ("_MAX_WHITEBOARD_MEDIA_BYTES", len(fixture.preview_bytes)),
    }[kind]
    monkeypatch.setattr(module, limit, value)
    with pytest.raises(InvalidArgumentError, match="whiteboard"):
        fixture.run()
    assert fixture.nodes.call_count == 2
    assert not list(fixture.tmp_path.iterdir())


def test_empty_board_retains_its_preview_and_source(whiteboard_import):
    fixture = whiteboard_import
    fixture.nodes.return_value = _response({"data": {"nodes": []}})
    resource = fixture.run()
    try:
        assert "https://example.feishu.cn/docx/document#board-block" in resource.path.read_text()
        assert list((resource.path.parent / "images").iterdir())
    finally:
        resource.cleanup()


def test_board_preview_and_document_image_have_separate_filenames(whiteboard_import, monkeypatch):
    fixture = whiteboard_import
    fixture.blocks.insert(
        2,
        {"block_id": "ordinary-image", "block_type": 27, "image": {"token": "board-token"}},
    )
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(buffer, format="PNG")
    ordinary_image = buffer.getvalue()
    monkeypatch.setattr(
        FeishuAccessor, "_download_image", lambda *args, **kwargs: (ordinary_image, "image/png")
    )
    resource = fixture.run()
    try:
        images = list((resource.path.parent / "images").iterdir())
        assert {p.read_bytes() for p in images} == {ordinary_image, fixture.preview_bytes}
        assert all(
            p.relative_to(resource.path.parent).as_posix() in resource.path.read_text()
            for p in images
        )
    finally:
        resource.cleanup()


def test_document_without_boards_keeps_existing_output_and_requests(whiteboard_import):
    fixture = whiteboard_import
    fixture.blocks[:] = [fixture.blocks[0], fixture.blocks[1], fixture.blocks[-1]]
    resource = fixture.run()
    try:
        assert resource.path.read_text() == "Before the board\n\nAfter the board"
        fixture.nodes.assert_not_called()
        fixture.preview.assert_not_called()
    finally:
        resource.cleanup()
