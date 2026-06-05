#!/usr/bin/env python3
"""
HTTP demo for shared-session + peer_id semantics.

This script creates one account, creates one regular USER, then runs two scenarios:

1. `multi-user`
   Uses an ADMIN key to switch effective user context within one account, and
   demonstrates that ADMIN may explicitly pass peer_id.

2. `normal-user`
   Uses a USER key to show that explicit peer_id is accepted and missing
   peer_id is not autofilled from request context.

Create account:
    export URL=http://127.0.0.1:1933
    export ROOT_KEY=<root-key>

    curl -sS -X POST "$URL/api/v1/admin/accounts" \
      -H "X-API-Key: $ROOT_KEY" \
      -H "Content-Type: application/json" \
      -d '{
        "account_id": "demo-peer",
        "admin_user_id": "alice"
      }'

Create a regular USER (the returned `result.user_key` is the key to use with
the `normal-user` scenario):
    curl -sS -X POST "$URL/api/v1/admin/accounts/demo-peer/users" \
      -H "X-API-Key: $ROOT_KEY" \
      -H "Content-Type: application/json" \
      -d '{"user_id": "bob", "role": "user"}'

Examples:
    python examples/multi_tenant/shared_session_peer_id_http.py \
      --url http://127.0.0.1:1933 \
      --root-key <root-key>
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any, Dict, Optional

import httpx

OK = "[OK]"
FAIL = "[FAIL]"
UNSET = object()


def build_headers(
    *,
    api_key: str,
    account: Optional[str] = None,
    user: Optional[str] = None,
) -> Dict[str, str]:
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }
    if account:
        headers["X-OpenViking-Account"] = account
    if user:
        headers["X-OpenViking-User"] = user
    return headers


def decode_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def print_response(label: str, response: httpx.Response) -> None:
    payload = decode_json(response)
    print(f"{label}: HTTP {response.status_code}")
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(payload)
    print()


def expect_status(response: httpx.Response, expected: int, label: str) -> None:
    if response.status_code != expected:
        print_response(f"{FAIL} {label}", response)
        raise SystemExit(1)
    print(f"{OK} {label}: HTTP {response.status_code}")


def create_session(
    client: httpx.Client,
    base_url: str,
    headers: Dict[str, str],
    session_id: Optional[str] = None,
) -> str:
    payload: Dict[str, Any] = {}
    if session_id:
        payload["session_id"] = session_id
    response = client.post(f"{base_url}/api/v1/sessions", headers=headers, json=payload)
    expect_status(response, 200, "create session")
    return response.json()["result"]["session_id"]


def create_account(
    client: httpx.Client,
    base_url: str,
    *,
    root_key: str,
    account_id: str,
    admin_user_id: str,
) -> Dict[str, Any]:
    response = client.post(
        f"{base_url}/api/v1/admin/accounts",
        headers=build_headers(api_key=root_key),
        json={
            "account_id": account_id,
            "admin_user_id": admin_user_id,
        },
    )
    expect_status(response, 200, f"create account {account_id}")
    return response.json()["result"]


def register_user(
    client: httpx.Client,
    base_url: str,
    *,
    api_key: str,
    account_id: str,
    user_id: str,
    role: str = "user",
) -> Dict[str, Any]:
    response = client.post(
        f"{base_url}/api/v1/admin/accounts/{account_id}/users",
        headers=build_headers(api_key=api_key),
        json={"user_id": user_id, "role": role},
    )
    expect_status(response, 200, f"register user {user_id} in {account_id}")
    return response.json()["result"]


def add_message(
    client: httpx.Client,
    base_url: str,
    session_id: str,
    headers: Dict[str, str],
    *,
    role: str,
    content: str,
    peer_id: object = UNSET,
) -> httpx.Response:
    payload: Dict[str, Any] = {
        "role": role,
        "content": content,
    }
    if peer_id is not UNSET:
        payload["peer_id"] = peer_id
    return client.post(
        f"{base_url}/api/v1/sessions/{session_id}/messages",
        headers=headers,
        json=payload,
    )


def get_context(
    client: httpx.Client,
    base_url: str,
    session_id: str,
    headers: Dict[str, str],
) -> httpx.Response:
    return client.get(
        f"{base_url}/api/v1/sessions/{session_id}/context",
        headers=headers,
    )


def run_multi_user_flow(
    client: httpx.Client,
    *,
    base_url: str,
    account: str,
    admin_key: str,
    effective_user_a: str,
    effective_user_b: str,
    assistant_peer_id: Optional[str],
    session_id: Optional[str],
) -> Dict[str, Any]:
    admin_headers_a = build_headers(
        api_key=admin_key,
        account=account,
        user=effective_user_a,
    )
    admin_headers_b = build_headers(
        api_key=admin_key,
        account=account,
        user=effective_user_b,
    )

    session_id = create_session(
        client,
        base_url,
        admin_headers_a,
        session_id=session_id,
    )
    print(f"{OK} session_id = {session_id}")
    print()

    response = add_message(
        client,
        base_url,
        session_id,
        admin_headers_a,
        role="user",
        content=f"implicit actor from effective context: {effective_user_a}",
    )
    expect_status(response, 200, "admin user message without peer_id")

    response = add_message(
        client,
        base_url,
        session_id,
        admin_headers_a,
        role="user",
        content=f"explicit peer set by admin: {effective_user_b}",
        peer_id=effective_user_b,
    )
    expect_status(response, 200, "admin explicit user peer_id")

    resolved_assistant_peer_id = assistant_peer_id or "assistant"
    response = add_message(
        client,
        base_url,
        session_id,
        admin_headers_a,
        role="assistant",
        content=f"explicit assistant peer: {resolved_assistant_peer_id}",
        peer_id=resolved_assistant_peer_id,
    )
    expect_status(response, 200, "admin explicit assistant peer_id")

    context_from_other_user = get_context(
        client,
        base_url,
        session_id,
        admin_headers_b,
    )
    expect_status(context_from_other_user, 200, "shared session visible from other user view")
    print_response("shared session context", context_from_other_user)
    return {
        "session_id": session_id,
        "context": decode_json(context_from_other_user),
    }


def run_normal_user_flow(
    client: httpx.Client,
    *,
    base_url: str,
    user_key: str,
    account: Optional[str],
    user: Optional[str],
    explicit_peer_id: Optional[str],
    session_id: Optional[str],
) -> Dict[str, Any]:
    user_headers = build_headers(
        api_key=user_key,
        account=account,
        user=user,
    )

    session_id = create_session(
        client,
        base_url,
        user_headers,
        session_id=session_id,
    )
    print(f"{OK} session_id = {session_id}")
    print()

    response = add_message(
        client,
        base_url,
        session_id,
        user_headers,
        role="user",
        content="implicit actor from user key context",
    )
    expect_status(response, 200, "user message without peer_id")

    response = add_message(
        client,
        base_url,
        session_id,
        user_headers,
        role="assistant",
        content="assistant message without peer_id",
    )
    expect_status(response, 200, "user assistant message without peer_id")

    resolved_explicit_peer_id = explicit_peer_id or "explicit-user"
    response = add_message(
        client,
        base_url,
        session_id,
        user_headers,
        role="user",
        content=f"explicit peer_id from user client: {resolved_explicit_peer_id}",
        peer_id=resolved_explicit_peer_id,
    )
    expect_status(response, 200, "user explicit peer_id")

    context_response = get_context(client, base_url, session_id, user_headers)
    expect_status(context_response, 200, "load session context")
    print_response("normal user session context", context_response)
    return {
        "session_id": session_id,
        "context": decode_json(context_response),
    }


def run_setup_and_run(args: argparse.Namespace) -> Dict[str, Any]:
    base_url = args.url.rstrip("/")
    run_id = args.run_id or uuid.uuid4().hex[:8]
    name_prefix = args.name_prefix or f"demo-auto-{run_id}"
    summary: Dict[str, Any] = {
        "run_id": run_id,
        "name_prefix": name_prefix,
        "accounts": [],
    }

    with httpx.Client(timeout=args.timeout) as client:
        health = client.get(f"{base_url}/health")
        expect_status(health, 200, "health check")

        account_id = name_prefix
        print(f"== Account {account_id} ==")
        account_result = create_account(
            client,
            base_url,
            root_key=args.root_key,
            account_id=account_id,
            admin_user_id=args.admin_user,
        )
        user_result = register_user(
            client,
            base_url,
            api_key=args.root_key,
            account_id=account_id,
            user_id=args.regular_user,
            role="user",
        )

        multi_session_id = f"{account_id}-multi-{run_id}"
        normal_session_id = f"{account_id}-normal-{run_id}"

        print(f"-- multi-user scenario for {account_id} --")
        multi_result = run_multi_user_flow(
            client,
            base_url=base_url,
            account=account_id,
            admin_key=account_result["user_key"],
            effective_user_a=args.admin_user,
            effective_user_b=args.regular_user,
            assistant_peer_id=args.assistant_peer_id,
            session_id=multi_session_id,
        )

        print(f"-- normal-user scenario for {account_id} --")
        normal_result = run_normal_user_flow(
            client,
            base_url=base_url,
            user_key=user_result["user_key"],
            account=None,
            user=None,
            explicit_peer_id=args.explicit_peer_id,
            session_id=normal_session_id,
        )

        summary["accounts"].append(
            {
                "account_id": account_id,
                "admin_user_id": args.admin_user,
                "admin_key": account_result["user_key"],
                "regular_user_id": args.regular_user,
                "regular_user_key": user_result["user_key"],
                "multi_user": multi_result,
                "normal_user": normal_result,
            }
        )
        print()

    print("== Summary ==")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shared session + peer_id HTTP demo")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:1933",
        help="OpenViking server base URL",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds",
    )
    parser.add_argument("--root-key", required=True, help="ROOT API key")
    parser.add_argument(
        "--name-prefix",
        default=None,
        help="Account prefix. Defaults to demo-auto-<run_id>",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional fixed run id used in account/session naming",
    )
    parser.add_argument(
        "--admin-user",
        default="alice",
        help="Admin user created in the account",
    )
    parser.add_argument(
        "--regular-user",
        default="bob",
        help="Regular USER created in the account",
    )
    parser.add_argument(
        "--assistant-peer-id",
        default=None,
        help="Optional explicit assistant peer_id for the ADMIN scenario",
    )
    parser.add_argument(
        "--explicit-peer-id",
        default=None,
        help="Optional explicit peer_id used for the USER scenario",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        run_setup_and_run(args)
    except httpx.HTTPError as exc:
        print(f"{FAIL} HTTP error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
