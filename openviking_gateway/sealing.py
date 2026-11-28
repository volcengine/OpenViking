# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""``seal_secrets``: registered secrets leave for the model only as references.

OpenViking's seal endpoint does the matching; the gateway decides which strings of a
request are content, which user text may enroll new tokens, and keeps the results in
memory. It never holds plain values beyond one reply's tool calls.

When sealing cannot be done (an OpenViking without the seal endpoint or of unknown
version, a call that fails, a request or reply it cannot handle), the request or reply
fails: nothing goes on unsealed.
"""

import asyncio
import copy
import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass

import orjson

from . import blocks, protocols
from .client import FEATURES, VikingError, supports
from .observe import GatewayError
from .protocols import classify, string_type
from .tool_protocols import tool_protocol
from .tool_protocols.common import ToolLoopError

# CONTRACT §1: per call.
MAX_TEXTS = 1024
MAX_BYTES = 16 * 1024 * 1024
# Registered values are at least this long, so shorter strings cannot hold one.
MIN_LENGTH = 6
# Revision changes seen within one pass before it gives up.
MAX_ROUNDS = 4
# One seal or resolve call's limit, and the sealed results one process keeps, by size.
TIMEOUT_SECONDS = 10
CACHE_BYTES = 64 * 1024 * 1024
REFERENCE = re.compile(
    r"\{\{ov_privacy:([^\s:{}]+):viking://~/privacy/(?:credential|auto)/([^#\s/{}]+)"
    r"#/values/([^{}\s]+)\}\}"
)
# A tool argument holding this command hands references to the OpenViking CLI as they are.
OV_PRIVACY = re.compile(r"(?<![\w-])ov\s+privacy\b")
NOTE = (
    "OpenViking stored a secret from the user's message and replaced it with a reference: "
    "{references}. You will not see the value. Unless it is clearly for one-off use, save it "
    "under a proper name now with privacy_upsert (or `ov privacy upsert`), passing the "
    "reference as the value and a short description of what it is for, for example "
    'category="credential", target_key="github", values={{"token": "{first}"}}, '
    'description="GitHub PAT for the acme org". The temporary entry is '
    "removed once it is saved, so use the reference that privacy_upsert returns from then on. "
    "Write the reference as is wherever the secret is needed, for example `ov privacy run "
    "--env NAME='<reference>' -- sh -c '<command using \"$NAME\">'`; do not resolve it to "
    "plain text or print any part of it. Tell the user what was stored and under which name."
)
# No reference-shaped example: a model copies one as if it were real.
SESSION_NOTE = (
    "- Secrets are sealed: OpenViking replaces the secret values it knows with references, "
    "which start with `{{ov_privacy:` and end with `}}`, in what you read. The replacement "
    "exists only in your view. A file on disk and the real output of a command still contain "
    "the secret itself, and a tool you call receives the real value in place of a reference "
    "you write. So a reference seen in a file does not mean the file is free of secrets or "
    "safe to share, and a file you write through a tool holds the real value. Write a "
    "reference exactly as it is wherever the secret is needed, such as in a tool call's "
    "arguments, and never ask for the value in plain text. Read files and run commands "
    "normally: do not mask, cut or re-encode secrets yourself, because a partial or altered "
    "value is not recognised and is sent as it is. Never print a secret or any part of it. "
    "When the `ov` CLI is installed, `ov privacy run` runs a command with the references it "
    "is given; with `--env NAME=…`, use the variable inside `sh -c '…'`, because `$NAME` "
    "written directly on the command line is expanded by your own shell before `ov` runs. A "
    "reference under `…/privacy/auto/…` was created automatically for a secret OpenViking "
    "found and works like any other reference. Save one the user gave you and will use again "
    "under a proper name with a description, as the note that comes with it says; ones that "
    "only appeared in tool output or files need no action."
)
# For a session that gets no session-start text, on requests where sealing replaced a value.
REPLACED_NOTE = (
    "OpenViking replaced secret values in this conversation with references "
    "(`{{ov_privacy:…}}`). The replacement exists only in your view: files on disk and real "
    "command output still contain the secret, and a tool receives the real value in place of "
    "a reference you write. Write references exactly as they are; do not mask, cut or print "
    "secrets or parts of them yourself."
)

# Structure: never content, whatever the protocol.
SKIP_KEYS = frozenset(
    {
        "id",
        "type",
        "role",
        "status",
        "name",
        "model",
        "signature",
        "encrypted_content",
        "tool_use_id",
        "call_id",
        "tool_call_id",
        "previous_response_id",
        "item_id",
        "response_id",
        "cache_control",
        "media_type",
        "detail",
        "namespace",
        "server_label",
        "server_name",
        "stop_reason",
        "finish_reason",
        "object",
    }
)
# A call's or result's own identity; everything else in it is content.
TOOL_KEYS = frozenset(
    {
        "type",
        "role",
        "id",
        "call_id",
        "tool_use_id",
        "tool_call_id",
        "name",
        "namespace",
        "status",
        "server_label",
        "server_name",
        "cache_control",
    }
)
TOOL_NODE = re.compile(r"(?:tool_use|tool_result|_call|_call_output)$")
BASE64 = re.compile(r"[A-Za-z0-9+/=_-]{256,}")
KEY_FOLLOWS = re.compile(r"\s*:")
SUBAGENT_LINE = re.compile(r"^\s*\[Subagent Context\][^\n]*", re.M)
STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.S)
ESCAPE = re.compile(r"\\(?:u([0-9a-fA-F]{4})|(.))", re.S)
SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r"}
SIMPLE_ESCAPES["t"] = "\t"
# What a client can do about a failure that is not its request's fault.
RETRY = ". Retry once OpenViking is reachable, or turn off seal_secrets in the profile"
# The OpenViking capability sealing needs.
FEATURE = "privacy_seal"


class SealError(Exception):
    """Sealing could not finish. A message, when there is one, says why the request itself
    cannot be sealed: a retry cannot help."""


def seal_failure(error):
    """400 when ``error`` says why the request cannot be sealed, else 503."""
    if why := str(error):
        return GatewayError(
            "secret_seal_invalid_request",
            f"{why}; it was not sent to the model. Turn off seal_secrets in the profile to "
            "send such requests",
        )
    return GatewayError(
        "secret_seal_failed",
        "OpenViking could not seal secrets for this request; it was not sent to the model" + RETRY,
    )


# JSON inside a string: literals are rewritten in place, never re-encoded whole.


@dataclass
class Literal:
    start: int
    end: int
    text: str
    # Source offset of each decoded code point, and of the closing quote; None maps 1:1.
    offsets: list[int] | None

    def source(self, index):
        return self.start + 1 + index if self.offsets is None else self.offsets[index]


def decode_literal(text, start, end):
    inner = text[start + 1 : end - 1]
    if "\\" not in inner:
        return Literal(start, end, inner, None)
    chars, offsets, position, base = [], [], 0, start + 1
    matches = list(ESCAPE.finditer(inner))
    index = 0
    while index < len(matches):
        match = matches[index]
        run = inner[position : match.start()]
        chars.append(run)
        offsets.extend(range(base + position, base + match.start()))
        width = match.end() - match.start()
        if match[1] is None:
            char = SIMPLE_ESCAPES.get(match[2], match[2])
        else:
            code = int(match[1], 16)
            following = matches[index + 1] if index + 1 < len(matches) else None
            if (
                0xD800 <= code < 0xDC00
                and following is not None
                and following.start() == match.end()
                and following[1] is not None
                and 0xDC00 <= int(following[1], 16) < 0xE000
            ):
                code = 0x10000 + ((code - 0xD800) << 10) + (int(following[1], 16) - 0xDC00)
                width += following.end() - following.start()
                index += 1
            char = chr(code)
        chars.append(char)
        offsets.append(base + match.start())
        position = match.start() + width
        index += 1
    chars.append(inner[position:])
    offsets.extend(range(base + position, base + len(inner)))
    offsets.append(end - 1)
    return Literal(start, end, "".join(chars), offsets)


def json_literals(text):
    """The value string literals of a JSON document, or None when ``text`` is not JSON."""
    if '"' not in text:
        return None
    try:
        json.loads(text)
    except (ValueError, RecursionError):
        return None
    return [
        decode_literal(text, m.start(), m.end())
        for m in STRING.finditer(text)
        if not KEY_FOLLOWS.match(text, m.end())
    ]


def encode_like(value, literal_source):
    """``value`` escaped for a JSON literal the way ``literal_source`` was written."""
    ascii_only = literal_source.isascii() and "\\u" in literal_source
    encoded = json.dumps(value, ensure_ascii=ascii_only)[1:-1]
    return encoded.replace("/", "\\/") if "\\/" in literal_source else encoded


def splice(text, edits):
    """``text`` with ``edits``, ``(literal, [(start, end, replacement)])``, applied in place."""
    parts, position = [], 0
    for literal, spans in edits:
        source = text[literal.start : literal.end]
        for start, end, replacement in spans:
            begin, finish = literal.source(start), literal.source(end)
            parts.append(text[position:begin])
            parts.append(encode_like(replacement, source))
            position = finish
    parts.append(text[position:])
    return "".join(parts)


def rewrite_json(text, change):
    """Apply ``change(decoded)`` -> spans to each value literal; ``None`` if not JSON."""
    literals = json_literals(text)
    if literals is None:
        return None
    edits = [(literal, spans) for literal in literals if (spans := change(literal.text))]
    return splice(text, edits) if edits else text


# What a body holds: plain strings, and JSON strings whose literals are sealed apart.


@dataclass
class Slot:
    path: tuple
    value: str
    # The user typed it in the latest turn: it enrolls, and its temporary references are
    # owed the note.
    typed: bool
    # It may enroll new tokens whole, client-written blocks included (enroll_tool_secrets).
    enroll: bool
    is_json: bool


def is_data(value):
    return value.startswith("data:") and "," in value[:256]


class Walker:
    def __init__(self, typed, sources=frozenset()):
        # (id(container), key) of the user's latest typed strings.
        self.typed, self.slots = typed, []
        # id() of history entries, or (id(container), key) of strings, that enroll whole.
        self.sources, self.inside = sources, False

    def text(self, value, path, parent, key):
        if len(value) >= MIN_LENGTH:
            enroll = self.inside or (id(parent), key) in self.sources
            self.slots.append(Slot(path, value, (id(parent), key) in self.typed, enroll, False))

    def arguments(self, value, path):
        if len(value) >= MIN_LENGTH:
            self.slots.append(Slot(path, value, False, False, True))

    def body(self, body):
        for key, value in body.items():
            if key == "model":
                continue
            if isinstance(value, str):
                if not is_data(value):
                    self.text(value, (key,), body, key)
            else:
                self.structure(value, (key,))

    def structure(self, node, path, parent=None, key=None):
        if isinstance(node, str):
            if key in SKIP_KEYS or is_data(node):
                return
            if key in ("data", "file_data") and BASE64.fullmatch(node):
                return
            self.text(node, path, parent, key)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                self.structure(item, (*path, index), node, index)
        elif isinstance(node, dict):
            if id(node) in self.sources and not self.inside:
                self.inside = True
                try:
                    self.structure(node, path, parent, key)
                finally:
                    self.inside = False
                return
            kind = string_type(node)
            if kind in ("redacted_thinking", "reasoning") or (
                kind == "thinking" and node.get("signature")
            ):
                return
            if TOOL_NODE.search(kind) or node.get("role") in ("tool", "function"):
                self.tool(node, path)
                return
            for name, value in node.items():
                if name in SKIP_KEYS and not isinstance(value, (dict, list)):
                    continue
                if name == "tool_calls" and isinstance(value, list):
                    for index, item in enumerate(value):
                        self.tool(item, (*path, name, index))
                elif name == "function_call" and isinstance(value, dict):
                    self.tool(value, (*path, name))
                elif name == "arguments" and isinstance(value, str):
                    self.arguments(value, (*path, name))
                else:
                    self.structure(value, (*path, name), node, name)

    def tool(self, node, path):
        """A call or a result: no field but its own identity is exempt."""
        if not isinstance(node, dict):
            self.content(node, path)
            return
        for name, value in node.items():
            if name in TOOL_KEYS and isinstance(value, str):
                continue
            if name in ("function", "custom") and isinstance(value, dict):
                self.tool(value, (*path, name))
            elif name == "arguments" and isinstance(value, str):
                self.arguments(value, (*path, name))
            else:
                self.content(value, (*path, name), name)

    def content(self, node, path, key=None):
        if isinstance(node, str):
            # Encoded files, as in structure: no text to seal, and too large to send.
            if is_data(node) or (key in ("data", "file_data") and BASE64.fullmatch(node)):
                return
            self.text(node, path, None, None)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                self.content(item, (*path, index))
        elif isinstance(node, dict):
            for name, value in node.items():
                if name == "arguments" and isinstance(value, str):
                    self.arguments(value, (*path, name))
                else:
                    self.content(value, (*path, name), name)


def enroll_marks(body, protocol, headers):
    """Where the user's latest typed text is: it enrolls new tokens, and only it is owed
    the note."""
    if protocol is None:
        return set()
    adapter = tool_protocol(protocol)
    messages = adapter.messages(body)
    if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
        return set()
    try:
        kind, anchor = classify(body, headers, messages)
    except (TypeError, AttributeError, KeyError):
        # A malformed history enrolls nothing; its strings are still sealed.
        return set()
    if kind == "subagent" or anchor < 0:
        return set()
    if isinstance(body.get(adapter.field), str):
        return {(id(body), adapter.field)}
    message = messages[anchor]
    content = message.get("content")
    if isinstance(content, str):
        return {(id(message), "content")}
    if not isinstance(content, list):
        return set()
    return {
        (id(part), "text")
        for part in content
        if string_type(part) in ("text", "input_text") and isinstance(part.get("text"), str)
    }


def enroll_sources(body, protocol):
    """With ``enroll_tool_secrets``: the user-role and tool-role entries of the history,
    every turn, whose strings all enroll. Assistant entries, the system prompt or
    instructions and tool definitions are never sources."""
    if protocol is None:
        return set()
    field = tool_protocol(protocol).field
    history = body.get(field)
    if isinstance(history, str):
        return {(id(body), field)}
    if not isinstance(history, list):
        return set()
    return {
        id(entry)
        for entry in history
        if isinstance(entry, dict)
        and (
            entry.get("role") in ("user", "tool", "function")
            or string_type(entry).endswith(("_output", "tool_result"))
        )
    }


def noise_spans(text):
    """Spans of ``text`` a client wrote, not the user: harness blocks such as reminders."""
    spans = [
        m.span()
        for pattern in (*protocols.NOISE_BLOCKS, SUBAGENT_LINE, *protocols.NOISE_PATTERNS)
        for m in pattern.finditer(text)
        if m.end() > m.start()
    ]
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def segments(text, typed, enroll=False):
    """``text`` split into ``(part, enroll)``: typed parts enroll; client-written parts of
    typed text enroll only when ``enroll`` says all of the text may."""
    if not typed:
        return [(text, enroll)]
    parts, position = [], 0
    for start, end in noise_spans(text):
        if start > position:
            parts.append((text[position:start], True))
        parts.append((text[start:end], enroll))
        position = end
    if position < len(text):
        parts.append((text[position:], True))
    return parts or [(text, True)]


def rebuild(node, changes):
    """``node`` with ``changes`` (path -> value) applied, copying only what they touch."""
    tree = {}
    for path, value in changes.items():
        branch = tree
        for key in path[:-1]:
            branch = branch.setdefault(key, {})
        branch[path[-1]] = Leaf(value)

    def apply(target, branch):
        if isinstance(branch, Leaf):
            return branch.value
        copied = list(target) if isinstance(target, list) else dict(target)
        for key, sub in branch.items():
            copied[key] = apply(target[key], sub)
        return copied

    return apply(node, tree)


@dataclass(frozen=True)
class Leaf:
    value: str


# Results: None leaves a text as it is; otherwise its sealed form and replacements.


@dataclass(frozen=True)
class Sealed:
    text: str
    # (start, end, reference, temporary), in code points of the original text. Both are
    # stable for a revision; ``created`` is not, so it is never part of a result.
    spans: tuple

    @property
    def temporary(self):
        return [span[2] for span in self.spans if span[3]]

    def size(self):
        return len(self.text) + 96 * len(self.spans)


def parse_item(original, item, created):
    """One ``result.texts`` entry; its sealed text is built from its spans. Raises on a
    span that does not fit ``original``; no spans is no change. ``temporary`` and
    ``created`` count only when true. References this call enrolled go to ``created``."""
    if item is None or not item["replaced"]:
        return None
    spans, position, parts = [], 0, []
    for span in item["replaced"]:
        start, end, reference = span["start"], span["end"], span["reference"]
        if not (position <= start < end <= len(original) and REFERENCE.fullmatch(reference)):
            raise ValueError("seal span")
        parts += [original[position:start], reference]
        position = end
        spans.append((start, end, reference, span.get("temporary") is True))
        if span.get("created") is True:
            created.add(reference)
    return Sealed("".join([*parts, original[position:]]), tuple(spans))


class SealCache:
    """Per-instance results by (user, revision, text digest, enroll), bounded by size."""

    def __init__(self, max_bytes, max_users=65536):
        self.max_bytes, self.max_users = max_bytes, max_users
        self.entries, self.size = OrderedDict(), 0
        # The revision this instance last saw for each user.
        self.revisions = OrderedDict()

    def get(self, key):
        if key not in self.entries:
            return MISSING
        self.entries.move_to_end(key)
        return self.entries[key][0]

    def put(self, key, value):
        if not self.max_bytes:
            return
        size = 160 + (value.size() if value else 0)
        if key in self.entries:
            self.size -= self.entries.pop(key)[1]
        self.entries[key] = (value, size)
        self.size += size
        while self.size > self.max_bytes and self.entries:
            self.size -= self.entries.popitem(last=False)[1][1]

    def revision(self, user):
        return self.revisions.get(user)

    def saw(self, user, revision):
        self.revisions[user] = revision
        self.revisions.move_to_end(user)
        if len(self.revisions) > self.max_users:
            self.revisions.popitem(last=False)


MISSING = object()


def digest(text):
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest()


def batches(units):
    batch, size = [], 0
    for unit in units:
        length = len(unit[0].encode("utf-8", "surrogatepass"))
        if length > MAX_BYTES:
            raise SealError("A request text exceeds the seal endpoint's size limit")
        if batch and (len(batch) == MAX_TEXTS or size + length > MAX_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(unit)
        size += length
    if batch:
        yield batch


class Sealer:
    """The process-wide part: the OpenViking client, the cache and the call timeout."""

    def __init__(self, viking):
        self.viking, self.timeout = viking, TIMEOUT_SECONDS
        self.cache = SealCache(CACHE_BYTES)

    def session(self, credential, metrics):
        return SealSession(self, credential, metrics)


class SealSession:
    """One gateway request's sealing: every pass shares its confirmed revision."""

    def __init__(self, sealer, credential, metrics):
        self.sealer, self.cache, self.metrics = sealer, sealer.cache, metrics
        self.key = credential["openviking_key"]
        self.user_id = credential["user_id"]
        self.user = (
            credential.get("openviking_account", credential["account"]) + "\0" + self.user_id
        )
        # The revision a call made for this request reported, so later passes need none.
        self.checked = None
        # References live calls of this request enrolled; only a statistic.
        self.created = set()
        for name in ("secrets_sealed", "seal_calls", "seal_cache_hits"):
            metrics.setdefault(name, 0)

    async def require(self):
        """Raise unless this caller's OpenViking can seal, by its version; the seal endpoint
        is never called when it cannot, or when its version is unknown."""
        version = await self.sealer.viking.server_version(self.key)
        if supports(FEATURE, version):
            return
        if version:
            found = (
                f"OpenViking {version} cannot seal secrets; sealing needs "
                f"{FEATURES[FEATURE]} or later. Upgrade OpenViking"
            )
        else:
            found = (
                "OpenViking's version could not be read, so secrets cannot be sealed. Check "
                "that OpenViking is reachable"
            )
        raise GatewayError(
            "secret_seal_failed",
            f"{found}, or turn off seal_secrets in the profile. The request was not sent to "
            "the model",
        )

    def lookup(self, revision, unit):
        if revision is None:
            return MISSING
        return self.cache.get((self.user, revision, digest(unit[0]), unit[1]))

    def store(self, revision, unit, value):
        text, enroll = unit
        flags = (False, True) if enroll else (False,)
        for flag in flags:
            self.cache.put((self.user, revision, digest(text), flag), value)
        if value is not None:
            # Sealed output seals to itself: the next pass and the next turn skip it.
            for flag in flags:
                self.cache.put((self.user, revision, digest(value.text), flag), None)

    async def call(self, batch):
        self.metrics["seal_calls"] += 1
        try:
            result = await self.sealer.viking.seal(
                self.key, [{"text": t, "enroll": e} for t, e in batch], self.sealer.timeout
            )
            revision, texts = result["revision"], result["texts"]
            if not (revision and isinstance(revision, str)):
                raise ValueError("seal response")
            created = set()
            items = [
                parse_item(t, item, created) for (t, _), item in zip(batch, texts, strict=True)
            ]
        except Exception as error:
            # Any outcome but a well-formed answer, an older server's 404 included.
            raise SealError() from error
        self.created |= created
        if self.created:
            self.metrics["secrets_enrolled"] = len(self.created)
        return revision, items

    async def seal_units(self, units):
        """Results for ``units``, ``(text, enroll)`` pairs, all at one current revision."""
        units = list(dict.fromkeys(units))
        results, revision, checked = {}, self.cache.revision(self.user), self.checked
        # Kept only once the pass succeeds: nothing is cached from a failed one.
        answered = []
        for _ in range(MAX_ROUNDS):
            pending = []
            for unit in units:
                if unit in results and results[unit][0] == revision:
                    continue
                hit = self.lookup(revision, unit)
                if hit is MISSING:
                    pending.append(unit)
                else:
                    self.metrics["seal_cache_hits"] += 1
                    results[unit] = (revision, hit)
            if not pending and revision is not None and checked == revision:
                for answer, unit, item in answered:
                    self.store(answer, unit, item)
                self.checked = checked
                self.cache.saw(self.user, revision)
                return {unit: value for unit, (_, value) in results.items()}
            # Nothing new still asks once per request: the revision may have moved.
            sent = list(batches(pending or [("", False)]))
            answers = await asyncio.gather(*(self.call(batch) for batch in sent))
            if pending:
                for batch, (answer, items) in zip(sent, answers, strict=True):
                    for unit, item in zip(batch, items, strict=True):
                        results[unit] = (answer, item)
                        answered.append((answer, unit, item))
            revision = answers[-1][0]
            checked = revision if len({answer for answer, _ in answers}) == 1 else None
        raise SealError()

    async def seal(self, body, protocol=None, headers=None, egress=False, tool_output=False):
        """``body`` with its content sealed, and the temporary references in its latest
        user text: auto-enrolled ones not yet saved under a name. ``tool_output`` also
        enrolls from every user-role and tool-role text, which is never owed the note."""
        started = time.monotonic()
        try:
            marks = set() if egress else enroll_marks(body, protocol, headers or {})
            sources = enroll_sources(body, protocol) if tool_output and not egress else set()
            walker = Walker(marks, sources)
            walker.body(body)
            units, literals = [], {}
            for slot in walker.slots:
                if slot.is_json:
                    found = json_literals(slot.value)
                    if found is None:
                        units.append((slot.value, False))
                    else:
                        literals[slot.path] = found
                        units += [(x.text, False) for x in found if len(x.text) >= MIN_LENGTH]
                else:
                    units += segments(slot.value, slot.typed, slot.enroll)
            results = await self.seal_units(units) if units else {}
            changes, temporary = {}, []
            for slot in walker.slots:
                if slot.path in literals:
                    edits = [
                        (lit, [(s, e, ref) for s, e, ref, _ in value.spans])
                        for lit in literals[slot.path]
                        if (value := results.get((lit.text, False)))
                    ]
                    if edits:
                        changes[slot.path] = splice(slot.value, edits)
                    continue
                parts = segments(slot.value, slot.typed, slot.enroll)
                sealed = [results.get(part) for part in parts]
                if any(sealed):
                    changes[slot.path] = "".join(
                        value.text if value else part
                        for (part, _), value in zip(parts, sealed, strict=True)
                    )
                if slot.typed:
                    # Only the user's typed parts are owed the note, not attached blocks.
                    temporary += [
                        ref
                        for (_, typed), value in zip(
                            segments(slot.value, True), sealed, strict=True
                        )
                        if typed and value
                        for ref in value.temporary
                    ]
            self.metrics["secrets_sealed"] += len(changes)
            return (rebuild(body, changes) if changes else body), list(dict.fromkeys(temporary))
        finally:
            self.metrics["seal_ms"] = round(
                self.metrics.get("seal_ms", 0) + (time.monotonic() - started) * 1000, 2
            )

    async def egress(self, body):
        """The final sweep before a body goes to a model upstream."""
        sealed, _ = await self.seal(body, egress=True)
        return sealed


def secret_note(references):
    if not references:
        return ""
    return NOTE.format(references=", ".join(references), first=references[0])


def defines_tools(body):
    return isinstance(body, dict) and bool(body.get("tools") or body.get("functions"))


def unseal_block_reason(protocol, path, body):
    """Why a request that defines tools cannot have its tool calls unsealed, or ``""``."""
    if not defines_tools(body) or path.endswith("count_tokens"):
        return ""
    if protocol is None:
        return "unsupported_endpoint"
    if body.get("functions"):
        return "legacy_functions"
    if protocol == "responses" and body.get("background"):
        # A reply fetched later bypasses the unsealing of this request.
        return "background"
    return ""


def unparseable(raw, headers):
    """Whether a body that did not parse safely is JSON the gateway would have to seal."""
    if headers.get("content-encoding", "identity").lower() != "identity":
        return True
    if "json" in headers.get("content-type", "").lower():
        return True
    head = raw.lstrip()[:1]
    return head in (b"{", b"[", b'"')


async def inbound(session, body, raw, headers, protocol, path, tool_output=False):
    """Seal a client request before the kernel sees it: ``(body, temporary)``. Raises
    what the client gets when it cannot be sealed."""
    if raw and body is None:
        if not unparseable(raw, headers):
            return body, []
        raise seal_failure("The request body is compressed or cannot be parsed safely")
    if not isinstance(body, dict):
        return body, []
    if reason := unseal_block_reason(protocol, path, body):
        raise seal_failure(f"Tool calls of this request cannot be unsealed ({reason})")
    await session.require()
    try:
        return await session.seal(body, protocol, headers, tool_output=tool_output)
    except SealError as error:
        raise seal_failure(error) from None


# Unsealing: references in the client's copy of its own tool calls become values.


def exempt(name):
    """OpenViking's own tools understand references, so they get them as they are."""
    return isinstance(name, str) and "openviking" in name.lower()


class Unsealer:
    """One reply's unsealing; values live only here, for this reply."""

    def __init__(self, session):
        self.session = session
        self.values = {}
        # References put in place of a value at least once.
        self.replaced = set()

    def own(self, text):
        return [m[0] for m in REFERENCE.finditer(text) if m[1] == self.session.user_id]

    # Each transform takes ``edit(value, is_json)``; ``collect`` and ``replace`` are the two.

    def collect(self, found):
        def edit(value, is_json):
            if is_json:
                literals = json_literals(value)
                if literals is not None:
                    for lit in literals:
                        if not OV_PRIVACY.search(lit.text):
                            found.update(self.own(lit.text))
                    return value
            if not OV_PRIVACY.search(value):
                found.update(self.own(value))
            return value

        return edit

    @property
    def unsealed(self):
        return len(self.replaced)

    def replace(self):
        def spans(text):
            if OV_PRIVACY.search(text):
                return []
            found = [m for m in REFERENCE.finditer(text) if self.values.get(m[0]) is not None]
            self.replaced.update(m[0] for m in found)
            return [(m.start(), m.end(), self.values[m[0]]) for m in found]

        def edit(value, is_json):
            if is_json:
                rewritten = rewrite_json(value, spans)
                if rewritten is not None:
                    return rewritten
            found = spans(value)
            if not found:
                return value
            parts, position = [], 0
            for start, end, replacement in found:
                parts += [value[position:start], replacement]
                position = end
            return "".join([*parts, value[position:]])

        return edit

    async def resolve(self, references):
        async def one(reference):
            try:
                result = await self.session.sealer.viking.resolve(
                    self.session.key, reference, self.session.sealer.timeout
                )
            except VikingError as error:
                if error.status == 404:
                    return reference, None
                raise ToolLoopError(
                    f"OpenViking could not resolve a secret reference in a tool call{RETRY}",
                    502,
                    code="secret_unseal_failed",
                ) from None
            value = result.get("value") if isinstance(result, dict) else None
            return reference, value if isinstance(value, str) and value else None

        resolved = dict(await asyncio.gather(*(one(r) for r in references)))
        candidates = sorted({v for v in resolved.values() if v})
        try:
            # Only values that seal back to their own reference: the next turn must match.
            checked = (
                await self.session.seal_units([(v, False) for v in candidates])
                if candidates
                else {}
            )
        except SealError:
            raise ToolLoopError(
                f"OpenViking could not confirm a secret reference in a tool call{RETRY}",
                502,
                code="secret_unseal_failed",
            ) from None
        for reference, value in resolved.items():
            sealed = checked.get((value, False)) if value else None
            exact = sealed is not None and [s[:3] for s in sealed.spans] == [
                (0, len(value), reference)
            ]
            self.values[reference] = value if exact else None

    async def transform(self, apply, value):
        found = set()
        apply(value, self.collect(found))
        missing = sorted(found - self.values.keys())
        if missing:
            await self.resolve(missing)
        if not any(self.values.get(r) for r in found):
            return value
        value = copy.deepcopy(value)
        return apply(value, self.replace())

    async def events(self, protocol, events):
        """Streamed client events with values in place of references."""
        return await self.transform(EVENTS[protocol], events)

    async def reply(self, protocol, final):
        """The whole reply the client gets."""
        return await self.transform(REPLIES[protocol], final)


def raw_strings(node, edit):
    """``node`` with ``edit`` applied to every string in it, as raw values; same if unchanged."""
    if isinstance(node, str):
        return edit(node, False)
    if isinstance(node, list):
        items = [raw_strings(item, edit) for item in node]
        return node if all(a is b for a, b in zip(items, node, strict=True)) else items
    if isinstance(node, dict):
        items = {key: raw_strings(item, edit) for key, item in node.items()}
        return node if all(items[k] is node[k] for k in node) else items
    return node


def anthropic_reply(final, edit):
    for block in final.get("content") or []:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and not exempt(block.get("name"))
        ):
            block["input"] = raw_strings(block.get("input"), edit)
    return final


def edit_whole(pieces, field, edit, is_json):
    """Edit the text ``pieces`` hold together under ``field``; a reference may be split
    between them, so a changed text goes whole into the first piece."""
    pieces = [piece for piece in pieces if isinstance(piece.get(field), str)]
    whole = "".join(piece[field] for piece in pieces)
    changed = edit(whole, is_json) if whole else whole
    if changed != whole:
        for position, piece in enumerate(pieces):
            piece[field] = "" if position else changed


def anthropic_events(events, edit):
    calls = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use" and not exempt(block.get("name")):
                calls[event.get("index")] = []
                if block.get("input"):
                    block["input"] = raw_strings(block["input"], edit)
        elif event.get("type") == "content_block_delta" and event.get("index") in calls:
            calls[event.get("index")].append(event.get("delta") or {})
    for deltas in calls.values():
        edit_whole(deltas, "partial_json", edit, True)
    return events


def chat_calls(calls, edit):
    for call in calls or []:
        if not isinstance(call, dict):
            continue
        function, custom = call.get("function"), call.get("custom")
        if isinstance(function, dict) and isinstance(function.get("arguments"), str):
            if not exempt(function.get("name")):
                function["arguments"] = edit(function["arguments"], True)
        if isinstance(custom, dict) and isinstance(custom.get("input"), str):
            if not exempt(custom.get("name")):
                custom["input"] = edit(custom["input"], False)


def chat_reply(final, edit):
    for choice in final.get("choices") or []:
        chat_calls((choice.get("message") or {}).get("tool_calls"), edit)
    return final


def chat_events(events, edit):
    calls = {}
    for event in events:
        for choice in (event.get("choices") if isinstance(event, dict) else None) or []:
            for call in (choice.get("delta") or {}).get("tool_calls") or []:
                if isinstance(call, dict):
                    key = (choice.get("index", 0), call.get("index", 0))
                    calls.setdefault(key, []).append(call)
    for fragments in calls.values():
        for part, field, is_json in (("function", "arguments", True), ("custom", "input", False)):
            pieces = [f[part] for f in fragments if isinstance(f.get(part), dict)]
            name = "".join(p["name"] for p in pieces if isinstance(p.get("name"), str))
            if pieces and not exempt(name):
                edit_whole(pieces, field, edit, is_json)
    return events


def responses_name(item):
    return (item.get("namespace", "") + "." if item.get("namespace") else "") + item.get("name", "")


def responses_item(item, edit):
    if not isinstance(item, dict) or exempt(responses_name(item)):
        return item
    if item.get("type") == "function_call" and isinstance(item.get("arguments"), str):
        item["arguments"] = edit(item["arguments"], True)
    elif item.get("type") == "custom_tool_call" and isinstance(item.get("input"), str):
        item["input"] = edit(item["input"], False)
    return item


def responses_reply(final, edit):
    for item in final.get("output") or []:
        responses_item(item, edit)
    return final


def responses_events(events, edit):
    # A reference may be split between deltas: a changed call's first delta carries all of it.
    full = {}
    for event in events:
        if event.get("type") == "response.output_item.done":
            item = event.get("item") or {}
            if not exempt(responses_name(item)):
                full[event.get("output_index")] = item
        elif event.get("type") in ("response.completed", "response.incomplete"):
            # A stream cut short may end before an item's own done event.
            for index, item in enumerate((event.get("response") or {}).get("output") or []):
                if isinstance(item, dict) and not exempt(responses_name(item)):
                    full.setdefault(index, item)
    first = set()
    for event in events:
        kind, index = event.get("type"), event.get("output_index")
        if kind in ("response.output_item.added", "response.output_item.done"):
            responses_item(event.get("item"), edit)
        elif kind == "response.completed" or kind == "response.incomplete":
            responses_reply(event.get("response") or {}, edit)
        elif index in full and kind in (
            "response.function_call_arguments.delta",
            "response.custom_tool_call_input.delta",
            "response.function_call_arguments.done",
            "response.custom_tool_call_input.done",
        ):
            item = full[index]
            is_json = item.get("type") == "function_call"
            whole = item.get("arguments" if is_json else "input")
            if not isinstance(whole, str):
                continue
            changed = edit(whole, is_json)
            if changed == whole:
                continue
            if kind.endswith(".done"):
                event["arguments" if is_json else "input"] = changed
            else:
                event["delta"] = "" if index in first else changed
                first.add(index)
    return events


EVENTS = {"anthropic": anthropic_events, "chat": chat_events, "responses": responses_events}
REPLIES = {"anthropic": anthropic_reply, "chat": chat_reply, "responses": responses_reply}


def opens_call(protocol, event):
    """Whether a stream event starts, or carries, a client tool call that may be unsealed."""
    kind = event.get("type")
    if protocol == "anthropic":
        block = event.get("content_block") or {}
        return kind == "content_block_start" and block.get("type") == "tool_use"
    if protocol == "chat":
        return any(
            isinstance(choice, dict) and (choice.get("delta") or {}).get("tool_calls")
            for choice in event.get("choices") or []
        )
    if kind == "response.output_item.added":
        return string_type(event.get("item")) in ("function_call", "custom_tool_call")
    if kind in ("response.completed", "response.incomplete"):
        output = (event.get("response") or {}).get("output") or []
        return any(string_type(i) in ("function_call", "custom_tool_call") for i in output)
    return False


def reframe(frame, value):
    """``frame`` with its data replaced by ``value``; its other lines stay."""
    lines, data = [], b"data: " + orjson.dumps(value)
    for line in frame.rstrip(b"\r\n").split(b"\n"):
        if not line.startswith(b"data:"):
            lines.append(line)
        elif data:
            lines.append(data)
            data = b""
    return b"\n".join(lines) + b"\n\n"


class UnsealRelay:
    """Unsealing for a reply the tool loop does not handle, as the upstream sent it.

    Frames pass as they are until a client tool call starts; from then on they are held
    until the reply ends, and only the frames whose calls change are rewritten.
    """

    def __init__(self, unsealer, protocol):
        self.unsealer, self.protocol = unsealer, protocol
        self.held = []

    def feed(self, frames):
        """The bytes the client gets now for ``frames``, complete SSE frames in order."""
        passed = []
        for frame in frames:
            if not self.held:
                event = protocols.SSEDecoder.data(frame)
                if not (event and opens_call(self.protocol, event)):
                    passed.append(frame)
                    continue
            self.held.append(frame)
        return b"".join(passed)

    async def flush(self, rest=b""):
        """The held frames, unsealed, and ``rest``, an unterminated last frame, as it is."""
        frames, self.held = self.held, []
        events = [protocols.SSEDecoder.data(frame) for frame in frames]
        present = [event for event in events if event is not None]
        original = copy.deepcopy(present)
        changed = iter(await self.unsealer.events(self.protocol, present))
        before = iter(original)
        out = []
        for frame, event in zip(frames, events, strict=True):
            if event is None:
                out.append(frame)
                continue
            value, old = next(changed), next(before)
            out.append(frame if value == old else reframe(frame, value))
        return b"".join(out) + rest

    async def reply(self, content):
        """A whole non-streamed reply's bytes, unsealed; the same bytes when nothing changed."""
        try:
            final = orjson.loads(content)
        except ValueError:
            return content
        if not isinstance(final, dict):
            return content
        sent = await self.unsealer.reply(self.protocol, final)
        return content if sent is final else orjson.dumps(sent)


def unseal_failure(protocol, body, response_id, error):
    """The event that ends a relayed stream whose tool calls could not be unsealed."""
    adapter = tool_protocol(protocol)(body or {})
    adapter.begin()
    if response_id:
        adapter.identifier = response_id
    events = adapter.error(str(error), kind="secret_unseal_failed")
    return b"".join(adapter.encode(event) for event in events)


def with_note(body, protocol, headers, references, kind=None, replaced=False):
    """``body`` with the note after its latest user text, for a session the kernel injects
    nothing into. Only a main conversation request gets it; nothing records it, and it
    goes away once the latest user text holds no temporary reference. ``replaced`` (sealing
    changed something in this request) puts ``REPLACED_NOTE`` before it."""
    text = "\n\n".join(
        part for part in (REPLACED_NOTE if replaced else "", secret_note(references)) if part
    )
    if not text or protocol is None or not isinstance(body, dict):
        return body
    adapter = tool_protocol(protocol)
    messages = adapter.messages(body)
    if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
        return body
    classified, anchor = classify(body, headers, messages)
    # Every request of the turn carries it alike, a tool round's continuation included.
    if (kind or classified) not in ("user", "continuation") or anchor < 0:
        return body
    messages = [dict(m) for m in messages]
    adapter.append_context(messages[anchor], blocks.block("gateway-secrets", text))
    return {**body, adapter.field: messages}
