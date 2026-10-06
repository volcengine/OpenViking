# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session-start profile and catalogs, matching the shared memory plugin."""

import asyncio
import html
import re
from urllib.parse import urlencode

import async_timeout

from .blocks import neutralize
from .client import VikingError

SKILL_USAGE = (
    "  OpenViking skills (stored in OpenViking, not local files). Before following one, "
    "read <dir>/<name>/SKILL.md with the openviking_read tool."
)
SKILL_MORE = "search OpenViking skills to find the rest"


def estimate_tokens(text):
    # The plugin counts CJK at 1.5 tokens per character and other text at chars/4.
    return (sum(6 if ord(c) >= 0x3000 else 1 for c in text) + 3) // 4


def truncate(text, budget, suffix="…"):
    if estimate_tokens(text) <= budget:
        return text
    remaining = (budget - estimate_tokens(suffix)) * 4
    if remaining < 0:
        return ""
    end = 0
    for c in text:
        remaining -= 6 if ord(c) >= 0x3000 else 1
        if remaining < 0:
            break
        end += 1
    return text[:end].rstrip() + suffix


def elide_profile(text, budget):
    if estimate_tokens(text) <= budget:
        return text
    lines = text.split("\n")
    if len(lines) > 12:
        head = "\n".join(lines[:8]) + "\n... [profile middle elided] ...\n"
        tail = []
        for line in reversed(lines[9:]):
            candidate = [line, *tail]
            if estimate_tokens(head + "\n".join(candidate)) > budget:
                break
            tail = candidate
        if len(tail) >= 2:
            return head + "\n".join(tail)
    return truncate(text, budget, "\n... [profile truncated]")


def format_listing(uri, entries, budget, more_hint):
    if not entries:
        return [], 0, 0
    header = f"  {uri}/"
    lines = [header]
    for entry in entries:
        abstract = re.sub(r"\s+", " ", entry["abstract"])[:200]
        line = f"    - {entry['name']}" + (f" — {abstract}" if abstract else "")
        if estimate_tokens("\n".join([*lines, line])) > budget:
            break
        lines.append(line)
    dropped = len(entries) - len(lines) + 1
    if dropped:
        while len(lines) > 1:
            tail = f"    ... +{dropped} more, {more_hint}"
            if estimate_tokens("\n".join([*lines, tail])) <= budget:
                lines.append(tail)
                break
            lines.pop()
            dropped += 1
        if len(lines) == 1:
            lines = [f"  {uri}/  ({len(entries)} entries, budget too tight; {more_hint})"]
    used = estimate_tokens("\n".join(lines))
    return (lines, used, dropped) if used <= budget else ([], 0, len(entries))


def sanitize_inline(text):
    text = neutralize(re.sub(r"\s+", " ", text).strip())
    return re.sub(
        r"</?(?:available-skills|available-memories|user-profile|memory)\b[^>]*>",
        lambda match: html.escape(match[0], quote=False),
        text,
        flags=re.I,
    )


def skill_groups(result):
    own, shared = [], []
    skills = result.get("skills", [])
    for skill in skills if isinstance(skills, list) else []:
        if not isinstance(skill, dict):
            continue
        name, uri = skill.get("name"), skill.get("uri")
        if not isinstance(name, str) or not isinstance(uri, str):
            continue
        name, uri = name.strip(), uri.rstrip("/")
        if not name or "/" not in uri:
            continue
        entry = {
            "name": sanitize_inline(name),
            "root": neutralize(uri.rsplit("/", 1)[0]),
            "abstract": truncate(
                sanitize_inline(skill["description"])
                if isinstance(skill.get("description"), str)
                else "",
                40,
            ),
        }
        (shared if uri.startswith("viking://agent/skills/") else own).append(entry)
    own_names = {entry["name"] for entry in own}
    shared = [entry for entry in shared if entry["name"] not in own_names]
    return [sorted(group, key=lambda entry: entry["name"]) for group in (own, shared) if group]


def format_skill_catalog(groups, budget):
    count = sum(map(len, groups))
    if not count:
        return ""
    frame = f"<available-skills>\n{SKILL_USAGE}\n\n</available-skills>"
    listing_budget = max(0, budget - estimate_tokens(frame))
    for descriptions in (True, False):
        lists = [
            [entry if descriptions else {**entry, "abstract": ""} for entry in group]
            for group in groups
        ]
        full_cost = [
            format_listing(group[0]["root"], group, float("inf"), SKILL_MORE)[1] for group in lists
        ]
        lines, used, dropped = [], 0, 0
        for index, group in enumerate(lists):
            remaining = listing_budget - used
            share = max(remaining // (len(lists) - index), remaining - sum(full_cost[index + 1 :]))
            part, cost, omitted = format_listing(group[0]["root"], group, share, SKILL_MORE)
            lines.extend(part)
            used += cost + 1
            dropped += omitted
        if not dropped:
            break
    text = "\n".join(["<available-skills>", SKILL_USAGE, *lines, "</available-skills>"])
    if dropped >= count or estimate_tokens(text) > budget:
        text = (
            f"<available-skills>{count} OpenViking skills; "
            "search OpenViking skills to find them.</available-skills>"
        )
    return text if estimate_tokens(text) <= budget else ""


async def resolve_user_space(fetch):
    status, entries = await asyncio.gather(
        fetch("/api/v1/system/status", {}, {}),
        fetch("/api/v1/fs/ls", {"uri": "viking://user", "output": "original"}, []),
    )
    fallback = status["user"].strip() if isinstance(status.get("user"), str) else ""
    fallback = fallback or "default"
    spaces = [
        entry["name"].strip()
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("isDir")
        and isinstance(entry.get("name"), str)
        and entry["name"].strip()
        and not entry["name"].strip().startswith(".")
        and entry["name"].strip() != "memories"
    ]
    if fallback in spaces:
        return fallback
    if "default" in spaces:
        return "default"
    return spaces[0] if len(spaces) == 1 else fallback


async def read_profile(fetch, uri):
    result = await fetch("/api/v1/content/read", {"uri": uri}, "")
    return neutralize(result.strip()) if isinstance(result, str) else ""


async def ls_dir(fetch, uri):
    result = await fetch(
        "/api/v1/fs/ls",
        {"uri": uri, "output": "agent", "recursive": "true", "abs_limit": 512, "node_limit": 512},
        [],
    )
    entries = []
    for entry in result:
        if not isinstance(entry, dict):
            continue
        name = entry.get("rel_path")
        if not isinstance(name, str) or not name:
            name = entry.get("name", "")
        if not isinstance(name, str):
            continue
        if not entry.get("isDir") and name.endswith(".md"):
            abstract = entry.get("abstract")
            entries.append(
                {
                    "name": neutralize(name),
                    "abstract": neutralize(abstract.strip()) if isinstance(abstract, str) else "",
                }
            )
    return sorted(entries, key=lambda entry: entry["name"])


async def build_profile(viking, key, policy, tools):
    names = {tool["function"]["name"] for tool in tools}
    catalog = "openviking_read" in names
    budget = policy.profile_max_tokens
    if not budget or not (policy.profile or catalog):
        return {"text": "", "reason": "disabled"}
    deadline = asyncio.get_running_loop().time() + policy.recall_timeout
    failures = []

    async def fetch(path, params, default):
        try:
            async with async_timeout.timeout_at(deadline):
                result = await viking.request(
                    "GET",
                    path + ("?" + urlencode(params) if params else ""),
                    key,
                    timeout=max(0.001, deadline - asyncio.get_running_loop().time()),
                )
                if not isinstance(result, type(default)):
                    raise ValueError("invalid profile response")
                return result
        except (VikingError, asyncio.TimeoutError, ValueError) as error:
            reason = (
                "profile_timeout"
                if isinstance(error, asyncio.TimeoutError)
                else "profile_invalid_response"
            )
            failures.append(getattr(error, "reason", reason))
            return default

    async def memories():
        space = await resolve_user_space(fetch)
        root = f"viking://user/{space}/memories"
        paths, requests = [], []
        if policy.profile:
            paths.append("profile.md")
            requests.append(read_profile(fetch, root + "/profile.md"))
        if catalog:
            for path in ("preferences", "entities"):
                paths.append(path)
                requests.append(ls_dir(fetch, root + "/" + path))
        values = await asyncio.gather(*requests)
        return root, dict(zip(paths, values, strict=True))

    async def skills():
        if not catalog or budget < 4:
            return ""
        result = await fetch("/api/v1/skills", {"node_limit": 200}, {})
        return format_skill_catalog(skill_groups(result), budget // 4)

    (root, values), skill_text = await asyncio.gather(memories(), skills())
    remaining = budget - estimate_tokens(skill_text) - bool(skill_text)
    profile = values.get("profile.md", "")
    profile_text = ""
    if profile:
        opening = f'<user-profile uri="{html.escape(root + "/profile.md", quote=True)}">'
        frame_cost = estimate_tokens(opening + "\n\n</user-profile>")
        content = elide_profile(profile, max(0, remaining // 2 - frame_cost))
        if content:
            profile_text = f"{opening}\n{content}\n</user-profile>"
    remaining -= estimate_tokens(profile_text) + bool(profile_text)
    frame_cost = estimate_tokens("<available-memories>\n\n</available-memories>")
    listing_budget = max(0, remaining - frame_cost)
    more = "use `openviking_list`" if "openviking_list" in names else "more entries omitted"
    pref, used, _ = format_listing(
        root + "/preferences", values.get("preferences", []), listing_budget // 2, more
    )
    ent, _, _ = format_listing(
        root + "/entities",
        values.get("entities", []),
        max(0, listing_budget - used - bool(pref)),
        more,
    )
    memory_text = (
        "\n".join(["<available-memories>", *pref, *ent, "</available-memories>"])
        if pref or ent
        else ""
    )
    text = "\n".join(part for part in (profile_text, memory_text, skill_text) if part)
    reason = "injected" if text else "empty"
    if failures:
        reason = "partial" if text else failures[0]
    return {"text": text, "reason": reason}
