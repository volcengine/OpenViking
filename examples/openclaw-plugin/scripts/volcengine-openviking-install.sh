#!/bin/bash

set -euo pipefail

# Compatibility wrapper: Volcengine-specific installation is now handled by the
# single global install.sh entrypoint.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

exec "$SCRIPT_DIR/install.sh" "$@"
