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

Ordinary Linux x64 tests select a compute pool through `_linux-runner.yml`.
Set repository variable `CI_LINUX_POOL` to `auto` (the default), `github-hosted`,
`openviking-ecs`, or `ov-ci-runner`. This covers API and effect tests, the shared
API native runtime producer, cuVS, full/lite suites, plugin tests, SDK/framework
tests, Hermes x64, Rust tests and Linux musl CLI builds (including ARM64
cross-compilation on x64), and Gateway/chart/kind tests. Native Linux ARM jobs, macOS,
Windows, wheel distribution builds, publishing and lightweight planning/version
checks retain their existing hosted runners.
Manual test workflows also accept `linux_pool` to override the repository
variable for one run; reusable full/lite workflows accept the same input.

The selector itself runs on `[self-hosted, Linux, X64, ci-linux-selector]` in
the official repository. Add the `ci-linux-selector` custom label to both the
original ECS and ov-ci-runner; GitHub assigns the job to an available matching
host. It does not need GitHub-hosted Linux capacity to begin selection. If both
hosts are busy or offline, the selector waits in the self-hosted queue.
Non-official repositories retain a hosted selector because they do not have
these prepared hosts.

The selector runs embedded Python in a disposable `python:3.12-slim` container,
without checkout or local actions. Python isolated mode and working directory
`/` prevent importing files left in the runner workspace. The embedded script
is checked against `.github/scripts/linux_runner.py` by the regression suite.
Fork PRs may run this selection job on the shared hosts and receive no inventory
secret. Their test code uses GitHub-hosted unless their source repository is
explicitly trusted. Set the official repository variable
`CI_LINUX_TRUSTED_REPOSITORIES` to a JSON list of approved source repositories.
No fork is trusted by default. An absent, empty or malformed variable trusts no
forks; `[]` explicitly disables fork trust. Names match exactly, and non-official
destinations remain hosted. Branches in the official repository do not require
this variable to use self-hosted capacity.

In `auto`, the selector queries runner inventory, chooses an online idle Linux
x64 pool (also allowing its own host, which becomes free when selection finishes),
and alternates ties by workflow run ID. Without inventory credentials, trusted
jobs use `[self-hosted, Linux, X64, ci-linux-tests]` if the selector's runner name
is `openviking-ecs` or `ov-ci-runner`. Add `ci-linux-tests` to both prepared hosts:
GitHub can assign each dependent job to either host, instead of pinning a whole
workflow to the selector's host. This also lets approved fork PRs use the
self-hosted pools without receiving a secret. API failures, offline/busy
inventory or an unrecognized selector host with no credentials fall back to
`ubuntu-24.04`. The selector's summary records its decision. This is an
availability snapshot, not a capacity reservation: concurrent workflows can
select the same runner, and GitHub cannot move an already queued job to another
pool. Matrix jobs may queue behind other jobs on the selected pool.

For inventory-based automatic selection, add repository secret `CI_RUNNER_READ_TOKEN` containing
a fine-grained token scoped to this repository with **Administration: read**,
or an equivalent GitHub App installation token. The normal workflow token does
not grant runner inventory access. Never reuse a developer's CLI credential.
An explicit ECS/ov-ci-runner selection without this secret schedules directly
on its labels and waits for capacity; with inventory available, an unavailable
explicit pool falls back to hosted. Untrusted fork PRs and non-official
repositories always select hosted.

Each self-hosted pool must have `[self-hosted, Linux, X64]`, both shared labels
(`ci-linux-selector` and `ci-linux-tests`), and its corresponding custom label
(`openviking-ecs` or `ov-ci-runner`), plus a working Docker daemon,
Docker socket access for the runner user, and outbound access to Actions,
Ubuntu packages and container registries. Limit these custom labels to prepared
hosts. Linux x64 tests run in disposable Ubuntu 24.04 containers on every pool,
with job-local HOME/Cargo/Rustup directories and Git installed before checkout.
The bootstrap action's post-job hook restores workspace/temp ownership to the
host runner user, so subsequent host jobs can clean container build outputs.
This keeps native bundle OS/libc validation consistent and isolates API servers
and configuration from services already running on ECS.

Keep the Actions runner application up to date. GitHub stops assigning jobs to
runners that have not installed an available update within 30 days, even if
inventory still reports the runner as online and idle. Check the runner version
and its `_diag` update logs when correctly labeled idle hosts do not accept jobs.

Gateway Redis services use a private job network without published host ports;
the manually started cluster containers have run/attempt names and always-run
cleanup. kind acceptance runs on the host because it creates Docker/Kubernetes
clusters, but uses a private Python environment, HOME, kubeconfig, npm prefix,
unique cluster/image names and always-run cleanup. Abrupt host/runner failure
can still require removing that run's orphaned Docker resources.

The existing P0 Memory Tests remain on their original ECS environment, selected
with `[self-hosted, Linux, X64, openviking-ecs]`. That runner must have the
`openviking-ecs` custom label; its display name alone does not select it.

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
python3 -m pip install pytest PyYAML
python3 -m pytest --noconftest -q -o addopts='' \
  tests/misc/test_abi3_packaging_config.py \
  tests/misc/test_release_tag_selection.py \
  tests/misc/test_linux_runner.py
actionlint .github/workflows/pr.yml .github/workflows/api_test.yml \
  .github/workflows/_build.yml .github/workflows/_test_lite.yml \
  .github/workflows/_test_full.yml .github/workflows/rust-cli.yml
```

Compare wall time, runner queue time, compiler cache hits and native build counts
on GitHub before quoting a speedup. A local workflow validation cannot measure
runner queueing or exercise the complete multi-platform build.
