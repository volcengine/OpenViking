#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TAU2_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_REPO_DIR="$TAU2_DIR/.external/tau2-bench"
DEFAULT_VENV_DIR="$TAU2_DIR/.venv-tau2"

REPO_URL="${TAU2_REPO_URL:-https://github.com/sierra-research/tau2-bench.git}"
REPO_DIR="${TAU2_REPO:-$DEFAULT_REPO_DIR}"
VENV_DIR="${TAU2_VENV:-$DEFAULT_VENV_DIR}"
REF="${TAU2_REF:-}"
INSTALL=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url)
      REPO_URL="$2"
      shift 2
      ;;
    --repo-dir)
      REPO_DIR="$2"
      shift 2
      ;;
    --venv)
      VENV_DIR="$2"
      shift 2
      ;;
    --ref)
      REF="$2"
      shift 2
      ;;
    --no-install)
      INSTALL=false
      shift
      ;;
    --help|-h)
      cat <<'EOF'
Usage:
  benchmark/tau2/scripts/setup_tau2_repo.sh [--repo-url URL] [--repo-dir DIR] [--venv DIR] [--ref REF] [--no-install]

Clones TAU-2 into a local ignored directory and optionally installs it into a
local virtualenv. The script writes benchmark/tau2/.env.tau2 with TAU2_REPO and
TAU2_CLI for the benchmark runner.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$(dirname "$REPO_DIR")"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone "$REPO_URL" "$REPO_DIR"
else
  git -C "$REPO_DIR" fetch --all --prune
fi

if [[ -n "$REF" ]]; then
  git -C "$REPO_DIR" checkout "$REF"
fi

TAU2_CLI="tau2"
if [[ "$INSTALL" == true ]]; then
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -e "$REPO_DIR"
  TAU2_CLI="$VENV_DIR/bin/tau2"
fi

cat > "$TAU2_DIR/.env.tau2" <<EOF
export TAU2_REPO="$REPO_DIR"
export TAU2_CLI="$TAU2_CLI"
EOF

echo "[tau2-setup] repo: $REPO_DIR"
echo "[tau2-setup] cli:  $TAU2_CLI"
echo "[tau2-setup] wrote $TAU2_DIR/.env.tau2"
echo "[tau2-setup] next: source $TAU2_DIR/.env.tau2"
