---
name: ov-tasks
description: Pick up, execute, and hand off long-running tasks stored as structured markdown files in OpenViking (task board under viking://agent/tasks/<scope>/). Use when the user says "ov tasks", asks to create a task on the board, hand off the current work, continue a task another agent started, or run an unattended task loop. Each task file is the handoff itself - a frontmatter header (id, status, owner, scope) plus Goal, Context, Decisions, Progress, Next, Questions, Log - so any agent with the `ov` CLI can resume it.
---

# OV Tasks

A task is one markdown file in OpenViking. The file is both the ticket and
the handoff: whoever reads it can continue the work without the previous
conversation. OpenViking stores it, indexes it (`find`/`grep`), and shares
it across every agent and user of the account. There is no other state.

```text
<root>/<scope>/<id>.md            the task (header + handoff body), rewritten every turn
<root>/<scope>/archive/<id>.md    optional append-only history of status transitions
```

- `<root>` is `viking://agent/tasks`. Probe it once per session with
  `ov stat viking://agent/tasks` (or `ov mkdir` when it does not exist yet);
  if the server answers `PERMISSION_DENIED` (older servers block writes under
  `viking://agent` except skills), use `viking://resources/tasks` instead.
  `$OV_TASKS_ROOT`, when set, overrides the probe. The protocol is identical,
  only the root changes.
- `<scope>` is the isolation unit (a project, repo, or team). It is a plain
  directory: `ov acl` on it decides who may read or take tasks. Create one
  with `ov mkdir <root>/<scope>` and `ov mkdir <root>/<scope>/archive`
  (`write --mode create` needs the parent directory to exist).
- `<id>` is `YYYY-MM-DD-short-slug`, unique inside the scope.

Use the `ov` CLI (works from any agent that has a shell). If the OpenViking
MCP tools `list`, `read`, `write`, `grep` are registered, they are
equivalent; never fall back to raw HTTP. Prefer the CLI for writes: a
`write` that runs into the MCP client timeout has usually still landed, so
`read` the file back before writing again.

## Task file

Copy [references/task-template.md](references/task-template.md). Header:

| field | values |
|---|---|
| `id` | `YYYY-MM-DD-short-slug`, equals the file name |
| `title` | one line |
| `status` | `open` · `in_progress` · `needs_user` · `blocked` · `done` · `cancelled` |
| `owner` | agent id currently holding the task, empty when handed off |
| `scope` | the scope directory name |
| `updated` | ISO time with offset, set on every write |

Body sections, in this order: `Goal` (done criteria checklist), `Context`
(cwd/repo/branch, key files or URIs, constraints), `Decisions` (dated, with
why; user answers land here), `Progress` (verified only, with evidence),
`Next` (ordered checklist; first unchecked box is the next action),
`Questions` (concrete questions for the user; non-empty means
`status: needs_user`), `Log` (one dated line per turn, keep the last 10).

The file is the truth. If it disagrees with the live repo, trust the repo,
fix the file, and say so in `Log`.

Write the body in the task's main language: the language the user gave the
task in, or the one an existing task already uses. Header keys and status
values stay English.

## One turn

Every turn is bounded: pick, claim, one slice, verify, write back, stop.

1. **Pick.** List runnable tasks in the scope:
   ```bash
   ov grep '^status: (open|in_progress)' -u <root>/<scope> -x <root>/<scope>/archive -o json
   ```
   Take the task the user named, else an `open` one, else an
   `in_progress` one whose `updated` is older than 2 hours (stale claim).
   Skip `in_progress` tasks another agent updated recently.
2. **Claim.** `ov read` the file. Set `status: in_progress`,
   `owner: <your agent id>`, `updated: now`, add a `Log` line, write back
   (step 5). Agent id is `$OV_TASKS_AGENT` if set, else `<tool>@<host>`
   (e.g. `codex@mbp`).
3. **Slice.** Do the first unchecked item in `Next`, or more while it stays
   one coherent, verifiable piece of work. Read `Context` and `Decisions`
   first; they are the contract from earlier turns and the user.
4. **Verify.** Run the check that proves the slice (test, build, command
   output, diff). Only verified work goes into `Progress`, with its evidence.
5. **Write back.** Rewrite the whole file with the updated header and body:
   ```bash
   ov write <root>/<scope>/<id>.md --from-file ./task.md --wait
   ```
   On `CONFLICT` (path busy) wait a few seconds and retry once; on
   `ALREADY_EXISTS` when creating (`--mode create`) pick another id. Then
   choose the exit state:
   - more to do and you continue next turn → keep `in_progress`;
   - handing off (context nearly full, wrong tool, or the user asked) → clear
     `owner`, set `status: open`, make sure `Next` is precise enough for a
     cold reader;
   - a decision only the user can make → write the question in `Questions`,
     set `status: needs_user`, clear `owner`, then ask the user in the
     current session if there is one and stop;
   - waiting on something external (a PR review, another task) → `blocked`,
     name what unblocks it in `Next`;
   - all `Goal` boxes ticked with evidence → `done`.
6. **Archive (optional).** On every status change append one entry to
   `<root>/<scope>/archive/<id>.md`: the timestamp, `from -> to`, owner, and
   the current `Progress` + `Next` sections. Create it with `--mode create`
   the first time; the task file itself never moves.
   ```bash
   ov write <root>/<scope>/archive/<id>.md --append --from-file ./entry.md --wait
   ```

Creating a task is the same write with `--mode create` and `status: open`;
fill `Goal` and `Context` from the user's request and put the first concrete
step in `Next`.

Answering a `needs_user` task: record the answer in `Decisions`, clear
`Questions`, set `status: open`, write back. The next turn picks it up.

## Long-running and multi-agent

- **Unattended loop.** [references/loop.sh](references/loop.sh) runs one
  agent turn after another (`codex exec` by default, any CLI via
  `OV_TASKS_CMD`) until no runnable task is left or the tick budget is
  spent, and notifies through herdr when it stops. `needs_user` tasks are
  not runnable, so the loop drains around them and stops when only those
  remain.
  ```bash
  zsh references/loop.sh <scope> 5 /path/to/repo
  ```
- **Cross-agent handoff.** Any agent that reads the file continues it;
  Claude, Codex, or a human via `ov tui`. Nothing is tool-specific except
  the agent id in `owner`.
- **Handoff to a person.** A step only a person can do (approve a release,
  run a privileged command) is a `needs_user` gate: name the person and the
  exact action in `Questions`, clear `owner`, stop. When they report back,
  record the outcome in `Decisions` and set `status: open`.
- **Several tasks at once.** Give each subagent its own agent id and one
  task; they never share a file. Serialize tasks that touch the same files
  by listing the dependency in `Next` and setting the later one `blocked`.
- **Retrieval.** `ov find "<topic>" -u <root>` locates related tasks
  across scopes; `ov ls <root>/<scope>` shows abstracts for a quick scan.

## Rules

- Never claim a task another agent updated within the last 2 hours.
- Never write secrets, tokens, or raw logs into a task; write conclusions.
- Never mark `Progress` without evidence, never mark `done` with an
  unchecked `Goal` box.
- Do not run a second task in the same turn; write back, then pick again.
