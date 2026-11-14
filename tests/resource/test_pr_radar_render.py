# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.resource.github_radar.render import render
from openviking.resource.github_radar.summarizer import Summary
from tests.utils.radar_fixtures import ANCHOR, REPO, pr


def test_render_keeps_github_facts_and_escapes_untrusted_title():
    item = pr().model_copy(update={"title": "title ](https://evil.invalid) <script>"})
    report = render(REPO, item.merged_at, ANCHOR, ANCHOR, [item], Summary(), backfill=True)
    assert item.url in report
    assert "PR 数量：1" in report
    assert "生成模式：template" in report
    assert "状态：MERGED" in report
    assert "<script>" not in report
    assert f"窗口：[{item.merged_at.isoformat()}" in report
