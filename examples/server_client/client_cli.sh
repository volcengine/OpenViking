#!/usr/bin/env bash
# ============================================================================
# OpenViking CLI Usage Examples
#
# This script demonstrates the OpenViking CLI commands and options.
# It walks through a typical workflow: health check → add resource → browse →
# search → session management → cleanup.
#
# Prerequisites:
#   1. Configure & start the server:
#      Server reads ov.conf from (priority high → low):
#        a) $OPENVIKING_CONFIG_FILE               # env var, highest priority
#        b) ~/.openviking/ov.conf                  # default path
#      See ov.conf.example for template.
#
#      openviking serve                            # default: localhost:1933
#      openviking serve --port 8080                # custom port
#      openviking serve --config /path/to/ov.conf  # explicit config path
#
#   2. Configure CLI connection (ovcli.conf):
#      CLI reads ovcli.conf from (priority high → low):
#        a) $OPENVIKING_CLI_CONFIG_FILE            # env var, highest priority
#        b) ~/.openviking/ovcli.conf               # default path
#
#      Example ovcli.conf:
#        {
#          "url": "http://localhost:1933",
#          "api_key": null,
#          "output": "table"
#        }
#
#      Fields:
#        url      - Server address (required)
#        api_key  - API key for authentication (null = no auth)
#        output   - Default output format: "table" or "json" (default: "table")
#
# Usage:
#   bash client_cli.sh
# ============================================================================

set -euo pipefail


section() { printf '\n\033[1;36m── %s ──\033[0m\n' "$1"; }

# ============================================================================
# Global Options
# ============================================================================
#
#   --output, -o   Output format: table (default) or json
#   --json         Compact JSON with {"ok": true, "result": ...} wrapper
#   --version      Show version and exit
#
# Global options MUST be placed before the subcommand:
#   openviking -o json ls viking://       ✓
#   openviking --json find "query"        ✓
#   openviking ls viking:// -o json       ✗ (won't work)

printf '\033[1m=== OpenViking CLI Usage Examples ===\033[0m\n'

openviking --version

# ============================================================================
# 1. Health & Status
# ============================================================================

section "1.1 Health Check"
openviking health                          # table: {"healthy": true}
# openviking -o json health                # json:  {"healthy": true}
# openviking --json health                 # script: {"ok": true, "result": {"healthy": true}}

section "1.2 System Status"
openviking status                          # table: component status with ASCII tables

section "1.3 Observer (per-component)"
openviking observer queue                  # queue processor status
# openviking observer vikingdb             # VikingDB connection status
# openviking observer vlm                  # VLM service status
# openviking observer system               # all components (same as `status`)

# ============================================================================
# 2. Resource Management
# ============================================================================

section "2.1 Add Resource"
# Add from URL (use --json to capture root_uri for later commands)
ROOT_URI=$(openviking --json add-resource \
  "https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['root_uri'])")
echo "  root_uri: $ROOT_URI"

# Other add-resource options:
# openviking add-resource ./file --to viking://dst  # specify target URI
# openviking add-resource ./file --reason "..."     # attach import reason
# openviking add-resource ./file --wait             # block until processing done
# openviking add-resource ./file --wait --timeout 60

section "2.2 Add Skill"
# openviking add-skill ./my_skill/SKILL.md          # from SKILL.md file
# openviking add-skill ./skill_dir                  # from directory
# openviking add-skill "raw skill content"          # from inline text
# openviking add-skill ./skill --wait --timeout 30

section "2.3 Wait for Processing"
openviking wait                            # block until all queues are idle
# openviking wait --timeout 120            # with timeout (seconds)

# ============================================================================
# 3. File System
# ============================================================================

section "3.1 List Directory"
openviking ls viking://resources/                   # table: name, size, mode, ...
# openviking ls viking://resources/ --simple        # simple: paths only
# openviking ls viking://resources/ --recursive     # recursive listing
# openviking -o json ls viking://resources/         # json output

section "3.2 Directory Tree"
openviking tree "$ROOT_URI"

section "3.3 File Metadata"
openviking stat "$ROOT_URI"                # table: single-row with all metadata

section "3.4 File Operations"
# openviking mkdir viking://resources/new_dir
# openviking mv viking://resources/old viking://resources/new
# openviking rm viking://resources/file
# openviking rm viking://resources/dir --recursive

# ============================================================================
# 4. Content Reading (3 levels of detail)
# ============================================================================

section "4.1 Abstract (L0 - shortest summary)"
openviking abstract "$ROOT_URI"

section "4.2 Overview (L1 - structured overview)"
openviking overview "$ROOT_URI"

section "4.3 Read (L2 - full content)"
# openviking read "$ROOT_URI"              # prints full file content

# ============================================================================
# 5. Search
# ============================================================================

section "5.1 Semantic Search (find)"
openviking find "what is openviking" --limit 3
# openviking find "auth" --uri viking://resources/docs/  # search within URI
# openviking find "auth" --limit 5 --threshold 0.3       # with score threshold
# openviking -o json find "auth"                         # json output

section "5.2 Pattern Search (grep)"
openviking grep "viking://" "OpenViking"
# openviking grep "viking://resources/" "pattern" --ignore-case

section "5.3 File Glob"
openviking glob "**/*.md"
# openviking glob "*.py" --uri viking://resources/src/   # search within URI

# ============================================================================
# 6. Relations
# ============================================================================

section "6.1 List Relations"
openviking relations "$ROOT_URI"

section "6.2 Link / Unlink"
# openviking link viking://a viking://b viking://c --reason "related docs"
# openviking unlink viking://a viking://b

# ============================================================================
# 7. Session Management
# ============================================================================

section "7.1 Create Session"
SESSION_ID=$(openviking --json session new | python3 -c "
import sys, json; print(json.load(sys.stdin)['result']['session_id'])
")
echo "  session_id: $SESSION_ID"

section "7.2 Add Messages"
openviking session add-message "$SESSION_ID" \
  --role user --content "Tell me about OpenViking"
openviking session add-message "$SESSION_ID" \
  --role assistant --content "OpenViking is an agent-native context database."

section "7.3 Get Session Details"
openviking session get "$SESSION_ID"

section "7.4 Context-Aware Search"
# search uses session history for better relevance
openviking search "how to use it" --session-id "$SESSION_ID" --limit 3
# openviking search "query" --session-id "$SESSION_ID" --threshold 0.3

section "7.5 List All Sessions"
openviking session list

section "7.6 Commit Session (archive + extract memories)"
# openviking session commit "$SESSION_ID"

section "7.7 Delete Session"
openviking session delete "$SESSION_ID"

# ============================================================================
# 8. Import / Export
# ============================================================================

section "8.1 Export"
# openviking export viking://resources/docs ./docs.ovpack

section "8.2 Import"
# openviking import ./docs.ovpack viking://resources/imported
# openviking import ./docs.ovpack viking://resources/imported --force
# openviking import ./docs.ovpack viking://resources/imported --no-vectorize

# ============================================================================
# Output Format Comparison
# ============================================================================

section "Output: table (default)"
openviking ls viking://resources/

section "Output: json"
openviking -o json ls viking://resources/

section "Output: --json (for scripts)"
openviking --json ls viking://resources/

printf '\n\033[1m=== Done ===\033[0m\n'
