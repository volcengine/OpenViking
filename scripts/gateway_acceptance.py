#!/usr/bin/env python3
"""Opt-in live protocol/cache acceptance using explicit gateway credentials.

Reports contain usage and protocol metadata only. Credentials come from the
OV_GW_TEST_* environment variables and are never printed or written to reports.
"""

import argparse
import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import aiohttp

from openviking_gateway.protocols import SSEDecoder
from openviking_gateway.tool_protocols import ResponseCapture


def check_cache(turns, block_tokens):
    """Allow the provider's final partial cache block; never treat zero as a hit."""
    for previous, current in zip(turns, turns[1:], strict=False):
        expected = max(1, previous["usage"]["input_tokens"] - block_tokens)
        if current["usage"]["cached_tokens"] < expected:
            raise RuntimeError(
                f"turn {current['turn']}: cached prefix is below the preceding input "
                f"({current['usage']['cached_tokens']} < {expected}, "
                f"allowance={block_tokens} tokens)"
            )


async def run(args):
    base, key, model = (
        os.environ[name] for name in ("OV_GW_TEST_BASE_URL", "OV_GW_TEST_KEY", "OV_GW_TEST_MODEL")
    )
    paths = {
        "anthropic": "/v1/messages",
        "chat": "/v1/chat/completions",
        "responses": "/v1/responses",
    }
    identifier = "acceptance-" + uuid.uuid4().hex
    headers = {
        "Authorization": "Bearer " + key,
        "X-OpenViking-Session": identifier,
    }
    if args.protocol == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if args.binding_check:
            headers["anthropic-beta"] = "thinking-binding-controls-2026-08-01"
    prefix = (
        identifier
        + "\n"
        + "\n".join(
            f"Reference row {i:04d}: stable synthetic background for cache validation. "
            "Keep this background unchanged; it contains no private information."
            for i in range(args.prefix_rows)
        )
    )
    history = []
    if args.protocol != "anthropic" and args.prefix_rows:
        history.append({"role": "system", "content": prefix})
    turns = []
    report = {"protocol": args.protocol, "model": model, "turns": turns}
    async with aiohttp.ClientSession() as client:
        for index, prompt in enumerate(
            (
                "Remember the synthetic test project uses a blue deployment cluster. Reply briefly.",
                "Which deployment cluster does the synthetic test project use? Reply briefly.",
                "Repeat the cluster name and nothing else.",
            ),
            1,
        ):
            if index == 1:
                # Recall decisions are shared by matching user prefixes. A new
                # session header alone must not reuse a previous acceptance run.
                prompt += f"\nSynthetic acceptance run: {identifier}."
            history.append({"role": "user", "content": prompt})
            streaming = args.stream_turn == index
            body = {
                "model": model,
                "stream": streaming,
                "input" if args.protocol == "responses" else "messages": history,
            }
            if args.protocol == "anthropic":
                body["max_tokens"] = 2048
                if args.prefix_rows:
                    body["system"] = prefix
                if args.thinking or args.binding_check:
                    body["thinking"] = {"type": "enabled", "budget_tokens": 1024}
                if args.binding_check:
                    body["thinking"]["block_binding"] = {"prefix_mismatch_behavior": "error"}
            else:
                if args.protocol == "responses":
                    body.update(store=False, max_output_tokens=512)
                else:
                    body.update(max_tokens=512)
                    if streaming:
                        body["stream_options"] = {"include_usage": True}
                if args.disable_thinking:
                    body["thinking"] = {"type": "disabled"}
            started = time.monotonic()
            first_event = None
            capture = ResponseCapture(args.protocol)
            native = None
            saw_transformations = False
            saw_signature = False
            async with client.post(
                base.rstrip("/") + paths[args.protocol],
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=args.timeout),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    # Provider error bodies may reflect credentials. Report status only.
                    raise RuntimeError(
                        f"turn {index}: HTTP {response.status}; inspect protected provider logs"
                    )
                if streaming:
                    decoder = SSEDecoder()
                    async for chunk in response.content.iter_any():
                        for frame in decoder.feed(chunk):
                            data = decoder.data(frame)
                            if not data:
                                continue
                            if first_event is None:
                                first_event = round(time.monotonic() - started, 3)
                            if data.get("error"):
                                raise RuntimeError(f"turn {index}: upstream emitted an error event")
                            message = data.get("message", data)
                            saw_transformations |= "input_transformations" in message
                            if message.get("input_transformations"):
                                raise RuntimeError(
                                    f"turn {index}: provider transformed signed input"
                                )
                            capture.event(data)
                    if decoder.buffer.strip():
                        raise RuntimeError(f"turn {index}: incomplete SSE event")
                else:
                    native = await response.json()
                    saw_transformations = "input_transformations" in native
                    if native.get("input_transformations"):
                        raise RuntimeError(f"turn {index}: provider transformed signed input")
                    capture.nonstream(native)
            if not capture.complete or not capture.message:
                raise RuntimeError(f"turn {index}: response did not complete")
            if args.protocol == "anthropic":
                saw_signature = any(
                    block.get("type") == "thinking" and bool(block.get("signature"))
                    for block in capture.message.get("content", [])
                )
                if args.binding_check and not (saw_transformations and saw_signature):
                    raise RuntimeError(
                        f"turn {index}: native thinking-binding evidence is unavailable; "
                        "an Anthropic-compatible response is not a binding check"
                    )
            history.extend(capture.output_items or [capture.message])
            row = {
                "turn": index,
                "status": 200,
                "stream": streaming,
                "usage": capture.usage or {},
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "first_event_seconds": first_event,
                "binding_metadata_present": saw_transformations,
                "thinking_signature_present": saw_signature,
            }
            turns.append(row)
            print(json.dumps(row), flush=True)
            if index < 3:
                await asyncio.sleep(args.pause)
    if args.require_cache:
        check_cache(turns, args.cache_block_tokens)
    report.update(
        cache_checked=args.require_cache,
        cache_block_allowance=args.cache_block_tokens if args.require_cache else None,
        binding_checked=args.binding_check,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        "Live protocol checks passed. Real-client replay and archive-boundary acceptance are separate checks."
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=["anthropic", "chat", "responses"], required=True)
    parser.add_argument(
        "--prefix-rows",
        type=int,
        default=180,
        help="Synthetic cacheable prefix; use 0 for a minimal smoke",
    )
    parser.add_argument(
        "--stream-turn", type=int, choices=[0, 1, 2, 3], default=2, help="0 disables streaming"
    )
    parser.add_argument("--require-cache", action="store_true")
    parser.add_argument(
        "--cache-block-tokens",
        type=int,
        default=128,
        help="Provider cache block allowance; set from that provider's behavior",
    )
    parser.add_argument("--thinking", action="store_true", help="Enable Anthropic thinking")
    parser.add_argument(
        "--binding-check",
        action="store_true",
        help="Require native Anthropic thinking signatures and binding-control metadata",
    )
    parser.add_argument(
        "--disable-thinking",
        action="store_true",
        help="Send thinking.type=disabled on Chat/Responses compatible providers",
    )
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--pause", type=float, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.prefix_rows < 0 or args.cache_block_tokens < 0 or args.pause < 0 or args.timeout <= 0:
        parser.error(
            "row count, cache allowance and pause must be nonnegative; timeout must be positive"
        )
    if args.binding_check and args.protocol != "anthropic":
        parser.error("--binding-check requires --protocol anthropic")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
