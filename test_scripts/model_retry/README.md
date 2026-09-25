# Model retry diagnostic validation

Production retry contracts are covered by the focused suites under `tests/`. This
directory contains only the native service/QueueFS diagnostic used for bounded
fault injection; generated JSON reports must be written outside the repository.

## Integrated contract checks

```sh
PYTHONPATH=. python -m pytest --no-cov -q \
  tests/unit/test_model_call.py \
  tests/models/test_model_retry_transport.py \
  tests/storage/test_model_retry_terminal.py \
  tests/unit/session/test_session_commit_resume.py \
  tests/metrics/integration/test_model_retry.py
```

These cover the actual retry owner, real provider SDKs with local mock transports,
queue terminal handling, Session Phase 2, and metric export. They do not establish
a durable cross-process attempt budget or cluster-wide admission control.

## Native service and queue diagnostic

`native_e2e.py` runs an in-process OpenViking service with native RAGFS, SQLite
QueueFS, filesystem PathLock, and the local vector engine. It uses the real OpenAI
SDK against a loopback-only HTTP fixture and requires no provider credentials.

Run each case in a separate process and keep the output in a temporary directory:

```sh
PYTHONPATH=. python test_scripts/model_retry/native_e2e.py \
  --case resource-success --output /tmp/model-retry-resource-success.json
```

Supported cases are `resource-success`, `resource-embedding-429`,
`resource-embedding-401`, `session-success`, `session-vlm-429`,
`session-vlm-401`, and `resource-cancel`.

This diagnostic does not cover HTTP ingress, multiple Pods, Redis, process crashes,
real model interoperability, or billing. Record the Python revision and native
runtime image when the binary was built from another revision.
