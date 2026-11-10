---
name: ov-kanban
description: Work through tasks on a kanban board stored in OpenViking (viking://agent/kanban/<board>/, one markdown file per task). Use when the user says "ov kanban", "kanban", "看板", asks to put a task on the board, hand off the current work, continue a task another agent started, or run an unattended task loop. A board holds many tasks; each task file is the handoff itself - a frontmatter header (id, status, owner, board) plus Goal, Context, Decisions, Progress, Next, Questions, Log - and its process detail is folded into an archive file instead of being summarized away, so any agent with the `ov` CLI can resume it and drill down when needed.
---

# OV Kanban

A board is a directory in OpenViking that holds many tasks. A task is one
markdown file. The file is both the ticket and the handoff: whoever reads it
can continue the work without the previous conversation. OpenViking stores
it, indexes it (`find`/`grep`), and shares it across every agent and user of
the account. There is no other state.

```text
<root>/<board>/<id>.md            the task: header + current state, rewritten every turn
<root>/<board>/archive/<id>.md    its folded detail: append-only, never rewritten
```

- `<root>` is `viking://agent/kanban`. Probe it once per session with
  `ov stat viking://agent/kanban` (or `ov mkdir` when it does not exist yet);
  if the server answers `PERMISSION_DENIED` (older servers block writes under
  `viking://agent` except skills), use `viking://resources/kanban` instead.
  `$OV_KANBAN_ROOT`, when set, overrides the probe. The protocol is identical,
  only the root changes.
- `<board>` is the isolation unit (a project, repo, or team). It is a plain
  directory: `ov acl` on it decides who may read or take tasks. Create one
  with `ov mkdir <root>/<board>` and `ov mkdir <root>/<board>/archive`
  (`write --mode create` needs the parent directory to exist).
- `<id>` is `YYYY-MM-DD-short-slug`, unique inside the board.

Use the `ov` CLI (works from any agent that has a shell). If the OpenViking
MCP tools `list`, `read`, `write`, `grep` are registered, they are
equivalent; never fall back to raw HTTP. Prefer the CLI for writes: a
`write` that runs into the MCP client timeout has usually still landed, so
`read` the file back before writing again.

## Read in layers

Detail is folded, not compressed: a lower layer holds the full text the
upper layer points to. Read top-down and stop at the first layer that
answers the question.

| layer | what it holds | how to read |
|---|---|---|
| boards | one abstract per board | `ov ls <root>` |
| board view | `title`, `status`, `owner`, `updated` of every task | the `ov grep` below |
| task | current state, enough to continue the work | `ov read <root>/<board>/<id>.md` |
| archive | everything folded out of the task, verbatim | `ov grep '<keyword>' -u <root>/<board>/archive/<id>.md`, then `ov read` |

```bash
ov grep '^(title|status|owner|updated):' -u <root>/<board> -x <root>/<board>/archive -o json
```

Open the archive only when a `Decisions` or `Progress` line needs its
history or evidence. `ov find "<topic>" -u <root>` locates related tasks
across boards.

## Task file

Copy [references/task-template.md](references/task-template.md). Header:

| field | values |
|---|---|
| `id` | `YYYY-MM-DD-short-slug`, equals the file name |
| `title` | one line |
| `status` | `open` · `in_progress` · `needs_user` · `blocked` · `done` · `cancelled` |
| `owner` | agent id currently holding the task, empty when handed off |
| `board` | the board directory name |
| `updated` | output of `date +%Y-%m-%dT%H:%M:%S%z`, set on every write |

Body sections, in this order: `Goal` (done criteria checklist), `Context`
(cwd/repo/branch, key files or URIs, constraints), `Decisions` (dated, with
why; user answers land here), `Progress` (verified only, one line per item
with its evidence), `Next` (ordered checklist; first unchecked box is the
next action), `Questions` (concrete questions for the user; non-empty means
`status: needs_user`), `Log` (one dated line per turn, the last 10).

The file is the truth. If it disagrees with the live repo, trust the repo,
fix the file, and say so in `Log`.

Write the body in the task's main language: the language the user gave the
task in, or the one an existing task already uses. Header keys and status
values stay English.

## Fold

The task file stays short enough to read in one call. Text leaves it by
moving to the archive, never by deletion or by a shorter rewrite:

- `Log` lines beyond the last 10;
- `Next` steps and `Decisions` that a later turn replaced;
- evidence longer than one line (command output, diff summary, review
  notes): the `Progress` line keeps the result, the archive keeps the rest.

Append one entry per turn: a `### <updated> <agent id>` heading, the status
change as `from -> to` when there is one, then the moved text verbatim.

```bash
ov write <root>/<board>/archive/<id>.md --append --from-file ./entry.md --wait
```

Create the archive with `--mode create` the first time. A turn that moves
nothing out and keeps the status writes no entry.

## One turn

Every turn is bounded: pick, claim, one slice, verify, write back, stop.

1. **Pick.** Read the board view (the `ov grep` above). Take the task the
   user named, else an `open` one, else an `in_progress` one whose `updated`
   is older than 2 hours (stale claim). Skip `in_progress` tasks another
   agent updated recently.
2. **Claim.** `ov read` the file. Set `status: in_progress`,
   `owner: <your agent id>`, `updated: now`, add a `Log` line, write back
   (step 5). Agent id is `$OV_KANBAN_AGENT` if set, else `<tool>@<host>`
   (e.g. `codex@mbp`).
3. **Slice.** Do the first unchecked item in `Next`, or more while it stays
   one coherent, verifiable piece of work. Read `Context` and `Decisions`
   first; they are the contract from earlier turns and the user.
4. **Verify.** Run the check that proves the slice (test, build, command
   output, diff). Only verified work goes into `Progress`, with its evidence.
5. **Write back.** Fold first (see above), then rewrite the whole file with
   the updated header and body:
   ```bash
   ov write <root>/<board>/<id>.md --from-file ./task.md --wait
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
   - all `Goal` boxes ticked with evidence → `done`;
   - the user dropped the task → `cancelled`, clear `owner`, record who
     decided and why in `Decisions`.

Creating a task is the same write with `--mode create` and `status: open`;
fill `Goal` and `Context` from the user's request and put the first concrete
step in `Next`.

Answering a `needs_user` task: record the answer in `Decisions`, clear
`Questions`, set `status: open`, write back. The next turn picks it up.

## Long-running and multi-agent

- **Unattended loop.** [references/loop.sh](references/loop.sh) runs one
  agent turn after another (`codex exec` by default, any CLI via
  `OV_KANBAN_CMD`) until no runnable task is left or the tick budget is
  spent, and notifies through herdr when it stops. `needs_user` tasks are
  not runnable, so the loop drains around them and stops when only those
  remain.
  ```bash
  bash references/loop.sh <board> 5 /path/to/repo
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

## Rules

- Never claim a task another agent updated within the last 2 hours.
- Never write secrets, tokens, or raw logs into a task or its archive; write
  conclusions and the evidence that backs them.
- Never mark `Progress` without evidence, never mark `done` with an
  unchecked `Goal` box.
- Never drop text from a task file; fold it into the archive.
- Do not run a second task in the same turn; write back, then pick again.
