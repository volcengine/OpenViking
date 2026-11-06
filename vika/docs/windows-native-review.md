# Native Windows review

This review covers the SuperAgent Windows changes after baseline `40a26ec`, including unchanged code reached by installation, setup, Lark media, OpenViking connections, runtime execution, and PM2 control. It does not cover porting the separate OpenViking server. See [the Windows preview guide](../WINDOWS.md) for supported installation steps.

## Findings addressed

- **Private media directories:** POSIX mode checks rejected Windows directories, blocking received Lark media, outgoing file/card snapshots, and the Lark image cache. `private-temp.mjs` now uses `windows-acl.mjs` on Windows. Only a newly created dedicated leaf directory receives a protected, current-user-only DACL. Existing directories must already have current-user ownership, protected inheritance, and no third-party allow ACE; they are never silently repaired. Symlinks and junctions remain rejected.
- **PowerShell module compatibility:** a PowerShell 7 parent can export a module path incompatible with Windows PowerShell 5.1. Production ACL calls and their test helpers remove every casing of `PSModulePath`, allowing Windows PowerShell to establish its own defaults. The fixed encoded script receives paths through environment data, not interpolated command text. Diagnostic causes retain a category and exit information without raw commands, paths, or stderr.
- **Command execution and quoting:** runtime argv stays in native argument arrays. Supported npm shims resolve to their Node entrypoints, including extensionless entrypoints, without evaluating arbitrary batch commands. Windows command examples use PowerShell invocation syntax. Pi and Durable select native PowerShell coding tools.
- **Autostart:** VBS output uses UTF-16LE with a BOM so Unicode paths survive Windows Script Host decoding. The startup path now respects `APPDATA` instead of unconditionally assuming the default profile layout.
- **PM2:** the CLI uses the local Node entrypoint. Windows control uses installation-scoped named pipes, handles release aliases during verification, and excludes unsupported release-updater operations. Daemon and web processes accept graceful shutdown IPC.

## Remaining limitations

### Process cleanup after the parent has exited

`packages/daemon/src/process-utils.mjs` uses `taskkill /PID <parent> /T /F`. This terminates a live parent's tree, but does not provide a verified guarantee for descendants surviving an already-exited parent. The current Windows process test keeps the parent alive. A stronger guarantee requires ownership independent of the live parent, such as a Windows Job Object. A nonzero taskkill exit is not proof that descendants were cleaned up.

### Filesystem and local-user trust

The directory checks and ACL changes are separate path-based operations; they do not claim protection against a malicious local process racing filesystem changes. ACL validation starts Windows PowerShell on each check, which adds overhead to attachment operations. Existing broad directories are rejected intentionally and require an explicit migration decision.

Named-pipe transport retains action authority checks, but uses Windows' default pipe ACL. This preview targets trusted single-user workstations. Credentials and configuration elsewhere are not automatically protected by the media-directory ACL helper.

`APPDATA` handles ordinary roaming-profile redirection. An independently redirected Startup known folder may still require resolution through the Windows known-folder API.

### Unsupported and unverified scenarios

Email remains disabled because its account-file ACL support is not implemented. Automatic release upgrades remain disabled. Arbitrary batch wrappers are rejected. Interactive service authentication, real connector deliveries, and a complete authenticated OpenViking workflow need end-to-end Windows validation. A credential-free installer or PM2 smoke test is not evidence that those external integrations work.

## Validation evidence and scope

- The Windows workflow contains native tests for argv/shims, named pipes, process termination, shell commands, autostart, PM2, ACLs, and coding tools. It also invokes the actual source installer in a disposable Unicode home with npm lifecycle scripts enabled and checks the installed launchers/binaries. Workflow presence does not itself establish a successful run; consult the run associated with the reviewed commit.
- ACL tests cover new directories, unchanged rejection of broad existing ACLs, explicit third-party allow rules, junctions, Unicode paths, and inherited incompatible PowerShell module paths. Real caller tests exercise Lark media preparation/pruning, SQLite image-cache persistence, and outgoing file snapshot cleanup.
- The local POSIX Lark/media/action regression run passed **100 tests**. Native ACL tests are skipped on Linux, so that result does not validate NTFS behavior.
- A Linux full-suite run at `bdccae0` reported **1211 passed, 0 failed, 2 cancelled, 14 skipped**; it was not a clean full-suite pass. The cancelled OV lifecycle tests were independently reproduced at baseline `40a26ec` in a detached checkout: **17 passed, 2 cancelled** in the focused file.
- The OV fixture waited for an unreferenced `AbortSignal.timeout` without the socket handle a real HTTP request would retain. A test-only, bounded watchdog now keeps the mock request alive until abort, then clears itself. The focused lifecycle file passes **19/19** after that change. The subsequent Linux full-suite run passed **1213 tests, 0 failed, 0 cancelled, 14 skipped** (1227 total). Windows-only skips are covered separately by native CI; the Linux total does not establish native Windows correctness.

The cancelled lifecycle tests predate the Windows port; their fix changes the test fixture, not production timeout behavior.

Final code-bearing Windows CI: [42199232146](https://github.com/openviking-superagent/OpenViking/actions/runs/42199232146), commit `e26a9a6`, succeeded with 25 native tests and 58 Feishu/OpenViking contract tests, zero failures/skips, plus the real Unicode-home installer smoke. The installer installed dependencies with lifecycle scripts, ran Pi 1.4.0 and Lark CLI 1.0.94, checked both launchers, and refused an existing installation. This does not replace authenticated external-service acceptance.
