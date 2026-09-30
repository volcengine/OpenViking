#!/usr/bin/env bash
# Write a copy of the shared installer that reports the release it was
# published with. The repository copy keeps INSTALLER_VERSION="dev", which the
# installer reads as "not a release build".

set -euo pipefail

SRC="${1:?usage: stamp-installer-version.sh <installer> <version> <out>}"
VERSION="${2:?usage: stamp-installer-version.sh <installer> <version> <out>}"
OUT="${3:?usage: stamp-installer-version.sh <installer> <version> <out>}"

# The version lands inside a double-quoted shell string and a sed replacement.
case "${VERSION}" in
  *[!0-9A-Za-z._+-]*)
    echo "Refusing to stamp installer version '${VERSION}'" >&2
    exit 1
    ;;
esac

count="$(grep -cxF 'INSTALLER_VERSION="dev"' "${SRC}" || true)"
if [ "${count}" != 1 ]; then
  echo "${SRC}: expected one INSTALLER_VERSION=\"dev\" line, found ${count}" >&2
  exit 1
fi

sed "s/^INSTALLER_VERSION=\"dev\"\$/INSTALLER_VERSION=\"${VERSION}\"/" "${SRC}" > "${OUT}"
