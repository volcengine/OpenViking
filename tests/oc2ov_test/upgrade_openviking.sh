#!/bin/bash

set -e

# Trap signals for cleanup on interruption
trap 'cleanup_on_exit' INT TERM EXIT

cleanup_on_exit() {
    log ""
    log "========================================="
    log "Cleanup: Script interrupted or failed"
    log "========================================="
    
    # Stop OpenClaw if running
    if command -v openclaw &> /dev/null; then
        log "Stopping OpenClaw gateway..."
        openclaw gateway stop 2>&1 | tee -a "$LOG_FILE" || true
        sleep 2
    fi
    
    # Kill any remaining OpenClaw processes
    if ps aux | grep -v grep | grep -q "[o]penclaw"; then
        log "Killing remaining OpenClaw processes..."
        pkill -9 -f "openclaw" || true
        sleep 1
    fi
    
    # Clean up backup if exists
    if [ -d "/root/project/OpenViking_backup" ]; then
        log "Removing backup directory..."
        rm -rf "/root/project/OpenViking_backup" || true
    fi
    
    # Clean up build artifacts
    if [ -d "/root/project/OpenViking/build" ]; then
        log "Removing build artifacts..."
        rm -rf "/root/project/OpenViking/build" || true
    fi
    
    # Clean up venv if created by tests
    if [ -d "/root/project/OpenViking/tests/oc2ov_test/venv" ]; then
        log "Removing test virtual environment..."
        rm -rf "/root/project/OpenViking/tests/oc2ov_test/venv" || true
    fi
    
    log "Cleanup completed"
    log "========================================="
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="/root/project/OpenViking"
BACKUP_DIR="/root/project/OpenViking_backup"
LOG_FILE="/var/log/openviking_upgrade.log"
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
if [ -d "$BACKUP_DIR" ]; then
    rm -rf "$BACKUP_DIR"
fi
cp -r "$PROJECT_DIR" "$BACKUP_DIR"
log "Backup created at: $BACKUP_DIR"

log "[3/8] Configuring Git remote and pulling latest code..."
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
BUILD_SUCCESS=false
for i in $(seq 1 $MAX_RETRIES); do
    log "Build attempt $i/$MAX_RETRIES..."
    
    if make build 2>&1 | tee -a "$LOG_FILE"; then
        BUILD_SUCCESS=true
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

# Verify gateway is stopped
if ps aux | grep -v grep | grep -q "[o]penclaw"; then
    log "⚠️  OpenClaw process still running, killing forcefully..."
    pkill -9 -f "openclaw" || true
    sleep 2
fi
log "✅ OpenClaw gateway stopped"

# Step 2: Clear OpenClaw cache to force reload Python packages
log "Step 2: Clearing OpenClaw cache..."
rm -rf ~/.openclaw/cache/* 2>/dev/null || true
rm -rf ~/.openclaw/tmp/* 2>/dev/null || true
log "✅ Cache cleared"

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
        nohup python -m openviking.server.bootstrap --config "$OV_CONF" > /tmp/openviking.log 2>&1 &
        log "Started OpenViking server with config: $OV_CONF (PID: $!)"
    else
        nohup python -m openviking.server.bootstrap > /tmp/openviking.log 2>&1 &
        log "Started OpenViking server without explicit config (PID: $!)"
    fi

    for i in $(seq 1 15); do
        sleep 2
        if command -v ss &> /dev/null; then
            if ss -tuln 2>/dev/null | grep -q ":1933 "; then
                OV_SERVER_RUNNING=true
                log "✅ OpenViking server is listening on port 1933 (after ${i}x2s)"
                break
            fi
        elif command -v netstat &> /dev/null; then
            if netstat -tuln 2>/dev/null | grep -q ":1933 "; then
                OV_SERVER_RUNNING=true
                log "✅ OpenViking server is listening on port 1933 (after ${i}x2s)"
                break
            fi
        fi
    done

    if [ "$OV_SERVER_RUNNING" = false ]; then
        log "⚠️  WARNING: OpenViking server failed to start on port 1933"
        log "   Check logs: tail -50 /tmp/openviking.log"
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

exit 0
