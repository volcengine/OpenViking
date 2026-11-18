# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Generate directory abstracts and render bounded navigation from node summaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from openviking.prompts import render_prompt
from openviking.storage.abstract_overview import AbstractOverviewDocument, markdown_safe_viking_uri
from openviking_cli.utils.config.parser_config import SemanticConfig


@dataclass(frozen=True)
class DirectoryGeneration:
    abstract: str
    overview: str
    preset_abstract: bool = False


def render_directory_overview(
    dir_uri: str,
    abstract: str,
    entries: Mapping[str, Mapping[str, str]],
    *,
    config: SemanticConfig,
    language: str,
    total_entries: int,
    navigation_renderer: Callable[[list[str]], str] | None = None,
) -> str:
    """Reserve space for navigation links before adding entry descriptions."""
    chinese = language.lower().startswith("zh")
    title = dir_uri.rstrip("/").rsplit("/", 1)[-1]
    navigation_title = "目录导航" if chinese else "Directory navigation"
    guide_title = "使用指引" if chinese else "Access guide"
    guide = (
        "已确定文件位置时，直接读取对应文件；位置不明确时，先在本目录内检索。"
        "需要了解目录结构或查看更多条目时，可分页浏览目录。"
        "可参考 `read`、`find`、`list` 等工具。"
        if chinese
        else "Read the file directly when its location is known; otherwise search within this "
        "directory first. Browse directory entries with pagination to explore its structure or "
        "find more entries. Tools such as `read`, `find`, and `list` can support these operations."
    )
    notice = (
        "已展示部分条目，可在本目录内检索或分页浏览。"
        if chinese
        else "Only some entries are listed. Search within this directory or browse further pages."
    )
    prefix = f"# {title}\n\n{abstract.strip()}\n\n## {navigation_title}\n\n"
    suffix = f"\n\n## {guide_title}\n\n{guide}"

    def render(navigation: str, *, partial: bool) -> str:
        if partial:
            navigation += f"\n\n{notice}"
        return prefix + navigation + suffix

    ordered = sorted(entries.items(), key=lambda item: (item[1]["kind"] != "directory", item[0]))
    candidates = ordered[: config.overview_sample_limit]
    partial = len(candidates) < total_entries
    if navigation_renderer is not None:
        overview = render(navigation_renderer([name for name, _ in candidates]), partial=partial)
        if len(overview) <= config.overview_max_chars:
            return overview

    links: list[str] = []
    summaries: list[str] = []
    for name, entry in candidates:
        label = entry.get("title") or name
        label = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        label = " ".join(label.splitlines())
        target = markdown_safe_viking_uri(f"{dir_uri.rstrip('/')}/{name}")
        links.append(f"- [{label}]({target})")
        summaries.append(" ".join(entry["summary"].splitlines()).strip())

    if len(render("\n".join(links), partial=partial)) > config.overview_max_chars:
        partial = True
    budget = config.overview_max_chars - len(render("", partial=partial))
    if budget < 0:
        raise ValueError("overview_max_chars cannot fit the directory abstract and access guide")
    selected: list[tuple[str, str]] = []
    for link, summary in zip(links, summaries, strict=True):
        cost = len(link) + bool(selected)
        if cost <= budget:
            selected.append((link, summary))
            budget -= cost

    separator = "：" if chinese else ": "
    # Give short descriptions their full text first and share the rest among
    # longer descriptions. Links have already been budgeted independently.
    lines = [link for link, _ in selected]
    descriptions = sorted(
        (len(summary), index, summary) for index, (_, summary) in enumerate(selected) if summary
    )
    for offset, (size, index, summary) in enumerate(descriptions):
        limit = budget // (len(descriptions) - offset) - len(separator)
        if limit <= 0:
            break
        if size > limit:
            summary = summary[: limit - 1].rstrip() + "…"
        lines[index] += separator + summary
        budget -= len(separator) + len(summary)
    return render("\n".join(lines), partial=partial)


async def generate_directory_semantics(
    dir_uri: str,
    entries: Mapping[str, Mapping[str, str]],
    *,
    previous: AbstractOverviewDocument | None,
    total_entries: int,
    config: SemanticConfig,
    language: str,
    complete: Callable[[str], Awaitable[str]],
    navigation_renderer: Callable[[list[str]], str] | None = None,
    preset_abstract: str | None = None,
) -> DirectoryGeneration:
    """Regenerate L0 from the selected inputs, then render L1 without a model."""
    manual = previous is not None and previous.is_manual_abstract
    # A template-backed directory has a fixed purpose. Preserve any existing
    # L0, or seed the template text on the first refresh of a populated folder.
    seeded = previous is None and bool(preset_abstract) and bool(entries)
    if previous is not None and (manual or preset_abstract is not None):
        abstract = previous.body.strip()
    else:
        abstract = (preset_abstract or "") if seeded else ""

    def prompt(material: list[str]) -> str:
        return render_prompt(
            "semantic.abstract_generation",
            {
                "dir_name": dir_uri.rstrip("/").rsplit("/", 1)[-1],
                "material": "\n".join(material),
                "sampled_entries": len(entries),
                "total_entries": total_entries,
                "output_language": language,
                "max_chars": config.abstract_max_chars,
            },
        )

    async def summarize(material: list[str]) -> str:
        text = prompt(material)
        if len(text) > config.max_overview_prompt_chars:
            raise ValueError("directory abstract input exceeds max_overview_prompt_chars")
        result = (await complete(text)).strip()
        if not result:
            raise ValueError("directory abstract generation returned empty content")
        return result[: config.abstract_max_chars].rstrip()

    def batches(material: list[str]) -> list[list[str]]:
        result: list[list[str]] = []
        batch: list[str] = []
        for item in material:
            if batch and (
                len(batch) >= config.overview_batch_size
                or len(prompt([*batch, item])) > config.max_overview_prompt_chars
            ):
                result.append(batch)
                batch = []
            if len(prompt([item])) > config.max_overview_prompt_chars:
                raise ValueError("a node summary exceeds the directory abstract input budget")
            batch.append(item)
        if batch:
            result.append(batch)
        return result

    if not manual and preset_abstract is None and entries:
        material = [
            json.dumps({"name": name, **entry}, ensure_ascii=False)
            for name, entry in sorted(entries.items())
        ]
        while True:
            groups = batches(material)
            if len(groups) == 1:
                abstract = await summarize(groups[0])
                break
            merged = [await summarize(group) for group in groups]
            if len(merged) >= len(material) and sum(map(len, merged)) >= sum(map(len, material)):
                raise ValueError("directory abstract budget cannot merge summary batches")
            material = merged

    overview = render_directory_overview(
        dir_uri,
        abstract,
        entries,
        config=config,
        language=language,
        total_entries=total_entries,
        navigation_renderer=navigation_renderer,
    )
    return DirectoryGeneration(abstract=abstract, overview=overview, preset_abstract=seeded)
