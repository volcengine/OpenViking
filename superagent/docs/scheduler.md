# Scheduler

How accepted inputs reach the model. Set per instance with `agents[].scheduler`
in `config.json` (`OV_SUPERAGENT_SCHEDULER` in the daemon env), read once at start.
`ov-superagent setup` writes it for a new instance: `workers` on Pi, Codex,
TraeX or Claude Code, `legacy` on other runtimes. An instance without it runs
`legacy`, and editing an instance in setup keeps what it has; to change it,
edit `config.json` and restart the instance.

| Value | Behaviour |
|---|---|
| `legacy` (default) | Every input goes into the running turn as soon as it arrives (Codex/TraeX `turn/steer`, Pi `steer`, ...); an idle runtime starts a turn with it. Group lines that arrive within 400 ms start one turn together. |
| `inbox` | While the model works, inputs wait and the model is told they are there; it reads them at a checkpoint. |
| `workers` | The inbox, and the main session becomes a router: it hands longer work to worker sessions (on the router's runtime unless `/workers` sets another) and relays their results. |

With `legacy`, the prompt, tool list and policyHash stay the same. The ready
inbox table is created on first use by the inbox and workers schedulers.

## Inbox

Two problems with putting every input into the running turn at once:

- A long task is interrupted by every new line, from every chat.
- Under load the model tends to answer the newest line and lose the older ones.

The inbox (`packages/daemon/src/inbox.mjs`) changes when and how inputs reach the
model, not what they look like: an input line is the same line either way, with
the same `event=`, `msg=` and authenticated tags.

### Flow

```
Connector event → Harness (validate, dedupe, ledger marks pending preparation)
                → ordered context preparation → ready in inbox_pending
                                                         │
          runtime idle ──────────────────────────────────┤── wake: start a turn with the oldest
                                                         │   waiting inputs (up to 20; the rest
                                                         │   are noticed in that turn)
          runtime working ───────────────────────────────┤── notice: one [daemon inbox] line,
                                                         │   ~3 s after the first input of a burst
          message_check / held delivery ─────────────────┘── deliver: oldest first, through the
                                                             runtime's own submit path
```

- **Wake.** An idle runtime starts a turn with the oldest waiting inputs. A
  direct line starts it at once, a group line after the legacy 400 ms burst
  window, so a burst still starts one turn.
- **Notice.** While a turn runs, the first waiting input of a burst arms a
  3-second timer that later inputs do not extend. The notice is one line per
  chat with new inputs: where (`chat=`/`thread=`/`source=`/`type=`), how many
  wait there, the latest senders, and `owner=1` / `mention=me` when the owner
  or a mention of the agent is among them. Its header counts every waiting
  input. It never carries message text, and it is not an input: nothing is
  decided on it. A chat already noticed is named again only when new inputs
  arrive there.
- **`message_check`.** The model reads waiting inputs at a checkpoint in its
  work, oldest first, optionally from one chat or thread. They are delivered
  through `AgentRuntime.submit`, so the adapter books them like any steered
  input: the cognition journal, the turn's inbound seqs (and so the decision
  check at turn end), and the `agent.steered` activity.
- **Turn end.** Inputs still waiting when a turn ends start the next one.

### Interplay with the Core

- **Freshness fence.** Unchanged: a delivery is still refused when a newer
  accepted input exists in its scope. Under the inbox that input may still be
  waiting, so the model could not reconsider anything. The refusal then
  delivers that scope's waiting inputs at once and says so; a `stay_silent` on
  a superseded input does the same.
- **Decisions.** An input counts as consumed when it is delivered, by a wake,
  `message_check` or a held delivery, exactly as a steered input does. Inputs
  never delivered are never nudged.
- **Durability.** The inbox and workers schedulers mark ordinary model inputs
  as pending preparation in the same transaction that accepts them. The Harness
  prepares their context in ingress order and atomically clears the marker and
  writes a ready row when handing off to the inbox. Only ready rows can appear
  in a notice or be delivered by `message_check`. OpenViking history sync pauses
  at the first pending preparation. A failed preparation gets two retries while
  the daemon runs. After the last failure, the daemon keeps the accepted event
  in history and clears the marker so later entries can sync. On restart, the
  daemon loads ready rows first, then queues pending preparations by their
  original `seq` before subscribing to live Connector events. A restart into
  `legacy` also recovers and drains these rows. The same event ID still deduplicates
  against the ledger. Once `AgentRuntime.submit` returns `accepted: true`,
  delivery belongs to the runtime and follows its usual turn recovery rules.
- **Expiry and upgrade.** The event age limit (`OV_SUPERAGENT_MAX_EVENT_AGE_MS`)
  starts at receipt for pending preparations and at ready-row creation for
  regular ready inputs. Maintenance protects pending preparations during that
  window, then clears their markers and applies ordinary ledger retention.
  Ready task reports keep their existing expiry exception. On upgrade, existing
  `DONE` rows start with the marker off and existing ready rows still recover.
  An input in the legacy 400 ms group window can be lost when the process stops.
- **Runtimes.** The inbox needs the Core tools (`message_check`). Ark has none,
  so a manifest with `scheduler: "inbox"` on Ark is rejected; switching to Ark
  at run time drains ready inputs by scope through the runtime's submit path.
  The inbox retries rejected batches and removes rows once the runtime accepts
  them. Pending preparations that qualified for the inbox use the same handoff.
  An input queued behind a switch to a runtime with Core tools goes through the
  inbox. During normal operation on Ark, new inputs follow the legacy route.
  A runtime that cannot take input mid-turn (OpenCode, `activeInput: queue-next-turn`)
  gets no notices. Its waiting inputs start its next turn.
- **Slash commands, approvals, meeting notes.** The Core handles slash commands,
  approval replies and dedicated event handlers before inbox preparation.
  Daemon notes about a chat (`[mail reply sent]`) and undecided-line reminders
  wait in the inbox like inputs. The meeting inbox keeps its own notice.

### Observing it

Activities `inbox.notified` (how many wait, how many are new) and
`inbox.delivered` (`reason`: `wake`, `check`, `held_delivery` or `drain`, the
inbound seqs, and `waitedMs`, the longest wait among them).

## Workers

With `workers`, the main session is a **router**. It still reads every input,
through the inbox above, and answers what is quick. A request that needs real
work (running or changing code, investigating, research, writing a document)
becomes a **task** (`packages/daemon/src/tasks.mjs`), and a **worker** does it:
a separate session, on the runtime the workers' settings name (the router's
unless the owner set another with `/workers`). The worker never talks to
people; it reports, and the router relays the report where the request was
made. The router stays the only outlet, so every check on the delivery path
(authority, freshness fence, thread placement, bot fuse, approvals, outbox
idempotency) applies to what a worker produced, without a second copy.

```
input ─▶ inbox ─▶ router ──task_create / task_route──▶ task (SQLite) ──▶ worker turn
                    ▲                                                      │
                    └──────── [task report] (through the inbox) ◀── task_report
router ──reply_message / send_message──▶ ActionService ──▶ Connector
```

### Tasks

A task is a row in `tasks` (created on first use, like `inbox_pending`): its
origin (the input the result answers: `event=`, `msg=`, chat, thread, requester),
title, the router's note (≤ 30 words of routing facts, never a plan), status,
the worker's session, and the latest summary, draft, question and artifacts.
`task_inputs` holds every line routed to it, in order. `task_input_claims`
associates inputs with the turn reading them (including `task_check`); only a
successful turn acknowledges them. Failed or interrupted turns release their
claims so the inputs can be replayed. `task_reports` is the append-only list of
reports.

Status: `queued` (waiting for a slot), `running`, `waiting` (a question for a
person), and the ended `done`, `blocked`, `dropped`, `failed`, `stopped`.

`waiting` is the only status that keeps a task live without a worker on it:
its worker asked one thing a person can answer or do (decide, tell, grant an
access, confirm), and goes on in the same round when they do. A round its
worker cannot finish for a missing access or prerequisite that no one can
clear now ends `blocked`, like one that `failed`: the requester is told what
is missing, and the input that clears it reopens the task for a round. A
`blocked` task used to stay live until someone wrote to it again. Nothing
aged it: on one long-running instance six sat for up to twelve days with no
worker, counted as in progress by `/tasks`, kept in every input's working set
and holding their directories, one of them blocked only because its worker
could not edit a pull request's description after the work was done. A
daemon that starts on such tasks ends each, with its round, when its worker
last moved it.

A `waiting` task can wait as long: nobody may ever answer. With nothing for
`OV_SUPERAGENT_TASK_DORMANT_DAYS` (5 by default: a question asked on a Friday
is still near on Monday; 0 turns it off) it is **dormant**. It stays live: an
answer still goes on in the same round, and its directory stays held. But it
leaves the working set, so it no longer takes one of the three places in
every nearby input's `[daemon tasks]` line, nor counts as waiting for its
requester on everything they say in a direct chat. A reply to its messages
or a line in its thread still finds it, as do `task_list` with `for_event`,
`query` or its id; the default `task_list` names the dormant tasks in one
line, and `/tasks` lists them apart, in one line at the end. The router is
told that a question nobody answered for days drops out of its hints, and to
look an answer that comes that late up with `for_event`. Of tasks with equal
weight in a hint, the one that moved last comes first, not the one created
first: a live task created days ago no longer goes before one that ended
minutes ago.

### A task is one result, pushed forward in rounds

A week of online traffic (221 tasks) showed what happens when a task takes
whatever arrives near it: 307 of 398 follow-ups reopened a task that had
already reported, some a day later, and a task became "the thread's worker".
About a fifth of the follow-ups asked for another result (a PR after an
assessment, a review of a new revision, an unrelated bug), and people's talk
among themselves went to the worker as if it were the requester's. A follow-up
sent to a task that was still working waited for that turn to end: four
minutes at the median and over two hours at p90.

Refusing every input to an ended task fixed that, at a cost. Work that keeps
going on one result after it was delivered (a pull request reviewed, fixed and
rebased over a dozen exchanges) became a chain of tasks, each following the
last. A replay of twelve such exchanges on one pull request made ten tasks:
the router was shown three same-thread tasks with near-identical titles, every
task cloned the repository again (17 clones), and every task's first turn
copied the whole chain's session (1.7 MB of sessions for a final session of
265 KB). The copy did not even keep the prefix cache: the first model call of
each forked task read the inherited history uncached (2048 of up to 52k tokens
cached), because the system prompt named each task's own directory (see
Workers), while the router, carrying on its own session, had nearly all of it
cached.

So a task is one result someone keeps pushing forward (an answer, a fix, a
pull request, a document), and it takes only what is about that result:

- **The router decides by what an input asks for.** Input that changes,
  narrows, corrects or adds a condition to a task's work, or answers its
  question, is `owner` input for it while it works. After it reported, more
  work on what it delivered (a change, fix or re-run of its pull request,
  branch or document; the fix for what its investigation found) is `owner`
  input too, and reopens it. Another result (another question or answer, a
  review, a document, a fix or change of something else) is a new task, which
  **follows** the task it builds on (`task_create` with `follows`,
  `parent_id`): its brief carries that task's request, its last round's
  request, summary, last answer, artifacts and directory. When unsure, the
  router creates the new task: a wrong split costs a fresh start from that
  task's work, a wrong merge makes a worker do unrelated work and keeps the
  person waiting behind it. People's talk, a remark on a result included,
  never reopens a task. The `[daemon tasks]` line lists the tasks near an
  input (same thread as any of their rounds, a reply to their messages,
  waiting for this person), what each is doing (`working`, `waiting for … for
  2d`, `done 12m ago, 3 rounds`) and the first 40 characters of what its current
  round asks (`now on:`) or its last one asked (`last asked:`): a title is
  written when a task is created and says nothing of its later rounds. A
  reply to a message the router sent for a task counts as a reply to that
  task's message whether the router decided on the round's origin or on a
  later input there: the delivery that settled the task's report names that
  message (about 4% of relayed results in replays were otherwise not linked).
  "Same requester" is no longer a reason, and similar words only rank what an
  exact signal found, or point the owner across chats. Similar words look only
  at the working set (live tasks but the dormant ones, and the 32 that ended
  last, about a day of tasks online); the exact signals reach past it: the task a line replies to,
  and the latest ten tasks asked in its thread, however long ago. Every one of
  these reads goes through an index (the working set by end time, a message's
  task, a thread's tasks), so a hint costs about the same with 100 000 tasks as
  with 1 000 (1.5 ms measured; reading the working set by status took 89 ms at
  100 000). The rule for a working task is the one from before rounds,
  unchanged.
- **An ended task takes a new request as a round.** `owner` input to a task
  that is `done`, `blocked`, `dropped` or `failed` reopens it for its next round
  (`task_rounds`); `context` reopens nothing, and a `stopped` task stays
  stopped (work on it again is a new task that follows it). A round records
  where it was asked and by whom: its report says `round=N`, its origin is that
  round's message, so the answer goes there and an answer there settles that
  round's report. A round that reopens a task which ended `blocked` or
  `failed` is told so, with why, and to check first whether that still
  stands. A delivery that names the task (`task_ids`) settles the
  reports of the round it answers, never the task's other rounds: the round
  it was decided on (its origin, or another input of it), else the rounds
  asked where it went; only an answer given where no round was asked settles
  every round. So when two rounds' reports wait, asked in two threads, the
  reply in one leaves the other offered. Reports of earlier rounds stay
  deliverable while the task works on the next. The working task holds its directory again: what the sweep did to it
  is undone where it can be (a removed directory is made again, empty, and its
  clean-up marks are cleared) and the brief says what is gone. The worker's
  session carries on when it is still there and under 4 MB; otherwise
  (deleted with the directory, cleaned up by Claude Code, Codex or TraeX themselves,
  or too large) the round starts a fresh session from the task record, round
  by round. A turn whose CLI says the session it resumed is gone ("No
  conversation found", "no rollout found") runs again the same way instead of
  failing the task. A round may take the follow-up slot.
- **A round that is not the task's never happened.** When the worker hands
  back every request line of a round, nothing of that round is relayed (a
  report it filed is kept, settled as handed back), and the task goes back to
  how it ended, with the summary and answer of its last real round (not of an
  earlier round handed back too). The router
  decides on the handed-back lines as new inputs.
- **One request, one live task.** `task_create` refuses a line a live task
  already holds as part of its request; `task_route` refuses lines from another
  chat than the task's unless the owner wrote them.
- **Live input for workers.** New task inputs reach a working worker within a
  step instead of after its turn. Nothing is pushed: the worker's own runtime
  pulls them (`task_check` under the worker's authority) at two points, after a
  model step or tool call and when the run is about to end, and adds them to the
  session; at the second point the run goes on instead of ending.
  - Pi: `pi-mcp-extension.mjs` on `turn_end` and `agent_before_settle`.
  - Claude Code, Codex and TraeX: `task-input-hook.mjs` as a `PostToolUse` hook
    (`additionalContext`) and a `Stop` hook (`decision: block`), the hook format
    the three CLIs share. Claude Code gets them in the worker's `--settings`;
    Codex and TraeX as `-c hooks.<event>=[…]` overrides for that run, which need
    `--dangerously-bypass-hook-trust` (worker runs already bypass approvals and
    the sandbox). Nothing is written to `~/.codex`, `~/.trae` or `~/.claude`.
  Both points are where Pi's own `steer` delivers too; a long tool call still
  finishes first.
- **Workers hand back what is not theirs.** `task_handback` (worker) returns
  routed lines that ask for something else; a task that fails or is stopped
  hands back the follow-ups no turn ever took. Both reach the router as one
  `[task handback]` inbox item with the original lines; it decides on them as
  new inputs and never routes them back to that task.

### Router tools

| Tool | Does |
|---|---|
| `task_create` | A task from the named input lines (`trigger_ids`, the first one is the origin), a title, an optional note, and `follows` when it builds on a task's result as a separate one. |
| `task_route` | Lines for a task: `owner` (something on the result it works toward or delivered; for an ended task, a new round) or `context` (background for a task at work). Refused for a stopped task, and `context` for an ended one. |
| `task_stop` | Stops a task and its worker; only when the requester or the owner asks. |
| `task_list` | The working set (every live task but the dormant ones, named in one line, and the 32 most recently ended), one task in detail, the tasks one person asked for (`for_event`), or a search over titles, dormant tasks included. Each line says how long a task has been working and whether its latest result was sent (how long ago, as which message). |

Routing an input to a task records it as decided (`decisions`, `silent`), so the
turn-end check does not ask again; the relayed result records the delivery over
it.

### Workers

A worker turn is one run of its task's runtime CLI in its non-interactive
mode, resuming the task's own session (`worker-executor.mjs`):

| Runtime | Turn | First turn of a task that follows one on the same runtime |
|---|---|---|
| Pi | `pi --mode json -p --session-id <task> ...` | `--fork <its session file>` |
| Codex | `codex exec --json ...`, then `codex exec resume <thread>` | `codex exec ... fork <its thread>` |
| TraeX | `traex exec --json ...`, then `traex exec resume <thread>` | `traex exec ... fork <its thread>` |
| Claude Code | `claude --print --output-format stream-json --session-id/--resume <task>` | `--resume <its session> --fork-session --session-id <task>` |

A task that follows another starts from a copy of that task's session when the
session is still there and at most 4 MB, else from the summary in its brief.
The copy is meant to keep the provider's prefix cache, and how much it keeps
depends on the runtime. First model call of a copy into another task's
directory, measured:

- **Pi: 97% cached.** Pi names the working directory at the end of the system
  prompt and records a new one as a later system update, but folds that update
  into the first system message for providers without mid-conversation system
  messages (`openai-completions` by default): one changed character before all
  the history, and only 2048 tokens were cached. So every worker's system
  prompt names the same directory, "the task directory your brief names"
  (`pi-mcp-extension.mjs`, on `before_agent_start`); the brief names it, and
  the worker's shell and file tools start there.
- **Claude Code: the whole copy.** It appends the change of directory after
  the copied history.
- **TraeX: 98% cached.** Its exec is Codex's (the same flags, events, hooks
  and thread files, in `~/.trae/cli/sessions`), but the copy keeps its cache;
  measured through the daemon's executor, a follows task's first turn into
  another task's directory.
- **Codex: only its fixed prefix** (system prompt and tools, about 12k
  tokens). Codex keys the prompt cache by thread (`prompt_cache_key`), which a
  fork changes, and nothing sets it. Resuming a task's own thread, as each
  later turn and round does, is 99% cached.

No process runs between turns, so an idle task holds nothing; Durable,
OpenCode and Ark have no worker mode, and `task_create` tells the router to
handle the request itself when workers are set to run there. A task keeps its
runtime while it works (see Runtime, model and effort).
Three turns run at once, plus one slot only a follow-up to an existing task may
take. Each task works in a directory under `<workspace>/tasks/` (see Task
directories).

A worker's own shell is not capped like the router's: it may build, test and
wait. Pi's `bash` alone has no default timeout, so a Pi worker's command
without a `timeout` is stopped after 300 seconds and none may ask for more than
3600; the worker's policy says so, for builds and test runs that need longer.
Codex returns a long command's output after at most 30 seconds and lets it run
on; Claude Code gives a command 2 minutes unless it asks for up to 10. TraeX
waits for a command to end, so a TraeX worker's commands are stopped after an
hour (`--shell-tool-timeout`), the longest a Pi worker's may run, and its
policy says so.
Every worker is told to search with the bundled `rg` rather than `grep -r`, in
its own directory first, and never over a whole home directory, `/`, or the
OpenViking SuperAgent installation and data. Its working directory is its task
directory, but nothing else confines it.

A worker's policy is `agent/worker.md`, the router's rules that hold for its
work too, its shell rules, the instance Role, the writing rules, and its
Standing preferences. The shared rules are lines of `core.md`, `openviking.md`
and the instance's connector sections that end with `<!-- worker -->` (the
information boundary, the ordinary work a request authorizes, reading
OpenViking, the mailbox), under the headings they sit under there; the router
reads the same lines without the marker. Standing preferences are the owner's
and the current round's requester's, read from OpenViking for every turn as
the router's recall reads them after a line (`OVRecall.preferencesFor`); a
turn goes on without them when OpenViking is slow or down.

Its first input is the brief: the original input lines, who asked and where,
how a reply reads there (the origin connector's lines marked
`<!-- draft -->`: plain text, length, quoting), the router's note, the task it
follows, the requester's earlier tasks in that chat (in a direct chat, all of
theirs), and its directory. Its Core tools are `task_check` (lines routed to it
since it last looked), `task_report`, `task_list` (only what its requester asked
for, scoped like that list) and the reads (chat and thread history, search,
media, mail, OpenViking reads); never a delivery, a decision, a timer or a
memory write. Its authority token is valid for its turn only.

A turn ends with the latest final `task_report` (`done` with a ready-to-send
draft, `needs_user` with one question, `blocked`, `dropped`, `failed`), or,
without one, the worker's last words as an `unstructured=1` result. Before a
report is relayed, inputs the requester added during the turn must be in it:
if any were not read, the worker runs again (up to three times). Past that the
report goes out as the result so far, marked `working=1`, while the worker goes
on with the newest input. A turn that failed for a passing reason (429, 5xx, a
reset connection) runs again, twice at most, unless its worker had already
filed its final report: that report stands, the inputs the worker had taken by
then are acknowledged, and only lines it took after the report make a catch-up
turn. A model error Pi retried past by itself does not fail the turn.

A successful turn records a session checkpoint. A failed or interrupted turn
starts a new session with the persisted request history, routing note, previous
summary/draft and artifacts, and an instruction to check existing work before
repeating outside actions. A preallocated session ID or turn counter alone is
never evidence that Claude can resume a session. Recovery can repeat work;
outside actions are not guaranteed to run exactly once.


### Runtime, model and effort

Workers have settings of their own, apart from the router's
(`worker-settings.mjs`, kept in SQLite): the runtime, and per runtime the
model and effort. The owner sets them with `/workers` in a direct chat
(`/workers runtime pi`, `/workers model <name>` or `<runtime>:<name>`,
`/workers effort high`, `/workers fallback claude`, `… default` / `router` to
go back). Unset, they follow the router, as workers did before: the router's
runtime, and its model and effort when the worker runs on the same runtime
(the CLI's own default on another). So the router can run where workers
cannot (DSH, Ark) and hand work to workers on Codex, or the two can use
different models on one runtime.

A task takes its runtime, model and effort when it starts, and keeps them as
long as its session goes on. Changing a setting, or the router's runtime,
never moves a task that can carry on where it is: tasks at work finish where
they started, and a task that reopens for a new round goes on in its session,
on its runtime, model and effort. Only a new task, or a round whose session
cannot go on (gone, too large, or never checkpointed), takes the settings of
the moment; such a round starts a fresh session anyway, from a handover when
that is another runtime. The one exception is a runtime that cannot serve:

- **Out of quota.** A turn that fails with a used-up quota, usage or billing
  limit (Codex "You've hit your usage limit", Claude Code "5-hour limit
  reached", `insufficient_quota`, an overdue balance, a 402) marks its runtime
  out for 30 minutes, for every task. The task moves at once, without a retry
  on that runtime. While a runtime is out, new tasks and new rounds start on
  the fallback, and a queued task about to take a turn there moves before it
  runs. After the pause, the first task to try the runtime finds out whether
  it is back. Setting a runtime with `/workers runtime` or `/workers fallback`
  lifts its pause.
- **Still failing.** A turn that fails for a passing reason (5xx, overloaded,
  a reset connection, a dropped stream: what the CLI calls transient or the
  router's failure policy calls recoverable) runs again on its runtime twice,
  as before; if it still fails, the task moves.

A task that must move goes to the runtime workers are set to, when that is
another one, else to the fallback: `/workers fallback`, by default the
router's `fallbackRuntime` when workers can run there. Never to a runtime that
is out itself. One round moves at most twice for failures, so two failing
runtimes are not tried back and forth; with nowhere to go, the failure is
reported as before. A failure another runtime would share (a cancelled turn,
a rejected key, a bad request) moves nothing. A turn whose worker filed its
final report before the failure is complete: the report stands, and the
runtime is only marked out.

### Handover

One runtime cannot resume another's session. A task that moves (a new round
whose session cannot go on, on the runtime workers use now, or a runtime that
cannot serve) records a
handover (`task_handovers`): where it was (runtime, model, the session its last
turn ran in and that session's file) and why it moves. Its next turn starts a
new session on the new runtime, in the same task directory, from a brief that
begins with the handover:

```
[handover] You take this task over from a worker that ran on codex (gpt-5.6-sol). codex cannot serve any more for now: its quota or usage limit is used up ("You've hit your usage limit. …").
You run on pi, in the same directory (<workspace>/tasks/t12), with whatever that worker left there. Before you go on, check where the work stands (files, git status and branches) and what was already done outside the directory (commits, pushes, pull requests, comments, documents); do not repeat an outside action that was already done.
The last messages of that worker's session, oldest first (clipped; tool output left out):
| [input] [task t12] …
| [worker] Implemented slugify; tests pass. Pushing the branch next.
| [tool calls] exec_command {"cmd":"git push -u origin slugify"}
Its whole session, if you need more of it: ~/.codex/sessions/2026/11/19/rollout-…-<thread>.jsonl (Codex rollout JSONL: …). It is long: search or read only the parts you need.
The task record follows.
[task t12] …   (the rebuilt brief: every round's request, context, note, directory, summary, last answer, artifacts)
```

The last messages (`worker-transcript.mjs`) are read from the end of the
session file each runtime keeps (Pi in `<dataDir>/workers/<task>/`, Codex and
TraeX rollouts in their homes, Claude Code under `~/.claude/projects/`): the
inputs the worker got, its own words, and one line per tool call; at most
eight entries and 6,000 characters. Tool output and reasoning are left out:
they are long, and the directory holds the real state. The whole file stays
where it is, for the new worker to read when it needs more; earlier sessions
of the task are named too. Until a turn on the new runtime completes, every
brief carries the handover (a failed first turn, or a restart, does not lose
it); after that the new session carries on as any other.

`/tasks t12` shows a task's runtime, model and effort, and its handovers.

### Task directories

A task works in `<workspace>/tasks/<id>/`, where its worker clones, installs
and builds; its policy says to keep all of that inside the directory. Nothing
deleted these directories, and each can hold a clone, `node_modules`, virtual
environments and build output. The download caches (`~/.npm`, pip, uv) are
already shared, since workers use the same home.

What an ended task left is deleted in stages, counted from when it ended:

| After | Deleted | Kept |
|---|---|---|
| 3 days (`OV_SUPERAGENT_TASK_DIR_PRUNE_DAYS`) | Dependency folders: `node_modules`, Python virtual environments (a folder with `pyvenv.cfg`), `__pycache__` and the test/type-checker caches, `.tox`, `.nox`, `.gradle`, `.next`, `.nuxt`, `.turbo`, `.parcel-cache`, and `target` next to `Cargo.toml` or `pom.xml` | Sources, changes, `.git`, results |
| 14 days (`OV_SUPERAGENT_TASK_DIR_REMOVE_DAYS`) | The whole directory, and its worker's sessions | The task record |

Past `OV_SUPERAGENT_TASK_DIR_BUDGET_GB` (20 by default) for all task
directories, dependency folders go first and whole directories after, those of
the task that ended longest ago first. 0 turns each limit off. A directory a
live task works in, or reads as its parent's, is never touched, nor are those
of a stopped task until its worker process has exited, nor anything outside
`<workspace>/tasks/t<n>`. Links are never followed: a folder is deleted only
if it is still a real directory reached with no link on the way, and the trash
is removed as a whole, so a link in its place goes as a link. A task that
follows one whose directory was deleted starts from its report, as when its
session cannot be forked; one whose parent lost its dependency folders is told
to install again what it needs.

A directory whose git repositories hold work that exists nowhere else is not
deleted whole for its age: changes not committed (untracked files included),
or commits on a branch, a tag, a detached HEAD or the stash that no
remote-tracking branch has (a repository with no remote has nothing pushed).
That is often the very work a `blocked` task could not push. Its dependency
folders still go after 3 days; the directory goes only past the budget,
after every directory without such work, with a `task_dir_removed_unsaved`
warning. A repository git cannot answer for (a timeout, an error) counts as
holding such work; a `.git` git does not take for a repository counts as
none. The sweep asks git only about the directories it may delete whole,
read-only (`git status`, `git log --all --not --remotes`), with no optional
locks, no `core.fsmonitor`, and never about a repository above the one it
names.

The daemon sweeps a minute after it starts and every hour. Measuring runs in
the background; choosing what to delete and moving it to a trash folder next
to it happen in one step, so a task never starts in, or reads, a directory
being deleted, and the trash is emptied after. A round that reopens a task
makes it live again in that step's sense: what was already moved away is gone
(the round is told so), nothing more is. A directory a task used while
it was being measured waits for the next sweep. `/tasks` shows how much the
task directories took at the last sweep. Claude Code, Codex and TraeX keep
their own session files in their homes; those are not swept.

### What the router sees

```
[task report] task=t3 round=2 state=done title="排查飞书名字泄露" requester=陈锦
origin: chat=oc_... thread=omt_... source=lark msg=om_... event=...
summary:
| ...
draft:
| ...
```

`round=` appears once a task has had more than one round; the origin is that
round's. Worker text is quoted (`| `), so it cannot pass for an input line. The router
receives the full stored draft (up to the 20,000-character reporting limit)
and relays it with `reply_message` to the origin `msg=`, as written: the worker
wrote it with the same preferences, writing rules and channel limits, and what
happened since reaches the worker as input. The router adds only the line a
`working=1` report asks for, joins reports answered in one place, sends a draft
the channel refuses for its length as a file, and holds back one that would
break the information boundary (the requester hears it waits for the owner's
check, and the owner gets the draft with `notify_owner`).

Ending a task and selecting its final report for delivery are committed together
in SQLite (`task_report_deliveries`). The inbox row, report's delivery timestamp
and report-to-inbox receipt then commit in one transaction, before waking the
router. The report ID is the idempotency key; consuming the inbox row does not
erase its receipt. Failed writes retry automatically and pending deliveries are
recovered at startup, without rerunning the worker. Task reports in the inbox
do not expire under the incoming-event age limit.

A report is **settled** when the router answers for its task, not when it was
shown: a final delivery or `stay_silent` that names the task (`task_ids`,
offered on those tools only with this scheduler) or is made on its origin. An
answer to a later input of the task settles nothing, since it may be an
acknowledgement. Each report carries a receipt (`task-report:<id>`) that the
runtimes return when a router turn ends:

- a finished turn that left it unsettled offers it once more, marked
  `undelivered=1`; past that it is logged as `task.report_unrelayed`;
- a failed, abandoned or stopped turn offers it again as it was (up to three
  times), and so does a restart after a turn that held it;
- a delivery naming a task whose worker is working again (new input from the
  requester's side came in after that report) is refused: its next report
  replaces the old one. A `working=1` report is the exception until it is
  settled: it went out on purpose, past the catch-up turns.

So a report is neither lost to a failed router turn nor relayed twice after one.
The settlement records how (`settled_by`: the tool, or `unrelayed`) and, for a
delivery, its outbox row, which is what `task_list` and `/tasks` show.

**Progress questions.** "Is mine done?" is answered from `task_list` without new
work: `for_event` with the question's `event=` lists what its sender asked for,
newest first, ended tasks included. In a group that is only what they asked in
that chat; in a direct chat, everything they asked for. A result already sent is
pointed to (when, which message) and its conclusion restated; a draft that was
never sent is sent then. Being the same person is not a routing signal (see the
hint above): it only scopes this lookup.
Settling on "the router turn succeeded" instead would do both: a turn that
skipped a report would bury it, and a turn that relayed it and then failed would
offer it again with nothing to say it was already sent (the outbox key includes
the reply text, so a rewritten reply is a new message).

### Interplay with the Core

- **OnIt.** The router acknowledges a task with a temporary OnIt. A turn's end
  takes OnIt reactions down except in a scope where a task is still `queued` or
  `running`; the relayed result takes those down.
- **Router tools.** With workers scheduling, each router turn gets six native
  work-tool calls (shell commands, file reads/edits and searches). The shared
  `router-tool-budget.mjs` counter refuses later calls with a pointer to
  `task_create`. Pi uses its `tool_call` handler; Codex, TraeX and Claude Code
  use `PreToolUse` hooks backed by the daemon's authenticated action socket.
  Parallel calls share the budget. Turn completion, failure or cancellation
  resets it; steering new input into an active turn does not. Workers, Core
  MCP tools and transport wrappers do not consume the budget, so routing and
  replying remain available. Codex's hosted web search does not pass through
  PreToolUse and is outside this limit. Pi additionally caps router bash at
  30 seconds and disallows `sleep`; those shell-specific rules remain Pi-only.
- **Restart.** Tasks, inputs and reports are in SQLite. A turn cut by a
  shutdown is not reported; at start its task runs again from the persisted
  request history, told to check what it already did before repeating an outside
  action. Selected final reports still awaiting the inbox are delivered again
  through their idempotent receipt.

The owner sees the tasks with `/tasks` in a direct chat (how many are running,
queued, waiting for a reply and dormant; the tasks with a worker on them, then
the questions waiting, the latest first; recent ended ones; the dormant ones
in one line; slots; whether each result reached the requester). On Lark the
overview is a card (Card JSON 2.0, Lark 7.20 or later): a table per group of
tasks, with the status as a coloured tag, every @ of what people wrote shown
as ＠ (Lark refuses a bot message with an email address in it); elsewhere,
and when Lark refuses the card, it is the text. A group shows by its name
(clipped), a direct chat as `飞书私聊` / `微信私聊` (it has no name). At the
end of the card a dropdown lists every task it shows: choosing one sends that
task's card, exactly as `/tasks t12` would. The choice comes back as a card
callback on a card this daemon registered when it sent it, and runs as that
command only in a direct chat, only `/tasks` and only a task id, with the
owner checks of a typed command and without the model. `/tasks t12` is a card
too: its facts, its requests round by round, the user's additions and extra
context (each with its round, its kind and whether its worker has read it),
its results (one per round, plus any a newer addition superseded) and its
runtime moves; the text says the same. Both cards end with when they were
drawn and a 刷新 button: it runs the `/tasks` (or `/tasks t12`) that drew the
card, as a card callback under the same rules, and redraws that card in place
rather than send a new one, off the queue the Core takes messages in: waiting
on Lark holds up no message. For a few seconds after a click on a card, Lark
refuses an edit of it (230099 with ErrCode 11310 "card action is lock" or 200800
"create universal card fail"): the Core makes the edit again a second later, up
to four tries, for every card it edits. Only the newest press on a card is
drawn: a press made while the card is being drawn takes the place of any still
waiting, and the drawing stops waiting for the card. A press only ever edits
its card. Only when Lark says the card is too old to edit (230031, 14 days) does
the answer go as a new card; a card still busy at the last try is being pressed
again, and any other refusal is logged, with nothing sent. Each press logs what
became of it (`command_card_refresh`). The owner stops a task with
`/tasks stop t12`; `/status` counts the tasks
at work. `/workers` shows and sets what workers run on (see Runtime, model and
effort). The Core answers these without the model.

Activities: `task.created` (with `parent`), `task.reopened` (round, the
status it had, what it found of its directory and session),
`task.turn_started`, `task.turn_completed` (with usage), `task.reported`,
`task.handed_back`, `task.round_handed_back`, `task.report_unrelayed`,
`task.ended`, `task.dirs_cleaned` (what a sweep deleted and freed),
`task.runtime_switched` (from, to, why, whether a session was handed over).
