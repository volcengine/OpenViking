#!/bin/zsh
# loop.sh — run one CLI agent turn after another against an ov-tasks scope
# until no runnable task remains or the tick budget is spent.
#
#   zsh loop.sh <scope> [max_ticks=5] [cwd=$PWD]
#
# env:
#   OV_TASKS_ROOT   task board root (default: viking://agent/tasks, falls back to
#                   viking://resources/tasks when the server rejects agent/tasks)
#   OV_TASKS_AGENT  agent id written as owner  (default codex@<host>)
#   OV_TASKS_CMD    agent command template with {PROMPT} placeholder
#                   (default: codex exec, non-interactive, prompt from file)
#
# Each tick = one bounded turn of the ov-tasks skill: pick, claim, one slice,
# verify, write back. The task file in OV is the only state between ticks.
# ponytail: max_ticks is the whole quota model; add per-task budgets if a task
# ever spins.
set -uo pipefail

scope="${1:?scope}"; max_ticks="${2:-5}"; cwd="${3:-$PWD}"
# root: viking://agent/tasks when the server accepts it, else viking://resources/tasks
ov_ok() { command ov "$@" -o json 2>&1 | grep -q '^{"ok":true'; }
if [ -z "${OV_TASKS_ROOT:-}" ]; then
  if ov_ok stat viking://agent/tasks || ov_ok mkdir viking://agent/tasks; then OV_TASKS_ROOT=viking://agent/tasks
  else OV_TASKS_ROOT=viking://resources/tasks; fi
fi
: "${OV_TASKS_AGENT:=codex@$(hostname -s)}"
: "${OV_TASKS_CMD:=codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C ${cwd} - < {PROMPT\}}"
skill_md="${0:A:h:h}/SKILL.md"
scope_uri="${OV_TASKS_ROOT}/${scope}"

runnable() {
  command ov grep '^status: (open|in_progress)' -u "$scope_uri" -x "$scope_uri/archive" -o json 2>/dev/null \
    | python3 -c 'import sys,json; d=json.loads(next(l for l in sys.stdin if l.startswith("{"))); print(len({m["uri"] for m in d.get("result",{}).get("matches",[])}))' 2>/dev/null || echo 0
}
notify() { command -v herdr >/dev/null && herdr notification show "$1" --sound done >/dev/null 2>&1; echo "$1"; }

for tick in $(seq 1 "$max_ticks"); do
  n=$(runnable)
  if [ "$n" = 0 ]; then notify "ov-tasks ${scope}: no runnable task (needs_user/blocked/done only)"; exit 0; fi
  prompt="$(mktemp -t ov-tasks-prompt.XXXXXX.md)"
  cat > "$prompt" <<PROMPT
Read ${skill_md} and follow it exactly.
Task board root: ${OV_TASKS_ROOT}   scope: ${scope}   your agent id: ${OV_TASKS_AGENT}
Do exactly ONE turn: pick one runnable task in this scope, claim it, do one bounded slice, verify it, write the task file back, then stop.
If the task needs a user answer, set status: needs_user, put the concrete question in ## Questions, write back, and stop.
Do not start a second task in this turn.
PROMPT
  echo "== tick ${tick}/${max_ticks} (${n} runnable) =="
  eval "${OV_TASKS_CMD//\{PROMPT\}/$prompt}"
done
notify "ov-tasks ${scope}: tick budget ${max_ticks} spent, $(runnable) runnable left"
