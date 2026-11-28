# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Generate directory abstracts and render bounded navigation from node summaries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from openviking.concurrency import bounded_map
from openviking.observability.log_safety import safe_uri_for_log
from openviking.prompts import render_prompt
from openviking.storage.abstract_overview import AbstractOverviewDocument, markdown_safe_viking_uri
from openviking.system_assets import BUILTIN_SEMANTIC_TEMPLATES_DIR
from openviking_cli.utils.config.parser_config import SemanticConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_OVERVIEW_TEMPLATES = Environment(
    loader=FileSystemLoader(BUILTIN_SEMANTIC_TEMPLATES_DIR),
    autoescape=False,
    auto_reload=False,
    undefined=StrictUndefined,
)


@dataclass(frozen=True)
class DirectoryGeneration:
    abstract: str | None
    overview: str | None
    preset_abstract: bool = False
    sampled_entries: int = 0


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
) -> str:
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

    if (
        links
        and len(render("\n".join(links), partial=partial, summary=directory_summary))
        > overview_limit
    ):
        partial = True
    fixed_overview = render("", partial=partial, summary="")
    budget = overview_limit - len(fixed_overview)
    if budget < 0:
        return fixed_overview[:overview_limit].rstrip()
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
    return render("\n".join(lines), partial=partial, summary=directory_summary)


async def generate_directory_semantics(
    dir_uri: str,
    entries: Mapping[str, Mapping[str, str]],
    *,
    previous: AbstractOverviewDocument | None,
    total_entries: int,
    config: SemanticConfig,
    language: str,
    complete: Callable[[str], Awaitable[str]],
    max_concurrent_llm: int,
    navigation_renderer: Callable[[list[str]], str] | None = None,
    preset_abstract: str | None = None,
) -> DirectoryGeneration:
    """Build L0 and L1 from the same directory summary."""
    sampled_entries = len(entries)
    manual = previous is not None and previous.is_manual_abstract
    # A template-backed directory has a fixed purpose. Preserve any existing
    # L0, or seed the template text on the first refresh of a populated folder.
    seeded = previous is None and bool(preset_abstract) and bool(entries)
    if previous is not None and (manual or preset_abstract is not None):
        summary = previous.body.strip()
    else:
        summary = (preset_abstract or "") if seeded else ""
    generate_summary = not manual and preset_abstract is None and bool(entries)

    async def summarize(material: list[str]) -> str:
        try:
            prompt = render_prompt(
                "semantic.abstract_generation",
                {
                    "dir_name": dir_uri.rstrip("/").rsplit("/", 1)[-1],
                    "material": "\n".join(material),
                    "sampled_entries": sampled_entries,
                    "total_entries": total_entries,
                    "output_language": language,
                },
            )
            return (await complete(prompt)).strip()
        except Exception as exc:
            logger.info(
                "Directory summary generation failed target=%s error_type=%s degraded=true",
                safe_uri_for_log(dir_uri),
                type(exc).__name__,
            )
            return ""

    if generate_summary:
        items = [{"name": name, **entry} for name, entry in sorted(entries.items())]
        material = [json.dumps(item, ensure_ascii=False) for item in items]
        budget = config.max_overview_prompt_chars
        batch_size = config.overview_batch_size
        over_budget = len("\n".join(material)) > budget
        if over_budget and len(material) > batch_size:
            batches = [
                material[start : start + batch_size]
                for start in range(0, len(material), batch_size)
            ]
            results = await bounded_map(
                batches, summarize, concurrency=min(len(batches), max_concurrent_llm)
            )
            partials = [partial for partial in results if partial]
            summary = partials[0] if partials else ""
            if len(partials) > 1:
                summary = await summarize(partials) or summary
        else:
            if over_budget:
                child_material = "\n".join(
                    line
                    for item, line in zip(items, material, strict=True)
                    if item["kind"] == "directory"
                )
                file_count = sum(item["kind"] == "file" for item in items)
                per_file = max(100, (budget - len(child_material)) // max(file_count, 1))
                material = [
                    json.dumps({**item, "summary": item["summary"][:per_file]}, ensure_ascii=False)
                    if item["kind"] == "file"
                    else line
                    for item, line in zip(items, material, strict=True)
                ]
            summary = await summarize(material)
        summary = summary or "[Directory overview is not generated]"

    abstract = summary
    if generate_summary:
        abstract = _truncate_generated_text(summary, config.abstract_max_chars)
    elif preset_abstract is not None and not summary:
        abstract = None
    overview = _render_directory_overview(
        dir_uri,
        summary,
        entries,
        config=config,
        language=language,
        total_entries=total_entries,
        navigation_renderer=navigation_renderer,
    )
    return DirectoryGeneration(
        abstract=abstract,
        overview=overview,
        preset_abstract=seeded,
        sampled_entries=sampled_entries,
    )
