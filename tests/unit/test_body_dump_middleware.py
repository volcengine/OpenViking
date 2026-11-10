# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.server.body_dump_middleware import _should_skip


def test_zip_responses_skip_body_capture():
    assert _should_skip("application/zip")
    assert _should_skip("application/zip; charset=binary")
