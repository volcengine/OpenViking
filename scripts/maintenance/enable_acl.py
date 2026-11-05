#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Add VikingDB ACL schema, then enable ACL for existing OpenViking accounts.

Run from the repository root with ``python -m scripts.maintenance.enable_acl``.
The default is a read-only dry run. See enable_acl.md for the upgrade sequence.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

# Keep aligned with CollectionSchemas.context_collection. "inherit" with empty
# grants would deny historical shared content; missing fields must default to none.
ACL_FIELDS = [
    {"FieldName": "acl_mode", "FieldType": "string", "DefaultValue": "none"},
    {"FieldName": "acl_direct_grants", "FieldType": "list<string>", "DefaultValue": ["none"]},
    {"FieldName": "acl_inherited_grants", "FieldType": "list<string>", "DefaultValue": ["none"]},
]
ACL_NAMES = [field["FieldName"] for field in ACL_FIELDS]


def vector_target(vector: dict) -> tuple[str, str, str, str]:
    return (
        vector.get("backend") or "local",
        vector.get("project") or vector.get("project_name") or "default",
        vector.get("name") or "context",
        vector.get("index_name") or "default",
    )


def check_account_target(account: str, settings: dict, vector: dict):
    override = settings.get("vectordb")
    if override is None:
        return  # Older accounts inherit the deployment's vector configuration.
    if not isinstance(override, dict):
        raise RuntimeError(f"Account {account}: unexpected vectordb configuration")
    merged = {**vector, **override}
    if "project_name" in override and "project" not in override:
        merged["project"] = override["project_name"]
    expected_connection = vector.get("volcengine") or {}
    account_connection = override.get("volcengine") or {}
    # Account vector overrides own the complete connection, not just the name.
    # Compare credentials without logging them; same names in another cloud
    # account or region do not identify the same collection.
    same_connection = all(
        (account_connection.get(key) or None) == (expected_connection.get(key) or None)
        for key in ("ak", "sk", "region", "host", "api_key")
    )
    if vector_target(merged) != vector_target(vector) or not same_connection:
        raise RuntimeError(
            f"Account {account} uses a different vector target/connection. "
            "Run separately with --account and an ov.conf matching its vectordb settings."
        )


class Console:
    """Use the existing signer, but do not swallow management-plane errors."""

    def __init__(self, client: Any, project: str, collection: str, index: str):
        self.client = client
        self.target = {"ProjectName": project, "CollectionName": collection}
        self.index = index

    def call(self, action: str, **payload: Any) -> dict:
        from openviking.storage.vectordb.collection.volcengine_clients import VIKING_DB_VERSION

        response = self.client.do_req(
            "POST",
            req_params={"Action": action, "Version": VIKING_DB_VERSION},
            req_body={**self.target, **payload},
        )
        response.raise_for_status()
        body = response.json()
        error = body.get("ResponseMetadata", {}).get("Error")
        if error:
            raise RuntimeError(f"{action} failed: {error.get('Code', 'unknown error')}")
        result = body.get("Result", {})
        if not isinstance(result, dict):
            raise RuntimeError(f"{action}: unexpected Result shape")
        return result

    def collection_meta(self) -> dict:
        meta = self.call("GetVikingdbCollection")
        if meta.get("CollectionName") != self.target["CollectionName"]:
            raise RuntimeError("GetVikingdbCollection did not return the target collection")
        return meta

    def index_meta(self) -> dict:
        meta = self.call("GetVikingdbIndex", IndexName=self.index)
        if meta.get("IndexName") != self.index:
            raise RuntimeError("GetVikingdbIndex did not return the target index")
        return meta


def missing_fields(meta: dict) -> list[dict]:
    fields = meta.get("Fields")
    if not isinstance(fields, list):
        raise RuntimeError("Collection metadata is missing Fields")
    existing = {field["FieldName"]: field for field in fields}
    missing = []
    for expected in ACL_FIELDS:
        current = existing.get(expected["FieldName"])
        if current is None:
            missing.append(dict(expected))
        elif any(current.get(key) != expected[key] for key in ("FieldType", "DefaultValue")):
            raise RuntimeError(
                f"Incompatible existing ACL field {expected['FieldName']}; "
                f"expected {expected}. Existing fields cannot be changed by this migration."
            )
    return missing


def scalar_fields(meta: dict) -> list[str] | None:
    if "ScalarIndex" not in meta:
        raise RuntimeError("Index metadata is missing ScalarIndex")
    fields = meta["ScalarIndex"]
    # VikingDB null means ALL fields, whereas [] means NO scalar indexes.
    if fields is None:
        return None
    if not isinstance(fields, list):
        raise RuntimeError("Unexpected ScalarIndex shape")
    names = [field.get("FieldName") if isinstance(field, dict) else field for field in fields]
    if not all(isinstance(name, str) and name for name in names):
        raise RuntimeError("Unexpected ScalarIndex field name")
    return list(dict.fromkeys(names))


def wait_for(check, label: str, timeout: float, poll_interval: float):
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {label}; account ACL has not been changed")
        time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))


def ready_index(console: Console) -> dict | None:
    meta = console.index_meta()
    status = meta.get("Status")
    if status in {"FAILED", "DISABLED", "DELETING"}:
        raise RuntimeError(f"Index is {status}; account ACL has not been changed")
    if status not in {"CREATING", "READY", "UPDATING"}:
        raise RuntimeError(f"Unrecognized index Status: {status!r}")
    return meta if status == "READY" else None


def migrate_schema(console: Console, *, execute: bool, update: bool, timeout: float, poll: float):
    missing = missing_fields(console.collection_meta())
    index_meta = console.index_meta()
    indexed = scalar_fields(index_meta)
    to_index = [] if indexed is None else [name for name in ACL_NAMES if name not in indexed]
    print(
        json.dumps(
            {
                "collection": console.target,
                "index": console.index,
                "add_fields": missing,
                "add_scalar_indexes": to_index,
                "index_status": index_meta.get("Status"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not execute:
        return
    if not update and (missing or to_index):
        raise RuntimeError("ACL schema is incomplete; run --phase schema --execute first")

    wait_for(lambda: ready_index(console), "index READY", timeout, poll)
    if missing:
        # UpdateVikingdbCollection accepts ONLY the new fields.
        console.call("UpdateVikingdbCollection", Fields=missing)
        wait_for(
            lambda: not missing_fields(console.collection_meta()),
            "ACL collection fields",
            timeout,
            poll,
        )

    # Re-read after the collection update so concurrent additions are preserved.
    index_meta = wait_for(lambda: ready_index(console), "index READY", timeout, poll)
    indexed = scalar_fields(index_meta)
    if indexed is not None and any(name not in indexed for name in ACL_NAMES):
        if not update:
            raise RuntimeError("ACL scalar indexes are missing")
        merged = list(dict.fromkeys([*indexed, *ACL_NAMES]))
        console.call("UpdateVikingdbIndex", IndexName=console.index, ScalarIndex=merged)

    def complete():
        meta = ready_index(console)
        if meta is None or missing_fields(console.collection_meta()):
            return False
        fields = scalar_fields(meta)
        return fields is None or all(name in fields for name in ACL_NAMES)

    wait_for(complete, "ACL scalar indexes READY", timeout, poll)
    print("ACL schema verified; index READY", flush=True)


class Admin:
    def __init__(self, url: str, root_key: str, vector: dict | None = None):
        self.url = url.rstrip("/") + "/api/v1/admin"
        self.vector = vector
        self.session = requests.Session()
        self.session.headers["X-API-Key"] = root_key

    def call(self, method: str, path: str, body: dict | None = None):
        response = self.session.request(
            method,
            self.url + path,
            json=body,
            timeout=30,
            allow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "ok":
            # Do not print full configuration responses: they may contain secrets.
            raise RuntimeError(f"Admin API {method} {path} did not return status=ok")
        return payload["result"]

    @staticmethod
    def settings_path(account: str) -> str:
        # This compatibility endpoint returns effective ACL settings;
        # PATCH changes only the supplied ACL section.
        return f"/accounts/{quote(account, safe='')}/settings"

    @staticmethod
    def enabled(result: dict) -> bool:
        value = result.get("settings", {}).get("acl", {}).get("enabled")
        if type(value) is not bool:
            raise RuntimeError("Server did not return acl.enabled; deploy an ACL-capable version")
        return value

    def account_plan(self, accounts: list[str] | None) -> dict[str, bool]:
        if accounts is None:
            rows = self.call("GET", "/accounts")  # No limit: one snapshot of all accounts.
            if not isinstance(rows, list):
                raise RuntimeError("Unexpected account list response")
            accounts = []
            for row in rows:
                if row.get("status", "active") == "active":
                    accounts.append(row["account_id"])
                else:
                    print(f"Skip inactive account: {row['account_id']}", flush=True)
        plan = {}
        for account in dict.fromkeys(accounts):
            if self.vector is not None:
                configuration = self.call(
                    "GET",
                    f"/accounts/{quote(account, safe='')}/configuration",
                )
                settings = configuration.get("settings")
                if not isinstance(settings, dict):
                    raise RuntimeError(f"Account {account}: missing configuration settings")
                check_account_target(account, settings, self.vector)
            plan[account] = self.enabled(self.call("GET", self.settings_path(account)))
        return plan

    def enable(self, plan: dict[str, bool], *, execute: bool):
        for account, enabled in plan.items():
            if enabled:
                print(f"Already enabled: {account}", flush=True)
                continue
            if not execute:
                print(f"Would enable ACL: {account}", flush=True)
                continue
            result = self.call("PATCH", self.settings_path(account), {"acl": {"enabled": True}})
            if not self.enabled(result):
                raise RuntimeError(f"Account {account}: PATCH did not enable ACL")
            print(f"Enabled ACL: {account}", flush=True)


def run(console: Console, admin: Admin | None, args):
    # Preflight all account reads before changing any schema or settings.
    plan = admin.account_plan(args.account) if admin is not None else {}
    migrate_schema(
        console,
        execute=args.execute,
        update=args.phase != "accounts",
        timeout=args.wait_timeout,
        poll=args.poll_interval,
    )
    if admin is not None:
        admin.enable(plan, execute=args.execute)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add VikingDB ACL schema, then enable ACL for existing accounts. "
        "Default: dry run. See scripts/maintenance/enable_acl.md."
    )
    parser.add_argument("--config", required=True, type=Path, help="Target ov.conf (JSON)")
    parser.add_argument("--phase", choices=["schema", "accounts", "all"], default="all")
    parser.add_argument("--url", help="Target OpenViking base URL, e.g. http://127.0.0.1:1933")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--account", action="append", help="Account ID; may be repeated")
    scope.add_argument("--all-accounts", action="store_true", help="All currently active accounts")
    parser.add_argument("--execute", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument(
        "--root-key-env", default="OV_ROOT_API_KEY", help="Root key environment variable"
    )
    parser.add_argument("--console-host", help="Optional control-plane hostname (without https://)")
    parser.add_argument(
        "--wait-timeout", type=float, default=600, help="Seconds per readiness wait"
    )
    parser.add_argument("--poll-interval", type=float, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.wait_timeout <= 0 or args.poll_interval <= 0:
        parser.error("wait timeout and poll interval must be positive")
    if args.phase != "schema" and (not args.url or not (args.account or args.all_accounts)):
        parser.error("accounts/all requires --url and either --account or --all-accounts")
    try:
        config = json.loads(args.config.read_text())
        vector = config["storage"]["vectordb"]
        if vector.get("backend") != "volcengine":
            raise ValueError("This script supports storage.vectordb.backend=volcengine only")
        credentials = vector.get("volcengine", {})
        values = {
            key: os.environ.get(env) or credentials.get(key)
            for key, env in (
                ("ak", "VIKINGDB_AK"),
                ("sk", "VIKINGDB_SK"),
                ("region", "VIKINGDB_REGION"),
                ("session_token", "VIKINGDB_SESSION_TOKEN"),
            )
        }
        if not all(values[key] for key in ("ak", "sk", "region")):
            raise ValueError(
                "Management-plane AK/SK/region are required; data API keys cannot change schema"
            )
        from openviking.storage.vectordb.collection.volcengine_clients import ClientForConsoleApi

        console = Console(
            ClientForConsoleApi(**values, host=args.console_host),
            *vector_target(vector)[1:],
        )
        admin = None
        if args.phase != "schema":
            root_key = os.environ.get(args.root_key_env)
            if not root_key:
                raise ValueError(f"Set {args.root_key_env} to the target OpenViking ROOT API key")
            admin = Admin(args.url, root_key, vector)
        print("EXECUTE" if args.execute else "DRY RUN (no changes)", flush=True)
        run(console, admin, args)
        return 0
    except (KeyError, ValueError, OSError, RuntimeError, requests.RequestException) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
