# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Feishu OpenAPI error mapping."""

from types import SimpleNamespace

import pytest

from openviking.parse.accessors.feishu_accessor import _raise_from_lark_response
from openviking_cli.exceptions import OpenVikingError


def _fake_response(
    *,
    code: int,
    msg: str,
    http_status: int = 400,
):
    return SimpleNamespace(
        code=code,
        msg=msg,
        raw=SimpleNamespace(status_code=http_status),
    )


def _raised_error(response, *, operation="resolve wiki node", resource=None):
    with pytest.raises(OpenVikingError) as exc_info:
        _raise_from_lark_response(response, operation=operation, resource=resource)
    return exc_info.value


@pytest.mark.parametrize("feishu_code", [1770032, 131006])
def test_maps_feishu_forbidden_to_permission_denied_and_keeps_details(feishu_code):
    response = _fake_response(
        code=feishu_code,
        msg="forBidden",
        http_status=400,
    )
    exc = _raised_error(
        response,
        operation="fetch document blocks",
        resource="doc_token",
    )

    assert exc.code == "PERMISSION_DENIED"
    assert f"code={feishu_code}, msg=forBidden" in exc.message
    assert exc.details["feishu_code"] == feishu_code
    assert exc.details["feishu_msg"] == "forBidden"
    assert exc.details["http_status"] == 400
    assert exc.details["resource"] == "doc_token"
    assert exc.details["error_source"] == "upstream_feishu"


def test_maps_missing_bitable_scope_without_parsing_message():
    response = _fake_response(
        code=99991672,
        msg="opaque provider message",
        http_status=400,
    )

    exc = _raised_error(response, operation="list bitable tables")

    assert exc.code == "FAILED_PRECONDITION"
    assert exc.message == (
        "Feishu application is missing required Bitable permissions: "
        "code=99991672, msg=opaque provider message"
    )


@pytest.mark.parametrize(
    ("http_status", "error_code"),
    [
        (401, "UNAUTHENTICATED"),
        (404, "NOT_FOUND"),
        (429, "RESOURCE_EXHAUSTED"),
        (500, "UNAVAILABLE"),
        (400, "INVALID_ARGUMENT"),
    ],
)
def test_maps_http_status_to_openviking_error(http_status, error_code):
    response = _fake_response(code=123, msg="failed", http_status=http_status)

    exc = _raised_error(response)

    assert exc.code == error_code
    assert exc.details["feishu_code"] == 123
    assert exc.details["http_status"] == http_status


def test_generic_http_forbidden_is_not_explicit_source_revocation():
    exc = _raised_error(_fake_response(code=123, msg="forbidden", http_status=403))
    assert exc.code == "PERMISSION_DENIED"
    assert "error_source" not in exc.details


@pytest.mark.asyncio
async def test_feishu_preflight_binds_only_upstream_denials(monkeypatch):
    from unittest.mock import Mock

    from openviking.parse.accessors.feishu_accessor import FeishuAccessor
    from openviking_cli.exceptions import PermissionDeniedError

    source = "https://example.feishu.cn/wiki/source"
    for error in (
        _raised_error(_fake_response(code=131006, msg="denied")),
        PermissionDeniedError("local network policy"),
    ):
        worker = SimpleNamespace(_preflight_source_sync=Mock(side_effect=error))
        monkeypatch.setattr(FeishuAccessor, "_new_operation", lambda *a, worker=worker, **k: worker)
        with pytest.raises(OpenVikingError) as raised:
            await FeishuAccessor().preflight_source(source)
        if error.details.get("error_source") == "upstream_feishu":
            assert raised.value.details["source_uri"] == source
        else:
            assert "source_uri" not in raised.value.details


def test_feishu_child_permission_evidence_survives_directory_normalization(tmp_path):
    from openviking.parse.accessors.feishu_accessor import FeishuAccessor
    from openviking.parse.parsers.directory import DirectoryParser

    source = "https://example.feishu.cn/wiki/source"
    upstream = _raised_error(_fake_response(code=1770032, msg="denied"))
    skipped = []
    FeishuAccessor._record_skipped_drive_item(
        skipped,
        item_type="docx",
        token="child",
        name="child",
        target_dir=tmp_path,
        error=upstream,
        source_uri=source,
    )
    failures = DirectoryParser._source_skipped_items(
        {"feishu_folder_skipped_items": skipped}, tmp_path
    )
    assert failures[0]["error_details"] == {
        "error_source": "upstream_feishu",
        "source_uri": source,
    }
