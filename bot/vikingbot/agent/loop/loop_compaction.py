"""Tool-result previews, media budgets, and context compaction."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from loguru import logger

from openviking.utils.rule_delivery import MAX_RULE_RESULT_CHARS, bound_rule_text
from vikingbot.agent.tools.base import TOOL_RESULT_DIRECTORY
from vikingbot.config.schema import SessionKey


def _compact_msg_chars(messages: list[dict]) -> int:
    """Count text and message structure; inline image bytes have a separate limit."""
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            budget_content = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"image_url", "input_image"}:
                    image_url = part.get("image_url")
                    url = image_url.get("url") if isinstance(image_url, dict) else image_url
                    if isinstance(url, str) and _base64_data_url_bytes(url) > 0:
                        bounded_url = url.split(",", 1)[0] + ",[inline media]"
                        part = {
                            **part,
                            "image_url": (
                                {**image_url, "url": bounded_url}
                                if isinstance(image_url, dict)
                                else bounded_url
                            ),
                        }
                budget_content.append(part)
            message = {**message, "content": budget_content}
        total += len(json.dumps(message, ensure_ascii=False, default=str))
    return total


def _is_rule_result(message: dict) -> bool:
    name = str(message.get("name", ""))
    return message.get("role") == "tool" and (
        name in {"rule_search", "openviking_rule_search", "viking_rule_search", "ov_rule_search"}
        or (name.startswith("mcp_") and name.endswith("_rule_search"))
    )


def _base64_data_url_bytes(url: str) -> int:
    if not url.startswith("data:"):
        return 0
    marker = ";base64,"
    marker_index = url.find(marker)
    if marker_index < 0:
        return 0
    encoded_length = len(url) - marker_index - len(marker)
    if encoded_length <= 0:
        return 0
    padding = 2 if url.endswith("==") else 1 if url.endswith("=") else 0
    return max(0, encoded_length * 3 // 4 - padding)


def _inline_media_bytes(messages: list[dict]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") not in {
                "image_url",
                "input_image",
            }:
                continue
            image_url = part.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else image_url
            if isinstance(url, str):
                total += _base64_data_url_bytes(url)
    return total


def _demote_historical_tool_media(
    messages: list[dict], *, history_end: int, bytes_needed: int
) -> int:
    candidates: list[tuple[dict, int]] = []
    reclaimable_bytes = 0
    for message in messages[:history_end]:
        if message.get("role") != "tool" or not isinstance(message.get("content"), list):
            continue
        media_bytes = _inline_media_bytes([message])
        if media_bytes <= 0:
            continue
        candidates.append((message, media_bytes))
        reclaimable_bytes += media_bytes
        if reclaimable_bytes >= bytes_needed:
            break

    if reclaimable_bytes < bytes_needed:
        return 0

    for message, _media_bytes in candidates:
        text_parts = [
            part["text"]
            for part in message["content"]
            if isinstance(part, dict)
            and part.get("type") in {"text", "input_text"}
            and isinstance(part.get("text"), str)
        ]
        text = "\n".join(text_parts)
        omission_note = (
            "Media content from this earlier tool result was omitted from subsequent "
            "requests to keep the inline media budget."
        )
        message["content"] = f"{text}\n\n{omission_note}" if text else omission_note
    return reclaimable_bytes


def _compact_render_message(message: dict) -> str:
    role = message.get("role")
    content = message.get("content")
    if isinstance(content, (list, dict)):
        text_parts: list[str] = []

        def _collect(value: Any) -> None:
            if isinstance(value, str):
                if value.strip():
                    text_parts.append(value)
            elif isinstance(value, list):
                for item in value:
                    _collect(item)
            elif isinstance(value, dict):
                for key in ("text", "content"):
                    if key in value:
                        _collect(value[key])

        _collect(content)
        content = "\n".join(text_parts)

    body = str(content or "").strip()
    if role == "assistant":
        lines: list[str] = []
        if body:
            lines.append(body)
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            name = function.get("name") or "?"
            args = str(function.get("arguments") or "")
            lines.append(f"[tool_call] {name}({args})")
        if message.get("reasoning_content"):
            lines.append(f"[reasoning] {str(message.get('reasoning_content'))[:2_000]}")
        body = "\n".join(lines) or "(assistant)"
        label = "assistant"
    elif role == "tool":
        label = f"tool result: {message.get('name') or message.get('tool_name') or '?'}"
    else:
        label = str(role or "message")
    if len(body) > 6_000:
        body = body[:6_000] + "\n...<truncated>"
    return f"[{label}]\n{body}"


def _compact_split_into_blocks(messages: list[dict]) -> list[list[dict]]:
    blocks: list[list[dict]] = []
    i, n = 0, len(messages)
    while i < n:
        message = messages[i]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            j = i + 1
            while j < n and messages[j].get("role") == "tool":
                j += 1
            blocks.append(messages[i:j])
            i = j
        else:
            blocks.append([messages[i]])
            i += 1
    return blocks


def _compact_render_transcript(messages: list[dict]) -> str:
    text = "\n\n".join(_compact_render_message(m) for m in messages)
    if len(text) > 256_000:
        text = "[... older transcript omitted ...]\n\n" + text[-256_000:]
    return text


def _compact_split_chunks(text: str) -> list[str]:
    if len(text) <= 32_000:
        return [text]
    return [text[i : i + 32_000] for i in range(0, len(text), 32_000)]


def _compact_strip_note_header(content: str) -> str:
    text = str(content or "").strip()
    if text.startswith("[Context compaction]"):
        text = text[len("[Context compaction]") :].strip()
    if text.startswith("Findings so far:"):
        text = text[len("Findings so far:") :].strip()
    return text.strip()


def _compact_build_note(previous_summary: str, new_summary: str) -> str:
    """Join complete compaction summaries without dropping saved facts or unfinished work."""
    previous = (previous_summary or "").strip()
    new = (new_summary or "").strip()
    parts = ["[Context compaction]"]
    if previous:
        parts.append(previous)
    if new:
        parts.append(f"## Progress since last compaction\n{new}" if previous else new)
    if not previous and not new:
        parts.append(
            "Earlier turns were compacted to fit the context window; earlier tool "
            "results are no longer quoted verbatim."
        )
    return "\n\n".join(parts)


class LoopCompactionMixin:
    """Tool-result previews, media budgets, and context compaction. State is owned by AgentLoop."""

    async def _compact_tool_loop(
        self,
        messages: list[dict],
        session_key: SessionKey,
        *,
        budget_chars: int | None = None,
    ) -> list[dict]:
        """Compress older tool turns to fit the context window.

        Keeps system messages and the original task, folds older turns into a
        structured summary note (the previous note is reused verbatim, only the
        region since it is summarized), and retains the most recent complete turns.
        Retain the latest Rule lookup with complete blocks within its inline budget.
        Expired lookups are explicitly marked missing rather than summarized as
        constraints. Keep call/result pairing and the latest user correction.
        Refuse an oversized context if fixed instructions alone prevent fitting.
        """
        budget = budget_chars or 240_000

        i = 0
        while i < len(messages) and messages[i].get("role") == "system":
            i += 1
        system_msgs = messages[:i]
        task_msg = messages[i] if i < len(messages) and messages[i].get("role") == "user" else None
        rest = messages[i + 1 :] if task_msg is not None else messages[i:]

        blocks = _compact_split_into_blocks(rest)
        # Never summarize Rule bodies into replacement constraints. The latest
        # bounded lookup stays in its original call/result block.
        mandatory_indices: set[int] = set()
        note_indices: set[int] = set()
        previous_summary = ""
        latest_user_idx: int | None = None
        rule_indices: list[int] = []
        for idx, block in enumerate(blocks):
            message = block[0]
            content = str(message.get("content") or "")
            if message.get("role") == "user":
                if content.strip().startswith("[Context compaction]"):
                    note_indices.add(idx)
                    previous_summary = _compact_strip_note_header(content)
                    continue
                latest_user_idx = idx
            if any(_is_rule_result(message) for message in block):
                rule_indices.append(idx)
        expired_rules = set(rule_indices[:-1])
        for idx in expired_rules:
            # A Rule lookup may share its turn with writes or other useful work.
            # Expire only its own call/result pair, leaving that progress available
            # for retention or summarization in the original order.
            block = blocks[idx]
            expired_ids = {
                message.get("tool_call_id") for message in block if _is_rule_result(message)
            }
            progress = []
            for message in block:
                if _is_rule_result(message):
                    continue
                if message.get("tool_calls"):
                    calls = [
                        call for call in message["tool_calls"] if call.get("id") not in expired_ids
                    ]
                    message = {**message}
                    if calls:
                        message["tool_calls"] = calls
                    else:
                        message.pop("tool_calls")
                        if not message.get("content"):
                            continue
                progress.append(message)
            blocks[idx] = progress
        if rule_indices:
            latest_rules = rule_indices[-1]
            mandatory_indices.add(latest_rules)
            # Account for JSON escaping (notably newline-heavy results) and for
            # parallel tool results sharing a single lookup block.
            rule_count = sum(_is_rule_result(message) for message in blocks[latest_rules])
            rule_budget = min(MAX_RULE_RESULT_CHARS, max(80, budget // (4 * rule_count)))
            blocks[latest_rules] = [
                {
                    **message,
                    "content": await self._preview_tool_result(
                        message["content"],
                        session_key,
                        preserve_complete=True,
                        rule_budget=rule_budget,
                    ),
                }
                if _is_rule_result(message) and isinstance(message.get("content"), str)
                else message
                for message in blocks[latest_rules]
            ]
        if len(blocks) <= 3 and not expired_rules:
            bounded = [
                *system_msgs,
                *([task_msg] if task_msg is not None else []),
                *(message for block in blocks for message in block),
            ]
            if _compact_msg_chars(bounded) <= budget:
                return bounded
        # Choose the tail before filtering Rules, so pinning an old Rule cannot
        # reorder recent turns or silently push the latest user out of context.
        turn_indices = [
            idx
            for idx, block in enumerate(blocks)
            if block and idx not in note_indices and block[0].get("role") != "tool"
        ]
        if not turn_indices:
            if _compact_msg_chars(messages) > budget:
                raise ValueError("Context exceeds output budget: fixed instructions cannot fit.")
            return messages
        mandatory_indices.add(turn_indices[-1])
        if latest_user_idx is not None:
            mandatory_indices.add(latest_user_idx)
        window_indices = turn_indices[-3:]
        older_indices = set(turn_indices[:-3])
        summary_indices = [
            idx
            for idx in turn_indices
            if idx not in mandatory_indices and (idx in older_indices or idx in expired_rules)
        ]
        to_summarize = [message for idx in summary_indices for message in blocks[idx]]

        new_summary = ""
        if to_summarize:
            system_text = str(system_msgs[0].get("content") or "") if system_msgs else ""
            task_text = str(task_msg.get("content") or "") if task_msg else ""
            chunks = _compact_split_chunks(_compact_render_transcript(to_summarize))
            try:
                if len(chunks) == 1:
                    new_summary = await self._summarize_compact_chunk(
                        session_key, chunks[0], system_text=system_text, task_text=task_text
                    )
                else:
                    summaries = await asyncio.gather(
                        *(
                            self._summarize_compact_chunk(
                                session_key,
                                chunk,
                                system_text=system_text if idx == 0 else "",
                                task_text=task_text,
                            )
                            for idx, chunk in enumerate(chunks)
                        )
                    )
                    new_summary = await self._merge_compact_summaries(
                        session_key,
                        [s for s in summaries if s],
                        system_text=system_text,
                        task_text=task_text,
                    )
            except Exception as exc:
                logger.warning("Tool-loop compaction summarization failed: {}", exc)
                new_summary = ""
            if not new_summary:
                # Keep progress accompanying expired lookups if the summary cannot
                # preserve it. The final budget check still prevents an oversized request.
                mandatory_indices.update(idx for idx in summary_indices if idx in expired_rules)

        note_msg = {"role": "user", "content": _compact_build_note(previous_summary, new_summary)}
        if expired_rules:
            note_msg["content"] += (
                "\n\n[Rule history] delivery.complete=false. Older Rule lookups expired; "
                "refresh applicable Rules before acting on constraints absent from the latest lookup."
            )

        fixed_chars = _compact_msg_chars(system_msgs) + _compact_msg_chars([note_msg])
        fixed_chars += sum(_compact_msg_chars(blocks[idx]) for idx in mandatory_indices)
        if task_msg is not None:
            fixed_chars += _compact_msg_chars([task_msg])
        remaining = max(0, budget - fixed_chars)
        keep_indices = set(mandatory_indices)
        for idx in reversed(window_indices):
            if idx in mandatory_indices:
                continue
            size = _compact_msg_chars(blocks[idx])
            if size > remaining:
                break
            keep_indices.add(idx)
            remaining -= size

        out: list[dict] = list(system_msgs)
        if task_msg is not None:
            out.append(task_msg)
        out.append(note_msg)
        for idx in sorted(keep_indices):
            out.extend(blocks[idx])
        if _compact_msg_chars(out) > budget:
            raise ValueError(
                "Context exceeds output budget after compaction; fixed instructions, "
                "latest user input or newest tool turn must be reduced before requesting the model."
            )
        return out

    async def _summarize_compact_chunk(
        self,
        session_key: SessionKey,
        chunk_text: str,
        *,
        system_text: str = "",
        task_text: str = "",
    ) -> str:
        user_content = f"Original task:\n{task_text[:20_000]}\n\nTranscript segment:\n{chunk_text}"
        if system_text:
            user_content = (
                "Original system instructions (preserve any hard constraints verbatim):\n"
                f"{system_text[:40_000]}\n\n{user_content}"
            )
        return await self._chat_compact_summary(
            session_key,
            [
                {
                    "role": "system",
                    "content": (
                        "You are a context-compaction summarizer for a long-running tool-use "
                        "agent. Produce a dense, factual summary that preserves everything "
                        "needed to continue the task without re-reading the transcript, using "
                        "exactly these headings:\n## Current goal\n## Key facts & progress\n"
                        "## Saved files & coverage\n## Decisions\n## Constraints\n## Next steps / open questions\n"
                        "Record saved draft paths, covered sources/ranges and unwritten gaps; "
                        "distinguish successful writes from plans or failed writes. Continue from saved files. "
                        "Be precise about names, paths, tools, and numbers. Do not invent anything."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
        )

    async def _merge_compact_summaries(
        self,
        session_key: SessionKey,
        segment_summaries: list[str],
        *,
        system_text: str = "",
        task_text: str = "",
    ) -> str:
        if not segment_summaries:
            return ""
        joined = "\n\n".join(
            f"## Segment {idx}\n{text}" for idx, text in enumerate(segment_summaries, start=1)
        )
        user_content = f"Original task:\n{task_text[:20_000]}\n\nSegment summaries:\n{joined}"
        if system_text:
            user_content = (
                "Original system instructions (preserve any hard constraints verbatim):\n"
                f"{system_text[:40_000]}\n\n{user_content}"
            )
        return await self._chat_compact_summary(
            session_key,
            [
                {
                    "role": "system",
                    "content": (
                        "You are the final assembly step of a context-compaction pipeline. "
                        "Merge the segment summaries below into ONE note with exactly these "
                        "headings:\n## Current goal\n## Key facts & progress\n## Saved files & coverage\n## Decisions\n"
                        "## Constraints\n## Next steps / open questions\n"
                        "Keep saved draft paths, coverage and unwritten gaps, including failed writes. "
                        "Preserve ALL distinct facts across segments; drop only duplicates. "
                        "Keep names, paths, tools, numbers. Carry over hard constraints from "
                        "the system instructions verbatim."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
        )

    async def _chat_compact_summary(self, session_key: SessionKey, messages: list[dict]) -> str:
        for attempt in range(1, 4):
            try:
                response = await self.provider.chat(
                    messages=messages,
                    tools=[],
                    model=self.model,
                    temperature=0.0,
                    session_id=session_key.safe_name(),
                )
                summary = (response.content or "").strip()
                if summary:
                    return summary
                logger.warning("Tool-loop compaction summary attempt {}/3 was empty", attempt)
            except Exception as exc:
                logger.warning("Tool-loop compaction summary attempt {}/3 failed: {}", attempt, exc)
        return ""

    async def _preview_tool_result(
        self,
        result: Any,
        session_key: SessionKey,
        *,
        preserve_complete: bool = False,
        rule_budget: int = MAX_RULE_RESULT_CHARS,
    ) -> Any:
        """Save large text and return a bounded preview or complete Rule blocks.

        Non-text results pass through. Saving failures are explicit in the preview;
        original tool outcomes still determine success and stop conditions.
        """
        limit = rule_budget if preserve_complete else 8_000
        if not isinstance(result, str) or len(result) <= limit:
            return result
        path = f"{TOOL_RESULT_DIRECTORY}/{uuid.uuid4().hex}.txt"
        try:
            sandbox = await self.sandbox_manager.get_sandbox(session_key)
            await sandbox.write_file_bytes(path, result.encode("utf-8"))
            notice = (
                f"Full output ({len(result)} characters) saved at task-relative path {path}. "
                "Use read_file with offset/limit or a script to inspect selected sections; "
                "do not print the complete file. This file is not a deliverable."
            )
        except Exception as exc:
            notice = (
                f"Full output ({len(result)} characters) could NOT be saved: "
                f"{type(exc).__name__}. Only this preview is available; "
                "retrieve smaller sections from the original source if needed."
            )
        if preserve_complete:
            return bound_rule_text(result, rule_budget, notice)
        return f"{result[:4_000]}\n\n[Output truncated. {notice}]\n\n{result[-2_000:]}"
