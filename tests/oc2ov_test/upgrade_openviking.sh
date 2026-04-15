#!/bin/bash

set -e

UPGRADE_SUCCESS=false

cleanup_on_exit() {
    if [ "$UPGRADE_SUCCESS" = true ]; then
        return 0
    fi
    log ""
    log "========================================="
    log "Cleanup: Script interrupted or failed"
    log "========================================="
    
    if command -v openclaw &> /dev/null; then
        log "Stopping OpenClaw gateway..."
        openclaw gateway stop 2>&1 | tee -a "$LOG_FILE" || true
        sleep 2
    fi
    
    if ps aux | grep -v grep | grep -q "[o]penclaw"; then
        log "Killing remaining OpenClaw processes..."
        pkill -9 -f "openclaw" || true
        sleep 1
    fi
    
    if [ -d "/root/project/OpenViking_backup" ]; then
        log "Removing backup directory..."
        rm -rf "/root/project/OpenViking_backup" || true
    fi
    
    if [ -d "/root/project/OpenViking/build" ]; then
        log "Removing build artifacts..."
        rm -rf "/root/project/OpenViking/build" || true
    fi
    
    if [ -d "/root/project/OpenViking/tests/oc2ov_test/venv" ]; then
        log "Removing test virtual environment..."
        rm -rf "/root/project/OpenViking/tests/oc2ov_test/venv" || true
    fi
    
    log "Cleanup completed"
    log "========================================="
}

trap 'cleanup_on_exit' INT TERM EXIT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "$GITHUB_WORKSPACE" ]; then
    PROJECT_DIR="$GITHUB_WORKSPACE"
else
    PROJECT_DIR="/root/project/OpenViking"
fi
BACKUP_DIR="/root/project/OpenViking_backup"
LOG_FILE="/tmp/openviking_upgrade.log"
MAX_RETRIES=3
RETRY_DELAY=10
VENV_DIR="/root/.openviking/venv"

log() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] $1" | tee -a "$LOG_FILE"
}

log "========================================="
log "OpenViking Upgrade Script Started"
log "========================================="

log "[0/8] Checking disk space..."
AVAILABLE_GB=$(df -BG / | tail -1 | awk '{print $4}' | tr -d 'G')
if [ "$AVAILABLE_GB" -lt 3 ]; then
    log "⚠️  Low disk space: ${AVAILABLE_GB}GB available, cleaning up caches..."
    rm -rf /root/.cache/uv 2>/dev/null || true
    rm -rf /root/.cache/go-build 2>/dev/null || true
    rm -rf /root/.npm/_cacache 2>/dev/null || true
    rm -rf /root/.cargo/registry/cache 2>/dev/null || true
    rm -rf /root/.cargo/registry/src 2>/dev/null || true
    rm -rf "$PROJECT_DIR/target" 2>/dev/null || true
    rm -rf /tmp/openviking*.log /tmp/openclaw* /tmp/npm-* 2>/dev/null || true
    pip cache purge 2>/dev/null || true
    npm cache clean --force 2>/dev/null || true
    AVAILABLE_GB=$(df -BG / | tail -1 | awk '{print $4}' | tr -d 'G')
    log "After cleanup: ${AVAILABLE_GB}GB available"
fi
log "Disk space: ${AVAILABLE_GB}GB available"

log "[1/8] Checking prerequisites and activating virtual environment..."

# Check if OpenViking virtual environment exists
if [ -d "$VENV_DIR" ]; then
    log "Found OpenViking virtual environment at: $VENV_DIR"
    
    # Activate virtual environment
    if [ -f "$VENV_DIR/bin/activate" ]; then
        source "$VENV_DIR/bin/activate"
        log "✅ Virtual environment activated"
        
        # Verify Python is from venv
        PYTHON_PATH=$(which python3 || which python)
        log "Using Python: $PYTHON_PATH"
        
        if [[ "$PYTHON_PATH" != *"$VENV_DIR"* ]]; then
            log "⚠️  Warning: Python is not from the virtual environment"
        fi
    else
        log "⚠️  Virtual environment found but activate script missing"
    fi
else
    log "⚠️  OpenViking virtual environment not found at $VENV_DIR"
    log "Using system Python"
fi

if [ ! -d "$PROJECT_DIR" ]; then
    log "ERROR: OpenViking directory not found: $PROJECT_DIR"
    exit 1
fi

cd "$PROJECT_DIR" || exit 1

log "[2/8] Backing up current version..."
if [ -n "$GITHUB_WORKSPACE" ]; then
    log "CI environment detected, skipping backup"
else
    if [ -d "$BACKUP_DIR" ]; then
        rm -rf "$BACKUP_DIR"
    fi
    cp -r "$PROJECT_DIR" "$BACKUP_DIR"
    log "Backup created at: $BACKUP_DIR"
fi

log "[3/8] Configuring Git remote and pulling latest code..."
if [ -n "$GITHUB_WORKSPACE" ]; then
    log "CI environment detected, skipping git fetch/reset (checkout already done)"
    CURRENT_COMMIT=$(git rev-parse HEAD)
    log "Current commit: $CURRENT_COMMIT"
else
CURRENT_REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
log "Current remote URL: $CURRENT_REMOTE"

if [[ "$CURRENT_REMOTE" == *"github.com"* ]] && [[ "$CURRENT_REMOTE" != *"git@github.com"* ]]; then
    log "Switching from HTTPS to SSH for GitHub access..."
    git remote set-url origin git@github.com:volcengine/OpenViking.git
    log "✅ Remote URL updated to: git@github.com:volcengine/OpenViking.git"
elif [[ "$CURRENT_REMOTE" != *"github.com"* ]]; then
    log "Setting correct remote URL..."
    git remote set-url origin git@github.com:volcengine/OpenViking.git
    log "✅ Remote URL set to: git@github.com:volcengine/OpenViking.git"
fi

git fetch origin
git reset --hard origin/main
git clean -fd
CURRENT_COMMIT=$(git rev-parse HEAD)
log "Current commit: $CURRENT_COMMIT"
fi

log "[4/8] Checking OpenViking installation mode..."

# Use python (from venv if activated) instead of python3
INSTALL_MODE=$(python -c "import openviking; import os; path = openviking.__file__; print('dev' if 'site-packages' not in path else 'site-packages')" 2>/dev/null || echo "not_installed")
log "Current installation mode: $INSTALL_MODE"

if [ "$INSTALL_MODE" = "site-packages" ]; then
    log "⚠️  OpenViking is installed in site-packages mode"
    log "Uninstalling to switch to development mode..."
    pip uninstall -y openviking 2>&1 | tee -a "$LOG_FILE" || true
    log "✅ Uninstalled site-packages version"
fi

log "[5/8] Configuring Go proxy for China network..."
export GOPROXY=https://goproxy.cn,direct
export GOSUMDB=off
log "✅ Go proxy configured: $GOPROXY"

log "[5.5/8] Checking Rust toolchain..."
RUST_OK=false

if command -v rustc &> /dev/null; then
    RUST_VERSION=$(rustc --version 2>/dev/null | awk '{print $2}' || echo "")
    if [ -n "$RUST_VERSION" ]; then
        log "✅ Rust is already installed and working: $RUST_VERSION"
        RUST_OK=true
    fi
fi

if [ "$RUST_OK" = false ]; then
    log "Rust is not working properly, attempting to fix..."
    
    if command -v rustup &> /dev/null; then
        log "Found rustup, trying to install stable toolchain..."
        
        if rustup install stable 2>&1 | tee -a "$LOG_FILE"; then
            log "✅ Stable toolchain installed"
            
            if rustup default stable 2>&1 | tee -a "$LOG_FILE"; then
                log "✅ Stable set as default"
                RUST_OK=true
            fi
        else
            log "⚠️  Failed to install Rust toolchain via rustup"
            log "Please install Rust manually on the ECS node:"
            log "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
            log "  source \$HOME/.cargo/env"
            log "  rustup install stable"
            log "  rustup default stable"
        fi
    else
        log "⚠️  rustup not found"
        log "Please install Rust manually on the ECS node:"
        log "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
        log "  source \$HOME/.cargo/env"
        log "  rustup install stable"
        log "  rustup default stable"
    fi
fi

if [ "$RUST_OK" = true ]; then
    RUST_VERSION=$(rustc --version 2>/dev/null | awk '{print $2}' || echo "unknown")
    log "Rust version: $RUST_VERSION"
else
    log "⚠️  Rust toolchain setup failed, build may fail"
fi

log "[5.6/8] Checking Python build dependencies..."

# Check if setuptools-scm is already installed
if python -c "import setuptools_scm" 2>/dev/null; then
    log "✅ setuptools-scm is already installed"
else
    log "Installing setuptools-scm and other build tools..."
    
    if ! pip install --upgrade setuptools setuptools-scm wheel cmake build 2>&1 | tee -a "$LOG_FILE"; then
        log "Standard pip install failed, trying with --break-system-packages..."
        if pip install --break-system-packages --upgrade setuptools setuptools-scm wheel cmake build 2>&1 | tee -a "$LOG_FILE"; then
            log "✅ Build dependencies installed successfully with --break-system-packages"
        else
            log "⚠️  Failed to install some build dependencies, continuing anyway..."
        fi
    else
        log "✅ Build dependencies installed successfully"
    fi
fi

log "[6/8] Cleaning previous build artifacts..."
make clean 2>/dev/null || true
log "Clean completed"

log "[7/8] Building and installing OpenViking in development mode..."
export OV_SKIP_OV_BUILD=1
export OV_SKIP_OV_BUILD=1
log "OV_SKIP_OV_BUILD=1 set, skipping ov CLI Rust build (ragfs build still needed for server)"

mkdir -p "$PROJECT_DIR/openviking/bin"
touch "$PROJECT_DIR/openviking/bin/ov"
chmod +x "$PROJECT_DIR/openviking/bin/ov"
log "Created dummy ov binary so OV_SKIP_OV_BUILD won't fallback to cargo"

if [ -n "$GITHUB_WORKSPACE" ]; then
    if [ -z "$SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING" ]; then
        PRETEND_VERSION=$(python -c "
import re, subprocess
try:
    desc = subprocess.check_output(['git', 'describe', '--tags', '--always'], stderr=subprocess.DEVNULL).decode().strip()
    m = re.match(r'^(?:v)?([0-9]+(?:\.[0-9]+)*)', desc)
    if m:
        base = m.group(1)
        print(base + '.dev0')
    else:
        print('0.0.0.dev0')
except Exception:
    print('0.0.0.dev0')
" 2>/dev/null || echo "0.0.0.dev0")
        export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING="$PRETEND_VERSION"
        log "CI shallow clone detected, using pretend version: $PRETEND_VERSION"
    fi
fi
BUILD_SUCCESS=false
for i in $(seq 1 $MAX_RETRIES); do
    log "Build attempt $i/$MAX_RETRIES..."
    
    if python setup.py build_ext --inplace 2>&1 | tee -a "$LOG_FILE"; then
        log "build_ext completed, installing dependencies..."
        UV_EXTRA_ARGS=""
        if [ -n "$GITHUB_WORKSPACE" ]; then
            UV_EXTRA_ARGS="--index-url https://pypi.tuna.tsinghua.edu.cn/simple"
            log "Using Tsinghua PyPI mirror for faster downloads in CI"
        fi
        if command -v uv &> /dev/null && uv pip --help &> /dev/null; then
            if uv pip install -e . --no-build-isolation $UV_EXTRA_ARGS 2>&1 | tee -a "$LOG_FILE"; then
                BUILD_SUCCESS=true
            fi
        else
            PIP_EXTRA_ARGS=""
            if [ -n "$GITHUB_WORKSPACE" ]; then
                PIP_EXTRA_ARGS="-i https://pypi.tuna.tsinghua.edu.cn/simple"
            fi
            if pip install -e . --no-build-isolation $PIP_EXTRA_ARGS 2>&1 | tee -a "$LOG_FILE"; then
                BUILD_SUCCESS=true
            fi
        fi
        
        if [ "$BUILD_SUCCESS" = true ]; then
            log "Build completed successfully on attempt $i"
            
            INSTALL_PATH=$(python -c "import openviking; print(openviking.__file__)" 2>/dev/null || echo "unknown")
            log "OpenViking installed at: $INSTALL_PATH"
            
            if [[ "$INSTALL_PATH" == *"$PROJECT_DIR"* ]]; then
                log "✅ Confirmed: Using development mode (source code directory)"
            else
                log "⚠️  Warning: Not using source code directory"
                log "Expected path to contain: $PROJECT_DIR"
                log "Actual path: $INSTALL_PATH"
            fi
            break
        fi
    else
        if [ $i -lt $MAX_RETRIES ]; then
            log "Build failed on attempt $i, retrying in ${RETRY_DELAY}s..."
            sleep $RETRY_DELAY
            make clean 2>/dev/null || true
        fi
    fi
done

if [ "$BUILD_SUCCESS" = false ]; then
    log "ERROR: Build failed after $MAX_RETRIES attempts"
    log "Restoring backup..."
    rm -rf "$PROJECT_DIR"
    mv "$BACKUP_DIR" "$PROJECT_DIR"
    log "Backup restored"
    exit 1
fi

log "[7.5/8] Installing OpenClaw openviking plugin dependencies..."
PLUGIN_DIR="$PROJECT_DIR/examples/openclaw-plugin"

if [ -d "$PLUGIN_DIR" ]; then
    log "Plugin directory: $PLUGIN_DIR"
    if command -v npm &> /dev/null; then
        cd "$PLUGIN_DIR"
        if npm install --omit=dev 2>&1 | tee -a "$LOG_FILE"; then
            log "✅ Plugin npm dependencies installed"
        else
            log "⚠️  WARNING: npm install --omit=dev failed, continuing anyway"
        fi
        cd "$PROJECT_DIR"
    else
        log "⚠️  npm command not found, skipping plugin dependency install"
    fi
else
    log "⚠️  Plugin directory not found: $PLUGIN_DIR, skipping"
fi

log "[8/8] Restarting OpenClaw service to load latest OpenViking..."

# Load OpenClaw environment variables
if [ -f ~/.openclaw/openviking.env ]; then
    source ~/.openclaw/openviking.env
else
    log "WARNING: ~/.openclaw/openviking.env not found"
fi

# Step 1: Stop OpenClaw gateway completely
log "Step 1: Stopping OpenClaw gateway..."
if openclaw gateway stop 2>&1 | tee -a "$LOG_FILE"; then
    log "✅ OpenClaw gateway stopped gracefully"
else
    log "⚠️  Failed to stop gracefully, attempting force stop..."
fi

sleep 3

# Verify gateway is stopped - kill ALL openclaw processes to prevent multi-instance conflicts
REMAINING_PIDS=$(ps aux | grep -v grep | grep "[o]penclaw" | awk '{print $2}' || true)
if [ -n "$REMAINING_PIDS" ]; then
    REMAINING_COUNT=$(echo "$REMAINING_PIDS" | wc -l)
    log "⚠️  $REMAINING_COUNT OpenClaw process(es) still running, killing forcefully..."
    log "PIDs: $REMAINING_PIDS"
    pkill -9 -f "openclaw" || true
    sleep 2

    STILL_RUNNING=$(ps aux | grep -v grep | grep "[o]penclaw" | awk '{print $2}' || true)
    if [ -n "$STILL_RUNNING" ]; then
        log "⚠️  WARNING: Some processes could not be killed: $STILL_RUNNING"
    else
        log "✅ All OpenClaw processes terminated"
    fi
fi
log "✅ OpenClaw gateway stopped"

# Step 2: Clean up OpenClaw stale state (lock files, session locks, cache)
log "Step 2: Pre-start cleanup for OpenClaw..."

LOCK_COUNT=0
SESSION_LOCK_COUNT=0

if [ -d ~/.openclaw ]; then
    LOCK_COUNT=$(find ~/.openclaw -name "*.lock" -type f 2>/dev/null | wc -l)
    if [ "$LOCK_COUNT" -gt 0 ]; then
        log "Found $LOCK_COUNT stale lock file(s), removing..."
        find ~/.openclaw -name "*.lock" -type f -delete 2>/dev/null || true
        log "✅ Stale lock files removed"
    else
        log "No stale lock files found"
    fi

    SESSION_LOCK_COUNT=$(find ~/.openclaw/agents -name "*.jsonl.lock" -type f 2>/dev/null | wc -l)
    if [ "$SESSION_LOCK_COUNT" -gt 0 ]; then
        log "Found $SESSION_LOCK_COUNT stale session lock(s), removing..."
        find ~/.openclaw/agents -name "*.jsonl.lock" -type f -delete 2>/dev/null || true
        log "✅ Stale session locks removed"
    fi
else
    log "~/.openclaw directory not found, skipping lock cleanup"
fi

rm -rf ~/.openclaw/cache/* 2>/dev/null || true
rm -rf ~/.openclaw/tmp/* 2>/dev/null || true
log "✅ Pre-start cleanup completed (locks: $LOCK_COUNT, session locks: $SESSION_LOCK_COUNT, cache cleared)"

# Step 3: Verify OpenViking installation path before starting
log "Step 3: Verifying OpenViking installation path..."
OV_PATH=$(python -c "import openviking; print(openviking.__file__)" 2>/dev/null || echo "unknown")
log "OpenViking path: $OV_PATH"

if [[ "$OV_PATH" == *"$PROJECT_DIR"* ]]; then
    log "✅ Confirmed: OpenViking is in development mode"
else
    log "⚠️  WARNING: OpenViking is not in development mode!"
    log "Expected path to contain: $PROJECT_DIR"
    log "Actual path: $OV_PATH"
fi

# Step 3.5: Ensure OpenViking server is running on port 1933
log "Step 3.5: Ensuring OpenViking server is running..."
OV_SERVER_RUNNING=false

if command -v ss &> /dev/null; then
    if ss -tuln 2>/dev/null | grep -q ":1933 "; then
        OV_SERVER_RUNNING=true
        log "✅ OpenViking server is already listening on port 1933"
    fi
elif command -v netstat &> /dev/null; then
    if netstat -tuln 2>/dev/null | grep -q ":1933 "; then
        OV_SERVER_RUNNING=true
        log "✅ OpenViking server is already listening on port 1933"
    fi
fi

if [ "$OV_SERVER_RUNNING" = false ]; then
    log "OpenViking server not running, starting it..."

    pkill -f "openviking.server.bootstrap" 2>/dev/null || true
    sleep 1

    OV_CONF=""
    for conf_candidate in "$PROJECT_DIR/ov.conf.temp" "$PROJECT_DIR/ov.conf" "$HOME/.openviking/ov.conf"; do
        if [ -f "$conf_candidate" ]; then
            OV_CONF="$conf_candidate"
            break
        fi
    done

    if [ -n "$OV_CONF" ]; then
        log "Cleaning unknown config fields from: $OV_CONF"
        python -c "
import json, sys
try:
    with open('$OV_CONF') as f:
        cfg = json.load(f)
    changed = False
    for key in ['port', 'log_level', 'retry_times', 'mode']:
        if cfg.get('storage', {}).get('agfs', {}).pop(key, None) is not None:
            changed = True
    for section in ['embedding', 'vlm']:
        if section in cfg and 'dense' in cfg.get(section, {}):
            val = cfg[section]['dense'].get('api_base', '')
            cleaned = val.strip().strip('\`').strip()
            if cleaned != val:
                cfg[section]['dense']['api_base'] = cleaned
                changed = True
        elif section in cfg:
            val = cfg[section].get('api_base', '')
            cleaned = val.strip().strip('\`').strip()
            if cleaned != val:
                cfg[section]['api_base'] = cleaned
                changed = True
    if changed:
        with open('$OV_CONF', 'w') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print('Config cleaned')
    else:
        print('Config OK, no changes needed')
except Exception as e:
    print(f'Config cleanup error: {e}', file=sys.stderr)
" 2>&1 | tee -a "$LOG_FILE"
    fi

    OV_PYTHON=$(command -v python 2>/dev/null || echo "python")
    log "Using Python: $OV_PYTHON ($($OV_PYTHON --version 2>&1))"

    > /tmp/openviking.log

    if [ -n "$OV_CONF" ]; then
        nohup $OV_PYTHON -m openviking.server.bootstrap --config "$OV_CONF" > /tmp/openviking.log 2>&1 &
        OV_SERVER_PID=$!
        log "Started OpenViking server with config: $OV_CONF (PID: $OV_SERVER_PID)"
    else
        nohup $OV_PYTHON -m openviking.server.bootstrap > /tmp/openviking.log 2>&1 &
        OV_SERVER_PID=$!
        log "Started OpenViking server without explicit config (PID: $OV_SERVER_PID)"
    fi

    for i in $(seq 1 20); do
        sleep 3
        if ! kill -0 $OV_SERVER_PID 2>/dev/null; then
            log "⚠️  OpenViking server process (PID: $OV_SERVER_PID) exited prematurely after ${i}x3s"
            break
        fi
        if command -v ss &> /dev/null; then
            if ss -tuln 2>/dev/null | grep -q ":1933 "; then
                OV_SERVER_RUNNING=true
                log "✅ OpenViking server is listening on port 1933 (after ${i}x3s)"
                break
            fi
        elif command -v netstat &> /dev/null; then
            if netstat -tuln 2>/dev/null | grep -q ":1933 "; then
                OV_SERVER_RUNNING=true
                log "✅ OpenViking server is listening on port 1933 (after ${i}x3s)"
                break
            fi
        fi
    done

    if [ "$OV_SERVER_RUNNING" = false ]; then
        log "⚠️  WARNING: OpenViking server failed to start on port 1933"
        log "   Server log:"
        cat /tmp/openviking.log 2>/dev/null | tee -a "$LOG_FILE" || true
    fi
fi

# Step 4: Start OpenClaw gateway
RESTART_SUCCESS=false
for i in $(seq 1 $MAX_RETRIES); do
    log "Step 4: Starting OpenClaw gateway (attempt $i/$MAX_RETRIES)..."
    
    if openclaw gateway start 2>&1 | tee -a "$LOG_FILE"; then
        sleep 8
        
        GATEWAY_RUNNING=false
        
        # Check if gateway is running (multiple methods)
        if command -v netstat &> /dev/null; then
            if netstat -tuln 2>/dev/null | grep -q ":18789 "; then
                log "✅ Gateway port 18789 is listening"
                GATEWAY_RUNNING=true
            fi
        elif command -v ss &> /dev/null; then
            if ss -tuln 2>/dev/null | grep -q ":18789 "; then
                log "✅ Gateway port 18789 is listening"
                GATEWAY_RUNNING=true
            fi
        fi
        
        if [ "$GATEWAY_RUNNING" = false ]; then
            if ps aux | grep -v grep | grep -q "[o]penclaw"; then
                log "✅ OpenClaw process is running"
                GATEWAY_RUNNING=true
            fi
        fi
        
        if [ "$GATEWAY_RUNNING" = false ]; then
            if command -v curl &> /dev/null; then
                if curl -s -o /dev/null -w "%{http_code}" http://localhost:18789/health 2>/dev/null | grep -q "200\|404"; then
                    log "✅ Gateway HTTP endpoint is responding"
                    GATEWAY_RUNNING=true
                fi
            fi
        fi
        
        if [ "$GATEWAY_RUNNING" = true ]; then
            RESTART_SUCCESS=true
            log "OpenClaw gateway started successfully on attempt $i"
            break
        else
            log "Gateway not running after start, retrying..."
            sleep $RETRY_DELAY
        fi
    else
        if [ $i -lt $MAX_RETRIES ]; then
            log "Start failed on attempt $i, retrying in ${RETRY_DELAY}s..."
            sleep $RETRY_DELAY
        fi
    fi
done

if [ "$RESTART_SUCCESS" = false ]; then
    log "⚠️  WARNING: Failed to verify OpenClaw gateway status after $MAX_RETRIES attempts"
    log "This may be normal in container/non-systemd environments"
    log "Please manually verify OpenClaw is running: ps aux | grep openclaw"
fi

# Step 5: Verify OpenViking is correctly loaded by OpenClaw
log "Step 5: Verifying OpenViking is loaded by OpenClaw..."
sleep 3

# Check OpenClaw logs for OpenViking registration
if [ -f "/tmp/openclaw/openclaw-$(date +%Y-%m-%d).log" ]; then
    OV_LOADED=$(grep -i "openviking: registered context-engine" /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log | tail -1)
    if [ -n "$OV_LOADED" ]; then
        log "✅ OpenViking is successfully loaded by OpenClaw"
        log "   $OV_LOADED"
    else
        log "⚠️  WARNING: Could not verify OpenViking registration in logs"
        log "   Check logs manually: tail -f /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log | grep openviking"
    fi
else
    log "⚠️  WARNING: OpenClaw log file not found"
fi

log ""
log "========================================="
log "OpenViking Upgrade Completed"
log "========================================="
log "Commit: $CURRENT_COMMIT"
OPENVIKING_VERSION=$(python -c "import openviking; print(openviking.__version__)" 2>/dev/null || echo "unknown")
log "OpenViking version: $OPENVIKING_VERSION"
OPENCLAW_VERSION=$(openclaw --version 2>/dev/null || echo "unknown")
log "OpenClaw version: $OPENCLAW_VERSION"
log "Backup: $BACKUP_DIR"

UPGRADE_SUCCESS=true

exit 0
