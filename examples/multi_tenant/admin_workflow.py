#!/usr/bin/env python3
"""
Multi-Tenant Admin Workflow Example (Python SDK)

Demonstrates account and user management via the Admin API:
  1. Create account with first admin user
  2. Register regular users
  3. List accounts and users
  4. Change user roles
  5. Regenerate user keys
  6. Use user key to access data
  7. Remove users and delete accounts

Prerequisites:
    Start server with root_api_key configured in ov.conf:
      {
        "server": {
          "root_api_key": "my-root-key"
        }
      }

    python -m openviking serve

Usage:
    uv run admin_workflow.py
    uv run admin_workflow.py --url http://localhost:1933 --root-key my-root-key
"""

import argparse

import httpx

import openviking as ov


def admin_api(base_url: str, root_key: str):
    """Demonstrate admin operations using direct HTTP calls."""

    headers = {"X-API-Key": root_key, "Content-Type": "application/json"}
    base = base_url.rstrip("/")

    # ── 1. Health check (no auth) ──
    print("== 1. Health Check ==")
    resp = httpx.get(f"{base}/health")
    print(f"  {resp.json()}")
    print()

    # ── 2. Create account with first admin ──
    print("== 2. Create Account ==")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts",
        headers=headers,
        json={"account_id": "acme", "admin_user_id": "alice"},
    )
    result = resp.json()
    print(f"  Status: {resp.status_code}")
    print(f"  Result: {result}")
    alice_key = result["result"]["user_key"]
    print(f"  Alice's key: {alice_key[:16]}...")
    print()

    # ── 3. Register regular user (as ROOT) ──
    print("== 3. Register User (as ROOT) ==")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/acme/users",
        headers=headers,
        json={"user_id": "bob", "role": "user"},
    )
    result = resp.json()
    bob_key = result["result"]["user_key"]
    print(f"  Bob registered, key: {bob_key[:16]}...")
    print()

    # ── 4. Register another user (as ADMIN alice) ──
    print("== 4. Register User (as ADMIN alice) ==")
    alice_headers = {"X-API-Key": alice_key, "Content-Type": "application/json"}
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/acme/users",
        headers=alice_headers,
        json={"user_id": "charlie", "role": "user"},
    )
    result = resp.json()
    charlie_key = result["result"]["user_key"]
    print(f"  Charlie registered by alice, key: {charlie_key[:16]}...")
    print()

    # ── 5. List accounts (ROOT only) ──
    print("== 5. List Accounts ==")
    resp = httpx.get(f"{base}/api/v1/admin/accounts", headers=headers)
    print(f"  Accounts: {resp.json()['result']}")
    print()

    # ── 6. List users in account ──
    print("== 6. List Users in 'acme' ==")
    resp = httpx.get(f"{base}/api/v1/admin/accounts/acme/users", headers=headers)
    print(f"  Users: {resp.json()['result']}")
    print()

    # ── 7. Change user role ──
    print("== 7. Change Bob's Role to ADMIN ==")
    resp = httpx.put(
        f"{base}/api/v1/admin/accounts/acme/users/bob/role",
        headers=headers,
        json={"role": "admin"},
    )
    print(f"  Result: {resp.json()['result']}")
    print()

    # ── 8. Regenerate user key ──
    print("== 8. Regenerate Charlie's Key ==")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/acme/users/charlie/key",
        headers=headers,
    )
    new_charlie_key = resp.json()["result"]["user_key"]
    print(f"  Old key: {charlie_key[:16]}... (now invalid)")
    print(f"  New key: {new_charlie_key[:16]}...")
    print()

    # ── 9. Use user key to access data ──
    print("== 9. Access Data with User Key ==")
    bob_client = ov.SyncHTTPClient(url=base_url, api_key=bob_key, agent_id="demo-agent")
    bob_client.initialize()
    try:
        entries = bob_client.ls("viking://")
        print(f"  Bob can list root: {len(entries)} entries")
    finally:
        bob_client.close()
    print()

    # ── 10. Remove user ──
    print("== 10. Remove Charlie ==")
    resp = httpx.delete(
        f"{base}/api/v1/admin/accounts/acme/users/charlie",
        headers=headers,
    )
    print(f"  Result: {resp.json()['result']}")

    # Verify old key no longer works
    resp = httpx.get(
        f"{base}/api/v1/fs/ls",
        params={"uri": "viking://"},
        headers={"X-API-Key": new_charlie_key},
    )
    print(f"  Charlie's key after removal -> HTTP {resp.status_code}")
    print()

    # ── 11. Delete account ──
    print("== 11. Delete Account ==")
    resp = httpx.delete(f"{base}/api/v1/admin/accounts/acme", headers=headers)
    print(f"  Result: {resp.json()['result']}")

    # Verify alice's key no longer works
    resp = httpx.get(
        f"{base}/api/v1/fs/ls",
        params={"uri": "viking://"},
        headers={"X-API-Key": alice_key},
    )
    print(f"  Alice's key after deletion -> HTTP {resp.status_code}")
    print()

    print("== Done ==")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-tenant admin workflow example")
    parser.add_argument("--url", default="http://localhost:1933", help="Server URL")
    parser.add_argument("--root-key", default="my-root-key", help="Root API key")
    args = parser.parse_args()

    admin_api(args.url, args.root_key)
