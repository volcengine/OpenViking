#!/usr/bin/env bash
# One-step environment setup for the VikingBot × tau2-bench runner.
#
# Does everything in a single step:
#   1. creates a fresh .venv at the OpenViking repo root (if missing)
#   2. clones tau2-bench into ./tau2-bench (if missing; external dependency)
#   3. installs openviking + vikingbot  (pip install -e .[bot]  -> runs the Cargo build;
#      the [bot] extra provides prompt_toolkit/gradio/mcp/... needed by the runner and
#      `openviking-server --with-bot`)
#   4. ensures the ragfs_python native binding is built (via maturin) and bundled into
#      openviking/lib/ (the editable install can skip it under pip build isolation)
#   5. installs tau2-bench with the gym extra (pip install -e ./tau2-bench[gym] -> gymnasium)
#   6. installs smolagents
#   7. activates the venv and exports the runtime env vars
#
# Usage:
#   source setup_env.sh              # install on first run, then activate + export
#   source setup_env.sh --reinstall  # delete .venv and rebuild from scratch
#
# Safe to source repeatedly: the install phase runs only when the venv is missing
# (or when --reinstall is passed); later sources just activate + export.
#
# Overridable via env vars (export before sourcing):
#   TAU2_BENCH_ROOT   tau2-bench checkout location   (default ./tau2-bench)
#   TAU2_BENCH_REPO   git URL to clone tau2-bench    (default sierra-research/tau2-bench)
#   TAU2_BENCH_REF    git ref/branch/tag to check out after clone (default: repo default)
#   VIKINGBOT_ROOT    vikingbot package dir          (default REPO_ROOT/bot)
#   OPENVIKING_CONFIG_FILE, OPENVIKING_PROVISION_API_KEY, OPENAI_API_KEY / ARK_API_KEY, OPENAI_API_BASE

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# OpenViking repo root (this folder lives at benchmark/tau2/vikingbot/).
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
VENV="${REPO_ROOT}/.venv"
SETUP_MARKER="${VENV}/.tau2_setup_complete"

# tau2-bench checkout (external dependency; gitignored). Resolved early so the
# install phase can clone/install it and the exports can derive TAU2_DATA_ROOT.
TAU2_BENCH_ROOT="${TAU2_BENCH_ROOT:-${SCRIPT_DIR}/tau2-bench}"
TAU2_BENCH_REPO="${TAU2_BENCH_REPO:-https://github.com/sierra-research/tau2-bench}"

# Detect source vs execute so errors abort cleanly without killing the user's shell.
(return 0 2>/dev/null) && _SOURCED=1 || _SOURCED=0
_abort() { echo "[setup_env] ERROR: $*" >&2; if [[ "${_SOURCED}" -eq 1 ]]; then return 1; else exit 1; fi; }

# --- parse args ---
REINSTALL=0
for _arg in "$@"; do
  case "${_arg}" in
    --reinstall|--force) REINSTALL=1 ;;
    -h|--help)
      echo "Usage: source setup_env.sh [--reinstall]"
      { [[ "${_SOURCED}" -eq 1 ]] && return 0; } 2>/dev/null || exit 0 ;;
  esac
done

# --- install phase (one-time) ---------------------------------------------------
_setup_install() {
  if [[ "${REINSTALL}" -eq 1 && -d "${VENV}" ]]; then
    echo "[setup_env] --reinstall: removing ${VENV}"
    rm -rf "${VENV}"
  fi

  if [[ -f "${SETUP_MARKER}" && "${REINSTALL}" -eq 0 ]]; then
    return 0  # already installed; nothing to do
  fi

  if [[ ! -f "${VENV}/bin/activate" ]]; then
    echo "[setup_env] Creating venv at ${VENV}"
    python3 -m venv "${VENV}" || { echo "[setup_env] failed to create venv"; return 1; }
  fi

  local PY="${VENV}/bin/python"

  echo "[setup_env] Upgrading pip/setuptools/wheel"
  "${PY}" -m pip install --upgrade pip setuptools wheel || return 1

  # tau2-bench: external checkout. Clone it if it isn't present yet.
  if [[ ! -d "${TAU2_BENCH_ROOT}" ]]; then
    if ! command -v git >/dev/null 2>&1; then
      echo "[setup_env] 'git' not found; cannot clone tau2-bench into ${TAU2_BENCH_ROOT}"; return 1
    fi
    echo "[setup_env] Cloning tau2-bench: ${TAU2_BENCH_REPO} -> ${TAU2_BENCH_ROOT}"
    git clone "${TAU2_BENCH_REPO}" "${TAU2_BENCH_ROOT}" || { echo "[setup_env] tau2-bench clone failed"; return 1; }
    if [[ -n "${TAU2_BENCH_REF:-}" ]]; then
      echo "[setup_env] Checking out tau2-bench ref: ${TAU2_BENCH_REF}"
      git -C "${TAU2_BENCH_ROOT}" checkout "${TAU2_BENCH_REF}" || { echo "[setup_env] tau2-bench checkout failed"; return 1; }
    fi
  fi

  # openviking + vikingbot: editable install of the repo root (pyproject finds both).
  # NOTE: this triggers a Cargo/Rust release build of the `ov` CLI.
  if ! command -v cargo >/dev/null 2>&1; then
    echo "[setup_env] WARNING: 'cargo' not found on PATH; the openviking Rust build may fail." >&2
  fi
  # Install with the [bot] extra: the vikingbot runner imports vikingbot.cli and
  # `openviking-server --with-bot` needs the bot deps (prompt_toolkit, gradio, mcp, ...).
  echo "[setup_env] Installing openviking + vikingbot with bot extras (pip install -e ${REPO_ROOT}[bot])"
  "${PY}" -m pip install -e "${REPO_ROOT}[bot]" || { echo "[setup_env] openviking install failed"; return 1; }

  # Ensure the ragfs_python native (PyO3) binding is bundled. The editable install
  # can skip the maturin build under pip build isolation, leaving the server unable
  # to load RAGFS ("ragfs_python native library is not available"). If it's not
  # importable, build it via maturin and copy the extension into openviking/lib/.
  if ! "${PY}" -c "from openviking.pyagfs import get_binding_client; get_binding_client()" >/dev/null 2>&1; then
    echo "[setup_env] ragfs_python native binding missing; building via maturin..."
    "${PY}" -m pip install "maturin>=1.0,<2.0" || { echo "[setup_env] maturin install failed"; return 1; }
    _ragfs_out="$(mktemp -d)"
    if "${PY}" -m maturin build --release -m "${REPO_ROOT}/crates/ragfs-python/Cargo.toml" --out "${_ragfs_out}"; then
      "${PY}" - "${_ragfs_out}" "${REPO_ROOT}/openviking/lib" <<'PYEOF'
import sys, os, glob, zipfile
from pathlib import Path
out_dir, lib_dir = sys.argv[1], Path(sys.argv[2])
whls = sorted(glob.glob(os.path.join(out_dir, "ragfs_python-*.whl")))
if not whls:
    sys.exit("maturin produced no ragfs_python wheel")
lib_dir.mkdir(parents=True, exist_ok=True)
for pat in ("ragfs_python*.so", "ragfs_python*.pyd", "ragfs_python*.dylib"):
    for stale in lib_dir.glob(pat):
        stale.unlink()
with zipfile.ZipFile(whls[0]) as zf:
    for name in zf.namelist():
        base = Path(name).name
        if base == "ragfs_python.pyd" or (base.startswith("ragfs_python.abi3.") and base.endswith((".so", ".pyd"))):
            target = lib_dir / base
            with zf.open(name) as src, open(target, "wb") as dst:
                dst.write(src.read())
            if not sys.platform.startswith("win"):
                os.chmod(target, 0o755)
            print(f"[setup_env] ragfs_python: bundled {base} -> {target}")
            break
    else:
        sys.exit("ragfs_python native extension not found in wheel")
PYEOF
      _ragfs_rc=$?
      rm -rf "${_ragfs_out}"
      [[ "${_ragfs_rc}" -eq 0 ]] || { echo "[setup_env] ragfs_python bundling failed"; return 1; }
    else
      rm -rf "${_ragfs_out}"
      echo "[setup_env] maturin build of ragfs_python failed"; return 1
    fi
  else
    echo "[setup_env] ragfs_python native binding present"
  fi

  # tau2-bench: install the [gym] extra so tau2.gym (gymnasium) is available to the runner.
  echo "[setup_env] Installing tau2-bench with gym extra (pip install -e ${TAU2_BENCH_ROOT}[gym])"
  "${PY}" -m pip install -e "${TAU2_BENCH_ROOT}[gym]" || { echo "[setup_env] tau2-bench install failed"; return 1; }

  echo "[setup_env] Installing smolagents"
  "${PY}" -m pip install smolagents || { echo "[setup_env] smolagents install failed"; return 1; }

  touch "${SETUP_MARKER}"
  echo "[setup_env] Install complete."
  return 0
}

if ! _setup_install; then
  _abort "environment install failed (see messages above)"
  unset -f _setup_install _abort
  return 1 2>/dev/null || exit 1
fi
unset -f _setup_install

# --- activate venv --------------------------------------------------------------
if [[ ! -f "${VENV}/bin/activate" ]]; then
  _abort "venv not found at ${VENV} after install"
  return 1 2>/dev/null || exit 1
fi
# shellcheck disable=SC1090
source "${VENV}/bin/activate"
echo "[setup_env] venv activated: ${VENV}"

# --- runtime env vars -----------------------------------------------------------
# openviking + vikingbot come from the editable install; REPO_ROOT + bot/ kept on
# PYTHONPATH as a harmless import fallback.
OPENVIKING_TAU2_ROOT="${REPO_ROOT}"
VIKINGBOT_ROOT="${VIKINGBOT_ROOT:-${REPO_ROOT}/bot}"
export PYTHONPATH="${OPENVIKING_TAU2_ROOT}:${VIKINGBOT_ROOT}:${PYTHONPATH:-}"

# tau2 dataset root (derived from the tau2-bench checkout)
export TAU2_DATA_ROOT="${TAU2_DATA_ROOT:-${TAU2_BENCH_ROOT}/data/tau2}"

# OpenViking server config
export OPENVIKING_CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-${HOME}/.openviking/ov.conf}"

# LLM for the tau2 user simulator (e.g. Doubao via volcengine ARK, OpenAI-compatible).
# Provide your own key via ARK_API_KEY (do NOT commit real keys).
export OPENAI_API_KEY="${OPENAI_API_KEY:-${ARK_API_KEY:-}}"
export OPENAI_API_BASE="${OPENAI_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}"
if [[ -z "${OPENAI_API_KEY}" ]]; then
  echo "[setup_env] WARNING: OPENAI_API_KEY/ARK_API_KEY is empty; the tau2 user simulator will fail."
fi

echo "[setup_env] PYTHONPATH includes openviking (${OPENVIKING_TAU2_ROOT}) and vikingbot (${VIKINGBOT_ROOT})"
echo "[setup_env] TAU2_DATA_ROOT=${TAU2_DATA_ROOT}"
echo "[setup_env] OPENAI_API_BASE=${OPENAI_API_BASE}"

unset -f _abort 2>/dev/null || true
