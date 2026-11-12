# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Cuts: replacing the history up to an anchor with text, and the summaries that make them.

A REPLACEMENT record keyed by a cut anchor holds the text that stands in for
the history up to and including that anchor. Compaction writes model-written
summaries this way; every later request applies the latest cut in its history.
"""

import re

import orjson

from .blocks import block, history_hint, token_estimate
from .protocols import clean_text, is_user, text_content, unwrap_client
from .records import RecordKind as K
from .tool_protocols import tool_protocol

# Used when neither the upstream nor the policy gives the model's window.
DEFAULT_WINDOW = 1_000_000
BACKOFF_SECONDS = 60
# Inline base64 media costs the model far fewer tokens than its length suggests.
MEDIA = re.compile(r"[A-Za-z0-9+/=]{1024,}")
MEDIA_TOKENS = 1600
OPENING = re.compile(
    r'<openviking-context source="gateway-session-start">.*?</openviking-context>', re.S
)
HEADER = (
    "The OpenViking Gateway replaced the earlier part of this conversation with the "
    "summary below. The user did not write it, and the client does not show it."
)
INSTRUCTION = (
    "Summarize the conversation above. Your summary will replace it, and you will continue the "
    "work from the summary alone, so keep everything needed to carry on:\n"
    "- the user's goals, quoting the user's most recent request above verbatim;\n"
    "- key decisions and the current state of the work;\n"
    "- files, paths, URLs, identifiers and commands that matter;\n"
    "- errors met and how they were corrected;\n"
    "- open items and next steps;\n"
    "- key facts from the latest tool results.\n"
    "If an earlier summary from the OpenViking Gateway appears above, fold its content "
    "into this one. Leave out the gateway's opening notes; the gateway adds them again. Write "
    "plain text, do not call tools, and stay under {tokens} tokens."
)
# A continuation is cut while the model is still working on the latest request.
# Models summarizing their own tool loop tend to report that work as delivered.
UNFINISHED = (
    "The conversation above stops while you are still working on the user's most recent "
    "request: you have not answered it yet. Say so, and say what remains to be done."
)
IN_PROGRESS = (
    "When this summary was written, you had not yet answered the user's most recent request "
    "that it quotes; you were in the middle of working on it."
)
USER_QUOTE_MAX_TOKENS = 6000
USER_QUOTE_MESSAGE_MAX_CHARS = 4000
USER_QUOTE_OMISSION = "\n...[middle omitted by OpenViking Gateway]...\n"
USER_QUOTE_HEADER = "User messages from the replaced history, newest first:"
USER_QUOTE_FOOTER = (
    "These are the user's words from earlier in the conversation, not a new request."
)


def estimate(value):
    """Tokens of a JSON value, counting each inline media blob like one image."""
    text, blobs = MEDIA.subn("", orjson.dumps(value).decode())
    return token_estimate(text) + blobs * MEDIA_TOKENS


def window_size(request, policy):
    model = request.original.get("model", "")
    model = request.upstream.get("aliases", {}).get(model, model)
    return (
        request.upstream.get("context_windows", {}).get(model)
        or policy.context_window
        or DEFAULT_WINDOW
    )


def active_cut(request):
    """Index of the latest cut in the request's history, or -1."""
    for index in range(len(request.capture_chain) - 1, -1, -1):
        if (K.REPLACEMENT, request.capture_chain[index]) in request.records:
            return index
    return -1


def cut_point(request):
    """A new cut goes before the latest user message, or after a continuation's last message.

    Nothing generated before the cut stays, so thinking signatures and paired
    reasoning items after it always belong to the context they were made in.
    """
    end = request.anchor if request.kind == "user" else len(request.capture_chain)
    return next((i for i in range(end - 1, -1, -1) if request.capture_chain[i]), -1)


def cut_messages(messages, index, text):
    """Replace messages up to ``index`` with one user message; system messages stay first."""
    prefix = [m for m in messages[: index + 1] if m.get("role") in {"system", "developer"}]
    return [*prefix, {"role": "user", "content": text}, *messages[index + 1 :]]


def apply_cut(request):
    """Replace the history up to the latest cut with that cut's text."""
    index = active_cut(request)
    if index < 0:
        return
    anchor = request.capture_chain[index]
    field = tool_protocol(request.protocol).field
    rest = request.body_chain[index + 1 :]
    request.body[field] = cut_messages(
        request.body[field], index, request.records[K.REPLACEMENT, anchor]["text"]
    )
    request.body_chain = [""] * (len(request.body[field]) - len(rest)) + rest
    request.metrics["compaction_applied"] = anchor


def opening_block(records, chain):
    """The history's gateway-session-start block, verbatim, or ""."""
    for anchor in chain:
        decision = records.get((K.INJECTION, anchor))
        if decision is not None:
            match = OPENING.search(decision["text"])
            return match[0] if match else ""
    return ""


def cut_hint(request, user_id, policy):
    """The history hint at a cut; the current session comes first, as it gets the cut part."""
    capture = request.capture.value
    sessions = [capture["ov_session"], *capture.get("previous", [])] if capture else []
    return history_hint(user_id, sessions, request.root["tools"], policy.capture)


def summary_instruction(tokens, in_progress):
    instruction = INSTRUCTION.format(tokens=tokens)
    return instruction + "\n" + UNFINISHED if in_progress else instruction


def _bounded_user_text(text, cap):
    if len(text) <= cap:
        return text
    if cap <= len(USER_QUOTE_OMISSION):
        return text[:cap]
    available = cap - len(USER_QUOTE_OMISSION)
    head = available * 5 // 8
    return text[:head] + USER_QUOTE_OMISSION + text[-(available - head) :]


def user_quote_section(messages, context_window):
    """Bounded, deduplicated user text from the part a compaction replaces."""
    limit = min(USER_QUOTE_MAX_TOKENS, int(context_window * 0.02))
    used = token_estimate(USER_QUOTE_HEADER + "\n") + token_estimate("\n" + USER_QUOTE_FOOTER)
    if used >= limit:
        return ""
    quotes: list[str] = []
    seen = set()
    for message in reversed(messages):
        if not is_user(message):
            continue
        text = clean_text(unwrap_client(text_content(message)))
        if not text or text in seen:
            continue
        seen.add(text)
        separator = "" if not quotes else "\n\n"
        low, high, best, best_tokens = 1, min(len(text), USER_QUOTE_MESSAGE_MAX_CHARS), "", 0
        while low <= high:
            cap = (low + high) // 2
            quote = _bounded_user_text(text, cap)
            rendered = separator + "> " + quote.replace("\n", "\n> ")
            tokens = token_estimate(rendered)
            if used + tokens <= limit:
                best, best_tokens, low = quote, tokens, cap + 1
            else:
                high = cap - 1
        if not best:
            break
        quotes.append(best)
        used += best_tokens
    body = "\n\n".join("> " + quote.replace("\n", "\n> ") for quote in quotes)
    return USER_QUOTE_HEADER + "\n" + body + "\n" + USER_QUOTE_FOOTER if body else ""


def replacement_text(summary, hint, opening, in_progress=False, user_quotes=""):
    header = HEADER + " " + IN_PROGRESS if in_progress else HEADER
    text = block(
        "gateway-compaction",
        "\n\n".join(part for part in (header, summary, user_quotes, hint) if part),
    )
    return text + "\n\n" + opening if opening else text
