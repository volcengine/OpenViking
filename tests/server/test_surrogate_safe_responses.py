# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Lone-surrogate (U+D800-U+DFFF) safety at the HTTP response boundary (#4238)."""

import json

from fastapi.responses import JSONResponse

from openviking.server.responses import (
    SurrogateSafeJSONResponse,
    error_response,
    response_from_result,
)


def test_surrogate_safe_json_response_replaces_lone_surrogates():
    content = {
        "uri": "viking://resources/\ud800bad_name",
        "nested": {"paths": ["\udfff", "ok"]},
    }
    response = SurrogateSafeJSONResponse(status_code=200, content=content)
    body = response.render(content)
    decoded = json.loads(body)
    assert "\ud800" not in decoded["uri"]
    assert "\udfff" not in decoded["nested"]["paths"][0]
    assert "�" in decoded["uri"]


def test_surrogate_safe_json_response_keeps_valid_non_bmp_characters():
    content = {"name": "emoji \U0001F600 preserved"}
    response = SurrogateSafeJSONResponse(content=content)
    body = response.render(content)
    assert "\U0001F600" in json.loads(body)["name"]


def test_surrogate_safe_json_response_accepts_clean_content():
    content = {"status": "ok", "count": 3, "flag": True, "none": None}
    response = SurrogateSafeJSONResponse(content=content)
    body = response.render(content)
    assert json.loads(body) == content


def test_clean_content_is_byte_identical_to_plain_json_response():
    # The happy path must not pay the sanitizer cost or change the output.
    content = {"uri": "viking://resources/ok_name", "nested": [1, 2, {"k": "v"}]}
    safe = SurrogateSafeJSONResponse(content=content).render(content)
    plain = JSONResponse(content=content).render(content)
    assert safe == plain


def test_error_response_with_surrogate_message_renders():
    response = error_response("NOT_FOUND", "viking://resources/\ud800bad_name missing")
    assert isinstance(response, SurrogateSafeJSONResponse)
    assert response.status_code == 404
    body = response.body
    assert b"\xed\xb2\x80" not in body  # no raw surrogate bytes in UTF-8
    assert b"\xef\xbf\xbd" in body  # replacement character present


def test_response_from_result_error_path_renders_with_surrogate():
    response = response_from_result(
        {
            "status": "error",
            "code": "PROCESSING_ERROR",
            "message": "listed viking://resources/\ud800bad_name",
        }
    )
    assert isinstance(response, SurrogateSafeJSONResponse)
    body = response.body
    assert b"\xed\xb2\x80" not in body
    assert b"\xef\xbf\xbd" in body


def test_response_from_result_success_path_is_surrogate_safe_default():
    # The success path returns a plain dict, which the FastAPI app serializes with
    # the surrogate-safe default response class. Replicate that class's render on
    # the returned dict to prove the whole envelope stays well-formed.
    result = response_from_result({"uri": "viking://resources/\ud800x"})
    assert isinstance(result, dict)
    response = SurrogateSafeJSONResponse(status_code=200, content=result)
    body = response.render(result)
    assert b"\xed\xb2\x80" not in body
    assert b"\xef\xbf\xbd" in body
