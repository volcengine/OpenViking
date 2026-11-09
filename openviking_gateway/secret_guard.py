# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Registered credentials as OpenViking references: sealed upstream, unsealed for client tools.

Sealing replaces every registered value with its reference. It is idempotent, so each
upstream send seals again. Unsealing puts values back only into the arguments of the
calls the client runs, in the copy the client receives. Values are read with the
caller's own OpenViking key and stay in process memory.
"""

import asyncio
import hashlib
import json
import re
import time
from collections import OrderedDict
from urllib.parse import quote, unquote

from .cache import TTLCache
from .client import VikingError
from .protocols import string_type

# As OpenViking's privacy sanitizer: shorter values are never replaced.
MIN_CHARS = 6
REFRESH_SECONDS = 30
# A failed refresh keeps using a catalog this old; without one, requests fail closed.
STALE_SECONDS = 600
REFERENCE = re.compile(
    r"\{\{[ \t]*ov_privacy:(?P<owner>[^\s:{}]+):viking://~/privacy/credential/"
    r"(?P<target>[^#\s]+)#/values/(?P<field>[^}\s]+)[ \t]*\}\}"
)
# Upstreams verify these byte for byte; binary payloads hold no text to seal.
OPAQUE = {"thinking", "redacted_thinking", "reasoning"}
SIGNED = {"signature", "encrypted_content"}
DATA_URI = re.compile(r"data:[^\s,]*;base64,")
BASE64 = re.compile(r"[A-Za-z0-9+/=_-]{256,}")
# Where a client tool call, or one of its events, holds the arguments, and whether they
# are JSON text, which takes escaped values.
ARGUMENTS = {
    "tool_use": (("input",), False),
    "input_json_delta": (("partial_json",), True),
    "function": (("function", "arguments"), True),
    "custom": (("custom", "input"), False),
    "function_call": (("arguments",), True),
    "custom_tool_call": (("input",), False),
    "response.function_call_arguments.done": (("arguments",), True),
    "response.custom_tool_call_input.done": (("input",), False),
}
DELTAS = {
    "response.function_call_arguments.delta": True,
    "response.custom_tool_call_input.delta": False,
}
# Keys that lead from a reply, or one of its events, to the calls it carries.
PATH = {
    "choices",
    "message",
    "delta",
    "tool_calls",
    "content",
    "content_block",
    "output",
    "response",
    "item",
}


def reference(owner, target, field):
    """The canonical reference to a credential field, as OpenViking renders it."""
    return (
        f"{{{{ov_privacy:{owner}:viking://~/privacy/credential/"
        f"{quote(target, safe='')}#/values/{quote(field, safe='')}}}}}"
    )


def escape(text):
    """``text`` as it is spelled inside a JSON string."""
    return json.dumps(text, ensure_ascii=False)[1:-1]


def alternation(texts):
    """A regex matching the longest of ``texts`` at each position.

    Grouped by first character, the engine tries only the texts that can start at a
    position, not all of them: several times faster on large requests.
    """
    groups = {}
    for text in sorted(texts, key=lambda text: (-len(text), text)):
        groups.setdefault(text[0], []).append(re.escape(text[1:]))
    return "|".join(
        re.escape(char) + "(?:" + "|".join(rest) + ")" for char, rest in sorted(groups.items())
    )


def walk(value, edit, exempt=True):
    """``value`` with ``edit(text) -> (text, count)`` applied to its strings, and the count.

    Only changed paths are copied, so an unchanged ``value`` is returned itself. With
    ``exempt``, what upstreams verify and binary payloads are left alone; a tool call's
    input never is.
    """
    if isinstance(value, str):
        return (value, 0) if exempt and DATA_URI.match(value) else edit(value)
    if isinstance(value, list):
        items = enumerate(value)
    elif isinstance(value, dict):
        if exempt and string_type(value) in OPAQUE:
            return value, 0
        items = value.items()
    else:
        return value, 0
    result, total = value, 0
    for key, item in items:
        if exempt and (
            key in SIGNED or key == "data" and isinstance(item, str) and BASE64.fullmatch(item)
        ):
            continue
        arguments = key == "input" and string_type(value).endswith("tool_use")
        new, count = walk(item, edit, exempt and not arguments)
        if new is not item:
            if result is value:
                result = list(value) if isinstance(value, list) else dict(value)
            result[key] = new
        total += count
    return result, total


def at(value, path, edit):
    """``edit`` applied to the item at ``path`` in ``value``, as ``walk`` applies it."""
    key, *rest = path
    if not isinstance(value, dict) or key not in value:
        return value, 0
    new, count = at(value[key], rest, edit) if rest else edit(value[key])
    return ({**value, key: new}, count) if new is not value[key] else (value, 0)


def delta_key(event):
    """The call a Responses argument delta streams, or None for any other event."""
    return (event["type"], event.get("output_index")) if string_type(event) in DELTAS else None


class Catalog:
    """One caller's credentials: the reference of each value, and the value of each field."""

    def __init__(self, owner="", targets=None):
        # target -> (version, values), as OpenViking returned them.
        self.owner, self.targets = owner, targets or {}
        self.values, smallest = {}, {}
        # Known references map to themselves, so text inside one is never sealed; a forged
        # reference around a value is not known, and the value inside it is sealed.
        self.replacements = {}
        for target, (_, values) in self.targets.items():
            for field, value in values.items():
                ref = reference(owner, target, field)
                self.replacements[ref] = ref
                if isinstance(value, str) and len(value) >= MIN_CHARS:
                    self.values[target, field] = value
                    # A value in several fields seals to one of them, the same every time.
                    smallest[value] = min(smallest.get(value, ref), ref)
        for value, ref in smallest.items():
            self.replacements.setdefault(value, ref)
        for value, ref in smallest.items():
            self.replacements.setdefault(escape(value), ref)
        self.pattern = re.compile(alternation(self.replacements))

    def __bool__(self):
        return bool(self.values)

    def seal(self, value):
        """``value`` with each registered value replaced by its reference, and how many were."""
        return walk(value, self.seal_text) if self else (value, 0)

    def seal_text(self, text):
        count = 0

        def replace(match):
            nonlocal count
            ref = self.replacements[match[0]]
            count += ref != match[0]
            return ref

        sealed = self.pattern.sub(replace, text)
        return (sealed, count) if count else (text, 0)

    def unseal(self, value):
        """A reply, or a list of its events, with its client calls' references unsealed.

        Returns how many were too. ``value`` itself never changes: what the gateway records
        and captures keeps the references, only the client's copy gets values.
        """
        if isinstance(value, list):
            return self.unseal_events(value)
        if not isinstance(value, dict):
            return value, 0
        kind = string_type(value)
        if kind in ARGUMENTS:
            path, quoted = ARGUMENTS[kind]
            return at(value, path, lambda item: walk(item, self.opener(quoted), exempt=False))
        result, total = value, 0
        for key in PATH & value.keys():
            new, count = self.unseal(value[key])
            if new is not value[key]:
                result = {**result, key: new}
            total += count
        return result, total

    def unseal_events(self, events):
        # Responses streams arguments in deltas that may split a reference: a call whose
        # arguments hold one gets them whole in its first delta, and empty ones after it.
        streamed = {}
        for event in events:
            if key := delta_key(event):
                streamed[key] = streamed.get(key, "") + event["delta"]
        whole, total = {}, 0
        for key, text in streamed.items():
            opened, count = self.opener(DELTAS[key[0]])(text)
            if count:
                whole[key] = opened
                total += count
        result = events
        for index, event in enumerate(events):
            if (key := delta_key(event)) in whole:
                new, whole[key] = {**event, "delta": whole[key]}, ""
            else:
                new, count = self.unseal(event)
                total += count
            if new is not event:
                if result is events:
                    result = list(events)
                result[index] = new
        return result, total

    def opener(self, quoted):
        """An edit for ``walk`` that unseals the caller's own references, escaped if ``quoted``."""

        def unseal_text(text):
            count = 0

            def replace(match):
                nonlocal count
                field = unquote(match["target"]), unquote(match["field"])
                value = match["owner"] == self.owner and self.values.get(field)
                if not value:
                    return match[0]
                count += 1
                return escape(value) if quoted else value

            opened = REFERENCE.sub(replace, text)
            return (opened, count) if count else (text, 0)

        return unseal_text


class Secrets:
    """Each caller's catalog, read with their own OpenViking key and kept in memory only."""

    def __init__(self, viking, capacity=4096):
        self.viking, self.capacity = viking, capacity
        self.fresh = TTLCache(ttl=REFRESH_SECONDS, capacity=capacity)
        # Per key digest, the last catalog read and when; it outlives failed refreshes.
        self.last = OrderedDict()

    async def catalog(self, key):
        """The caller's catalog; ``VikingError`` when none is recent enough to seal with."""
        name = hashlib.sha256(key.encode()).hexdigest()

        async def load():
            read_at, previous = self.last.get(name, (0, None))
            try:
                catalog = await self.read(key, previous)
            except Exception as error:
                if previous is not None and time.monotonic() - read_at <= STALE_SECONDS:
                    return previous
                raise VikingError("privacy_unavailable") from error
            self.last[name] = time.monotonic(), catalog
            self.last.move_to_end(name)
            while len(self.last) > self.capacity:
                self.last.popitem(last=False)
            return catalog

        return await self.fresh.get(name, load)

    async def read(self, key, previous):
        """Read the credential tree, then the values of targets whose version changed."""
        tree = await self.viking.request(
            "GET", "/api/v1/privacy-configs/tree?category=credential", key, timeout=5
        )
        owner = tree["owner_user_id"]
        versions = {
            target["target_key"]: target["version"]
            for category in tree["categories"]
            if category["category"] == "credential"
            for target in category["targets"]
        }
        same = previous is not None and previous.owner == owner
        kept = previous.targets if same else {}
        changed = [t for t, version in versions.items() if t not in kept or kept[t][0] != version]
        if same and not changed and len(kept) == len(versions):
            return previous
        results = await asyncio.gather(
            *(
                self.viking.request(
                    "GET", "/api/v1/privacy-configs/credential/" + quote(t, safe=""), key, timeout=5
                )
                for t in changed
            )
        )
        targets = {t: kept[t] for t in versions if t not in changed}
        for target, result in zip(changed, results, strict=True):
            targets[target] = versions[target], result["current"]["values"]
        return Catalog(owner, targets)
