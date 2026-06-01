#!/usr/bin/env python3
"""OpenClaw plugin E2E for CJK-aware token estimation.

This test drives the real OpenClaw Gateway, then verifies the OpenViking plugin's
own assemble diagnostics. It intentionally checks the plugin-side token estimate,
not only the OV REST session counters.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import time
import uuid
from typing import Any

import requests


DEFAULT_GATEWAY_URL = os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:19830")
DEFAULT_OPENVIKING_URL = os.environ.get("OPENVIKING_BASE_URL", "http://127.0.0.1:2948")
DEFAULT_CJK_REPEAT = 120


def default_gateway_log_path() -> pathlib.Path:
    state_dir = os.environ.get("OPENCLAW_STATE_DIR")
    if state_dir:
        return pathlib.Path(state_dir) / "logs" / "gateway.stdout.log"
    return pathlib.Path.cwd() / "config" / ".openclaw" / "logs" / "gateway.stdout.log"


def discover_gateway_token() -> str:
    env_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip()
    if env_token:
        return env_token

    candidates: list[pathlib.Path] = []
    config_path = os.environ.get("OPENCLAW_CONFIG_PATH")
    if config_path:
        candidates.append(pathlib.Path(config_path))

    state_dir = os.environ.get("OPENCLAW_STATE_DIR")
    if state_dir:
        candidates.append(pathlib.Path(state_dir) / "openclaw.json")

    candidates.extend(
        [
            pathlib.Path.cwd() / "config" / ".openclaw" / "openclaw.json",
            pathlib.Path.home() / ".openclaw" / "openclaw.json",
        ]
    )

    for path in candidates:
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        token = str(cfg.get("gateway", {}).get("auth", {}).get("token", "")).strip()
        if token:
            return token
    return ""


def send_gateway_message(
    gateway_url: str,
    token: str,
    user_id: str,
    message: str,
    timeout: int,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.post(
        f"{gateway_url.rstrip('/')}/v1/responses",
        headers=headers,
        json={"model": "openclaw", "input": message, "user": user_id},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def iter_new_log_lines(path: pathlib.Path, offset: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as handle:
        handle.seek(min(offset, path.stat().st_size))
        return handle.read().decode("utf-8", errors="ignore").splitlines()


def parse_diag(line: str) -> dict[str, Any] | None:
    marker = "openviking: diag "
    pos = line.find(marker)
    if pos < 0:
        return None
    try:
        return json.loads(line[pos + len(marker) :].strip())
    except json.JSONDecodeError:
        return None


def find_assemble_diag_with_marker(
    log_path: pathlib.Path,
    offset: int,
    marker: str,
    timeout: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.time() + timeout
    last_seen = ""

    while time.time() < deadline:
        for line in iter_new_log_lines(log_path, offset):
            diag = parse_diag(line)
            if not diag or diag.get("stage") != "assemble_entry":
                continue
            data = diag.get("data", {})
            for message in data.get("messages", []):
                content = str(message.get("content", ""))
                if marker in content:
                    return diag, message
            last_seen = json.dumps(data, ensure_ascii=False)[:500]
        time.sleep(0.5)

    raise AssertionError(
        f"no assemble_entry diagnostic contained marker {marker!r}; last_seen={last_seen}"
    )


def flatten_message_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(flatten_message_text(item) for item in value)
    if isinstance(value, dict):
        chunks: list[str] = []
        for key in ("text", "abstract", "content"):
            if key in value:
                chunks.append(flatten_message_text(value.get(key)))
        for key in ("parts", "messages", "pre_archive_abstracts"):
            raw = value.get(key)
            if isinstance(raw, list):
                chunks.append(flatten_message_text(raw))
        return "\n".join(chunk for chunk in chunks if chunk)
    return ""


def ov_get(base_url: str, path: str) -> Any:
    response = requests.get(f"{base_url.rstrip('/')}{path}", timeout=10)
    response.raise_for_status()
    data = response.json()
    return data.get("result", data)


def find_ov_context_with_marker(
    openviking_url: str,
    marker: str,
    timeout: float,
) -> tuple[str, dict[str, Any]]:
    deadline = time.time() + timeout
    last_session_count = 0

    while time.time() < deadline:
        sessions = ov_get(openviking_url, "/api/v1/sessions")
        if not isinstance(sessions, list):
            sessions = []
        last_session_count = len(sessions)
        ordered = sorted(
            [s for s in sessions if isinstance(s, dict)],
            key=lambda s: str(s.get("updated_at", "")),
            reverse=True,
        )[:40]

        for session in ordered:
            session_id = str(session.get("session_id", ""))
            if not session_id or session_id.startswith("memory-store-"):
                continue
            try:
                ctx = ov_get(openviking_url, f"/api/v1/sessions/{session_id}/context?token_budget=128000")
            except requests.RequestException:
                continue
            if marker in flatten_message_text(ctx):
                return session_id, ctx

        time.sleep(1)

    raise AssertionError(
        f"marker {marker!r} was not found in OV contexts; checked sessions={last_session_count}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw plugin CJK token estimation E2E")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--openviking", default=DEFAULT_OPENVIKING_URL)
    parser.add_argument("--gateway-log", type=pathlib.Path, default=default_gateway_log_path())
    parser.add_argument("--repeat", type=int, default=DEFAULT_CJK_REPEAT)
    parser.add_argument("--gateway-timeout", type=int, default=300)
    parser.add_argument("--diag-timeout", type=float, default=45)
    parser.add_argument("--ov-timeout", type=float, default=45)
    args = parser.parse_args()

    marker = f"CJK_TOKEN_E2E_{uuid.uuid4().hex[:10]}"
    user_id = f"cjk-token-e2e-{uuid.uuid4().hex[:10]}"
    cjk_text = "你好世界" * args.repeat
    expected_cjk_tokens = math.ceil(len(cjk_text) * 1.5)
    naive_chars_div_4 = math.ceil(len(cjk_text) / 4)
    message = (
        f"{marker}\n"
        "请只回复 OK。下面这段中文只用于 OpenViking token 估算端到端回归测试：\n"
        f"{cjk_text}"
    )

    log_offset = args.gateway_log.stat().st_size if args.gateway_log.exists() else 0
    token = discover_gateway_token()

    print(f"Gateway: {args.gateway}")
    print(f"OpenViking: {args.openviking}")
    print(f"Gateway log: {args.gateway_log}")
    print(f"Marker: {marker}")
    print(
        "Expected CJK token floor: "
        f"{expected_cjk_tokens} (old chars/4 would be {naive_chars_div_4})"
    )

    send_gateway_message(args.gateway, token, user_id, message, args.gateway_timeout)

    diag, marked_message = find_assemble_diag_with_marker(
        args.gateway_log,
        log_offset,
        marker,
        args.diag_timeout,
    )
    observed_tokens = int(marked_message.get("tokens", 0) or 0)
    input_tokens = int(diag.get("data", {}).get("inputTokenEstimate", 0) or 0)
    if observed_tokens < expected_cjk_tokens:
        raise AssertionError(
            "plugin assemble token estimate under-counted CJK message: "
            f"observed={observed_tokens}, expected>={expected_cjk_tokens}, "
            f"old chars/4={naive_chars_div_4}, inputTokenEstimate={input_tokens}"
        )

    session_id, ctx = find_ov_context_with_marker(args.openviking, marker, args.ov_timeout)
    estimated_tokens = int(ctx.get("estimatedTokens", 0) or 0)
    active_tokens = int(ctx.get("stats", {}).get("activeTokens", 0) or 0)
    if max(estimated_tokens, active_tokens) < expected_cjk_tokens:
        raise AssertionError(
            "OV context after plugin afterTurn under-counted CJK message: "
            f"estimatedTokens={estimated_tokens}, activeTokens={active_tokens}, "
            f"expected>={expected_cjk_tokens}, session={session_id}"
        )

    print(
        "PASS: plugin CJK estimate "
        f"messageTokens={observed_tokens}, inputTokenEstimate={input_tokens}, "
        f"ovEstimatedTokens={estimated_tokens}, ovActiveTokens={active_tokens}, "
        f"session={session_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
