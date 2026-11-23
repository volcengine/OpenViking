# ov-kanban

A kanban board for agents, stored in OpenViking. `SKILL.md` is the protocol
agents follow; this file is for people: the idea and the roadmap.

## Idea

An agent works on a long task across many sessions, tools, and people. Each
session starts cold, so the task state has to live outside the conversation.
The usual answer is to compress: summarize the history, keep the summary,
drop the rest. The summary loses the detail the next turn turns out to need.

ov-kanban folds instead. The board keeps every task's process, arranged in
layers, and the agent reads down only as far as the question requires:

| layer | content | cost |
| --- | --- | --- |
| boards | one abstract per board | one `ov ls` |
| board view | title, status, owner, updated of every task | one `ov grep` |
| task | goal, context, decisions, verified progress, next steps | one `ov read` |
| archive | replaced plans, old log lines, full evidence, verbatim | `ov grep` / `ov read` on demand |

Text leaves a task file by moving to its archive, never by deletion. The
task file stays short, the history stays complete, and an agent that needs a
detail from three weeks ago greps for it.

A board holds many tasks. Agents keep working by taking the next runnable
task off the board: pick, claim, one slice, verify, fold, write back.

## Layout

```text
viking://agent/kanban/<board>/<id>.md            task
viking://agent/kanban/<board>/archive/<id>.md    folded detail
```

The skill and `loop.sh` probe `viking://agent/kanban` with `ov stat` / `ov mkdir`.
Servers that reject that location use `viking://resources/kanban` instead.
Set `OV_KANBAN_ROOT` to choose a root explicitly. The task protocol does not
depend on which root is used; a board's URI and ACL define its boundary.

## Design choices

The task file is the handoff: frontmatter records `id`, `status`, `owner`,
`board`, and `updated`; the body holds Goal, Context, Decisions, Progress,
Next, Questions, and Log. There is no separate handoff object or status store.
Replaced plans and decisions, older Log entries, and evidence longer than one
line move verbatim to the append-only archive. Keep the latest 10 Log entries
in the task, but archive the older entries before removing them.

| Choice | Reason and limit |
| --- | --- |
| One mutable task plus an append-only archive | Replaces separate revision, acknowledgement, and outcome objects. Claiming is recorded in Log; completion is status plus verified Progress. |
| Any CLI agent executes the task; OpenViking stores it | No separate execution kernel, capability/provider hierarchy, or extension runtime is needed. |
| A loop bounded by `max_ticks` | Each turn must write back; `needs_user` tasks stop for input. Scheduling, quota management, and self-repair are outside this protocol. |
| `owner` + `updated`, with a two-hour stale-claim convention | OpenViking writes do not provide compare-and-set. This is coordination by convention, not an atomic lease; concurrent claims can collide. |
| URI and ACL as the board boundary | A second opaque scope ID would duplicate the existing address and permission model. |
| Frontmatter queried with `ov grep` | Mirroring status and owner into retrieval tags would require another write and could drift out of sync. |
| CLI board views | A separate dashboard or Lark projection is deferred until needed. Experience extraction continues through OpenViking's existing `remember` path, outside the task protocol. |

The name `ov-kanban` distinguishes this board from `ov task`, which manages
server-side asynchronous jobs. A board is the task container; “scope” is
reserved for the planned repository/directory/branch matching rules.

## Roadmap

| item | today | target |
| --- | --- | --- |
| Task DAG | A dependency is prose in `Next` plus `status: blocked`. | Header field `blocked_by: [<board>/<id>, ...]`. A task is runnable when every blocker is `done`. The board view shows the runnable frontier, and `loop.sh` picks from it. Edges may cross boards. |
| Scoped kanban | The agent is told the board name. | A board declares its scope (repo, directories, branches) in `<board>/BOARD.md`. Recall matches the session's cwd and branch against board scopes, so a session sees the boards that concern its work and no others. |
| Recall on session start | The agent reads the board when the skill triggers. | Hook plugins inject the board view of matching boards at session start. Depends on scoped kanban. |
| Claim-aware loop | `loop.sh` counts every `open` or `in_progress` task as runnable, so a tick can find nothing to claim. | Count `open` tasks, stale claims, and the caller's own claims. |
| Atomic claim | `owner` + `updated` with a 2-hour convention; two agents can claim the same task at once. | Compare-and-set on write, once OpenViking `write` supports it. |
| `ov compile` | Not supported: compile rejects kanban targets and file sources. | `ov compile --from <board> --to <board> --skill ov-kanban` runs one turn on the server. It also needs an output tool that rewrites task files in place; the existing compile agent submits a wiki bundle. |
