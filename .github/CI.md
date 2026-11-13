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
Its API and database test jobs (3.10) and cuVS job (3.11) reuse that same-run artifact. Both
native extensions target `cp310-abi3`. Restore requires matching commit, OS,
architecture, runtime libc, platform, build options and ABI, and validates every
file checksum before writing it. Other test platforms build locally. Release
wheels build independently in Ubuntu 20.04/macOS/Windows; Ubuntu 24.04 test
binaries and musl CLI binaries are never reused in release wheels.

With repository variable `CI_LINUX_BUILDS_ENABLED=true`, selected Linux x86_64
builds alternate between GitHub-hosted and `[self-hosted, Linux, X64, ci-linux-builds]`.
Only the dedicated runner `iv-yezentk8ao3z47ge5tmm` has that custom label; runner
display names do not select machines.
Linux wheels use the dedicated runner on even workflow run numbers; API native
runtime preparation uses it on odd run numbers. Each workflow therefore sends
50% of consecutive runs to each destination, and reruns keep the same destination.
This is half of the selected build jobs over time, not half of all Linux jobs or
an equal share of execution time. Conditional skips can change the observed share.
The old ECS runner does not receive these build jobs.

Only official repository PRs from the same repository and branch pushes are
eligible. Release/tag events, schedules, manual builds, fork PRs and other
repositories use GitHub-hosted. Labels are selected directly in `runs-on`;
no selector job or token is needed. Studio, both musl CLI targets, API/database
tests, cuVS, other tests/checks and source packaging stay hosted. Native ARM,
macOS and Windows assignments are unchanged.

Self-hosted builds require Docker access and use disposable containers with
job-local HOME/Cargo/Rustup directories and post-job ownership cleanup.
Wheels retain Ubuntu 20.04/glibc 2.31. API native runtime preparation uses Ubuntu
24.04 on both destinations so its artifact remains compatible with hosted tests.
The Linux wheel execution timeout is 60 minutes and native preparation is limited
to 30 minutes. Timeouts start when execution begins, not while queued; there is
no automatic queue fallback.

Before enabling routing, dispatch `_build.yml` on this branch with
`check_linux_runner=true`, `build_wheels=false` and `build_sdist=false`. Its
five-minute diagnostic checks Docker access and both container images without
checking out repository code. Enable `CI_LINUX_BUILDS_ENABLED` only after it
passes. The runner user must be able to access Docker without interactive sudo.

If the dedicated runner is offline or stuck, set repository Actions variable
`CI_DISABLE_SELF_HOSTED=true`, cancel affected runs and trigger new runs.
New jobs use hosted runners; already queued jobs retain their original labels.
After repairing/updating the runner, remove the disable variable to resume the
half-load routing. Removing `CI_LINUX_BUILDS_ENABLED` also leaves all builds hosted.
Release builds remain hosted regardless of either variable.

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

## Database integration tests

The API workflow's `MySQL & Redis Integration Tests` job starts fresh MySQL 8.4
and Redis 6.2 service containers on Ubuntu 24.04. It runs for PR changes selected
by `api_changed`, including server/service/resource code, tests, dependencies and
CI scripts. Manual, scheduled and release runs use the workflow's existing
repository and release-tag rules. No external database or API-key secrets are
needed.

The job installs the locked `test` and `mysql` extras, checks both connections,
and executes the account store's deployment SQL and migrations. The database
user has privileges only on the disposable `openviking_test` database; fixtures
need DDL privileges to create and drop isolated Task/Work and Watch tables.

The executed suites cover account-store initialization and distributed
authentication, Redis/MySQL credential snapshots and cache invalidation,
Task/Work lifecycle and concurrency, and Watch persistence and cross-instance
ownership. File variants of Task/Work and Watch also run for comparison.
PostgreSQL account-store tests are not included because that backend is not yet
on this branch's `main` baseline.

`database_integration.py verify` rejects skipped/failed database cases, missing
account contracts, and any backend suite with zero passing cases. The two
Watch file-only skips are expected. GitHub publishes database case counts in
the job summary and retains `database-integration-results` JUnit XML for seven
days, including failed runs.

To reproduce against **fresh, disposable** local databases, set `OV_TEST_MYSQL`
and `OPENVIKING_WATCH_MYSQL_PARAMS` to JSON connection objects with `host`,
`port`, `user`, `password`, and `database`, set `OV_TEST_REDIS` to a standalone
Redis URL without a database path, and set `OPENVIKING_CONFIG_FILE` to a writable
temporary file. After preparing the native runtime and installing the extras,
run the same commands as the workflow:

```sh
uv run --no-sync python .github/scripts/database_integration.py prepare
uv run --no-sync python -m pytest -q -o addopts='' \
  tests/server/test_account_services.py::test_mysql_load_creates_default_account \
  tests/server/test_account_services.py::test_distributed_authentication_state_is_shared_and_service_scoped \
  tests/server/test_redis_mysql_account_store.py \
  tests/service/test_task_work_contract.py \
  tests/resource/test_watch_store.py \
  --junitxml=database-integration.xml
uv run --no-sync python .github/scripts/database_integration.py verify database-integration.xml
python3 -m pytest --noconftest -q -o addopts='' tests/misc/test_database_integration_ci.py
```
