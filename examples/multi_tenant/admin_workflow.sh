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
# 10. Remove User
# ============================================================================
# openviking admin remove-user <account_id> <user_id>  (ROOT or ADMIN)
#
# Removes the user and invalidates their key.

section "10. Remove Charlie"
ovcli "$ROOT_KEY" admin remove-user acme charlie

# Verify: charlie's key should now fail
info "Verify charlie's key is invalid:"
if ovcli "$NEW_CHARLIE_KEY" ls viking:// 2>/dev/null; then
  printf '  \033[31m✗ ERROR: expected authentication failure\033[0m\n'
else
  ok "Charlie's key rejected (expected)"
fi

# ============================================================================
# 11. Delete Account
# ============================================================================
# openviking admin delete-account <account_id>  (ROOT only)
#
# Deletes the account and all associated user keys.

section "11. Delete Account 'acme'"
ovcli "$ROOT_KEY" admin delete-account acme

# Verify: alice's key should now fail
info "Verify alice's key is invalid:"
if ovcli "$ALICE_KEY" ls viking:// 2>/dev/null; then
  printf '  \033[31m✗ ERROR: expected authentication failure\033[0m\n'
else
  ok "Alice's key rejected (expected)"
fi

printf '\n\033[1m=== Done ===\033[0m\n'
