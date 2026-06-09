# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.server.header_forwarding import extract_forward_headers


def test_extract_forward_headers_is_case_insensitive_and_filters_values():
    headers = {
        "X-Tos-Signature": "sig-value",
        "x-tos-date": "20260609T120000Z",
        "Authorization": "Bearer blocked",
    }

    result = extract_forward_headers(
        headers,
        ["x-tos-signature", "X-Tos-Date"],
    )

    assert result == {
        "x-tos-signature": "sig-value",
        "x-tos-date": "20260609T120000Z",
    }
