# Resource import policy validation

## Scope

Local validation of `import_policy` changes against unmodified HEAD `955aa6fc6`.
No release, account changes, or live resource operations were performed.
The surrounding ByteRec changes were left untouched.

## Fixes identified during continuation

- Directory failure metadata added an empty `error_code` to legacy responses.
  The field is now emitted only when present; tests cover absent, permission-denied,
  and unauthenticated codes without weakening legacy assertions.
- Quarantine held an exact ancestor-directory lock while removing child sidecars.
  Real RAGFS rejected the operation because that lease did not cover the files.
  The batch now locks the ancestor metadata and both summary paths explicitly.
- A root source failure can happen before the user's temporary parent directory
  exists. Quarantine now prepares that parent before invalidating derived views.
- Tests cover partial-commit rejection on authentication failure, quarantine on
  explicit permission denial, and real filesystem moves with and without a
  preacquired target lock.

## Results

The final combined Python run covers new policy tests, SDK behavior/upload tests,
resource processor, RNFV/diff/context plans, directory/Feishu parsing, Watch,
Connector, ingestion request, parse mode, and temporary upload tests:

- **766 passed, 9 failed, 5 setup errors**.
- The focused policy/parser/processor suite is fully green: **87 passed**,
  including both real-RAGFS quarantine lock-ownership cases.
- The nine failing tests have identical names and exception signatures in the
  unmodified HEAD run: six patch a removed `semantic_executor.get_openviking_config`,
  and three use filesystem doubles without the current `read_file` interface.
  That baseline subset reports **146 passed, 9 failed**.
- The five setup errors also occur on unmodified HEAD: the local vector engine
  lacks native `PersistStore`. The expanded baseline reports
  **450 passed, 5 setup errors**. They are not evidence of upload-path validation
  success and were not skipped or hidden.
- Rust CLI: **529 unit + 13 integration tests passed** using
  `RUST_MIN_STACK=16777216 cargo test -p ov_cli`.
- Changed Python files pass Ruff checks and formatting checks; `git diff --check`
  passes. The directory-chain `AGENTS.md` was reviewed and a scoped import-policy
  guide added locally. This repository ignores `AGENTS.md`; the local guide is
  not part of the Git diff. The implementation contract is also captured in
  `RESOURCE_IMPORT_POLICY_CN.md` for normal version control.

## Test environment boundary

Python validation used an existing Python 3.12 environment, local source for both
kernel and SDK, and a minimal local-only OpenViking configuration. The temporary
dependency directory supplies protobuf, prometheus-client, and the built RAGFS
binding. The Python environment's native `readline` import segfaults before pytest
collection, so only the test runner substitutes an empty `readline` module:

```python
import sys
import types
sys.modules["readline"] = types.ModuleType("readline")
import pytest
raise SystemExit(pytest.main(["-q", "-o", "addopts=", *test_paths, "--tb=short"]))
```

Both current and baseline runs use this identical runner. Production code, pytest
fixtures, and shared Python environments were not changed to work around these
environment failures. Real storage tests exercise RAGFS locking, content moves,
and sidecar removal, but use a mocked vector deletion call; live vector-store and
remote source end-to-end behavior remains unverified.

## Submission checks

The feature commit was rebased onto upstream `main` at `cd079c748` without conflicts.
After rebasing, the focused Python suite still reports **87 passed**, and Rust CLI
reports **529 unit + 13 integration tests passed**. All 22 changed Python files
pass Ruff check and format check; the outgoing diff passes whitespace checks.
`cargo fmt -p ov_cli -- --check` reports pre-existing formatting differences in
upstream files; new error-return expressions were formatted without unrelated
repository-wide cleanup. Mypy was not run. The PR references feature Issue #7337.
