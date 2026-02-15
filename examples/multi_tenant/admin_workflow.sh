#!/usr/bin/env bash
# ============================================================================
# OpenViking Multi-Tenant Admin Workflow (CLI)
#
# This script demonstrates account and user management through the CLI.
# It walks through a full lifecycle: create account → register users →
# manage roles/keys → access data → cleanup.
#
# Prerequisites:
#   1. Configure & start the server with root_api_key:
#      Copy ov.conf.example to ov.conf, fill in your model API keys, then:
#
#      openviking serve --config ./ov.conf
#
#      The key config for multi-tenant auth:
#        {
#          "server": {
#            "root_api_key": "my-root-key"
#          }
#        }
#
#   2. Set environment variables (or use defaults):
#      SERVER    - Server address (default: http://localhost:1933)
#      ROOT_KEY  - Root API key  (default: my-root-key)
#
# Usage:
#   bash admin_workflow.sh
#   ROOT_KEY=your-key SERVER=http://host:port bash admin_workflow.sh
# ============================================================================

set -euo pipefail

SERVER="${SERVER:-http://localhost:1933}"
ROOT_KEY="${ROOT_KEY:-my-root-key}"

section() { printf '\n\033[1;36m── %s ──\033[0m\n' "$1"; }
info()    { printf '  %s\n' "$1"; }
ok()      { printf '  \033[32m✓ %s\033[0m\n' "$1"; }
fail()    { printf '  \033[31m✗ %s\033[0m\n' "$1"; }

# Helper: expect a command to fail (exit non-zero)
expect_fail() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    fail "UNEXPECTED SUCCESS: $label"
    return 1
  else
    ok "$label"
  fi
}

# ── Temp config management ──
# The CLI reads ovcli.conf for url/api_key. We create temp configs
# to switch between different keys (root, alice, bob, etc.)
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Helper: run openviking CLI with a specific API key
ovcli() {
  local key="$1"; shift
  cat > "$TMPDIR/cli.conf" <<EOF
{"url": "$SERVER", "api_key": "$key"}
EOF
  OPENVIKING_CLI_CONFIG_FILE="$TMPDIR/cli.conf" openviking "$@"
}

# Helper: extract field from --json output
jq_field() {
  python3 -c "import sys,json; print(json.load(sys.stdin)['result']['$1'])"
}

printf '\033[1m=== OpenViking Multi-Tenant Admin Workflow (CLI) ===\033[0m\n'
info "Server:   $SERVER"
info "Root Key: ${ROOT_KEY:0:8}..."

# ============================================================================
# 1. Health Check
# ============================================================================
# `openviking health` never requires authentication.

section "1. Health Check (no auth required)"
ovcli "$ROOT_KEY" health

# ============================================================================
# 2. Create Account
# ============================================================================
# openviking admin create-account <account_id> --admin <admin_user_id>
#
# Creates a new account (workspace) with its first admin user.
# Returns the admin user's API key.

section "2. Create Account 'acme' (first admin: alice)"
RESULT=$(ovcli "$ROOT_KEY" --json admin create-account acme --admin alice)
echo "$RESULT" | python3 -m json.tool
ALICE_KEY=$(echo "$RESULT" | jq_field "user_key")
ok "Alice (ADMIN) key: ${ALICE_KEY:0:16}..."

# ============================================================================
# 3. Register User — as ROOT
# ============================================================================
# openviking admin register-user <account_id> <user_id> [--role user|admin]
#
# Register a user in the account. Default role is "user".

section "3. Register User 'bob' as USER (by ROOT)"
RESULT=$(ovcli "$ROOT_KEY" --json admin register-user acme bob --role user)
echo "$RESULT" | python3 -m json.tool
BOB_KEY=$(echo "$RESULT" | jq_field "user_key")
ok "Bob (USER) key: ${BOB_KEY:0:16}..."

# ============================================================================
# 4. Register User — as ADMIN
# ============================================================================
# ADMIN users can register new users within their own account.

section "4. Register User 'charlie' as USER (by ADMIN alice)"
RESULT=$(ovcli "$ALICE_KEY" --json admin register-user acme charlie --role user)
echo "$RESULT" | python3 -m json.tool
CHARLIE_KEY=$(echo "$RESULT" | jq_field "user_key")
ok "Charlie (USER) key: ${CHARLIE_KEY:0:16}..."

# ============================================================================
# 5. List Accounts
# ============================================================================
# openviking admin list-accounts  (ROOT only)

section "5. List All Accounts"
ovcli "$ROOT_KEY" admin list-accounts

# ============================================================================
# 6. List Users
# ============================================================================
# openviking admin list-users <account_id>  (ROOT or ADMIN)

section "6. List Users in 'acme'"
ovcli "$ROOT_KEY" admin list-users acme

# ============================================================================
# 7. Change User Role
# ============================================================================
# openviking admin set-role <account_id> <user_id> <role>  (ROOT only)

section "7. Promote Bob to ADMIN"
ovcli "$ROOT_KEY" admin set-role acme bob admin
ok "Bob is now ADMIN"

# Verify: Bob can now do admin operations in acme
info "Verify Bob's ADMIN privileges:"
ovcli "$BOB_KEY" admin list-users acme >/dev/null 2>&1
ok "Bob (ADMIN) can list users in acme"

# ============================================================================
# 8. Regenerate User Key
# ============================================================================
# openviking admin regenerate-key <account_id> <user_id>  (ROOT or ADMIN)
#
# Generates a new key; the old key is immediately invalidated.

section "8. Regenerate Charlie's Key"
info "Old key: ${CHARLIE_KEY:0:16}..."
RESULT=$(ovcli "$ROOT_KEY" --json admin regenerate-key acme charlie)
echo "$RESULT" | python3 -m json.tool
NEW_CHARLIE_KEY=$(echo "$RESULT" | jq_field "user_key")
ok "New key: ${NEW_CHARLIE_KEY:0:16}... (old key invalidated)"

# ============================================================================
# 9. Access Data with User Key
# ============================================================================
# Regular CLI commands accept user keys for authentication.

section "9. Bob Accesses Data"
info "openviking ls viking:// with Bob's key:"
ovcli "$BOB_KEY" ls viking://

# ============================================================================
# 10. Error Handling & Permission Tests
# ============================================================================
# Verify the system correctly rejects invalid keys, insufficient permissions,
# and duplicate operations.

section "10. Error Handling & Permission Tests"

# ── 10a. Invalid / missing key ──
info "10a. Invalid & missing API key:"
expect_fail "Random key rejected" \
  ovcli "this-is-not-a-valid-key-at-all" ls viking://
expect_fail "Empty key rejected" \
  ovcli "" ls viking://

# ── 10b. USER cannot do admin operations ──
# Charlie is still a USER at this point
info "10b. USER (charlie) cannot do admin operations:"
expect_fail "USER cannot list-accounts" \
  ovcli "$NEW_CHARLIE_KEY" admin list-accounts
expect_fail "USER cannot create-account" \
  ovcli "$NEW_CHARLIE_KEY" admin create-account evil --admin hacker
expect_fail "USER cannot register-user" \
  ovcli "$NEW_CHARLIE_KEY" admin register-user acme dave --role user
expect_fail "USER cannot delete-account" \
  ovcli "$NEW_CHARLIE_KEY" admin delete-account acme
expect_fail "USER cannot set-role" \
  ovcli "$NEW_CHARLIE_KEY" admin set-role acme bob user
expect_fail "USER cannot remove-user" \
  ovcli "$NEW_CHARLIE_KEY" admin remove-user acme bob
expect_fail "USER cannot regenerate-key" \
  ovcli "$NEW_CHARLIE_KEY" admin regenerate-key acme bob

# ── 10c. ADMIN cannot do ROOT-only operations ──
# Alice is ADMIN of acme
info "10c. ADMIN (alice) cannot do ROOT-only operations:"
expect_fail "ADMIN cannot list-accounts" \
  ovcli "$ALICE_KEY" admin list-accounts
expect_fail "ADMIN cannot create-account" \
  ovcli "$ALICE_KEY" admin create-account other --admin admin1
expect_fail "ADMIN cannot delete-account" \
  ovcli "$ALICE_KEY" admin delete-account acme
expect_fail "ADMIN cannot set-role" \
  ovcli "$ALICE_KEY" admin set-role acme charlie admin

# ── 10d. Duplicate account / user ──
info "10d. Duplicate creation rejected:"
expect_fail "Duplicate account rejected" \
  ovcli "$ROOT_KEY" admin create-account acme --admin alice2
expect_fail "Duplicate user rejected" \
  ovcli "$ROOT_KEY" admin register-user acme alice --role admin

# ── 10e. Old key after regeneration ──
info "10e. Old key after regeneration:"
expect_fail "Charlie's old key rejected" \
  ovcli "$CHARLIE_KEY" ls viking://

# ── 10f. ADMIN cross-account isolation ──
# Create a second account to test that ADMIN of one account cannot manage another
info "10f. ADMIN cross-account isolation:"
RESULT=$(ovcli "$ROOT_KEY" --json admin create-account beta --admin beta_admin)
BETA_ADMIN_KEY=$(echo "$RESULT" | jq_field "user_key")
ok "Created account 'beta' for cross-account test"

expect_fail "ADMIN (alice/acme) cannot register-user in beta" \
  ovcli "$ALICE_KEY" admin register-user beta intruder --role user
expect_fail "ADMIN (alice/acme) cannot list-users in beta" \
  ovcli "$ALICE_KEY" admin list-users beta
expect_fail "ADMIN (alice/acme) cannot remove-user in beta" \
  ovcli "$ALICE_KEY" admin remove-user beta beta_admin
expect_fail "ADMIN (alice/acme) cannot regenerate-key in beta" \
  ovcli "$ALICE_KEY" admin regenerate-key beta beta_admin
expect_fail "ADMIN (beta) cannot register-user in acme" \
  ovcli "$BETA_ADMIN_KEY" admin register-user acme intruder --role user

# Cleanup beta
ovcli "$ROOT_KEY" admin delete-account beta >/dev/null 2>&1
ok "Cleaned up account 'beta'"

# ── 10g. Non-existent account / user ──
info "10g. Non-existent account / user:"
expect_fail "Register user in non-existent account" \
  ovcli "$ROOT_KEY" admin register-user nonexistent dave --role user
expect_fail "List users of non-existent account" \
  ovcli "$ROOT_KEY" admin list-users nonexistent
expect_fail "Delete non-existent account" \
  ovcli "$ROOT_KEY" admin delete-account nonexistent
expect_fail "Remove non-existent user" \
  ovcli "$ROOT_KEY" admin remove-user acme nonexistent_user
expect_fail "Set role on non-existent user" \
  ovcli "$ROOT_KEY" admin set-role acme nonexistent_user admin
expect_fail "Regenerate key for non-existent user" \
  ovcli "$ROOT_KEY" admin regenerate-key acme nonexistent_user

# ============================================================================
# 11. Remove User
# ============================================================================
# openviking admin remove-user <account_id> <user_id>  (ROOT or ADMIN)
#
# Removes the user and invalidates their key.

section "11. Remove Charlie"
ovcli "$ROOT_KEY" admin remove-user acme charlie

# Verify: charlie's key should now fail
info "Verify charlie's key is invalid:"
if ovcli "$NEW_CHARLIE_KEY" ls viking:// 2>/dev/null; then
  fail "UNEXPECTED SUCCESS: Charlie's key should have been rejected"
else
  ok "Charlie's key rejected (expected)"
fi

# ============================================================================
# 12. Delete Account
# ============================================================================
# openviking admin delete-account <account_id>  (ROOT only)
#
# Deletes the account and all associated user keys.

section "12. Delete Account 'acme'"
ovcli "$ROOT_KEY" admin delete-account acme

# Verify: all keys from deleted account should fail
info "Verify all keys from deleted account are invalid:"
if ovcli "$ALICE_KEY" ls viking:// 2>/dev/null; then
  fail "UNEXPECTED SUCCESS: Alice's key should have been rejected"
else
  ok "Alice's key rejected (expected)"
fi
if ovcli "$BOB_KEY" ls viking:// 2>/dev/null; then
  fail "UNEXPECTED SUCCESS: Bob's key should have been rejected"
else
  ok "Bob's key rejected (expected)"
fi

printf '\n\033[1m=== Done ===\033[0m\n'
