# Windows fork review resolution and acceptance handoff

This maps the review of upstream PR #7178 at `21ef6630e` to the installed-source
`vika-win` fork, starting at `3af2496`. They are different scopes: the upstream
PR is a minimal platform adapter; this fork also has an installer, private PM2
transport, ACL checks, PowerShell tools and login startup. Do not remove working
fork features to reproduce an upstream preview limitation.

The acceptance target is Windows x64, Node 24, a non-administrator shell,
Durable and Feishu with an existing OV service. Docker/Linux is a reference,
not a native Windows test. Pin the same source commit, runtime, model, effort
and connector options for parity. Other runtimes/architectures are not claims
of this handoff.

## Review disposition

| ID | Fork disposition |
| --- | --- |
| C-1 | Fixed: POSIX spawn, callback execFile and promisified execFile now use native overloads unchanged. Differential tests cover omitted args, options in the second position, undefined args, callback results and the Promise child handle. Windows overloads are normalized separately. |
| C-2 | Retained deliberate, pre-existing POSIX correction: expandHome concatenates the literal home instead of interpreting replacement patterns such as `$&` and `$$`. This is not claimed as unchanged legacy behavior. |
| C-3 | Not present: this fork's PLATFORMS still contains exactly four Linux/macOS release targets, without sourceOnly. A test freezes that set without building a release. |
| W-1 | Different fork contract: native user Startup-folder support already exists. Keep and test enable/disable in an isolated home; no systemd or system service is expected on Windows. |
| W-2 | Fixed: normalize Path/PATH before constructing the child environment and adding bundled CLI directories. Ecosystem also preserves inherited Path on Windows. Native PATH test plus simulated three-platform ecosystem tests added. |
| W-3 | Still limited: long argv-based prompts or Lark markdown can exceed Windows command-line limits. Durable host uses stdin, but Lark CLI long-text delivery still needs native tests. No universal file/stdin migration is claimed. |
| W-4 | Fixed: Windows ecosystem omits vika-updater, including a start without --only. Linux/macOS retain it. Tests evaluate configurations without starting PM2. |
| W-5 | Upstream-only limitation: installed fork has current, lifecycle commands and the private PM2 shim. Updating a checkout alone does not update the installed copy. |
| W-6 | Retest installed fork login against its private PM2 home. An unmanaged external PM2 receiver is not presumed controlled by vika login; stop that specific receiver explicitly before reauthentication. |
| W-7 | Not resolved: deterministic action-pipe name and default Windows DACL/server identity have not been independently certified. Cross-user access and pipe squatting remain security gates. Authority checks alone do not establish transport isolation. |
| W-8 | Partial fix: no taskkill for a child known to have exited or received a terminating signal. Forced termination, leader-crash orphan descendants and remaining PID races still need native verification; this is not Job Object containment. |
| W-9 | Still unverified: locks/receiver leases use PID liveness. No claim of PID-reuse protection; test in disposable instances. |
| W-10 | Slack CLI is not bundled; Windows read tools remain unverified and are outside the Durable/Feishu acceptance claim. |
| W-11 | Different installer: ensure-windows-lark-cli invokes the pinned npm package's checksum-verifying installer and checks executable/version. It does not use the upstream review's ZIP extraction code. Use the PowerShell source installer; test download/permission failures natively. |
| W-12 | Documented target: x64/Node 24. Shared engines >=22.19 is not Windows certification on Node 22. ARM64 is outside scope. |

The isolation fixture inventories exact platform-branch and direct child-process
import lines in source/scripts/root CJS. Changes require explicit fixture review.
It tracks existing debt, not semantic equivalence or security proof. Native
process overloads and lifecycle tests remain necessary.

## Applying the acceptance checklist

Keep the original IDs in evidence. None of the mappings below grants PASS to
an unexecuted case.

| Original section | Fork interpretation |
| --- | --- |
| 0 Environment | Main target is non-elevated x64/Node 24. Record installed commit and PowerShell version. Chinese/space paths, OneDrive, antivirus, long paths and a second user need separate evidence. Missing environments are BLOCKED. Never publish full environments or credentials. |
| 1 Install/setup | Use install-windows.ps1. PM2 is bundled/shimmed and Durable uses PowerShell tools; global PM2/Git Bash are not prerequisites. Setup should start the installed instance, not fail for missing current. Preserve login/config; test cancellation, invalid paths, case-insensitive overlap and npm 12 lifecycle blocking. Email remains unsupported. |
| 2 Lifecycle | Validate private .vika/pm2, crash recovery, sleep/network and unrelated application isolation. Fork startup enable/disable is supported. Ecosystem must omit updater. Crash/orphan/PID tests are not waived by the exited-child guard. |
| 3 Connectors | Feishu private/group/thread delivery, media, cards, choices, reactions, retraction and long Unicode text require real credentials and a test group. Missing scopes are BLOCKED. Other connectors need their own results. |
| 4 Runtime | Durable: tools, interrupt/steer, host restart, model switching, /new, effective /effort, settings persistence and recall. Model self-description is not actual effort evidence. Do not replace PowerShell tools with Git Bash to match upstream-only assumptions. |
| 5 Delivery/tools | Preserve stale-decision refusal, thread routing, idempotency, UNCERTAIN handling, silence and timers. Prefer deterministic fixtures when an LLM cannot reliably force a case. Compare structures/outcomes, not exact model prose. |
| 6 OV | Use a separate test identity for writes, epochs, reconnect, isolation and recall. Do not stop shared OV or alter production credentials to inject faults. Record archive HTTP 404 behavior independently of Windows parity. |
| 7 Files | Test absolute/drive-relative/UNC paths, traversal, junction escape, CRLF, Unicode and attachment limits in disposable workspaces. OneDrive state directories are not a SQLite durability baseline. |
| 8 Upgrade | Automatic release upgrade remains disabled. Preserve state/config/old release on manual reinstall; remove only the junction, not its target. No packaged release was built or validated here. |
| 9 Security | Retain existing current-user ACL checks in the fork. Cross-user pipes, squatting and PID ownership remain gates. Localhost binding is not user authentication; shared-machine activity visibility remains a limitation. |
| 10 Diagnostics | Installed logs use .vika/pm2/logs, not global .pm2. Use bounded pm2 logs --nostream --lines N. Redact secrets. PS7 needs ConvertFrom-Json -AsHashtable for case-distinct PM2 keys. |
| 11 Parity | Compare identical source revisions, not just this fork versus an older Docker release. Normalize paths/time/generated IDs; preserve deterministic codes/schemas. Scope comparisons to Durable and configured connectors. |
| 12 Regression | Linux syntax/tests locally; macOS/Windows on native hosts. Platform simulation is configuration evidence only. Check release-platform membership without publishing/building artifacts. Never run service management tests against the development assistant itself. |

## Focused native retest

1. Record installed commit, Node version and non-elevated identity. Open a new
   terminal to avoid stale PATH. Updating Git alone does not update installed files.
2. Start Durable through the installed supervisor and an isolated foreground
   PowerShell setup with mixed-case Path. Execute git --version and node -v in
   the tool environment. Capture relevant PATH directories, not all environment variables.
3. Run platform.test.mjs, process-utils.test.mjs, windows-process.test.mjs,
   platform-isolation.test.mjs and windows-review-regression.test.mjs. Linux skips
   do not count as Windows success.
4. Inspect ecosystem app names before starting anything: vika-updater must be
   absent. Manage only disposable named instances; verify unrelated PM2 apps
   remain unchanged. Do not restart the assistant's own deployment.
5. Repeat Durable/Feishu/file/OV/restart recall. Then run original long-markdown,
   orphan-grandchild and cross-user pipe cases in isolated accounts. Preserve
   evidence of failures, never credentials or tokens.
6. Each original ID needs PASS/FAIL/BLOCKED/N.A., commit, environment, time,
   commands, sanitized evidence and reason. No all-pass statement is valid while
   security gates are unexecuted.
