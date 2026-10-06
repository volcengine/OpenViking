# Working memory is opt-in

OpenViking now defaults to `working_memory.enabled=false`. A commit still stores
raw messages and schedules long-term memory extraction. It does not generate an
archive overview or a retention checkpoint summary unless WM is explicitly
enabled. This is not a switch for long-term memory, recall, profiles, or tools.

## Upgrade the server and plugins

Updating the OpenViking Python package does **not** update copies installed in
agent hosts. Update each installed plugin using its installation instructions,
then restart that host. Do this before moving a conversation that depends on OV
history to native history management. Old takeover plugins are not supported
with the new default; there is no server-side workaround for their polling.

| Integration | New default | Explicit opt-in |
| --- | --- | --- |
| Claude Code, Codex / TraeCode CLI, OpenCode | No automatic archive injection on resume/compact; host history remains authoritative | `resumeArchiveInject: true` enables supplemental archive injection, not summary generation |
| Pi official extension | Native Pi compaction; no takeover or resume archive injection | `takeoverEnabled: true`; commits request WM explicitly |
| OpenClaw | `contextManagementMode: "native"`; keep host messages and delegate compaction to the host | `contextManagementMode: "openviking"`; commits request WM explicitly |
| VikingBot | `session_context_enabled: true`; OV manages history and compaction, and commits explicitly request WM | Set `session_context_enabled: false` to use VikingBot's existing local mode |
| LangChain / LangGraph | Middleware captures/recalls without fetching OV session history | Legacy OV history adapters require WM and an explicit application decision |

Existing explicit plugin settings and environment variables still take priority
over defaults. Remove them or set them to false to switch an installation that
previously enabled takeover/injection. Installers do not overwrite that choice.
Hermes, WorkBuddy, AstrBot and other capture/recall integrations inherit the
server default; their host history is not replaced by this change.

The Pi experimental context-management extension is excluded from this release's
WM-off adaptation. Do not use it as a WM-off history backend.

## Persisted policies and queued work

The previous serializer omitted `working_memory` when it was true. Consequently,
even a policy explicitly enabled through Studio or the normal user/session save
APIs may have lost that field. A selected policy without it now means **false**;
the original user intent cannot be reconstructed. Re-enable WM explicitly if
needed. An original configuration that still contains true is preserved when
that policy is selected. Policy precedence still selects a whole policy; missing
fields are not filled from a lower-priority policy.

New serialization always includes the WM boolean. New queue snapshots include
`memory_policy_version: 1`. Legacy queue/recovery snapshots without a version
retain their submission-time WM=true semantics when the WM field is absent.
Such already-submitted tasks can still generate a summary after upgrade. This
does not mark saved user/session policies as opted in. Drain old tasks before
upgrade if no post-upgrade WM generation is acceptable.

## Explicit per-commit generation

`POST /api/v1/sessions/{id}/commit` accepts `enable_working_memory`:

- Omitted or `null`: use the resolved session/user/server policy.
- `true` or `false`: override **only WM for this commit**, without changing
  `self`, `peer`, `memory_types`, or the saved policy.
- Strings and numbers are rejected. The response reports
  `effective_enable_working_memory` from the actual commit policy.

The updated takeover integrations require a true confirmation before relying on
a summary. Missing confirmation, disabled WM, or a completed/failed archive with
no usable summary leaves native history in place. Pi checks the archive state
while polling, re-reads once at the terminal boundary, then immediately falls
back to native compaction instead of waiting for its timeout.

## What history APIs return

`GET /sessions/{id}/context` is a bounded prompt view, not a complete transcript.
An existing eligible summary can still be read after the generation policy is
turned off. Once a newer WM-off archive completes, its boundary stops the view:
the overview is empty and only retained/active messages and newer pending raw
messages remain. It does not resurrect an older summary or replay all archives.
A normal WM-off completion is not a failed archive.

`GET /sessions/{id}/archives/{archive_id}` returns raw `messages` for a completed
WM-off archive, with `overview: ""` and `abstract: ""`. Explicit archive tools
continue to work. Pending, failed, missing, or corrupt archives retain their
existing error behavior. Recall can still use eligible older summaries internally;
turning off generation does not delete stored history.

## Applications and old conversations

For LangChain use `with_openviking_memory(..., history_factory=...)` with a host
history provider. The legacy `with_openviking_context` without a provider warns
that it depends on OV history. Middleware defaults to
`include_session_context=False`. Put it before native summarization middleware
so raw messages are captured before the framework replaces them. Message capture
watermarks are persisted in graph state. See the runnable
[native history example](https://github.com/volcengine/OpenViking/blob/main/examples/langchain-langgraph/langgraph/middleware/native_history.py)
and its pinned requirements for LangChain summarization with a SQLite checkpointer.

VikingBot is an exception to the host-history default: it still uses OV Working
Memory for context and compaction. Its session-context commits explicitly send
`enable_working_memory: true`, including for existing sessions whose saved
policy disables WM. It keeps the existing commit-and-clear flow; subsequent
context reads use OV's overview/checkpoints and retained messages, or pending
archive messages while generation is still running. If the server does not
confirm WM generation, the commit is treated as unsuccessful and local history
is kept for retry. This change does not replace VikingBot's local compaction.

Some old OV-managed conversations no longer have a full host transcript. Stop
writes and use the [history export tool](https://github.com/volcengine/OpenViking/blob/main/examples/session-history-export/README.md)
to prepare a new host conversation through its supported file/context input.
Verify the host has the necessary history before continuing there. Export alone
is **not** an imported or verified migration; the tool never edits private host
transcripts, changes policies, or deletes the source. Do not automatically
switch a history-dependent conversation when its raw source is incomplete.

## Diagnostics and rollback

OpenClaw health checks only require an overview when the commit confirms WM=true.
Pi live E2E defaults to native history; use `E2E_TAKEOVER=1` to test explicit
takeover. Tests that exercise WM/checkpoints explicitly opt in.

Re-enabling WM affects future commits; it does not recreate missing summaries
for previous archives. Retain raw backups and the updated plugin when rolling
back server behavior. A legacy client may reapply old takeover boundaries and
must not silently replace a conversation already migrated to host history.
