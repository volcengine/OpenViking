# Windows acceptance closeout (2026-11-08)

Historical comparison baseline: `40a436d8adeeb68b3d9c0e523a25f28e9aa20cce`.
Last Windows-tested candidate: `464c2f19cc9571b115949011425ebefd03995ff0`.

The submitted non-elevated Windows acceptance evidence supports the previous
incremental fixes and the durable + Lark + OpenViking smoke checks. It does not
establish full acceptance of every runtime or the entire acceptance matrix.
The four-file test command still returned exit code 1 (52 passed, 2 failed).
Both failures also reproduced against the preceding candidate.

Evidence review notes: the developer note's command omits the `vika/` prefix
while calling its working directory the repository root; use the command below.
The supplied 18-file archive contains result files and user tasks, but not the
projection script referenced by the report. Its 19 checks are submitted results,
not an independently rerun script. Neither issue invalidates the recorded test
failures or the durable activity evidence.

This follow-up fixes the remaining OpenCode test contracts:

- Compare PATH entries using `path.delimiter`.
- Inject process termination alongside process spawning. Fake children now exit
  through their own lifecycle instead of sending synthetic PIDs to the OS.
  Production defaults still use `terminateProcessTree`; actual Windows process
  tree cancellation remains covered by `windows-process.test.mjs`.
- Explicitly review and register the Windows config-projection branch in the
  platform isolation allowlist. The previous candidate omitted this entry; the
  full suite exposed it during this follow-up. Keep the isolation guard enabled.
- Run `opencode.test.mjs` in the Windows CI matrix, without skipping rotation.

## Final focused Windows check

Run from the **repository root**, as a non-administrator:

```powershell
node --test vika/packages/daemon/test/ov-isolation.test.mjs vika/packages/daemon/test/opencode.test.mjs vika/packages/cli/test/control.test.mjs vika/packages/daemon/test/durable.test.mjs vika/packages/daemon/test/windows-process.test.mjs vika/packages/daemon/test/platform-isolation.test.mjs
```

Record the exact source and installed candidate, exit code, pass/fail/skip counts,
and failure evidence. OpenCode startup must reach the XDG assertions; rotation
must terminate the old fake child and prepare the replacement session. The real
Windows process-tree tests must run too. Do not substitute fake-child success
for real taskkill coverage. No OpenCode installation or runtime switch is needed.

After installing the candidate in the designated test instance, repeat one
durable message, a known OV marker read, and normal restart recovery. Preserve
configuration, credentials, workspace and OV data. Do not run against unrelated
instances.

Successful checks can close this incremental repair round for the Windows
source preview. They do not close the arbitrary-shell ADS/sandbox limitation,
multi-person live isolation without a second authorized identity, hard-kill UI
recovery, unexecuted matrix items, or upstream review/merge. Record local Windows
results separately from CI results. No upstream merge is performed by this work.
