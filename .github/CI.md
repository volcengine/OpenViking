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

Ordinary Linux x64 jobs route **heavy builds to ov-ci-runner and tests and
checks to GitHub-hosted**. No runner inventory token or repository variable is
needed. `_linux-runner.yml` resolves
two lanes: `runs_on` uses `[self-hosted, Linux, X64, ov-ci-runner]` for heavy builds,
and `hosted_runs_on` uses `[ubuntu-24.04]` for tests and checks. Jobs and matrix
entries choose their lane in their `runs-on` expression, so the two pools execute
different jobs concurrently.
The resolver itself always runs on GitHub-hosted.

The default matrices place five heavy build slots on ov-ci-runner: Studio,
the Linux x86_64 wheel, the shared API native runtime, and both musl CLI targets.
All remaining routed slots use hosted runners so independent tests can run in
parallel while the self-hosted machine handles compilation. Routing follows
workload rather than a job-count ratio. Regression tests enforce this build list
and verify that tests, checks and source packaging use hosted runners.

| Jobs or matrix entries | Default lane |
| --- | --- |
| Studio, x86_64 wheels, native API runtime | ov-ci-runner |
| Both Linux musl CLI targets (including ARM64 cross-build on x64) | ov-ci-runner |
| API tests reusing the native runtime, cuVS/lite, Rust tests | GitHub-hosted |
| Source distribution | GitHub-hosted |
| Plugin, OpenClaw, all SDK and LangChain Python versions | GitHub-hosted |
| Hermes provider tests and all Linux Quick Local hosts | GitHub-hosted |
| Microsoft framework contracts, framework layout, host compatibility | GitHub-hosted |
| Gateway, chart checks and kind acceptance | GitHub-hosted |
| All full-suite Python versions and effect tests | GitHub-hosted |

Repository variable `CI_LINUX_POOL` and manual/reusable input `linux_pool` remain
optional overrides: `auto` is the default, `balanced` is a compatibility alias
for the same workload routing, and `github-hosted` or `ov-ci-runner` puts both lanes
on that pool. Manual input takes precedence over the repository variable.
There is no availability check or automatic fallback: assigned self-hosted jobs
wait if ov-ci-runner is busy or offline. Already queued jobs retain their labels.
For maintenance, set `CI_LINUX_POOL=github-hosted` before triggering new runs.
ECS is not an eligible pool.

Native ARM jobs, macOS, Windows, publishing and lightweight planning/version
checks retain their existing hosted runners. The legacy `os_json` value
`ubuntu-24.04` selects the Linux x86_64 wheel platform; `linux_pool` controls its
lane. Wheels continue compiling independently in Ubuntu 20.04 containers
(glibc 2.31). Studio/source builds use Ubuntu 24.04 containers.

The resolver runs embedded Python in a disposable `python:3.12-slim` container,
without checkout or local actions. Python isolated mode and working directory
`/` prevent importing files left in the runner workspace. The embedded script
is checked against `.github/scripts/linux_runner.py` by the regression suite.
Fork PRs use GitHub-hosted for both lanes unless their source repository is
explicitly trusted through the official repository variable
`CI_LINUX_TRUSTED_REPOSITORIES`, a JSON list of exact source repository names.
No fork is trusted by default. An absent, empty or malformed variable trusts no
forks; `[]` explicitly disables fork trust. Non-official destinations remain
hosted. Branches in the official repository use the split without configuration.

The self-hosted pool must have `[self-hosted, Linux, X64, ov-ci-runner]`,
a working Docker daemon, Docker socket access for the runner user, and outbound
access to Actions, Ubuntu packages and container registries. No selector/shared
custom labels are needed. Linux x64 tests run in disposable Ubuntu 24.04 containers
on every pool, with job-local HOME/Cargo/Rustup directories and Git installed before checkout.
Wheel jobs supply their own Ubuntu 20.04 packages and use the bootstrap action
with dependency installation disabled, retaining job-local HOME/Cargo/Rustup.
The bootstrap action's post-job hook restores workspace/temp ownership to the
host runner user, so subsequent host jobs can clean container build outputs.
This keeps native bundle OS/libc validation consistent and isolates API servers
and configuration from services already running on the host.

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

The legacy P0 Memory Tests workflow was removed in #6807 and has no scheduled,
prerelease or manual CI entry point. Its test sources remain in the repository.

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
