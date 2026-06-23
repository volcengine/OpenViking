#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION=""
TAG=""
RELEASE_DIR=""
DRY_RUN=0
PUBLISH_LATEST=0

usage() {
  cat <<'EOF'
Usage: scripts/release-to-tos.sh [options]

Build and publish the simplified OpenViking TOS release bundle:
  install.sh, openviking.tgz, manifest.json

Options:
  --release-dir <date>       Date directory to upload, e.g. 2026.6.3.
                             Defaults to today's yyyy.m.d.
  --version <version>        Release version metadata. Defaults to package.json version.
  --tag <tag>                Git tag metadata. Defaults to v<version>.
  --publish-latest           Also upload the three files to latest/.
                             Use only after the dated release is validated stable.
  --dry-run                  Build/generate metadata and print uploads without writing TOS.
  -h, --help                 Show this help.

Required for non-dry-run uploads:
  TEAM_TEST_AK
  TEAM_TEST_SK
EOF
}

die() {
  echo "[release-to-tos] ERROR: $*" >&2
  exit 1
}

info() {
  echo "[release-to-tos] $*"
}

today_release_dir() {
  node -e 'const d = new Date(); process.stdout.write(`${d.getFullYear()}.${d.getMonth() + 1}.${d.getDate()}`);'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --release-dir|--date)
      RELEASE_DIR="${2:-}"
      shift 2
      ;;
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    --tag)
      TAG="${2:-}"
      shift 2
      ;;
    --publish-latest)
      PUBLISH_LATEST=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

cd "$ROOT_DIR"

if [ "$DRY_RUN" -ne 1 ]; then
  [ -n "${TEAM_TEST_AK:-}" ] || die "TEAM_TEST_AK is required for non-dry-run release"
  [ -n "${TEAM_TEST_SK:-}" ] || die "TEAM_TEST_SK is required for non-dry-run release"
fi

if [ -z "$RELEASE_DIR" ]; then
  RELEASE_DIR=$(today_release_dir)
fi
if [ -z "$VERSION" ]; then
  VERSION=$(node -e 'const fs = require("fs"); process.stdout.write(JSON.parse(fs.readFileSync("package.json", "utf8")).version);')
fi
if [ -z "$TAG" ]; then
  TAG="v$VERSION"
fi

GIT_HASH=$(git rev-parse HEAD)

info "Building simplified TOS package version=$VERSION release_dir=$RELEASE_DIR tag=$TAG"
BUILD_RELEASE_PATH="$RELEASE_DIR" BUILD_VERSION="$VERSION" bash "$ROOT_DIR/build.sh"

node "$ROOT_DIR/scripts/generate-release-manifest.mjs" \
  --env "prod" \
  --version "$VERSION" \
  --tag "$TAG" \
  --git-hash "$GIT_HASH" \
  --artifact "$ROOT_DIR/output/openviking.tgz" \
  --artifact "$ROOT_DIR/output/install.sh" \
  --bucket "arkclaw-ov" \
  --region "cn-beijing" \
  --endpoint "tos-cn-beijing.volces.com" \
  --release-dir "$RELEASE_DIR" \
  --out "$ROOT_DIR/output/manifest.json"

info "Generated output/manifest.json"

upload_args=(
  "$ROOT_DIR/scripts/upload_tos.py"
  --release-dir "$RELEASE_DIR"
  --install-sh "$ROOT_DIR/output/install.sh"
  --tgz "$ROOT_DIR/output/openviking.tgz"
  --manifest "$ROOT_DIR/output/manifest.json"
)
if [ "$PUBLISH_LATEST" -eq 1 ]; then
  upload_args+=(--publish-latest)
fi
if [ "$DRY_RUN" -eq 1 ]; then
  upload_args+=(--dry-run)
fi

python3 "${upload_args[@]}"

info "TOS release complete"
info "Date path: $RELEASE_DIR/"
info "Latest updated: $PUBLISH_LATEST"
