#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VIKINGBOT_TEST_PYTHON="${VIKINGBOT_TEST_PYTHON:-$REPO_ROOT/.venv/bin/python}"

if [ ! -x "$VIKINGBOT_TEST_PYTHON" ]; then
    echo "Python not found: $VIKINGBOT_TEST_PYTHON" >&2
    echo "Install the repository's bot/dev dependencies or set VIKINGBOT_TEST_PYTHON." >&2
    exit 1
fi

cd "$REPO_ROOT"
exec "$VIKINGBOT_TEST_PYTHON" -m pytest -c bot/pytest.ini bot/tests "$@"
