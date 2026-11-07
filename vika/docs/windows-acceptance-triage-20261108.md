# Windows acceptance follow-up (2026-11-08)

Baseline: `625a53699d9ff93c727cf3e0934524780724416c`. The submitted Windows report uses Linux release `24e73d5a` as a behavioral reference, not a same-commit control. Its blocked and unexecuted cases remain unverified.

## Implemented changes

| Issue | Change | Verification and limits |
| --- | --- | --- |
| fork #12 | Durable's structured Windows read/write/edit tools reject ADS colons, reserved DOS names, invalid characters, trailing dots/spaces and ambiguous drive-relative paths. | Unit test checks rejection before IO. This does **not** intercept arbitrary PowerShell commands such as `Set-Content`: shell execution is not a filesystem sandbox. The reported shell reproduction therefore remains outside this guard. |
| fork #13 | Reject unsupported direct Windows script entrypoints with an explicit interpreter hint before CreateProcess. | Native executables, Node entrypoints and recognized npm shims retain their existing behavior. POSIX invocation remains unchanged. |
| fork #14 | New daemon locks include process creation identity (Linux process start ticks; Windows process StartTime ticks). Reclaim a live PID only when the recorded and observed identities differ. | Missing/failed identity queries and legacy live locks fail closed. No unrelated process is killed. A legacy lock containing only PID cannot establish PID reuse and may still require operator recovery after confirming the original daemon is gone. |
| fork #15 | `vika logs primary --lines 5 --no-follow` returns a bounded snapshot. `--nostream` is an alias. | Default follow behavior is preserved for interactive users. Child spawn errors reject, rather than hanging. |
| upstream #7210 | Apply existing value/key redaction to the whole logger record, including message, base/child context and top-level fields. | Synthetic secret regression only; this is pattern-based redaction, not a guarantee to recognize arbitrary unlabeled secrets. |
| upstream #7208 | Filter routine debug/info/warn structured records from connector crash fallback text. | Preserve explicit worker failure reports, error records and unstructured crash text. Exit code/signal remains in the diagnostic even when no detail exists. |
| upstream #7211 | Emit terminal failed/cancelled tool activity when Durable stops or a turn ends with unfinished tools. | Tool ID and duration are preserved; Windows UI recovery needs a real stop-during-tool retest. |

## Review items kept open

- **#7204 shell boundary:** setting cwd or checking realpath on a few arguments cannot sandbox arbitrary shell code. A real boundary requires an execution sandbox/OS policy and a product decision about host-machine access. Do not advertise a regex or path wrapper as security isolation. Workspace upload containment and shell host access are separate contracts.
- **#7205 log rotation:** observed growth establishes append behavior, not a leak. Long-running PM2 log retention is a separate deployment improvement. Do not substitute a process memory limit for log rotation. No rotation module is installed by these changes.
- **#7206 peer IDs:** current source explicitly permits `@` and `.` (`openviking/core/identifiers.py`, `openviking/ingest/peer.py`); Vika's peer tests follow that contract. Verify the deployed OV version and request before changing persistent identity mapping, which could split existing memories. This report used OV v0.5.3.
- **#7207 tests:** require the precise failing test names and outputs. Do not skip all 33 reported failures merely to obtain green results; distinguish inherently POSIX fixtures from cross-platform product regressions. Focused Windows CI now includes the new acceptance regressions.
- **#7209 authority:** rejected old authority is expected protection. The 31 failures alone do not identify which client process held the old token. Capture runtime/epoch and client lifecycle evidence (never token contents) to reproduce before changing authority ownership. Never fix this by letting a stale client fetch fresh authority.
- **HOME vs USERPROFILE:** separate installation root selection from default workspace selection. Explicit workspace paths remain authoritative; no home migration is performed here.

## Windows retest

Use the actual updated installation copy, not just the checkout. Record its full SHA. Run from a non-elevated PowerShell 7 session.

1. Exercise the structured Durable write/edit/read tools with `a:b.txt`, `CON.txt`, a trailing dot/space, and normal Chinese/space-containing paths. Invalid paths must fail before IO; normal files must round-trip. Do not claim arbitrary shell commands are sandboxed.
2. Invoke a `.ps1` directly through `executableInvocation`: require the explicit unsupported-entrypoint diagnostic. Explicit `pwsh.exe -File` and npm Node shims must still work.
3. On an isolated test instance, test same process identity rejection, changed identity recovery, unknown identity fail-closed and normal release/reacquire. Do not modify the live production lock or kill unrelated processes.
4. `vika logs primary --lines 5 --no-follow` must exit promptly. Plain `vika logs primary` should continue following until cancelled.
5. Log synthetic secret samples in message, base context, nested fields and top-level secret keys. Inspect only test logs; never use real credentials as fixtures.
6. Stop an isolated Durable instance during a long tool call. Its activity must become terminal and stop accumulating elapsed time. Restart and verify a fresh Feishu turn.
7. Repeat normal Feishu send/receive, workspace file IO, OV write/recall and PM2 lifecycle checks. Keep blocked ACL/second-user cases separate from passes.
