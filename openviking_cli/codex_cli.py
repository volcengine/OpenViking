# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import argparse
import json
import sys

from openviking.models.vlm.backends.codex_auth import (
    CodexAuthError,
    bootstrap_codex_auth,
    delete_codex_auth_store,
    get_codex_auth_status,
    get_codex_auth_store_path,
    login_codex_with_device_code,
    resolve_codex_runtime_credentials,
)


def _print_json(payload: dict) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _status_payload() -> dict:
    status = get_codex_auth_status()
    ready = False
    source = None
    base_url = None
    error = None
    try:
        creds = resolve_codex_runtime_credentials()
        ready = True
        source = creds.get("source")
        base_url = creds.get("base_url")
    except Exception as exc:
        error = str(exc)
    status["ready"] = ready
    status["active_source"] = source
    status["base_url"] = base_url
    status["error"] = error
    return status


def _cmd_status(args: argparse.Namespace) -> int:
    payload = _status_payload()
    if args.json:
        return _print_json(payload)
    print("Codex OAuth status")
    print(f"  Ready: {'yes' if payload['ready'] else 'no'}")
    print(f"  OV auth store: {payload['store_path']}")
    print(f"  OV auth present: {'yes' if payload['store_exists'] else 'no'}")
    print(f"  Env override: {'yes' if payload['env_override'] else 'no'}")
    if payload.get("bootstrap_path"):
        print(f"  Bootstrap source: {payload['bootstrap_path']}")
        print(f"  Bootstrap available: {'yes' if payload['bootstrap_available'] else 'no'}")
    if payload.get("active_source"):
        print(f"  Active source: {payload['active_source']}")
    if payload.get("expires_at"):
        print(f"  Access token expires: {payload['expires_at']}")
    if payload.get("last_refresh"):
        print(f"  Last refresh: {payload['last_refresh']}")
    if payload.get("imported_from"):
        print(f"  Imported from: {payload['imported_from']}")
    if payload.get("base_url"):
        print(f"  Base URL: {payload['base_url']}")
    if payload.get("error"):
        print(f"  Error: {payload['error']}")
    return 0 if payload["ready"] else 1


def _cmd_login(args: argparse.Namespace) -> int:
    if not args.force:
        try:
            creds = resolve_codex_runtime_credentials()
            print("Codex OAuth is already ready.")
            print(f"OV auth store: {get_codex_auth_store_path()}")
            print(f"Source: {creds.get('source')}")
            return 0
        except Exception:
            pass
    if not args.device_only:
        bootstrapped = bootstrap_codex_auth()
        if bootstrapped is not None:
            print("Imported Codex OAuth into the OV auth store.")
            print(f"OV auth store: {bootstrapped}")
            return 0
    path = login_codex_with_device_code()
    print("Codex OAuth login successful.")
    print(f"OV auth store: {path}")
    return 0


def _cmd_logout(args: argparse.Namespace) -> int:
    if not args.yes:
        try:
            reply = input("Delete OV Codex auth state? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            reply = "n"
        if reply not in {"y", "yes"}:
            print("Cancelled.")
            return 1
    removed = delete_codex_auth_store()
    if removed:
        print("Deleted OV Codex auth state.")
    else:
        print("No OV Codex auth state found.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ov codex", description="Manage OV Codex OAuth for VLM usage")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show Codex OAuth readiness")
    status_parser.add_argument("--json", action="store_true", help="Print status as JSON")
    status_parser.set_defaults(func=_cmd_status)

    login_parser = subparsers.add_parser("login", help="Sign in to Codex for OV")
    login_parser.add_argument("--force", action="store_true", help="Refresh setup even if OV already has auth")
    login_parser.add_argument("--device-only", action="store_true", help="Skip bootstrap and always use device login")
    login_parser.set_defaults(func=_cmd_login)

    logout_parser = subparsers.add_parser("logout", help="Delete OV-owned Codex auth state")
    logout_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    logout_parser.set_defaults(func=_cmd_logout)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CodexAuthError as exc:
        print(f"Codex OAuth error: {exc}", file=sys.stderr)
        return 1
