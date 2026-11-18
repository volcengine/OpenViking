# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Generate directory abstracts and render bounded navigation from node summaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from openviking.prompts import render_prompt
from openviking.storage.abstract_overview import AbstractOverviewDocument, markdown_safe_viking_uri
from openviking.system_assets import BUILTIN_SEMANTIC_TEMPLATES_DIR
from openviking_cli.utils.config.parser_config import SemanticConfig

_OVERVIEW_TEMPLATES = Environment(
    loader=FileSystemLoader(BUILTIN_SEMANTIC_TEMPLATES_DIR),
    autoescape=False,
    auto_reload=False,
    undefined=StrictUndefined,
)


@dataclass(frozen=True)
class DirectoryGeneration:
    abstract: str
    overview: str
    preset_abstract: bool = False


def _render_overview_body(
    dir_uri: str, summary: str, navigation: str, *, partial: bool, chinese: bool
) -> str:
    template = _OVERVIEW_TEMPLATES.get_template("directory_overview.md.j2")
    return template.render(
        title=dir_uri.rstrip("/").rsplit("/", 1)[-1],
        abstract=summary,
        navigation=navigation,
        partial=partial,
        chinese=chinese,
    )


def render_directory_overview(
    dir_uri: str,
    directory_summary: str,
    entries: Mapping[str, Mapping[str, str]],
    *,
    config: SemanticConfig,
    language: str,
    total_entries: int,
    navigation_renderer: Callable[[list[str]], str] | None = None,
) -> str:
    """Render the full directory summary and fit navigation into the remaining space."""
    chinese = language.lower().startswith("zh")
    directory_summary = directory_summary.strip()

    def render(navigation: str, *, partial: bool) -> str:
        return _render_overview_body(
            dir_uri, directory_summary, navigation, partial=partial, chinese=chinese
        )

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
    """Build L0 and L1 from the same directory summary."""
    manual = previous is not None and previous.is_manual_abstract
    # A template-backed directory has a fixed purpose. Preserve any existing
    # L0, or seed the template text on the first refresh of a populated folder.
    seeded = previous is None and bool(preset_abstract) and bool(entries)
    if previous is not None and (manual or preset_abstract is not None):
        summary = previous.body.strip()
    else:
        summary = (preset_abstract or "") if seeded else ""
    generate_summary = not manual and preset_abstract is None and bool(entries)

    def prompt(material: list[str], *, max_chars: int) -> str:
        return render_prompt(
            "semantic.abstract_generation",
            {
                "dir_name": dir_uri.rstrip("/").rsplit("/", 1)[-1],
                "material": "\n".join(material),
                "sampled_entries": len(entries),
                "total_entries": total_entries,
                "output_language": language,
                "max_chars": max_chars,
            },
        )

    async def summarize(material: list[str], *, max_chars: int) -> str:
        text = prompt(material, max_chars=max_chars)
        if len(text) > config.max_overview_prompt_chars:
            raise ValueError("directory abstract input exceeds max_overview_prompt_chars")
        result = (await complete(text)).strip()
        if not result:
            raise ValueError("directory abstract generation returned empty content")
        return result

    def batches(material: list[str], *, max_chars: int) -> list[list[str]]:
        result: list[list[str]] = []
        batch: list[str] = []
        for item in material:
            if batch and (
                len(batch) >= config.overview_batch_size
                or len(prompt([*batch, item], max_chars=max_chars))
                > config.max_overview_prompt_chars
            ):
                result.append(batch)
                batch = []
            if len(prompt([item], max_chars=max_chars)) > config.max_overview_prompt_chars:
                raise ValueError("a node summary exceeds the directory abstract input budget")
            batch.append(item)
        if batch:
            result.append(batch)
        return result

    if generate_summary:
        # Allow for the template and the coverage notice even when all links do not fit.
        summary_max_chars = config.overview_max_chars - len(
            _render_overview_body(
                dir_uri, "", "", partial=True, chinese=language.lower().startswith("zh")
            )
        )
        if summary_max_chars <= 0:
            raise ValueError("overview_max_chars cannot fit the directory summary and access guide")
        prompt_max_chars = max(config.abstract_max_chars, summary_max_chars)
        material = [
            json.dumps({"name": name, **entry}, ensure_ascii=False)
            for name, entry in sorted(entries.items())
        ]
        while True:
            groups = batches(material, max_chars=prompt_max_chars)
            if len(groups) == 1:
                summary = await summarize(groups[0], max_chars=summary_max_chars)
                break
            merged = []
            for group in groups:
                partial_summary = await summarize(group, max_chars=config.abstract_max_chars)
                merged.append(partial_summary[: config.abstract_max_chars].rstrip())
            if len(merged) >= len(material) and sum(map(len, merged)) >= sum(map(len, material)):
                raise ValueError("directory abstract budget cannot merge summary batches")
            material = merged

    overview = render_directory_overview(
        dir_uri,
        summary,
        entries,
        config=config,
        language=language,
        total_entries=total_entries,
        navigation_renderer=navigation_renderer,
    )
    abstract = summary[: config.abstract_max_chars].rstrip() if generate_summary else summary
    return DirectoryGeneration(abstract=abstract, overview=overview, preset_abstract=seeded)
