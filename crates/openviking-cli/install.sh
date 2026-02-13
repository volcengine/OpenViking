#!/bin/bash
set -e

# OpenViking CLI Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/<TAG>/crates/openviking-cli/install.sh | bash
# Example: curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/cli-0.1.0/crates/openviking-cli/install.sh | bash
# Skip checksum: SKIP_CHECKSUM=1 curl -fsSL ... | bash

REPO="volcengine/OpenViking"
BINARY_NAME="ov"
INSTALL_DIR="/usr/local/bin"
SKIP_CHECKSUM="${SKIP_CHECKSUM:-0}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Detect platform and architecture
detect_platform() {
    case "$(uname -s)" in
        Linux*)
            OS="linux"
            ;;
        Darwin*)
            OS="macos"
            ;;
        CYGWIN*|MINGW*|MSYS*)
            OS="windows"
            ;;
        *)
            error "Unsupported operating system: $(uname -s)"
            ;;
    esac

    case "$(uname -m)" in
        x86_64|amd64)
            ARCH="x86_64"
            ;;
        arm64|aarch64)
            ARCH="aarch64"
            ;;
        *)
            error "Unsupported architecture: $(uname -m)"
            ;;
    esac

    ARTIFACT_NAME="${BINARY_NAME}-${OS}-${ARCH}"
    if [[ "$OS" == "windows" ]]; then
        ARTIFACT_NAME="${ARTIFACT_NAME}.exe"
        ARCHIVE_EXT="zip"
    else
        ARCHIVE_EXT="tar.gz"
    fi
}

# Get latest release info
get_latest_release() {
    info "Getting latest release information..."
    LATEST_RELEASE=$(curl -s "https://api.github.com/repos/${REPO}/releases/latest")
    TAG_NAME=$(echo "$LATEST_RELEASE" | grep '"tag_name"' | sed -E 's/.*"tag_name": "([^"]+)".*/\1/')
    
    if [[ -z "$TAG_NAME" ]]; then
        error "Could not determine latest release version"
    fi
    
    info "Latest version: $TAG_NAME"
    DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${TAG_NAME}/${ARTIFACT_NAME}.${ARCHIVE_EXT}"
    CHECKSUM_URL="https://github.com/${REPO}/releases/download/${TAG_NAME}/${ARTIFACT_NAME}.${ARCHIVE_EXT}.sha256"
}

# Download and extract binary
download_binary() {
    info "Downloading ${ARTIFACT_NAME}.${ARCHIVE_EXT}..."
    TEMP_DIR=$(mktemp -d)
    ARCHIVE_FILE="$TEMP_DIR/${ARTIFACT_NAME}.${ARCHIVE_EXT}"
    CHECKSUM_FILE="$TEMP_DIR/${ARTIFACT_NAME}.${ARCHIVE_EXT}.sha256"

    # Download archive
    if ! curl -sSL -o "$ARCHIVE_FILE" "$DOWNLOAD_URL"; then
        error "Failed to download from $DOWNLOAD_URL"
    fi

    # Download and verify checksum
    if [[ "$SKIP_CHECKSUM" == "1" ]]; then
        warn "Skipping checksum verification (SKIP_CHECKSUM=1)"
    elif ! curl -sSL -o "$CHECKSUM_FILE" "$CHECKSUM_URL"; then
        warn "Could not download checksum file, skipping verification"
    else
        info "Verifying checksum..."
        # Debug: show checksum file content
        info "Checksum file content:"
        cat "$CHECKSUM_FILE"

        if command -v sha256sum >/dev/null; then
            (cd "$TEMP_DIR" && sha256sum -c "${ARTIFACT_NAME}.${ARCHIVE_EXT}.sha256") || error "Checksum verification failed"
        elif command -v shasum >/dev/null; then
            (cd "$TEMP_DIR" && shasum -a 256 -c "${ARTIFACT_NAME}.${ARCHIVE_EXT}.sha256") || error "Checksum verification failed"
        else
            warn "No checksum utility found, skipping verification"
        fi
    fi

    # Extract archive
    info "Extracting archive..."
    if [[ "$ARCHIVE_EXT" == "tar.gz" ]]; then
        tar -xzf "$ARCHIVE_FILE" -C "$TEMP_DIR" || error "Failed to extract archive"
    elif [[ "$ARCHIVE_EXT" == "zip" ]]; then
        unzip -q "$ARCHIVE_FILE" -d "$TEMP_DIR" || error "Failed to extract archive"
    fi

    TEMP_FILE="$TEMP_DIR/$BINARY_NAME"
    if [[ "$OS" == "windows" ]]; then
        TEMP_FILE="${TEMP_FILE}.exe"
    fi

    if [[ ! -f "$TEMP_FILE" ]]; then
        error "Binary not found after extraction: $TEMP_FILE"
    fi

    info "Download and extraction successful"
}

# Install binary
install_binary() {
    info "Installing to $INSTALL_DIR/$BINARY_NAME..."
    
    # Check if install directory exists and is writable
    if [[ ! -d "$INSTALL_DIR" ]]; then
        error "Install directory $INSTALL_DIR does not exist"
    fi
    
    # Try to install
    if [[ -w "$INSTALL_DIR" ]]; then
        cp "$TEMP_FILE" "$INSTALL_DIR/$BINARY_NAME"
    else
        info "Requesting sudo privileges for installation..."
        sudo cp "$TEMP_FILE" "$INSTALL_DIR/$BINARY_NAME"
        sudo chmod +x "$INSTALL_DIR/$BINARY_NAME"
    fi
    
    # Make executable
    chmod +x "$INSTALL_DIR/$BINARY_NAME" 2>/dev/null || sudo chmod +x "$INSTALL_DIR/$BINARY_NAME"
    
    # Cleanup
    rm -rf "$TEMP_DIR"
}

# Verify installation
verify_installation() {
    info "Verifying installation..."
    if command -v "$BINARY_NAME" >/dev/null; then
        VERSION=$($BINARY_NAME --version)
        info "Successfully installed: $VERSION"
        info "Run '$BINARY_NAME --help' to get started"
    else
        error "Installation failed - $BINARY_NAME not found in PATH"
    fi
}

main() {
    info "OpenViking CLI Installer"
    detect_platform
    info "Detected platform: $OS ($ARCH)"
    get_latest_release
    download_binary
    install_binary
    verify_installation
    info "Installation complete! 🎉"
}

# Run main function
main "$@"