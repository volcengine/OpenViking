#!/usr/bin/env bash
# loop.sh — run one CLI agent turn after another against an ov-kanban board
# until no runnable task remains or the tick budget is spent.
#
#   bash loop.sh <board> [max_ticks=5] [cwd=$PWD]
#
# env:
#   OV_KANBAN_ROOT   kanban root (default: viking://agent/kanban, falls back to
#                    viking://resources/kanban when the server rejects agent/kanban)
#   OV_KANBAN_AGENT  agent id written as owner  (default codex@<host>)
#   OV_KANBAN_CMD    agent command template with {PROMPT} placeholder
#                    (default: codex exec, non-interactive, prompt from file)
#
# Each tick = one bounded turn of the ov-kanban skill: pick, claim, one slice,
# verify, write back. The task file in OV is the only state between ticks.
# ponytail: max_ticks is the whole quota model; add per-task budgets if a task
# ever spins.
set -uo pipefail

board="${1:?board}"; max_ticks="${2:-5}"; cwd="${3:-$PWD}"
# root: viking://agent/kanban when the server accepts it, else viking://resources/kanban
ov_ok() { command ov "$@" -o json 2>&1 | grep -q '^{"ok":true'; }
if [ -z "${OV_KANBAN_ROOT:-}" ]; then
  if ov_ok stat viking://agent/kanban || ov_ok mkdir viking://agent/kanban; then OV_KANBAN_ROOT=viking://agent/kanban
  else OV_KANBAN_ROOT=viking://resources/kanban; fi
fi
: "${OV_KANBAN_AGENT:=codex@$(hostname -s)}"
[ -n "${OV_KANBAN_CMD:-}" ] || OV_KANBAN_CMD="codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C \"${cwd}\" - < {PROMPT}"
skill_md="$(cd "$(dirname "$0")/.." && pwd)/SKILL.md"
board_uri="${OV_KANBAN_ROOT}/${board}"

# ponytail: counts every open|in_progress task, including one another agent
# claimed minutes ago, so a tick can find nothing to claim. Filter on
# owner/updated here if idle ticks start to cost.
runnable() {
  command ov grep '^status: (open|in_progress)' -u "$board_uri" -x "$board_uri/archive" -o json 2>/dev/null \
    | python3 -c 'import sys,json; d=json.loads(next(l for l in sys.stdin if l.startswith("{"))); print(len({m["uri"] for m in d.get("result",{}).get("matches",[])}))' 2>/dev/null || echo 0
}
notify() { command -v herdr >/dev/null && herdr notification show "$1" --sound done >/dev/null 2>&1; echo "$1"; }

for tick in $(seq 1 "$max_ticks"); do
  n=$(runnable)
  if [ "$n" = 0 ]; then notify "ov-kanban ${board}: no runnable task (needs_user/blocked/done only)"; exit 0; fi
  prompt="$(mktemp "${TMPDIR:-/tmp}/ov-kanban-prompt.XXXXXX")"
  cat > "$prompt" <<PROMPT
Read ${skill_md} and follow it exactly.
Kanban root: ${OV_KANBAN_ROOT}   board: ${board}   your agent id: ${OV_KANBAN_AGENT}
Do exactly ONE turn: pick one runnable task on this board, claim it, do one bounded slice, verify it, fold and write the task file back, then stop.
If the task needs a user answer, set status: needs_user, put the concrete question in ## Questions, write back, and stop.
Do not start a second task in this turn.
PROMPT
  echo "== tick ${tick}/${max_ticks} (${n} runnable) =="
  eval "${OV_KANBAN_CMD//\{PROMPT\}/$prompt}"
  rm -f "$prompt"
done
notify "ov-kanban ${board}: tick budget ${max_ticks} spent, $(runnable) runnable left"
