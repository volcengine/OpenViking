#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -z "${PYTHON:-}" ]]; then
    if [[ -x "$SCRIPT_DIR/../../../.venv/bin/python" ]]; then
        PYTHON="$SCRIPT_DIR/../../../.venv/bin/python"
    elif command -v python >/dev/null 2>&1; then
        PYTHON="python"
    else
        PYTHON="python3"
    fi
fi

usage() {
    cat <<'USAGE'
Run a fixed LoCoMo benchmark suite.

Suites:
  e2e        Import through Hermes/OpenViking, then eval/judge/stats.
  preingest  Import directly into OpenViking, then eval/judge/stats.
  native     Import into Hermes native memory, then eval/judge/stats. No OpenViking checkpoint.

Usage:
  ./run_full_eval.sh --suite e2e [-cp] [options]
  ./run_full_eval.sh --suite preingest [-cp] [options]
  ./run_full_eval.sh --suite native [options]

Options:
  --suite NAME            Required: e2e, preingest, or native.
  -cp, --checkpoint       Copy an OpenViking checkpoint after import (e2e/preingest only).
  --sample N              Process one LoCoMo sample index.
  --count N               Limit QA questions per sample during eval.
  --force-ingest          Re-ingest even if import_success.csv exists.
  --force-eval            Re-run QA even if qa_results.csv exists.
  --skip-import           Run eval/judge/stats only; assumes benchmark state is already loaded.
  --import-csv PATH       Import CSV used for stats when --skip-import is used.
  --result-dir PATH       Result directory for CSVs and logs.
  --snapshot-root PATH    Root directory for OpenViking checkpoints when -cp is used.
  --state-source PATH     OpenViking workspace to copy when -cp is used.
  --run-id ID             Stable id used in result/checkpoint names.
  -h, --help              Show this help.

Environment:
  LOCOMO_JSON, HERMES_URL, HERMES_TOKEN, HERMES_MODEL, OPENVIKING_URL
  JUDGE_BASE_URL, JUDGE_TOKEN, JUDGE_MODEL
  IMPORT_PARALLEL, QA_PARALLEL, JUDGE_PARALLEL
  IMPORT_ERROR_RETRIES, QA_ERROR_RETRIES, JUDGE_ERROR_RETRIES, QUEUE_MAX_WAIT_SEC
  PYTHON, HERMES_STATE_DB, OPENVIKING_CONFIG_FILE, OPENVIKING_STATE_SOURCE, SNAPSHOT_ROOT, RESULT_DIR
  E2E_PREFLIGHT, SKIP_IMPORT, IMPORT_CSV_OVERRIDE
  OPENVIKING_ACCOUNT, OPENVIKING_USER, OPENVIKING_API_KEY

Dataset:
  By default this expects ../data/locomo10.json from benchmark/locomo/hermes.
  Set LOCOMO_JSON=/path/to/locomo10.json to use a local copy elsewhere.
USAGE
}

SUITE="${SUITE:-}"
LOCOMO_JSON="${LOCOMO_JSON:-../data/locomo10.json}"
HERMES_URL="${HERMES_URL:-http://127.0.0.1:8642}"
HERMES_TOKEN="${HERMES_TOKEN:-${API_SERVER_KEY:-}}"
HERMES_MODEL="${HERMES_MODEL:-hermes-agent}"
OPENVIKING_URL="${OPENVIKING_URL:-http://127.0.0.1:1933}"
OPENVIKING_ACCOUNT="${OPENVIKING_ACCOUNT:-default}"
OPENVIKING_USER="${OPENVIKING_USER:-default}"
OPENVIKING_API_KEY="${OPENVIKING_API_KEY:-}"
JUDGE_BASE_URL="${JUDGE_BASE_URL:-https://ark.cn-beijing.volces.com/api/v3}"
JUDGE_TOKEN="${JUDGE_TOKEN:-${ARK_API_KEY:-}}"
JUDGE_MODEL="${JUDGE_MODEL:-doubao-seed-2-0-pro-260215}"
IMPORT_PARALLEL="${IMPORT_PARALLEL:-4}"
QA_PARALLEL="${QA_PARALLEL:-4}"
JUDGE_PARALLEL="${JUDGE_PARALLEL:-5}"
IMPORT_ERROR_RETRIES="${IMPORT_ERROR_RETRIES:-2}"
QA_ERROR_RETRIES="${QA_ERROR_RETRIES:-2}"
JUDGE_ERROR_RETRIES="${JUDGE_ERROR_RETRIES:-2}"
QUEUE_MAX_WAIT_SEC="${QUEUE_MAX_WAIT_SEC:-1800}"
E2E_PREFLIGHT="${E2E_PREFLIGHT:-1}"
RUN_ID="${RUN_ID:-}"
RESULT_DIR="${RESULT_DIR:-}"
SNAPSHOT_ROOT="${SNAPSHOT_ROOT:-}"
OPENVIKING_STATE_SOURCE="${OPENVIKING_STATE_SOURCE:-}"
SAMPLE="${SAMPLE:-}"
COUNT="${COUNT:-}"
FORCE_INGEST="${FORCE_INGEST:-}"
FORCE_EVAL="${FORCE_EVAL:-}"
CHECKPOINT_REQUESTED="${CHECKPOINT_REQUESTED:-0}"
SKIP_IMPORT="${SKIP_IMPORT:-}"
IMPORT_CSV_OVERRIDE="${IMPORT_CSV_OVERRIDE:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --suite)
            SUITE="${2:-}"
            shift 2
            ;;
        --sample)
            SAMPLE="${2:-}"
            shift 2
            ;;
        --count)
            COUNT="${2:-}"
            shift 2
            ;;
        --force-ingest)
            FORCE_INGEST=1
            shift
            ;;
        --force-eval)
            FORCE_EVAL=1
            shift
            ;;
        --skip-import)
            SKIP_IMPORT=1
            shift
            ;;
        --import-csv)
            IMPORT_CSV_OVERRIDE="${2:-}"
            shift 2
            ;;
        -cp|--checkpoint)
            CHECKPOINT_REQUESTED=1
            shift
            ;;
        --result-dir)
            RESULT_DIR="${2:-}"
            shift 2
            ;;
        --snapshot-root)
            SNAPSHOT_ROOT="${2:-}"
            shift 2
            ;;
        --state-source)
            OPENVIKING_STATE_SOURCE="${2:-}"
            shift 2
            ;;
        --run-id)
            RUN_ID="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ -z "$SUITE" ]]; then
    echo "Error: --suite is required" >&2
    usage >&2
    exit 1
fi

CHECKPOINT_OPENVIKING=0
CAN_CHECKPOINT_OPENVIKING=0
case "$SUITE" in
    e2e)
        SUITE_LABEL="e2e"
        EVAL_SUITE="e2e"
        IMPORT_SCRIPT="import_e2e.py"
        BENCHMARK_TITLE="Hermes OpenViking E2E LoCoMo benchmark"
        RUN_ID="${RUN_ID:-locomo-e2e-session-no-pe-$(date +%Y%m%d-%H%M%S)}"
        CAN_CHECKPOINT_OPENVIKING=1
        if [[ -z "$RESULT_DIR" ]]; then
            if [[ "$CHECKPOINT_REQUESTED" == "1" ]]; then
                RESULT_DIR="$SCRIPT_DIR/result_e2e_checkpointed_${RUN_ID}"
            else
                RESULT_DIR="$SCRIPT_DIR/result_e2e_${RUN_ID}"
            fi
        fi
        ;;
    preingest)
        SUITE_LABEL="preingest"
        EVAL_SUITE="preingest"
        IMPORT_SCRIPT="import_to_ov.py"
        BENCHMARK_TITLE="Hermes + OpenViking pre-ingest LoCoMo benchmark"
        RUN_ID="${RUN_ID:-locomo-preingest-session-timestamps-visuals-no-pe-$(date +%Y%m%d-%H%M%S)}"
        RESULT_DIR="${RESULT_DIR:-$SCRIPT_DIR/result_preingest_${RUN_ID}}"
        CAN_CHECKPOINT_OPENVIKING=1
        ;;
    native|baseline)
        SUITE_LABEL="native"
        EVAL_SUITE="baseline"
        IMPORT_SCRIPT="import_to_native.py"
        BENCHMARK_TITLE="Hermes Native Memory LoCoMo benchmark"
        RUN_ID="${RUN_ID:-locomo-native-session-no-pe-$(date +%Y%m%d-%H%M%S)}"
        RESULT_DIR="${RESULT_DIR:-$SCRIPT_DIR/result_baseline_${RUN_ID}}"
        ;;
    *)
        echo "Error: --suite must be one of: e2e, preingest, native" >&2
        exit 1
        ;;
esac

if [[ "$CHECKPOINT_REQUESTED" == "1" ]]; then
    if [[ "$CAN_CHECKPOINT_OPENVIKING" == "1" ]]; then
        CHECKPOINT_OPENVIKING=1
    else
        echo "Note: -cp/--checkpoint is ignored for native suite; native has no OpenViking checkpoint." >&2
    fi
fi

if [[ "$SKIP_IMPORT" == "1" && "$CHECKPOINT_OPENVIKING" == "1" ]]; then
    echo "Note: -cp/--checkpoint is ignored with --skip-import; no import checkpoint will be produced." >&2
    CHECKPOINT_OPENVIKING=0
fi

SNAPSHOT_ROOT="${SNAPSHOT_ROOT:-$SCRIPT_DIR/openviking_state_snapshots}"
IMPORT_CSV="${RESULT_DIR}/import_success.csv"
STATS_IMPORT_CSV="${IMPORT_CSV_OVERRIDE:-$IMPORT_CSV}"
QA_CSV="${RESULT_DIR}/qa_results.csv"
LOG_DIR="${RESULT_DIR}/logs"
SNAPSHOT_RUN_DIR="${SNAPSHOT_ROOT}/${RUN_ID}"
SNAPSHOT_DIR="${SNAPSHOT_RUN_DIR}/openviking_workspace"
SNAPSHOT_MANIFEST="${SNAPSHOT_RUN_DIR}/manifest.txt"
RESULT_MANIFEST="${RESULT_DIR}/openviking_checkpoint_manifest.txt"
RESOLVED_OPENVIKING_CONFIG_FILE=""
export OPENVIKING_ACCOUNT OPENVIKING_USER OPENVIKING_API_KEY

if [[ ! -f "$LOCOMO_JSON" ]]; then
    echo "Error: LoCoMo dataset not found: $LOCOMO_JSON" >&2
    echo "Set LOCOMO_JSON=/path/to/locomo10.json or place it at benchmark/locomo/data/locomo10.json." >&2
    exit 1
fi

mkdir -p "$RESULT_DIR" "$LOG_DIR"
if [[ "$CHECKPOINT_OPENVIKING" == "1" ]]; then
    mkdir -p "$SNAPSHOT_ROOT"
fi

resolve_openviking_state_source() {
    "$PYTHON" - <<'PY'
import json
import os
from pathlib import Path

config_candidates = []
if os.environ.get("OPENVIKING_CONFIG_FILE"):
    config_candidates.append(Path(os.environ["OPENVIKING_CONFIG_FILE"]).expanduser())
config_candidates.append(Path.home() / ".openviking" / "ov.conf")
config_candidates.append(Path("/etc/openviking/ov.conf"))

config_path = next((path for path in config_candidates if path.exists()), None)
workspace = ""
if config_path is not None:
    raw = os.path.expandvars(config_path.read_text(encoding="utf-8-sig"))
    data = json.loads(raw)
    storage = data.get("storage", {})
    if isinstance(storage, dict):
        workspace = storage.get("workspace") or ""

if not workspace:
    workspace = str(Path.home() / ".openviking" / "data")

print(Path(workspace).expanduser().resolve())
print(config_path.expanduser().resolve() if config_path is not None else "")
PY
}

ensure_openviking_state_source() {
    if [[ -z "$OPENVIKING_STATE_SOURCE" ]]; then
        mapfile -t resolved_state < <(resolve_openviking_state_source)
        OPENVIKING_STATE_SOURCE="${resolved_state[0]:-}"
        RESOLVED_OPENVIKING_CONFIG_FILE="${resolved_state[1]:-}"
    else
        OPENVIKING_STATE_SOURCE="$("$PYTHON" -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$OPENVIKING_STATE_SOURCE")"
        RESOLVED_OPENVIKING_CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-}"
    fi

    if [[ -z "$OPENVIKING_STATE_SOURCE" || ! -d "$OPENVIKING_STATE_SOURCE" ]]; then
        echo "OpenViking state source does not exist: ${OPENVIKING_STATE_SOURCE:-<empty>}" >&2
        echo "Pass --state-source PATH or set OPENVIKING_STATE_SOURCE." >&2
        exit 1
    fi
}

verify_openviking_archive_readiness() {
    if [[ "$SUITE_LABEL" != "e2e" && "$SUITE_LABEL" != "preingest" ]]; then
        echo ">>> Step 2: SKIPPED (OpenViking archive readiness is not used for native suite)."
        return
    fi

    ensure_openviking_state_source

    echo ""
    echo ">>> Step 2: Waiting for OpenViking archive readiness..."
    OPENVIKING_STATE_SOURCE="$OPENVIKING_STATE_SOURCE" \
    LOCOMO_JSON="$LOCOMO_JSON" \
    SAMPLE="$SAMPLE" \
    IMPORT_CSV="$IMPORT_CSV" \
    RESULT_DIR="$RESULT_DIR" \
    SUITE_LABEL="$SUITE_LABEL" \
    QUEUE_MAX_WAIT_SEC="$QUEUE_MAX_WAIT_SEC" \
    "$PYTHON" - <<'PY'
import csv
import json
import os
import sys
import time
from pathlib import Path

source = Path(os.environ["OPENVIKING_STATE_SOURCE"]).expanduser().resolve()
locomo_json = Path(os.environ["LOCOMO_JSON"]).expanduser().resolve()
import_csv = Path(os.environ["IMPORT_CSV"]).expanduser().resolve()
result_dir = Path(os.environ["RESULT_DIR"]).expanduser().resolve()
suite = os.environ["SUITE_LABEL"]
sample_value = (os.environ.get("SAMPLE") or "").strip()
max_wait_sec = max(1, int(os.environ.get("QUEUE_MAX_WAIT_SEC") or "1800"))
account = os.environ.get("OPENVIKING_ACCOUNT", "default")
poll_interval = 5

def session_number(session_key: str) -> int:
    try:
        return int(session_key.split("_", 1)[1])
    except Exception:
        return 0

with locomo_json.open("r", encoding="utf-8") as f:
    data = json.load(f)

if sample_value:
    sample_index = int(sample_value)
    if sample_index < 0 or sample_index >= len(data):
        print(
            f"Sample index {sample_index} out of range for {locomo_json}",
            file=sys.stderr,
        )
        sys.exit(1)
    data = [data[sample_index]]

prefix = "locomo-e2e" if suite == "e2e" else "locomo-ovpreingest"
expected_ids = []
expected_pairs = set()
for item in data:
    sample_id = item.get("sample_id", "")
    conv = item.get("conversation", {})
    session_keys = sorted(
        [
            key for key in conv
            if key.startswith("session_") and not key.endswith("_date_time")
        ],
        key=session_number,
    )
    for session_key in session_keys:
        expected_pairs.add((sample_id, session_key))
        expected_ids.append(f"{prefix}-{sample_id}-{session_key}")

expected_ids = sorted(set(expected_ids))
if not expected_ids:
    print(f"No expected sessions found in {locomo_json}", file=sys.stderr)
    sys.exit(1)

def read_import_pairs() -> set[tuple[str, str]]:
    pairs = set()
    if not import_csv.exists():
        return pairs
    with import_csv.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sample_id = (row.get("sample_id") or "").strip()
            session_key = (row.get("session") or "").strip()
            if sample_id and session_key:
                pairs.add((sample_id, session_key))
    return pairs

def scan_expected_sessions():
    done_session_ids = set()
    done_files = 0
    memory_diff_files = 0
    failed_files = []

    for session_id in expected_ids:
        history_dir = source / "viking" / account / "session" / session_id / "history"
        try:
            if not history_dir.exists():
                continue
            for archive_dir in history_dir.iterdir():
                if not archive_dir.is_dir():
                    continue

                done_marker = archive_dir / ".done"
                failed_marker = archive_dir / ".failed.json"
                memory_diff = archive_dir / "memory_diff.json"

                if done_marker.exists():
                    done_files += 1
                    done_session_ids.add(session_id)
                if memory_diff.exists():
                    memory_diff_files += 1
                if failed_marker.exists():
                    failed_files.append(failed_marker)
        except OSError:
            continue

    return done_session_ids, done_files, memory_diff_files, failed_files

deadline = time.monotonic() + max_wait_sec
last_status = ""

while True:
    done_session_ids, done_files, memory_diff_files, failed_files = scan_expected_sessions()
    expected_done = done_session_ids & set(expected_ids)
    missing = [session_id for session_id in expected_ids if session_id not in done_session_ids]
    status = (
        f"expected_sessions={len(expected_ids)}, "
        f"done_sessions={len(expected_done)}, "
        f"done_files={done_files}, memory_diff_files={memory_diff_files}, "
        f"failed_files={len(failed_files)}"
    )

    if failed_files:
        print("OpenViking failed archive markers exist:", file=sys.stderr)
        for path in failed_files[:20]:
            print(f"  {path}", file=sys.stderr)
        if len(failed_files) > 20:
            print(f"  ... and {len(failed_files) - 20} more", file=sys.stderr)
        sys.exit(1)

    if not missing:
        print(f"Archive readiness: {status}")
        import_pairs = read_import_pairs()
        missing_csv_pairs = sorted(expected_pairs - import_pairs)
        report = {
            "suite": suite,
            "expected_sessions": len(expected_ids),
            "done_sessions": len(expected_done),
            "import_csv_rows": len(import_pairs & expected_pairs),
            "missing_import_csv_rows": [
                {"sample_id": sample_id, "session": session_key}
                for sample_id, session_key in missing_csv_pairs
            ],
        }
        report_path = result_dir / "logs" / "archive_readiness_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if missing_csv_pairs:
            print(
                "Archive readiness passed, but import_success.csv is "
                f"missing {len(missing_csv_pairs)} row(s); see {report_path}",
                file=sys.stderr,
            )
            sys.exit(1)
        else:
            print(f"Archive readiness report: {report_path}")
        break

    if time.monotonic() >= deadline:
        print(
            "OpenViking archive readiness timed out: "
            f"{len(missing)} expected session(s) have no .done marker.",
            file=sys.stderr,
        )
        print(f"Last status: {status}", file=sys.stderr)
        for session_id in missing[:20]:
            print(f"  {session_id}", file=sys.stderr)
        if len(missing) > 20:
            print(f"  ... and {len(missing) - 20} more", file=sys.stderr)
        sys.exit(1)

    if status != last_status:
        print(f"  .. archive readiness pending: {status}", file=sys.stderr)
        last_status = status
    time.sleep(poll_interval)
PY
}

copy_openviking_state() {
    if [[ "$CHECKPOINT_OPENVIKING" != "1" ]]; then
        echo ">>> Step 2: SKIPPED (OpenViking checkpoint disabled)."
        return
    fi

    ensure_openviking_state_source

    if [[ -e "$SNAPSHOT_DIR" ]]; then
        echo "Checkpoint destination already exists: $SNAPSHOT_DIR" >&2
        echo "Use a different --run-id or --snapshot-root." >&2
        exit 1
    fi

    local snapshot_abs
    snapshot_abs="$("$PYTHON" -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$SNAPSHOT_DIR")"
    case "${snapshot_abs}/" in
        "${OPENVIKING_STATE_SOURCE}/"*)
            echo "Refusing to place checkpoint inside the OpenViking source workspace." >&2
            echo "Source: $OPENVIKING_STATE_SOURCE" >&2
            echo "Destination: $snapshot_abs" >&2
            exit 1
            ;;
    esac

    mkdir -p "$SNAPSHOT_RUN_DIR"

    echo ""
    echo ">>> Step 2: Copying OpenViking state checkpoint..."
    echo "Source:      $OPENVIKING_STATE_SOURCE"
    echo "Checkpoint:  $SNAPSHOT_DIR"

    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete "${OPENVIKING_STATE_SOURCE}/" "${SNAPSHOT_DIR}/"
    else
        mkdir -p "$SNAPSHOT_DIR"
        cp -a "${OPENVIKING_STATE_SOURCE}/." "${SNAPSHOT_DIR}/"
    fi

    if [[ -n "$RESOLVED_OPENVIKING_CONFIG_FILE" && -f "$RESOLVED_OPENVIKING_CONFIG_FILE" ]]; then
        cp "$RESOLVED_OPENVIKING_CONFIG_FILE" "${SNAPSHOT_RUN_DIR}/ov.conf"
    fi

    {
        echo "OpenViking ${SUITE_LABEL} checkpoint"
        echo "created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "run_id=$RUN_ID"
        echo "suite=$SUITE_LABEL"
        echo "prompting=no_pe"
        echo "import_shape=flattened_locomo_session"
        echo "result_dir=$RESULT_DIR"
        echo "openviking_url=$OPENVIKING_URL"
        echo "source_workspace=$OPENVIKING_STATE_SOURCE"
        echo "checkpoint_workspace=$SNAPSHOT_DIR"
        echo "checkpoint_manifest=$SNAPSHOT_MANIFEST"
        echo "config_file=${RESOLVED_OPENVIKING_CONFIG_FILE:-}"
        echo "import_csv=$IMPORT_CSV"
        echo "qa_csv=$QA_CSV"
    } > "$SNAPSHOT_MANIFEST"

    cp "$SNAPSHOT_MANIFEST" "$RESULT_MANIFEST"

    echo "Checkpoint manifest: $SNAPSHOT_MANIFEST"
    echo "Result manifest:     $RESULT_MANIFEST"
    echo ">>> Step 2 done."
}

verify_hermes_openviking_target() {
    if [[ "$SUITE_LABEL" != "e2e" ]]; then
        return
    fi

    local preflight_value
    preflight_value="$(printf '%s' "$E2E_PREFLIGHT" | tr '[:upper:]' '[:lower:]')"
    case "$preflight_value" in
        0|false|no|off)
            echo ">>> Preflight skipped (E2E_PREFLIGHT=$E2E_PREFLIGHT)."
            return
            ;;
    esac

    local probe_session_id="openviking-e2e-preflight-${RUN_ID}-$$"
    echo ""
    echo ">>> Preflight: verifying Hermes writes to the configured OpenViking target..."
    echo "Probe session: $probe_session_id"

    HERMES_URL="$HERMES_URL" \
    HERMES_TOKEN="$HERMES_TOKEN" \
    HERMES_MODEL="$HERMES_MODEL" \
    OPENVIKING_URL="$OPENVIKING_URL" \
    PROBE_SESSION_ID="$probe_session_id" \
    "$PYTHON" - <<'PY'
import os
import sys
import time

import requests


def fail(message: str) -> None:
    print(f"Preflight failed: {message}", file=sys.stderr)
    sys.exit(1)


hermes_url = os.environ["HERMES_URL"].rstrip("/")
openviking_url = os.environ["OPENVIKING_URL"].rstrip("/")
token = os.environ.get("HERMES_TOKEN", "")
model = os.environ["HERMES_MODEL"]
session_id = os.environ["PROBE_SESSION_ID"]

headers = {
    "Content-Type": "application/json",
    "X-Hermes-Session-Id": session_id,
}
if token:
    headers["Authorization"] = f"Bearer {token}"

payload = {
    "model": model,
    "messages": [
        {
            "role": "user",
            "content": f"OpenViking benchmark preflight probe {session_id}. Acknowledge with OK.",
        }
    ],
}

try:
    response = requests.post(
        f"{hermes_url}/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=300,
    )
    response.raise_for_status()
except Exception as exc:
    fail(f"Hermes request did not succeed: {exc}")

resolved_session_id = response.headers.get("X-Hermes-Session-Id") or session_id

ov_headers = {
    "X-OpenViking-Account": os.environ.get("OPENVIKING_ACCOUNT", "default"),
    "X-OpenViking-User": os.environ.get("OPENVIKING_USER", "default"),
}
api_key = os.environ.get("OPENVIKING_API_KEY", "")
if api_key:
    ov_headers["X-API-Key"] = api_key

last_observed = "session not found"
deadline = time.monotonic() + 30.0
while time.monotonic() < deadline:
    try:
        session_response = requests.get(
            f"{openviking_url}/api/v1/sessions/{resolved_session_id}",
            headers=ov_headers,
            timeout=10,
        )
    except Exception as exc:
        last_observed = str(exc)
        time.sleep(0.5)
        continue

    if session_response.status_code == 200:
        try:
            body = session_response.json()
        except Exception:
            body = {}
        result = body.get("result") if isinstance(body, dict) else None
        session = result if isinstance(result, dict) else body
        pending_tokens = int(session.get("pending_tokens") or 0)
        message_count = int(session.get("message_count") or 0)
        last_observed = f"pending_tokens={pending_tokens}, message_count={message_count}"
        if pending_tokens > 0 or message_count > 0:
            print(
                "Preflight OK: expected OpenViking target received "
                f"{resolved_session_id} ({last_observed})."
            )
            try:
                requests.delete(
                    f"{openviking_url}/api/v1/sessions/{resolved_session_id}",
                    headers=ov_headers,
                    timeout=10,
                )
            except Exception as exc:
                print(f"Warning: failed to delete preflight session: {exc}", file=sys.stderr)
            sys.exit(0)

    time.sleep(0.5)

fail(
    "Hermes completed the probe, but the configured OpenViking target did not receive it. "
    f"Expected OPENVIKING_URL={openviking_url}, session={resolved_session_id}, "
    f"namespace={ov_headers.get('X-OpenViking-Account')}/"
    f"{ov_headers.get('X-OpenViking-User')}; "
    f"last observed: {last_observed}"
)
PY
}

warm_hermes_memory_provider() {
    if [[ "$SUITE_LABEL" != "e2e" ]]; then
        return
    fi

    echo ""
    echo ">>> Warmup: initializing Hermes memory provider before parallel QA..."

    HERMES_URL="$HERMES_URL" \
    HERMES_TOKEN="$HERMES_TOKEN" \
    HERMES_MODEL="$HERMES_MODEL" \
    "$PYTHON" - <<'PY'
import os
import sys
import uuid

import requests


hermes_url = os.environ["HERMES_URL"].rstrip("/")
token = os.environ.get("HERMES_TOKEN", "")
model = os.environ["HERMES_MODEL"]
session_id = f"openviking-e2e-warmup-{uuid.uuid4().hex}"

headers = {"Content-Type": "application/json"}
if token:
    headers["Authorization"] = f"Bearer {token}"

payload = {
    "model": model,
    "input": "OpenViking memory provider warmup. Acknowledge with OK.",
    "conversation": session_id,
    "session_id": session_id,
    "store": False,
}

try:
    response = requests.post(
        f"{hermes_url}/v1/responses",
        headers=headers,
        json=payload,
        timeout=180,
    )
    response.raise_for_status()
except Exception as exc:
    print(f"Warmup failed: {exc}", file=sys.stderr)
    sys.exit(1)

print(f"Warmup OK: {session_id}")
PY
}

write_run_command() {
    {
        echo "#!/usr/bin/env bash"
        printf 'cd %q\n' "$SCRIPT_DIR"
        printf 'PYTHON=%q RUN_ID=%q RESULT_DIR=%q SNAPSHOT_ROOT=%q OPENVIKING_STATE_SOURCE=%q ' \
            "$PYTHON" "$RUN_ID" "$RESULT_DIR" "$SNAPSHOT_ROOT" "$OPENVIKING_STATE_SOURCE"
        printf 'IMPORT_PARALLEL=%q QA_PARALLEL=%q JUDGE_PARALLEL=%q ' \
            "$IMPORT_PARALLEL" "$QA_PARALLEL" "$JUDGE_PARALLEL"
        printf 'IMPORT_ERROR_RETRIES=%q QA_ERROR_RETRIES=%q JUDGE_ERROR_RETRIES=%q QUEUE_MAX_WAIT_SEC=%q ' \
            "$IMPORT_ERROR_RETRIES" "$QA_ERROR_RETRIES" "$JUDGE_ERROR_RETRIES" "$QUEUE_MAX_WAIT_SEC"
        printf 'OPENVIKING_ACCOUNT=%q OPENVIKING_USER=%q E2E_PREFLIGHT=%q ' \
            "$OPENVIKING_ACCOUNT" "$OPENVIKING_USER" "$E2E_PREFLIGHT"
        [[ -n "${HERMES_STATE_DB:-}" ]] && printf 'HERMES_STATE_DB=%q ' "$HERMES_STATE_DB"
        printf './run_full_eval.sh --suite %q' "$SUITE_LABEL"
        [[ "$CHECKPOINT_OPENVIKING" == "1" ]] && printf ' -cp'
        [[ -n "$SAMPLE" ]] && printf ' --sample %q' "$SAMPLE"
        [[ -n "$COUNT" ]] && printf ' --count %q' "$COUNT"
        [[ -n "$FORCE_INGEST" ]] && printf ' --force-ingest'
        [[ -n "$FORCE_EVAL" ]] && printf ' --force-eval'
        [[ "$SKIP_IMPORT" == "1" ]] && printf ' --skip-import'
        [[ -n "$IMPORT_CSV_OVERRIDE" ]] && printf ' --import-csv %q' "$IMPORT_CSV_OVERRIDE"
        printf '\n'
    } > "${RESULT_DIR}/run_command.sh"
    chmod +x "${RESULT_DIR}/run_command.sh"
}

echo "================================================================="
echo "  $BENCHMARK_TITLE"
echo "================================================================="
echo "Suite:          $SUITE_LABEL"
echo "Run id:         $RUN_ID"
echo "LoCoMo data:    $LOCOMO_JSON"
echo "Hermes URL:     $HERMES_URL"
echo "OpenViking URL: $OPENVIKING_URL"
echo "Result dir:     $RESULT_DIR"
echo "Python:         $PYTHON"
echo "Checkpoint:     $([[ "$CHECKPOINT_OPENVIKING" == "1" ]] && echo enabled || echo disabled)"
if [[ "$CHECKPOINT_OPENVIKING" == "1" ]]; then
    echo "Snapshot root:  $SNAPSHOT_ROOT"
fi
echo "Import shape:   flattened LoCoMo session"
echo "Import:         $([[ "$SKIP_IMPORT" == "1" ]] && echo skipped || echo enabled)"
if [[ "$SKIP_IMPORT" != "1" ]]; then
    echo "Import workers: $IMPORT_PARALLEL"
fi
echo "QA workers:     $QA_PARALLEL"
echo "Judge workers:  $JUDGE_PARALLEL"
echo "Prompting:      no PE"
echo "================================================================="

if [[ "$SKIP_IMPORT" != "1" && ( "$SUITE_LABEL" == "e2e" || "$SUITE_LABEL" == "preingest" ) ]]; then
    ensure_openviking_state_source
    export OPENVIKING_STATE_SOURCE
fi

if [[ "$SKIP_IMPORT" == "1" ]]; then
    echo ">>> Preflight: SKIPPED (--skip-import)."
else
    verify_hermes_openviking_target
fi
write_run_command
warm_hermes_memory_provider

echo ""
if [[ "$SKIP_IMPORT" == "1" ]]; then
    echo ">>> Step 1: SKIPPED (--skip-import)."
    IMPORT_RC=0
else
    echo ">>> Step 1: Running import..."
    IMPORT_ARGS=(
        --input "$LOCOMO_JSON"
        --success-csv "$IMPORT_CSV"
    )

    if [[ "$SUITE_LABEL" == "e2e" || "$SUITE_LABEL" == "native" ]]; then
        IMPORT_ARGS+=(--base-url "$HERMES_URL" --model "$HERMES_MODEL")
        [[ -n "$HERMES_TOKEN" ]] && IMPORT_ARGS+=(--token "$HERMES_TOKEN")
    fi

    if [[ "$SUITE_LABEL" == "e2e" ]]; then
        IMPORT_ARGS+=(
            --openviking-url "$OPENVIKING_URL"
            --parallel "$IMPORT_PARALLEL"
            --error-retries "$IMPORT_ERROR_RETRIES"
            --queue-max-wait-sec "$QUEUE_MAX_WAIT_SEC"
        )
    elif [[ "$SUITE_LABEL" == "preingest" ]]; then
        IMPORT_ARGS+=(
            --error-log "${RESULT_DIR}/import_errors.log"
            --openviking-url "$OPENVIKING_URL"
            --account "$OPENVIKING_ACCOUNT"
            --user "$OPENVIKING_USER"
            --parallel "$IMPORT_PARALLEL"
            --error-retries "$IMPORT_ERROR_RETRIES"
            --queue-max-wait-sec "$QUEUE_MAX_WAIT_SEC"
        )
        [[ -n "$OPENVIKING_API_KEY" ]] && IMPORT_ARGS+=(--api-key "$OPENVIKING_API_KEY")
    elif [[ "$SUITE_LABEL" == "native" ]]; then
        IMPORT_ARGS+=(--error-retries "$IMPORT_ERROR_RETRIES")
    fi

    [[ -n "$SAMPLE" ]] && IMPORT_ARGS+=(--sample "$SAMPLE")
    [[ -n "$FORCE_INGEST" ]] && IMPORT_ARGS+=(--force-ingest)
    export QUEUE_MAX_WAIT_SEC
    set +e
    "$PYTHON" "$IMPORT_SCRIPT" "${IMPORT_ARGS[@]}" 2>&1 | tee "${LOG_DIR}/import.log"
    IMPORT_RC=${PIPESTATUS[0]}
    set -e
    if [[ "$IMPORT_RC" -ne 0 ]]; then
        if [[ "$SUITE_LABEL" != "e2e" && "$SUITE_LABEL" != "preingest" ]]; then
            echo ">>> Step 1 import command failed with status $IMPORT_RC." >&2
            exit "$IMPORT_RC"
        fi
        echo ">>> Step 1 import command exited with status $IMPORT_RC; checking OpenViking archive readiness before deciding whether to continue." >&2
    fi
    echo ">>> Step 1 done."

    verify_openviking_archive_readiness
    if [[ "$IMPORT_RC" -ne 0 ]]; then
        echo ">>> Continuing because OpenViking archive readiness passed." >&2
    fi

    copy_openviking_state
fi

echo ""
echo ">>> Step 3: Running QA evaluation..."
EVAL_ARGS=(
    "$LOCOMO_JSON"
    --suite "$EVAL_SUITE"
    --output "$QA_CSV"
    --base-url "$HERMES_URL"
    --model "$HERMES_MODEL"
    --parallel "$QA_PARALLEL"
    --error-retries "$QA_ERROR_RETRIES"
)
if [[ "$SUITE_LABEL" == "e2e" || "$SUITE_LABEL" == "preingest" ]]; then
    EVAL_ARGS+=(--openviking-url "$OPENVIKING_URL")
fi
[[ -n "$HERMES_TOKEN" ]] && EVAL_ARGS+=(--token "$HERMES_TOKEN")
[[ -n "$SAMPLE" ]] && EVAL_ARGS+=(--sample "$SAMPLE")
[[ -n "$COUNT" ]] && EVAL_ARGS+=(--count "$COUNT")
[[ -n "$FORCE_EVAL" ]] && EVAL_ARGS+=(--force)
"$PYTHON" eval.py "${EVAL_ARGS[@]}" 2>&1 | tee "${LOG_DIR}/eval.log"
echo ">>> Step 3 done."

echo ""
echo ">>> Step 4: Judging answers with LLM..."
JUDGE_ARGS=(
    --suite "$EVAL_SUITE"
    --input "$QA_CSV"
    --base-url "$JUDGE_BASE_URL"
    --model "$JUDGE_MODEL"
    --parallel "$JUDGE_PARALLEL"
    --error-retries "$JUDGE_ERROR_RETRIES"
)
[[ -n "$JUDGE_TOKEN" ]] && JUDGE_ARGS+=(--token "$JUDGE_TOKEN")
"$PYTHON" judge.py "${JUDGE_ARGS[@]}" 2>&1 | tee "${LOG_DIR}/judge.log"
echo ">>> Step 4 done."

echo ""
echo ">>> Step 5: Final statistics"
STATS_ARGS=(
    --suite "$EVAL_SUITE"
    --input "$QA_CSV"
    --import-csv "$STATS_IMPORT_CSV"
)
if [[ -n "${HERMES_STATE_DB:-}" ]]; then
    STATS_ARGS+=(--hermes-state-db "$HERMES_STATE_DB")
elif [[ -n "${HERMES_HOME:-}" && -f "${HERMES_HOME}/state.db" ]]; then
    STATS_ARGS+=(--hermes-state-db "${HERMES_HOME}/state.db")
fi
"$PYTHON" stat_judge_result.py "${STATS_ARGS[@]}" 2>&1 | tee "${LOG_DIR}/stats.log"

echo ""
echo "================================================================="
echo "  Benchmark complete"
echo "================================================================="
echo "Result dir: $RESULT_DIR"
if [[ "$CHECKPOINT_OPENVIKING" == "1" ]]; then
    echo "OpenViking checkpoint: $SNAPSHOT_DIR"
    echo "Checkpoint manifest:   $SNAPSHOT_MANIFEST"
fi
