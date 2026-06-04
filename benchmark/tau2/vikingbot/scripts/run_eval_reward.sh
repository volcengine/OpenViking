#!/usr/bin/env bash
set -euo pipefail

# Evaluate average reward for a given result folder, epoch, and try_no.
# Usage: bash run_eval_reward.sh <result_dir> [epoch] [try_no]

RESULT_DIR=${1:-""}
EPOCH=${2:-0}
TRY_NO=${3:-0}

if [[ -z "${RESULT_DIR}" ]]; then
  echo "Usage: bash run_eval_reward.sh <result_dir> [epoch] [try_no]" >&2
  exit 1
fi

if [[ ! -d "${RESULT_DIR}" ]]; then
  echo "Result dir not found: ${RESULT_DIR}" >&2
  exit 1
fi

RESULT_DIR="${RESULT_DIR}" EPOCH="${EPOCH}" TRY_NO="${TRY_NO}" python3 - <<'PY'
import glob
import json
import os
import sys

result_dir = os.environ.get("RESULT_DIR")
epoch = os.environ.get("EPOCH")
try_no = os.environ.get("TRY_NO")

pattern = os.path.join(result_dir, f"task_*_{epoch}_{try_no}_trajectory.json")
files = sorted(glob.glob(pattern))

if not files:
    print(f"No files matched: {pattern}")
    sys.exit(1)

total = 0.0
count = 0
missing = 0

for path in files:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        reward = data.get("reward")
        if reward is None:
            missing += 1
            continue
        total += float(reward)
        count += 1
    except Exception:
        missing += 1

avg = total / count if count else 0.0
print(f"Result dir: {result_dir}")
print(f"Epoch: {epoch}  Try: {try_no}")
print(f"Matched files: {len(files)}")
print(f"Used rewards: {count}  Missing/invalid: {missing}")
print(f"Average reward: {avg:.6f}")
PY
