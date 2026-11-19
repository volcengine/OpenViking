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

With repository variable `CI_LINUX_BUILDS_ENABLED=true`, trusted Linux API jobs
use `[self-hosted, Linux, X64, ci-linux-builds]`. Linux x86_64 wheel builds
alternate between GitHub-hosted and this dedicated pool.
Runners with that custom label form the dedicated pool; runner display names do
not select machines.
Linux wheels use the dedicated runner on even workflow run numbers.
API native runtime preparation and its short change-planning job always use
the dedicated runner for eligible events, regardless of workflow run number.
The planning job runs Git/Python on the host with a five-minute execution limit.
Linux API/CLI, MySQL/Redis and
cuVS tests inherit their same-run native producer's destination. This keeps the
entire trusted Linux API dependency chain off the hosted queue.
Wheel builds send 50% of consecutive runs to each destination, and reruns keep
the same destination. This is not half of all Linux jobs or an equal share of
execution time. Conditional skips can change the observed share.
The old ECS runner does not receive these jobs.

Only official repository PRs from the same repository and branch pushes are
eligible. Release/tag events, schedules, manual builds, fork PRs and other
repositories use GitHub-hosted. The native producer publishes its selected labels
and container image for its consumers; no extra selector job or token is needed.
Studio, both musl CLI targets, other tests/checks and source packaging stay hosted. Native ARM,
macOS and Windows assignments are unchanged.

Linux builds and tests using CI images require Docker access and use disposable containers with
job-local HOME/Cargo/Rustup directories and post-job ownership cleanup.
Wheels retain Ubuntu 20.04/glibc 2.31. API native runtime preparation uses Ubuntu
24.04 on both destinations so its artifact remains compatible with hosted tests.
API, database and cuVS consumers use Ubuntu 24.04 containers on both destinations.
Linux Full Test, API Effect Tests and CodeQL analysis also use the same image on
hosted runners. Database consumers use service-container DNS on both destinations.
The Linux wheel execution timeout is 60 minutes and native preparation is limited
to 45 minutes, with a 15-minute limit on system package installation. Timeouts start when execution begins, not while queued; there is
no automatic queue fallback.

The host planning job restores its workspace ownership before checkout to
recover root-owned build files from interrupted containers. Container cleanup
uses the host-created workspace parent to determine the owner; checkout can
recreate the checkout directory as root.

### Prebuilt Ubuntu 20.04 and 24.04 CI images

`.github/images/ubuntu20-ci.Dockerfile` preinstalls GCC/G++ 10, CMake, ccache and
the OS development libraries needed by Linux wheels. Both x86_64 and ARM Linux
wheels use GCC/G++ 10 and preserve Ubuntu 20.04/glibc 2.31; a different glibc
version is rejected before compilation. The published image is amd64, so only
x86_64 wheels use it. ARM retains stock `ubuntu:20.04` and installs dependencies.

`.github/images/ubuntu24-ci.Dockerfile` preinstalls Git, GCC/G++, CMake, ccache,
OpenSSL development headers, Python 3 and the system tools used by API, database
and cuVS tests. It contains no repository checkout, credentials or Python/Rust
project dependencies. Python versions, Rust and dependency lockfiles remain
managed by the existing setup actions. The API runtime uses Ubuntu 24.04's GCC.

Run **Build Linux CI Base Image** manually with `ubuntu_version=20.04` or
`24.04` (the default). Its default `publish=false` builds the amd64 image and
validates dependencies, OpenSSL linking, C++17 compilation and the optimized
AWS-LC memcmp probe without network access on a hosted runner. After that passes,
run with `publish=true` to publish the corresponding
`ghcr.io/volcengine/openviking-ci-ubuntu20:<commit-sha>` or
`ghcr.io/volcengine/openviking-ci-ubuntu24:<commit-sha>`. Make the package
publicly readable, then set the matching repository Actions variable to the
immutable digest reference printed in the workflow summary:

| Image | Repository variable | Consumers |
| --- | --- | --- |
| Ubuntu 20.04 amd64 | `CI_UBUNTU20_IMAGE` | Linux x86_64 wheels on hosted and self-hosted runners |
| Ubuntu 24.04 amd64 | `CI_UBUNTU24_IMAGE` | API native producer and Linux API/database/cuVS on hosted and eligible self-hosted runners; Full Test, API Effect Tests and CodeQL analysis on hosted runners only |

Another reachable public
registry can also host the image; avoid mutable `latest` tags.

The API native producer uses the Ubuntu 24.04 image and passes it to its Linux test
consumers regardless of runner pool. Other Linux test/analysis jobs select the
same variable directly. Lite Test accepts an explicit `linux_container` override;
an empty override uses the configured image. Each bootstrap checks actual dpkg installation status: complete images
skip apt, while incomplete images install the required packages. With no image
variable, these jobs use stock `ubuntu:24.04` and install their required packages.
With no `CI_UBUNTU20_IMAGE`, x86_64 wheels fall back to stock `ubuntu:20.04`.
GitHub-hosted jobs start on fresh machines and pull the image for each job;
there is no Docker layer cache shared between jobs. Self-hosted jobs on the same
host can reuse Docker's cached layers after the first pull. On hosted runners,
the image primarily fixes system package versions and aligns the environments;
skipping apt alone does not establish an end-to-end speedup.
Rebuild and validate periodically for OS package updates,
then update the digest variable. Remove the variable to return to stock Ubuntu.
The Ubuntu 20.04 Dockerfile can be built with the same local command below by
substituting `ubuntu20-ci.Dockerfile` and `openviking-ci-ubuntu20`.

For a local Linux build, provide the host CA bundle in the build context; minimal
Ubuntu needs it to verify HTTPS before installing `ca-certificates`. The Dockerfile
uses HTTPS sources and fails immediately on incomplete package indexes.
`UBUNTU_MIRROR` optionally selects an HTTPS mirror, including its `/ubuntu` path:

```sh
ci_context=$(mktemp -d)
cp .github/images/ubuntu24-ci.Dockerfile "$ci_context/Dockerfile"
cp /etc/ssl/certs/ca-certificates.crt "$ci_context/bootstrap-ca-certificates.crt"
docker build --platform linux/amd64 \
  --build-arg UBUNTU_MIRROR=https://mirrors.aliyun.com/ubuntu \
  -t ghcr.io/volcengine/openviking-ci-ubuntu24:local "$ci_context"
```

Before enabling routing, dispatch `_build.yml` on this branch with
`check_linux_runner=true`, `build_wheels=false` and `build_sdist=false`. Its
five-minute diagnostic checks Docker access, HTTPS package-index downloads and
package installation in both container images without checking out repository
code. Enable `CI_LINUX_BUILDS_ENABLED` only after it passes. The runner user must
be able to access Docker without interactive sudo.

To verify the published Ubuntu 20.04 image on the dedicated pool, use an eligible
PR/push build with an even workflow run number.
Confirm the x86_64 job's runner labels, configured digest pull, apt skip,
GCC/G++ 10, glibc 2.31, wheel smoke test and uploaded artifact.
The stock-container apt diagnostic runs only with `build_wheels=false`;
`check_linux_runner` does not change wheel routing.

CLI `find`/`search` integration tests retry explicit upstream model TPM/RPM
limits at most twice, with roughly 30- and 60-second backoff and jitter.
Persistent rate limits still fail; billing/quota exhaustion and other errors are
not retried by this helper. Production queries retain their existing retry policy.

Package sources use HTTPS. Minimal Ubuntu images bootstrap TLS verification with
the host's `/etc/ssl/certs/ca-certificates.crt`, mounted read-only at a temporary
path until the container installs its own certificates. The host CA bundle must
exist. Incomplete apt index downloads fail the job instead of continuing with
missing package metadata.
If official Ubuntu endpoints are unreachable from the dedicated machine, set
`CI_UBUNTU_MIRROR` to a reachable HTTPS Ubuntu mirror, including its `/ubuntu` path.
The override applies only to self-hosted builds/tests and the readiness diagnostic;
hosted builds retain the official sources. The diagnostic prints host/container
TCP reachability to help distinguish mirror access from Docker networking issues.

If the dedicated runner is offline or stuck, set repository Actions variable
`CI_DISABLE_SELF_HOSTED=true`, cancel affected runs and trigger new runs.
New jobs use hosted runners; already queued jobs retain their original labels.
After repairing/updating the runner, remove the disable variable to resume the
dedicated routing. Removing `CI_LINUX_BUILDS_ENABLED` also leaves these builds/tests hosted.
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
256 MiB per entry and Cargo incremental output is disabled. After a successful
macOS Intel warmup, a separate job removes superseded `cargo-v3-*` main entries,
keeping the two newest per OS/architecture/target/cache-format version plus all
entries created within 24 hours. Older toolchain and lockfile variants share
this retention group. Other main cache families are untouched. PR-scoped caches
are removed only for closed/merged PRs and only after the cache is seven days old.
Unknown key formats and refs are preserved; PR lookup errors abort cleanup.
Only the cleanup job receives `actions: write`; warmup remains read-only.
Preview the current deletion plan locally with
`python3 .github/scripts/cache_maintenance.py --repo volcengine/OpenViking`
(authenticated `gh` required); `--apply` performs deletion. GitHub's cache quota
and eviction still apply, including during bursts inside the retention windows.
Source changes still rebuild correctly through Cargo/ccache input validation.
Rust, C/C++ compiler or CMake upgrades change the toolchain fingerprint and can
miss both Cargo and ccache restores until main warmup finishes. A cold build
after such an upgrade is expected; compare timings with the same fingerprint.
Non-Linux wheel jobs have a 90-minute timeout and the shared Studio job has a
15-minute timeout, so a stuck producer cannot indefinitely block later warmups.
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
  tests/misc/test_linux_runner.py \
  tests/misc/test_macos_wheel_cache.py \
  tests/misc/test_cli_model_query_retry.py \
  tests/misc/test_cache_maintenance.py
actionlint .github/workflows/pr.yml .github/workflows/api_test.yml \
  .github/workflows/_build.yml .github/workflows/_test_lite.yml \
  .github/workflows/_test_full.yml .github/workflows/rust-cli.yml \
  .github/workflows/macos-wheel-cache.yml
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
