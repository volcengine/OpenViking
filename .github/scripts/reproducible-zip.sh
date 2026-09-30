#!/usr/bin/env bash
# Zip one directory so that every build of the same commit gives the same
# bytes. The versioned release keys are meant to be immutable and the
# marketplace manifests pin these zips' sha256, so re-running a release must
# not change them. Entries are sorted, stamped with the commit time and carry
# no extra attributes.

set -euo pipefail

OUT="${1:?usage: reproducible-zip.sh <out.zip> <parent-dir> <entry>}"
PARENT="${2:?usage: reproducible-zip.sh <out.zip> <parent-dir> <entry>}"
ENTRY="${3:?usage: reproducible-zip.sh <out.zip> <parent-dir> <entry>}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# Zip stores local time, so one fixed zone keeps the stamp the same everywhere.
export TZ=UTC
STAMP="$(git -C "${ROOT}" log -1 --format=%cd --date=format-local:%Y%m%d%H%M.%S)"

case "${OUT}" in /*) ;; *) OUT="${PWD}/${OUT}" ;; esac
rm -f "${OUT}"
cd "${PARENT}"
find "${ENTRY}" -exec touch -t "${STAMP}" {} +
find "${ENTRY}" | LC_ALL=C sort | zip -X -q "${OUT}" -@
