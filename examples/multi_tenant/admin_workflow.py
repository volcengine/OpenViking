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

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


def expect_error(resp: httpx.Response, label: str, expected_status: int = 0) -> None:
    """Assert that an HTTP response indicates an error."""
    if resp.is_success:
        print(f"  {FAIL} UNEXPECTED SUCCESS: {label} (HTTP {resp.status_code})")
    else:
        print(f"  {PASS} {label} -> HTTP {resp.status_code}")


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

    # Verify: Bob can now do admin operations in acme
    bob_headers = {"X-API-Key": bob_key, "Content-Type": "application/json"}
    resp = httpx.get(f"{base}/api/v1/admin/accounts/acme/users", headers=bob_headers)
    assert resp.is_success, "Bob (ADMIN) should be able to list users"
    print(f"  {PASS} Bob (ADMIN) can list users in acme")
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

    # ── 10. Error handling & permission tests ──
    print("== 10. Error Handling & Permission Tests ==")

    # 10a. Invalid / missing key
    print("  10a. Invalid & missing API key:")
    resp = httpx.get(
        f"{base}/api/v1/fs/ls",
        params={"uri": "viking://"},
        headers={"X-API-Key": "this-is-not-a-valid-key"},
    )
    expect_error(resp, "Random key rejected")
    resp = httpx.get(f"{base}/api/v1/fs/ls", params={"uri": "viking://"})
    expect_error(resp, "No key rejected")

    # 10b. USER cannot do admin operations
    print("  10b. USER (charlie) cannot do admin operations:")
    charlie_headers = {"X-API-Key": new_charlie_key, "Content-Type": "application/json"}
    resp = httpx.get(f"{base}/api/v1/admin/accounts", headers=charlie_headers)
    expect_error(resp, "USER cannot list-accounts")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts",
        headers=charlie_headers,
        json={"account_id": "evil", "admin_user_id": "hacker"},
    )
    expect_error(resp, "USER cannot create-account")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/acme/users",
        headers=charlie_headers,
        json={"user_id": "dave", "role": "user"},
    )
    expect_error(resp, "USER cannot register-user")
    resp = httpx.delete(f"{base}/api/v1/admin/accounts/acme", headers=charlie_headers)
    expect_error(resp, "USER cannot delete-account")
    resp = httpx.put(
        f"{base}/api/v1/admin/accounts/acme/users/bob/role",
        headers=charlie_headers,
        json={"role": "user"},
    )
    expect_error(resp, "USER cannot set-role")
    resp = httpx.delete(
        f"{base}/api/v1/admin/accounts/acme/users/bob",
        headers=charlie_headers,
    )
    expect_error(resp, "USER cannot remove-user")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/acme/users/bob/key",
        headers=charlie_headers,
    )
    expect_error(resp, "USER cannot regenerate-key")

    # 10c. ADMIN cannot do ROOT-only operations
    print("  10c. ADMIN (alice) cannot do ROOT-only operations:")
    resp = httpx.get(f"{base}/api/v1/admin/accounts", headers=alice_headers)
    expect_error(resp, "ADMIN cannot list-accounts")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts",
        headers=alice_headers,
        json={"account_id": "other", "admin_user_id": "admin1"},
    )
    expect_error(resp, "ADMIN cannot create-account")
    resp = httpx.delete(f"{base}/api/v1/admin/accounts/acme", headers=alice_headers)
    expect_error(resp, "ADMIN cannot delete-account")
    resp = httpx.put(
        f"{base}/api/v1/admin/accounts/acme/users/charlie/role",
        headers=alice_headers,
        json={"role": "admin"},
    )
    expect_error(resp, "ADMIN cannot set-role")

    # 10d. Duplicate account / user
    print("  10d. Duplicate creation rejected:")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts",
        headers=headers,
        json={"account_id": "acme", "admin_user_id": "alice2"},
    )
    expect_error(resp, "Duplicate account rejected")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/acme/users",
        headers=headers,
        json={"user_id": "alice", "role": "admin"},
    )
    expect_error(resp, "Duplicate user rejected")

    # 10e. Old key after regeneration
    print("  10e. Old key after regeneration:")
    resp = httpx.get(
        f"{base}/api/v1/fs/ls",
        params={"uri": "viking://"},
        headers={"X-API-Key": charlie_key},
    )
    expect_error(resp, "Charlie's old key rejected")

    # 10f. ADMIN cross-account isolation
    print("  10f. ADMIN cross-account isolation:")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts",
        headers=headers,
        json={"account_id": "beta", "admin_user_id": "beta_admin"},
    )
    beta_result = resp.json()
    beta_admin_key = beta_result["result"]["user_key"]
    beta_admin_headers = {"X-API-Key": beta_admin_key, "Content-Type": "application/json"}
    print(f"  {PASS} Created account 'beta' for cross-account test")

    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/beta/users",
        headers=alice_headers,
        json={"user_id": "intruder", "role": "user"},
    )
    expect_error(resp, "ADMIN (alice/acme) cannot register-user in beta")
    resp = httpx.get(f"{base}/api/v1/admin/accounts/beta/users", headers=alice_headers)
    expect_error(resp, "ADMIN (alice/acme) cannot list-users in beta")
    resp = httpx.delete(
        f"{base}/api/v1/admin/accounts/beta/users/beta_admin",
        headers=alice_headers,
    )
    expect_error(resp, "ADMIN (alice/acme) cannot remove-user in beta")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/beta/users/beta_admin/key",
        headers=alice_headers,
    )
    expect_error(resp, "ADMIN (alice/acme) cannot regenerate-key in beta")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/acme/users",
        headers=beta_admin_headers,
        json={"user_id": "intruder", "role": "user"},
    )
    expect_error(resp, "ADMIN (beta) cannot register-user in acme")

    # Cleanup beta
    httpx.delete(f"{base}/api/v1/admin/accounts/beta", headers=headers)
    print(f"  {PASS} Cleaned up account 'beta'")

    # 10g. Non-existent account / user
    print("  10g. Non-existent account / user:")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/nonexistent/users",
        headers=headers,
        json={"user_id": "dave", "role": "user"},
    )
    expect_error(resp, "Register user in non-existent account")
    resp = httpx.get(f"{base}/api/v1/admin/accounts/nonexistent/users", headers=headers)
    expect_error(resp, "List users of non-existent account")
    resp = httpx.delete(f"{base}/api/v1/admin/accounts/nonexistent", headers=headers)
    expect_error(resp, "Delete non-existent account")
    resp = httpx.delete(
        f"{base}/api/v1/admin/accounts/acme/users/nonexistent_user",
        headers=headers,
    )
    expect_error(resp, "Remove non-existent user")
    resp = httpx.put(
        f"{base}/api/v1/admin/accounts/acme/users/nonexistent_user/role",
        headers=headers,
        json={"role": "admin"},
    )
    expect_error(resp, "Set role on non-existent user")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/acme/users/nonexistent_user/key",
        headers=headers,
    )
    expect_error(resp, "Regenerate key for non-existent user")
    print()

    # ── 11. Remove user ──
    print("== 11. Remove Charlie ==")
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

    # ── 12. Delete account ──
    print("== 12. Delete Account ==")
    resp = httpx.delete(f"{base}/api/v1/admin/accounts/acme", headers=headers)
    print(f"  Result: {resp.json()['result']}")

    # Verify all keys from deleted account no longer work
    resp = httpx.get(
        f"{base}/api/v1/fs/ls",
        params={"uri": "viking://"},
        headers={"X-API-Key": alice_key},
    )
    print(f"  Alice's key after deletion -> HTTP {resp.status_code}")
    resp = httpx.get(
        f"{base}/api/v1/fs/ls",
        params={"uri": "viking://"},
        headers={"X-API-Key": bob_key},
    )
    print(f"  Bob's key after deletion -> HTTP {resp.status_code}")
    print()

    print("== Done ==")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-tenant admin workflow example")
    parser.add_argument("--url", default="http://localhost:1933", help="Server URL")
    parser.add_argument("--root-key", default="my-root-key", help="Root API key")
    args = parser.parse_args()

    admin_api(args.url, args.root_key)
