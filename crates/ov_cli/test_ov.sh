#!/bin/bash

# OpenViking CLI Comprehensive Test Script
# This script tests all major OpenViking CLI commands and scenarios
# Usage: ./test_ov_comprehensive.sh

set -e

OV_BIN="./target/release/ov"
TEST_DIR="/tmp/ov_test_$$"
mkdir -p "$TEST_DIR"

echo "=========================================="
echo "OpenViking CLI Comprehensive Test"
echo "=========================================="
echo ""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

print_test() {
    echo -e "${YELLOW}[TEST]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

print_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

# ============================================================================
# SCENARIO 1: System Commands
# Description: Check system health, status, and wait for async operations
# ============================================================================
print_test "Scenario 1: System Commands"
echo "Description: Check system health, status, and observer status"
echo ""

echo "1.1. System status..."
if $OV_BIN system status; then
    print_success "System status retrieved"
else
    print_error "System status failed"
fi
echo ""

echo "1.2. Observer queue status..."
if $OV_BIN observer queue; then
    print_success "Observer queue status retrieved"
else
    print_error "Observer queue status failed"
fi
echo ""

echo "1.3. Observer system status..."
if $OV_BIN observer system; then
    print_success "Observer system status retrieved"
else
    print_error "Observer system status failed"
fi
echo ""

# ============================================================================
# SCENARIO 2: Configuration Management
# Description: Show and validate OpenViking configuration
# ============================================================================
print_test "Scenario 2: Configuration Management"
echo "Description: Show and validate OpenViking configuration"
echo ""

echo "2.1. Show configuration..."
if $OV_BIN config show; then
    print_success "Configuration displayed"
else
    print_error "Configuration show failed"
fi
echo ""

echo "2.2. Validate configuration..."
if $OV_BIN config validate; then
    print_success "Configuration validated"
else
    print_error "Configuration validation failed"
fi
echo ""

# ============================================================================
# SCENARIO 3: Filesystem Operations
# Description: Test basic filesystem operations (ls, mkdir, tree, stat, mv, rm)
# ============================================================================
print_test "Scenario 3: Filesystem Operations"
echo "Description: Test basic filesystem operations"
echo ""

echo "3.1. List resources directory..."
if $OV_BIN ls "viking://resources"; then
    print_success "Resources directory listed"
else
    print_error "Resources directory listing failed"
fi
echo ""

echo "3.2. Create test directory..."
TEST_URI="viking://resources/test_cli_$$"
if $OV_BIN mkdir "$TEST_URI"; then
    print_success "Directory created: $TEST_URI"
else
    print_error "Directory creation failed"
fi
echo ""

echo "3.3. List created directory..."
if $OV_BIN ls "$TEST_URI"; then
    print_success "Directory listed"
else
    print_error "Directory listing failed"
fi
echo ""

echo "3.4. Get tree of resources..."
if $OV_BIN tree "viking://resources"; then
    print_success "Tree retrieved"
else
    print_error "Tree retrieval failed"
fi
echo ""

echo "3.5. Get stat of resources..."
if $OV_BIN stat "viking://resources"; then
    print_success "Stat retrieved"
else
    print_error "Stat retrieval failed"
fi
echo ""

echo "3.6. Rename directory..."
if ($OV_BIN mv "$TEST_URI" "viking://resources/test_cli_renamed_$$"); then
    print_success "Directory renamed"
else
    print_error "Directory rename failed"
fi
echo ""

# ============================================================================
# SCENARIO 4: Add Resource
# Description: Add a resource from URL or local file
# Note: --wait flag may cause hangs, use without wait for testing
# ============================================================================
print_test "Scenario 4: Add Resource"
echo "Description: Add a resource from URL (without --wait to avoid hangs)"
echo ""

echo "4.1. Add README from GitHub to resources scope..."
ADD_OUTPUT=$($OV_BIN add-resource "https://raw.githubusercontent.com/volcengine/OpenViking/main/README.md" --to "viking://resources/test_cli_$$" 2>&1)
if echo "$ADD_OUTPUT" | grep -q "root_uri\|success"; then
    print_success "Resource added successfully"
    README_URI=$(echo "$ADD_OUTPUT" | grep -o '"root_uri":"[^"]*"' | cut -d'"' -f4)
    if [ -z "$README_URI" ]; then
        README_URI=$(echo "$ADD_OUTPUT" | grep -o 'viking://[^[:space:]]*' | head -1)
    fi
    echo "Resource URI: $README_URI"
else
    print_error "Resource addition failed"
    echo "Output: $ADD_OUTPUT"
    README_URI=""
fi
echo ""

# ============================================================================
# SCENARIO 5: Search Operations
# Description: Test various search methods (find, search, grep, glob)
# ============================================================================
print_test "Scenario 5: Search Operations"
echo "Description: Test various search methods"
echo ""

echo "5.1. Semantic search (find)..."
if $OV_BIN find "what is OpenViking" --uri "viking://resources" --limit 5; then
    print_success "Find search completed"
else
    print_error "Find search failed"
fi
echo ""

echo "5.2. Context-aware search..."
if $OV_BIN search "context database" --uri "viking://resources" --limit 5; then
    print_success "Context-aware search completed"
else
    print_error "Context-aware search failed"
fi
echo ""

echo "5.3. Grep pattern search..."
if $OV_BIN grep "viking://resources" "OpenViking"; then
    print_success "Grep search completed"
else
    print_error "Grep search failed"
fi
echo ""

echo "5.4. Glob pattern search..."
if $OV_BIN glob "*.md" --uri "viking://resources"; then
    print_success "Glob search completed"
else
    print_error "Glob search failed"
fi
echo ""

# ============================================================================
# SCENARIO 6: Session Management
# Description: Test session lifecycle (create, list, get, add message, commit, delete)
# ============================================================================
print_test "Scenario 6: Session Management"
echo "Description: Test session lifecycle"
echo ""

echo "6.1. Create new session..."
SESSION_OUTPUT=$($OV_BIN session new 2>&1)
if echo "$SESSION_OUTPUT" | grep -q "session_id\|ok"; then
    print_success "Session created"
    SESSION_ID=$(echo "$SESSION_OUTPUT" | grep -o '"session_id":"[^"]*"' | cut -d'"' -f4)
    if [ -z "$SESSION_ID" ]; then
        SESSION_ID=$(echo "$SESSION_OUTPUT" | grep -o '[a-f0-9-]\{36\}' | head -1)
    fi
    echo "Session ID: $SESSION_ID"
else
    print_error "Session creation failed"
    echo "Output: $SESSION_OUTPUT"
    SESSION_ID=""
fi
echo ""

echo "6.2. List sessions..."
if $OV_BIN session list; then
    print_success "Sessions listed"
else
    print_error "Session listing failed"
fi
echo ""

if [ -n "$SESSION_ID" ]; then
    echo "6.3. Get session details..."
    if $OV_BIN session get "$SESSION_ID"; then
        print_success "Session details retrieved"
    else
        print_error "Session details retrieval failed"
    fi
    echo ""

    echo "6.4. Add message to session..."
    if $OV_BIN session add-message "$SESSION_ID" --role "user" --content "What is OpenViking?"; then
        print_success "Message added to session"
    else
        print_error "Message addition failed"
    fi
    echo ""

    echo "6.5. Commit session..."
    if $OV_BIN session commit "$SESSION_ID"; then
        print_success "Session committed"
    else
        print_error "Session commit failed"
    fi
    echo ""

    echo "6.6. Delete session..."
    if $OV_BIN session delete "$SESSION_ID"; then
        print_success "Session deleted"
    else
        print_error "Session deletion failed"
    fi
    echo ""
else
    print_error "Skipping session operations - no session ID available"
    echo ""
fi

# ============================================================================
# SCENARIO 7: Relations
# Description: Test relation management (link, unlink, relations)
# ============================================================================
print_test "Scenario 7: Relations"
echo "Description: Test relation management"
echo ""

if [ -n "$README_URI" ]; then
    echo "7.1. Create relation link..."
    if $OV_BIN link "$README_URI" "viking://resources/test" --reason "test relation"; then
        print_success "Relation link created"
    else
        print_error "Relation link creation failed"
    fi
    echo ""

    echo "7.2. List relations..."
    if $OV_BIN relations "$README_URI"; then
        print_success "Relations listed"
    else
        print_error "Relations listing failed"
    fi
    echo ""

    echo "7.3. Unlink relation..."
    if $OV_BIN unlink "$README_URI" "viking://resources/test"; then
        print_success "Relation unlinked"
    else
        print_error "Relation unlink failed"
    fi
    echo ""
else
    print_error "Skipping relation operations - no resource URI available"
    echo ""
fi

# ============================================================================
# SCENARIO 8: Pack Operations (Export/Import)
# Description: Test export and import of .ovpack files
# Note: Command syntax is 'ov export <URI> <TO>' (not --to)
# ============================================================================
print_test "Scenario 8: Pack Operations"
echo "Description: Test export and import of .ovpack files"
echo ""

if [ -n "$README_URI" ]; then
    PACK_FILE="$TEST_DIR/test.ovpack"
    PARENT_URI=$(dirname "$README_URI")

    echo "8.1. Export to .ovpack..."
    if $OV_BIN export "$PARENT_URI" "$PACK_FILE"; then
        print_success "Export completed"
    else
        print_error "Export failed"
    fi
    echo ""

    if [ -f "$PACK_FILE" ]; then
        echo "8.2. Import from .ovpack..."
        IMPORT_URI="viking://resources/test_import_$$"
        if $OV_BIN import "$PACK_FILE" "$IMPORT_URI" --force; then
            print_success "Import completed"
        else
            print_error "Import failed"
        fi
        echo ""
    else
        print_error "Skipping import - pack file not created"
        echo ""
    fi
else
    print_error "Skipping pack operations - no resource URI available"
    echo ""
fi

# ============================================================================
# SCENARIO 9: Version
# Description: Show CLI version
# ============================================================================
print_test "Scenario 9: Version"
echo "Description: Show CLI version"
echo ""

echo "9.1. Get version..."
if $OV_BIN version; then
    print_success "Version retrieved"
else
    print_error "Version retrieval failed"
fi
echo ""

# ============================================================================
# Summary
# ============================================================================
echo "=========================================="
echo "Test Script Completed"
echo "=========================================="
echo ""
echo "Test directory: $TEST_DIR"
echo "To clean up: rm -rf $TEST_DIR"
echo ""
