# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Generate directory abstracts and render bounded navigation from node summaries."""

from __future__ import annotations

import json
import re
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


def _truncate_generated_text(text: str, max_chars: int) -> str:
    """Prefer complete sentences when shortening the directory abstract."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    if max_chars <= 3:
        return text[:max_chars]

    first_sentence_end = None
    last_sentence_end_within_limit = None
    for sentence_end_match in re.finditer(r"\.(?!\d)(?=\s|$)|[!?](?=\s|$)|[。？！]", text):
        sentence_end = sentence_end_match.end()
        if first_sentence_end is None:
            first_sentence_end = sentence_end
        if sentence_end <= max_chars:
            last_sentence_end_within_limit = sentence_end
        elif last_sentence_end_within_limit is not None:
            break

    if last_sentence_end_within_limit is not None:
        return text[:last_sentence_end_within_limit].strip()
    if first_sentence_end is not None:
        return text[:first_sentence_end].strip()

    candidate = text[: max_chars - 3].rstrip()
    word_boundary = candidate.rfind(" ")
    if word_boundary > 0:
        return candidate[:word_boundary].rstrip() + "..."

    return candidate + "..."


def _render_directory_overview(
    dir_uri: str,
    directory_summary: str,
    entries: Mapping[str, Mapping[str, str]],
    *,
    config: SemanticConfig,
    language: str,
    total_entries: int,
    navigation_renderer: Callable[[list[str]], str] | None = None,
) -> tuple[str, str]:
    """Return the overview and the directory summary that fits in it."""
    chinese = language.lower().startswith("zh")
    title = dir_uri.rstrip("/").rsplit("/", 1)[-1]
    template = _OVERVIEW_TEMPLATES.get_template("directory_overview.md.j2")
    overview_limit = max(0, config.overview_max_chars)
    directory_summary = directory_summary.strip()

    def render(navigation: str, *, partial: bool, summary: str) -> str:
        return template.render(
            title=title, abstract=summary, navigation=navigation, partial=partial, chinese=chinese
        )

    ordered = sorted(entries.items(), key=lambda item: (item[1]["kind"] != "directory", item[0]))
    candidates = ordered[: config.overview_sample_limit]
    partial = len(candidates) < total_entries
    if navigation_renderer is not None:
        overview = render(
            navigation_renderer([name for name, _ in candidates]),
            partial=partial,
            summary=directory_summary,
        )
        if len(overview) <= overview_limit:
            return overview, directory_summary

    links: list[str] = []
    summaries: list[str] = []
    for name, entry in candidates:
        label = entry.get("title") or name
        label = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        label = " ".join(label.splitlines())
        target = markdown_safe_viking_uri(f"{dir_uri.rstrip('/')}/{name}")
        links.append(f"- [{label}]({target})")
        summaries.append(" ".join(entry["summary"].splitlines()).strip())

    if (
        links
        and len(render("\n".join(links), partial=partial, summary=directory_summary))
        > overview_limit
    ):
        partial = True
    fixed_overview = render("", partial=partial, summary="")
    budget = overview_limit - len(fixed_overview)
    if budget < 0:
        return fixed_overview[:overview_limit].rstrip(), ""
    directory_summary = directory_summary[:budget].rstrip()
    budget -= len(directory_summary)
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
    return render("\n".join(lines), partial=partial, summary=directory_summary), directory_summary


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

    def prompt(material: list[str]) -> str:
        return render_prompt(
            "semantic.abstract_generation",
            {
                "dir_name": dir_uri.rstrip("/").rsplit("/", 1)[-1],
                "material": "\n".join(material),
                "sampled_entries": len(entries),
                "total_entries": total_entries,
                "output_language": language,
            },
        )

    async def summarize(material: list[str]) -> str:
        text = prompt(material)
        if len(text) > config.max_overview_prompt_chars:
            raise ValueError("directory abstract input exceeds max_overview_prompt_chars")
        result = (await complete(text)).strip()
        if not result:
            raise ValueError("directory abstract generation returned empty content")
        return result

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

    if generate_summary:
        material = [
            json.dumps({"name": name, **entry}, ensure_ascii=False)
            for name, entry in sorted(entries.items())
        ]
        while True:
            groups = batches(material)
            if len(groups) == 1:
                summary = await summarize(groups[0])
                break
            merged = []
            for group in groups:
                partial_summary = await summarize(group)
                merged.append(partial_summary[: config.abstract_max_chars].rstrip())
            if len(merged) >= len(material) and sum(map(len, merged)) >= sum(map(len, material)):
                raise ValueError("directory abstract budget cannot merge summary batches")
            material = merged

    overview, overview_summary = _render_directory_overview(
        dir_uri,
        summary,
        entries,
        config=config,
        language=language,
        total_entries=total_entries,
        navigation_renderer=navigation_renderer,
    )
    abstract = (
        _truncate_generated_text(overview_summary, config.abstract_max_chars)
        if generate_summary
        else summary
    )
    return DirectoryGeneration(abstract=abstract, overview=overview, preset_abstract=seeded)
