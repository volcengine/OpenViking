# Native build reuse in CI

PR checks use `.github/scripts/ci_plan.py` and a merge-base diff (including both
sides of renames). Changes to native code, build configuration, dependencies or
CI helpers retain all five wheel platforms. CLI-only and Studio-only changes
build a Linux x86 wheel; Python runtime changes run API tests without building
release wheels. Independent plugin and SDK changes run their own checks.
Manual distribution builds and releases retain the full default matrix.

PR checks, API tests and Rust CLI checks cancel older runs for the same PR.
Release and scheduled runs are not cancelled by this policy.

The API workflow produces one Ubuntu 24.04 x86 native runtime with Python 3.10.
Its API test job (3.10) and cuVS job (3.11) reuse that same-run artifact. Both
native extensions target `cp310-abi3`. Restore requires matching commit, OS,
architecture, runtime libc, platform, build options and ABI, and validates every
file checksum before writing it. Other test platforms build locally. Release
wheels build independently in Ubuntu 20.04/macOS/Windows; Ubuntu 24.04 test
binaries and musl CLI binaries are never reused in release wheels.

Linux x64 CI tests and builds in `volcengine/OpenViking` default to
`[self-hosted, Linux, X64, ov-ci-runner]`: PR checks and their SDK/plugin tests,
distribution builds, Rust CLI tests and musl cross-builds, API/cuVS/effect tests,
Hermes/framework/host-compatibility tests, Gateway tests, CodeQL and docs builds,
and the amd64 Docker image build. This includes PRs whose head branch is in a
fork. Publishing, deployment and repository maintenance jobs retain their
existing runners. macOS, Windows and native ARM matrix entries keep their
original runners; matrix names and required check contexts stay unchanged.

The exception is `P0 Memory Tests` in `oc2ov_test.yml`: it requires
`[self-hosted, Linux, X64, openviking-ecs]` to use its original host and the
services installed there. Add the custom label `openviking-ecs` to that runner
and `ov-ci-runner` to the new build runner. Display names do not select runners,
and generic Linux labels would allow the memory suite onto either machine.

Manual API runs default to `native_runner=ov-ci-runner`; select
`native_runner=github-hosted` to override only the shared native build, or
`linux_only=true` for a focused manual trial.
The host needs a running Docker daemon accessible to the runner service and
outbound access to Ubuntu packages, GitHub, Rust and Python dependencies.
Ordinary self-hosted jobs run in Ubuntu 24.04 containers with explicit bootstrap
dependencies; release wheels retain their Ubuntu 20.04 build container. API and
cuVS consumers use the same Ubuntu 24.04 glibc/platform as the native bundle;
bundle validation remains mandatory. Gateway Redis/kind tests and Docker image
builds use the host Docker daemon directly and require an Ubuntu host with
passwordless sudo. Gateway jobs use isolated Python environments, run-specific
container/cluster names and unconditional cleanup of their own Docker resources.
Workflows running in fork repositories use GitHub-hosted runners. Manual defaults
retain the full API platform matrix. Automatic PR builds execute contributed code
on the dedicated host; use a disposable runner without unrelated credentials or
network access to production services.
A missing label or offline runner leaves affected jobs queued; there is no
automatic fallback. One runner service executes one job at a time, so multiple
jobs and matrix entries queue on that service.

The cuVS check now lives in the API workflow so it can consume the same artifact.
Its check name stays `cuvs-tests / Lite Test (ubuntu-24.04, 3.11)`, matching the
previous PR workflow. The caller job ID, reusable job name, Python version and
test list are preserved, so ordinary required status checks need no migration.

Cargo intermediates and ccache outputs are keyed by runner OS/architecture,
target and compiler fingerprint. Cargo also includes the lockfile. Both primary
keys end with the commit SHA so later main builds can save updated compiler
output into new immutable entries. Prefix restores select the newest compatible
cache, preferring the same Cargo lockfile before other entries for that toolchain.
Only non-PR runs on the official repository's `main`
branch save these caches; PR, fork and tag runs restore only. Ccache is capped at
256 MiB per entry and Cargo incremental output is disabled. Older main entries
remain subject to GitHub's cache quota and eviction. Source changes still rebuild
correctly through Cargo/ccache input validation.
Wheel building compiles each native component once.
Studio is built once per distribution workflow and downloaded by each wheel job.
Bootstrap/version/repair/test commands use `uv run --no-sync`; test environments
install dependencies first, then native binaries, then editable Python entry
points with native compilation disabled.

Markdown is not globally ignored at the workflow entry point or by the planner.
Known documentation trees and root README/security/contribution documents
skip API tests; runtime skills, prompt templates and bot workspace Markdown
select API tests, including newly introduced runtime paths.

Local checks:

```sh
python3 -m unittest discover -s .github/scripts -p test_ci_helpers.py -v
actionlint .github/workflows/pr.yml .github/workflows/api_test.yml \
  .github/workflows/_build.yml .github/workflows/_test_lite.yml \
  .github/workflows/_test_full.yml .github/workflows/rust-cli.yml
```

`.github/actionlint.yaml` registers both custom runner labels for validation.

Compare wall time, runner queue time, compiler cache hits and native build counts
on GitHub before quoting a speedup. A local workflow validation cannot measure
runner queueing or exercise the complete multi-platform build.
