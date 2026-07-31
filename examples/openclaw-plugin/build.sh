#!/bin/bash

set -euo pipefail

trap 'echo "Build failed." >&2' ERR

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PACKAGE_VERSION=$(npm pkg get version | tr -d '"')
VERSION="${BUILD_VERSION:-$PACKAGE_VERSION}"
RELEASE_PATH="${BUILD_RELEASE_PATH:-latest}"
PACKAGE_NAME="openviking"
OUTPUT_DIR="$ROOT_DIR/output"
TGZ_RELATIVE_PATH="output/openviking.tgz"
INSTALL_RELATIVE_PATH="output/install.sh"
VOLCENGINE_INSTALL_RELATIVE_PATH="output/volcengine-install.sh"
TGZ_PATH="$ROOT_DIR/$TGZ_RELATIVE_PATH"
INSTALL_OUTPUT="$ROOT_DIR/$INSTALL_RELATIVE_PATH"
VOLCENGINE_INSTALL_OUTPUT="$ROOT_DIR/$VOLCENGINE_INSTALL_RELATIVE_PATH"
STAGING_DIR=""
TAR_CREATE_FLAGS=(--exclude='._*' --exclude='.DS_Store' -zcf)

supports_tar_flag() {
  local flag="$1"
  tar "$flag" -cf /dev/null --files-from /dev/null >/dev/null 2>&1
}

if supports_tar_flag "--no-xattrs"; then
  TAR_CREATE_FLAGS=(--no-xattrs "${TAR_CREATE_FLAGS[@]}")
fi
if supports_tar_flag "--disable-copyfile"; then
  TAR_CREATE_FLAGS=(--disable-copyfile "${TAR_CREATE_FLAGS[@]}")
fi

cleanup() {
  case "${STAGING_DIR:-}" in
    "$OUTPUT_DIR"/.package.*)
      if [ -d "$STAGING_DIR" ]; then
        rm -rf "$STAGING_DIR"
      fi
      ;;
  esac
}
trap cleanup EXIT

info() {
  echo "[openviking] $*"
}

require_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    echo "Required file is missing: $path" >&2
    exit 1
  fi
}

require_dir() {
  local path="$1"
  if [ ! -d "$path" ]; then
    echo "Required directory is missing: $path" >&2
    exit 1
  fi
}

copy_optional_file() {
  local path="$1"
  local target_dir="$2"
  if [ -f "$path" ]; then
    cp "$path" "$target_dir/"
  else
    echo "Optional file is missing, skipped: $path" >&2
  fi
}

info "Building OpenViking plugin package $VERSION"
rm -rf dist "$OUTPUT_DIR"

info "Installing dependencies"
npm install

info "Running checks"
npm run typecheck
npm test
npm run build

require_file "dist/index.js"
require_file "dist/commands/setup.js"
require_file "package.json"
require_file "openclaw.plugin.json"
require_file "install-manifest.json"
require_file "config/feature-gates.json"
require_dir "skills"
require_file "scripts/install.sh"
require_file "scripts/volcengine-openviking-install.sh"

mkdir -p "$OUTPUT_DIR"
STAGING_DIR=$(mktemp -d "$OUTPUT_DIR/.package.XXXXXX")
require_dir "$STAGING_DIR"
PACKAGE_DIR="$STAGING_DIR/$PACKAGE_NAME"
mkdir -p "$PACKAGE_DIR"

info "Staging package files"
cp package.json "$PACKAGE_DIR/"
BUILD_VERSION="$VERSION" node -e 'const fs=require("fs"); const path=process.argv[1]; const pkg=JSON.parse(fs.readFileSync(path,"utf8")); pkg.version = process.env.BUILD_VERSION; fs.writeFileSync(path, `${JSON.stringify(pkg, null, 2)}\n`);' "$PACKAGE_DIR/package.json"
cp openclaw.plugin.json "$PACKAGE_DIR/"
cp install-manifest.json "$PACKAGE_DIR/"
cp -R config "$PACKAGE_DIR/"
cp -R dist "$PACKAGE_DIR/"
cp -R skills "$PACKAGE_DIR/"
copy_optional_file "README.md" "$PACKAGE_DIR"
copy_optional_file "INSTALL.md" "$PACKAGE_DIR"
copy_optional_file "INSTALL-ZH.md" "$PACKAGE_DIR"
copy_optional_file "INSTALL-AGENT.md" "$PACKAGE_DIR"

info "Installing production dependencies into package"
npm install --omit=dev --ignore-scripts --no-audit --no-fund --package-lock=false --prefix "$PACKAGE_DIR" # npm install --omit=dev
require_dir "$PACKAGE_DIR/node_modules/@sinclair/typebox"

sed \
  -e "s/^VERSION=\".*\"/VERSION=\"$VERSION\"/" \
  -e "s#^RELEASE_PATH=\"\${INSTALL_RELEASE_PATH:-.*}\"#RELEASE_PATH=\"\${INSTALL_RELEASE_PATH:-$RELEASE_PATH}\"#" \
  scripts/install.sh > "$INSTALL_OUTPUT"
chmod +x "$INSTALL_OUTPUT"
cp scripts/volcengine-openviking-install.sh "$VOLCENGINE_INSTALL_OUTPUT"
chmod +x "$VOLCENGINE_INSTALL_OUTPUT"

info "Creating $TGZ_PATH"
COPYFILE_DISABLE=1 tar "${TAR_CREATE_FLAGS[@]}" "$TGZ_PATH" -C "$STAGING_DIR" "$PACKAGE_NAME"

echo
info "Build complete"
echo "Package: $TGZ_RELATIVE_PATH"
echo "Installer: $INSTALL_RELATIVE_PATH"
echo "Volcengine installer: $VOLCENGINE_INSTALL_RELATIVE_PATH"
echo "Version: $VERSION"
