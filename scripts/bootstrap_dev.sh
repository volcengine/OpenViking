#!/bin/bash
################################################################################
# OpenViking Bootstrap Installer
# 
# Interactive script to install/build OpenViking components in parallel:
# - Python package (openviking)
# - Web-studio React application
# - ov CLI (Rust)
#
# Usage:
#   ./scripts/bootstrap_dev.sh
################################################################################

set -e

# ============================================================================
# Colors & Logging
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $@" >&2
}

log_ok() {
    echo -e "${GREEN}[OK]${NC} $@" >&2
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $@" >&2
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $@" >&2
}

# ============================================================================
# Utilities
# ============================================================================

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

get_root_dir() {
    cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

# ============================================================================
# Detector Functions
# ============================================================================

detect_python_cmd() {
    if command_exists uv; then
        echo "uv pip install"
    elif command_exists python3; then
        echo "python3 -m pip install"
    elif command_exists python; then
        echo "python -m pip install"
    else
        log_error "No Python/pip found"
        return 1
    fi
}

detect_npm_cmd() {
    local web_dir="${1}/web-studio"
    
    if [[ -f "$web_dir/pnpm-lock.yaml" ]] && command_exists pnpm; then
        echo "pnpm"
    elif [[ -f "$web_dir/yarn.lock" ]] && command_exists yarn; then
        echo "yarn"
    elif command_exists npm; then
        echo "npm"
    elif command_exists pnpm; then
        echo "pnpm"
    elif command_exists yarn; then
        echo "yarn"
    else
        log_error "No Node package manager found"
        return 1
    fi
}

# ============================================================================
# Menu Rendering
# ============================================================================

clear_screen() {
    clear || true
}

print_header() {
    cat << 'EOF'

╔════════════════════════════════════════════════════════════════╗
║         OpenViking Bootstrap Installer                        ║
╚════════════════════════════════════════════════════════════════╝

EOF
}

show_main_menu() {
    clear_screen
    print_header
    
    cat << 'EOF'
Select installation profile:

  [1] Full install: Python + web-studio + ov CLI (recommended)
  [2] Custom selection
  [3] Python package only
  [4] Web-studio only
  [5] ov CLI only
  [6] Exit

EOF
    read -p "Enter choice [1-6]: " -r MAIN_CHOICE
}

show_custom_menu() {
    clear_screen
    print_header
    
    local py_status="INSTALL"
    local py_extras_disp=""
    local web_status="INSTALL"
    local web_install_disp="with dependencies"
    local ov_status="INSTALL"
    
    [[ "$SKIP_PYTHON" == "1" ]] && py_status="SKIP"
    [[ "$SKIP_PYTHON" != "1" ]] && py_extras_disp=" (extras: $PYTHON_EXTRAS)"
    [[ "$SKIP_WEB" == "1" ]] && web_status="SKIP"
    [[ "$SKIP_WEB_INSTALL" == "1" ]] && web_install_disp="dependencies only, skip install"
    [[ "$SKIP_OV" == "1" ]] && ov_status="SKIP"
    
    cat << EOF
Custom Configuration:

  Components:
  ──────────────────────────────────────────────────
  [1] Python package       [$py_status]$py_extras_disp
  [2] Web-studio           [$web_status] $web_install_disp
  [3] ov CLI               [$ov_status]
  ──────────────────────────────────────────────────
  
  [4] Configure Python extras
  [5] Start installation
  [6] Back to main menu

EOF
    read -p "Enter choice [1-6]: " -r CUSTOM_CHOICE
}

show_extras_menu() {
    clear_screen
    print_header
    
    cat << EOF
Python Extras Selection:

  Current: $PYTHON_EXTRAS

  Presets:
  ──────────────────────────────────────────────────
  [1] bot-full (default, all bot features)
  [2] dev (mypy, ruff, setuptools_scm)
  [3] test (pytest, pytest-asyncio, etc.)
  [4] dev,test (dev + test)
  [5] all (comprehensive set)
  [6] custom (enter manually)
  [7] none (base only)
  [8] Back
  ──────────────────────────────────────────────────

EOF
    read -p "Enter choice [1-8]: " -r EXTRAS_CHOICE
}

# ============================================================================
# Installation Functions
# ============================================================================

run_python_install() {
    local pip_cmd
    pip_cmd=$(detect_python_cmd) || return 1
    
    local spec="."
    [[ -n "$PYTHON_EXTRAS" ]] && spec=".[${PYTHON_EXTRAS}]"
    
    log_info "Installing Python package: $spec"
    cd "$ROOT_DIR"
    
    # shellcheck disable=SC2086
    $pip_cmd -e "$spec" --force-reinstall || {
        log_error "Python installation failed"
        return 1
    }
    
    log_ok "Python installation completed"
}

run_web_install() {
    local npm_cmd
    npm_cmd=$(detect_npm_cmd "$ROOT_DIR") || return 1
    
    local web_dir="$ROOT_DIR/web-studio"
    
    log_info "Building web-studio (using: $npm_cmd)"
    
    if [[ "$SKIP_WEB_INSTALL" != "1" ]]; then
        log_info "Installing dependencies..."
        cd "$web_dir" && $npm_cmd install || {
            log_error "npm install failed"
            return 1
        }
    fi
    
    log_info "Running build..."
    cd "$web_dir" && $npm_cmd run build || {
        log_error "npm build failed"
        return 1
    }
    
    log_ok "Web-studio build completed"
}

run_ov_install() {
    if ! command_exists cargo; then
        log_error "Cargo not found. Install Rust from: https://rustup.rs/"
        return 1
    fi
    
    log_info "Installing ov CLI..."
    cd "$ROOT_DIR"
    
    cargo install --path crates/ov_cli --force || {
        log_error "ov CLI installation failed"
        return 1
    }
    
    log_ok "ov CLI installation completed"
}

# ============================================================================
# Main Execution
# ============================================================================

verify_prerequisites() {
    log_info "Checking prerequisites..."
    
    local missing=()
    
    if ! command_exists python3 && ! command_exists python; then
        missing+=("Python 3.10+")
    fi
    
    if [[ "$SKIP_WEB" != "1" ]]; then
        if ! command_exists npm && ! command_exists pnpm && ! command_exists yarn; then
            missing+=("Node package manager (npm/pnpm/yarn)")
        fi
    fi
    
    if [[ "$SKIP_OV" != "1" ]]; then
        if ! command_exists cargo; then
            missing+=("Rust/Cargo (for ov CLI)")
        fi
    fi
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo ""
        log_error "Missing dependencies:"
        for dep in "${missing[@]}"; do
            echo "  - $dep" >&2
        done
        echo "" >&2
        return 1
    fi
    
    log_ok "All prerequisites satisfied"
}

execute_stage1() {
    local any_work=0
    
    [[ "$SKIP_PYTHON" != "1" ]] && any_work=1
    [[ "$SKIP_WEB" != "1" ]] && any_work=1
    
    if [[ $any_work -eq 0 ]]; then
        return 0
    fi
    
    clear_screen
    print_header
    
    log_info "Stage 1: Parallel tasks (Python + web-studio)"
    
    # Background job PIDs
    local bg_pids=()
    
    # Python in background
    if [[ "$SKIP_PYTHON" != "1" ]]; then
        (run_python_install) &
        bg_pids+=($!)
    fi
    
    # Web in background
    if [[ "$SKIP_WEB" != "1" ]]; then
        (run_web_install) &
        bg_pids+=($!)
    fi
    
    # Wait for all background jobs
    local failed=0
    for pid in "${bg_pids[@]}"; do
        if ! wait "$pid"; then
            ((failed++)) || true
        fi
    done
    
    if [[ $failed -gt 0 ]]; then
        log_error "Stage 1 had $failed failure(s). Skipping ov CLI installation."
        return 1
    fi
    
    log_ok "Stage 1 completed"
    echo ""
}

execute_stage2() {
    if [[ "$SKIP_OV" == "1" ]]; then
        return 0
    fi
    
    log_info "Stage 2: Sequential task (ov CLI)"
    run_ov_install || return 1
    log_ok "Stage 2 completed"
    echo ""
}

show_completion() {
    clear_screen
    print_header
    
    log_ok "All installations completed!"
    
    echo ""
    echo "Next steps:"
    echo "  ─────────────────────────────────────────────────"
    
    if [[ "$SKIP_PYTHON" != "1" ]]; then
        echo "  • Verify Python: python -c 'import openviking'"
    fi
    
    if [[ "$SKIP_WEB" != "1" ]]; then
        echo "  • Start web-studio: cd web-studio && npm run dev"
    fi
    
    if [[ "$SKIP_OV" != "1" ]]; then
        echo "  • Check ov CLI: ov --help"
    fi
    
    echo "  • Docs: https://openviking.ai/docs"
    echo ""
}

# ============================================================================
# Menu Handlers
# ============================================================================

handle_main_menu() {
    case "$MAIN_CHOICE" in
        1)
            SKIP_PYTHON="0"
            SKIP_WEB="0"
            SKIP_OV="0"
            return 0
            ;;
        2)
            return 2  # go to custom menu
            ;;
        3)
            SKIP_PYTHON="0"
            SKIP_WEB="1"
            SKIP_OV="1"
            return 0
            ;;
        4)
            SKIP_PYTHON="1"
            SKIP_WEB="0"
            SKIP_OV="1"
            return 0
            ;;
        5)
            SKIP_PYTHON="1"
            SKIP_WEB="1"
            SKIP_OV="0"
            return 0
            ;;
        6)
            log_info "Exiting"
            exit 0
            ;;
        *)
            log_error "Invalid choice"
            sleep 1
            show_main_menu
            handle_main_menu
            return $?
            ;;
    esac
}

handle_custom_menu() {
    show_custom_menu
    
    case "$CUSTOM_CHOICE" in
        1)
            read -p "Install Python package? (Y/n): " -r resp
            [[ "$resp" =~ ^[nN]$ ]] && SKIP_PYTHON="1" || SKIP_PYTHON="0"
            handle_custom_menu
            ;;
        2)
            read -p "Build web-studio? (Y/n): " -r resp
            if [[ ! "$resp" =~ ^[nN]$ ]]; then
                SKIP_WEB="0"
                read -p "Skip dependency installation? (y/N): " -r resp2
                [[ "$resp2" =~ ^[yY]$ ]] && SKIP_WEB_INSTALL="1" || SKIP_WEB_INSTALL="0"
            else
                SKIP_WEB="1"
            fi
            handle_custom_menu
            ;;
        3)
            read -p "Install ov CLI? (Y/n): " -r resp
            [[ "$resp" =~ ^[nN]$ ]] && SKIP_OV="1" || SKIP_OV="0"
            handle_custom_menu
            ;;
        4)
            handle_extras_menu
            handle_custom_menu
            ;;
        5)
            return 0  # proceed to install
            ;;
        6)
            return 1  # back to main menu
            ;;
        *)
            log_error "Invalid choice"
            sleep 1
            handle_custom_menu
            ;;
    esac
}

handle_extras_menu() {
    show_extras_menu
    
    case "$EXTRAS_CHOICE" in
        1) PYTHON_EXTRAS="bot-full" ;;
        2) PYTHON_EXTRAS="dev" ;;
        3) PYTHON_EXTRAS="test" ;;
        4) PYTHON_EXTRAS="dev,test" ;;
        5) PYTHON_EXTRAS="bot-full,dev,test,doc,eval,gemini,gemini-async,ocr,benchmark" ;;
        6)
            read -p "Enter extras (comma-separated): " -r custom
            if [[ ! "$custom" =~ ^[a-zA-Z0-9,_-]+$ ]]; then
                log_error "Invalid characters in extras. Only letters, numbers, underscores, hyphens, and commas allowed."
                return 1
            fi
            PYTHON_EXTRAS="$custom"
            ;;
        7) PYTHON_EXTRAS="" ;;
        8) return ;;
        *)
            log_error "Invalid choice"
            sleep 1
            handle_extras_menu
            ;;
    esac
}

# ============================================================================
# Main Loop
# ============================================================================

main() {
    ROOT_DIR=$(get_root_dir)
    
    # Default values
    SKIP_PYTHON="0"
    SKIP_WEB="0"
    SKIP_OV="0"
    SKIP_WEB_INSTALL="0"
    PYTHON_EXTRAS="bot-full"
    
    # Check if terminal
    if [[ ! -t 0 ]]; then
        log_error "This script requires an interactive terminal"
        exit 1
    fi
    
    # Verify prerequisites early
    verify_prerequisites || exit 1
    
    # Main loop
    while true; do
        show_main_menu
        
        handle_main_menu
        ret=$?
        
        if [[ $ret -eq 2 ]]; then
            # Custom menu
            handle_custom_menu
            ret=$?
            [[ $ret -eq 1 ]] && continue  # back to main
        fi
        
        if [[ $ret -eq 0 ]]; then
            break  # proceed to install
        fi
    done
    
    # Execute installation
    execute_stage1 || {
        log_error "Installation failed at stage 1"
        exit 1
    }
    
    execute_stage2 || {
        log_error "Installation failed at stage 2"
        exit 1
    }
    
    show_completion
}

# ============================================================================
# Entry Point
# ============================================================================

main "$@"
