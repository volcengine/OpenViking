#!/usr/bin/env bash
# Replace a bare git repository served over git's dumb HTTP protocol from an
# S3-compatible bucket. Clients read info/refs and objects/info/packs, then
# fetch the packs those name, so the new packs go up first, the files that
# point at them next, and the previous release's objects are deleted last.

set -euo pipefail

REPO="${1:?usage: publish-dumb-git-repo.sh <bare-repo> <s3-url> [aws-s3-option...]}"
DEST="${2:?usage: publish-dumb-git-repo.sh <bare-repo> <s3-url> [aws-s3-option...]}"
DEST="${DEST%/}"
shift 2
AWS_ARGS=("$@")

# The repository is tiny and its refs must never be served stale.
NO_CACHE_CONTROL="no-store, no-cache, must-revalidate, max-age=0"

aws_s3() {
  aws s3 "$@" ${AWS_ARGS[@]+"${AWS_ARGS[@]}"} --cache-control "${NO_CACHE_CONTROL}" --only-show-errors
}

aws_s3 cp --recursive "${REPO}/objects/pack" "${DEST}/objects/pack"
for file in objects/info/packs packed-refs info/refs HEAD; do
  aws_s3 cp "${REPO}/${file}" "${DEST}/${file}"
done
aws_s3 sync --delete "${REPO}" "${DEST}"
