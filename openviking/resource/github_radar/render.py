# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Render factual Markdown with clearly marked model explanations."""

import html
import re
from datetime import datetime

from openviking.resource.github_radar.models import PullRequest
from openviking.resource.github_radar.summarizer import Summary


def text(value: str) -> str:
    value = html.escape(value).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_\[\]{}<>#!|])", r"\\\1", value)


def render(
    repository: str,
    start: datetime,
    end: datetime,
    generated_at: datetime,
    prs: list[PullRequest],
    summary: Summary,
    *,
    backfill: bool,
) -> str:
    bracket = "[" if backfill else "("
    lines = [
        f"# {repository} 已合并 PR Radar",
        "",
        f"- 仓库：https://github.com/{repository}",
        f"- 窗口：{bracket}{start.isoformat()}, {end.isoformat()}]",
        f"- 生成时间：{generated_at.isoformat()}",
        f"- PR 数量：{len(prs)}",
        f"- 生成模式：{summary.mode}",
        "",
        "事实来自 GitHub 采集；模型解读基于 PR 标题与描述，未经源码验证。",
        "",
    ]
    if summary.overviews:
        lines.extend(["## 主题概览（模型解读）", ""])
        for topic, overview in summary.overviews.items():
            lines.extend([f"### {text(topic)}", "", text(overview), ""])
            selected = summary.highlights.get(topic, [])
            if selected:
                lines.extend(["重点 PR：" + "、".join(f"#{number}" for number in selected), ""])
    lines.extend(["## PR 事实与解读", ""])
    for pr in sorted(prs, key=lambda item: (item.merged_at, item.number)):
        lines.extend(
            [
                f"### [#{pr.number} {text(pr.title)}]({pr.url})",
                "",
                f"- 作者：{text(pr.author)}",
                f"- 状态：{pr.state}；草稿：{'是' if pr.draft else '否'}",
                f"- 创建：{pr.created_at.isoformat()}",
                f"- 更新：{pr.updated_at.isoformat()}",
                f"- 关闭：{pr.closed_at.isoformat()}",
                f"- 合并：{pr.merged_at.isoformat()}",
                "- 标签：" + ("、".join(text(label) for label in pr.labels) or "无"),
            ]
        )
        item = summary.explanations.get(pr.number)
        if item:
            lines.extend(
                [
                    f"- 主题（模型）：{text(item.topic)}；关注级别：{item.priority}",
                    f"- 摘要（模型）：{text(item.summary)}",
                    f"- 影响（模型）：{text(item.impact)}",
                    f"- 建议（模型）：{text(item.advice)}",
                ]
            )
        else:
            lines.append("- 解读：未生成模型摘要，请以原 PR 内容为准。")
        lines.append("")
    return "\n".join(lines)
