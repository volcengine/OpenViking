# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.privacy import scan as privacy_scan
from openviking.privacy.sanitizer import PrivacyReplacement, PrivacySanitizationResult
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError, ResourceExhaustedError
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("account-a", "alice"), role=Role.USER)


class _VikingFS:
    def __init__(self):
        self.read_limits: list[int | None] = []

    async def stat(self, uri, *, ctx, skip_count):  # noqa: ANN001
        del ctx, skip_count
        if uri == "viking://resources/demo":
            return {"isDir": True, "size": 0}
        raise AssertionError(f"unexpected stat: {uri}")

    async def tree(self, uri, **kwargs):  # noqa: ANN001
        assert uri == "viking://resources/demo"
        assert kwargs["node_limit"] == privacy_scan.PRIVACY_SCAN_MAX_NODES
        return [
            {
                "uri": "viking://resources/demo/large.md",
                "size": privacy_scan.PRIVACY_SCAN_MAX_FILE_BYTES + 1,
                "isDir": False,
            },
            {
                "uri": "viking://resources/demo/content.md",
                "size": 13,
                "isDir": False,
            },
            {
                "uri": "viking://resources/demo/.abstract.md",
                "size": 10,
                "isDir": False,
            },
        ]

    async def read_file_bytes(self, uri, *, ctx, max_bytes):  # noqa: ANN001
        del ctx
        assert uri == "viking://resources/demo/content.md"
        self.read_limits.append(max_bytes)
        return b"prefix secret"


class _Sanitizer:
    async def sanitize(  # noqa: ANN001
        self,
        content,
        *,
        ctx,
        uri,
        max_replacements,
        fail_on_match_limit,
    ):
        del ctx
        assert content == "prefix secret"
        assert uri == "viking://resources/demo/content.md"
        assert max_replacements == privacy_scan.PRIVACY_SCAN_MAX_REPLACEMENTS_PER_FILE
        assert fail_on_match_limit is False
        return PrivacySanitizationResult(
            content="prefix reference",
            replacements=(PrivacyReplacement(7, 13, "reference"),),
        )


async def test_privacy_scan_applies_file_and_node_budgets_before_reading():
    viking_fs = _VikingFS()
    service = SimpleNamespace(viking_fs=viking_fs)

    result = await privacy_scan.scan_privacy_content(
        service=service,
        ctx=_ctx(),
        sanitizer=_Sanitizer(),
        uri="viking://resources/demo",
    )

    assert result["enumerated_files"] == 3
    assert result["scanned_files"] == 1
    assert result["matched_files"] == 1
    assert result["skipped_files"] == {"file_too_large": 1, "internal_metadata": 1}
    assert viking_fs.read_limits == [privacy_scan.PRIVACY_SCAN_MAX_FILE_BYTES]


async def test_privacy_scan_rejects_invalid_budget_and_concurrent_run():
    service = SimpleNamespace(viking_fs=_VikingFS())
    with pytest.raises(InvalidArgumentError, match="node_limit"):
        await privacy_scan.scan_privacy_content(
            service=service,
            ctx=_ctx(),
            sanitizer=_Sanitizer(),
            uri="viking://resources/demo",
            node_limit=privacy_scan.PRIVACY_SCAN_MAX_NODES + 1,
        )

    privacy_scan._SCAN_ACTIVE = True
    try:
        with pytest.raises(ResourceExhaustedError, match="already running"):
            await privacy_scan.scan_privacy_content(
                service=service,
                ctx=_ctx(),
                sanitizer=_Sanitizer(),
                uri="viking://resources/demo",
            )
    finally:
        privacy_scan._SCAN_ACTIVE = False
