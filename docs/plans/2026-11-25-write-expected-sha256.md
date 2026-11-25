# Write expected_sha256 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an optional SHA-256 precondition to single-file writes, matching batch-write.

**Architecture:** Validate the digest before dispatch, then compare the complete stored bytes while holding the existing exact file lease. Share the check across resource, skill, memory, semantic sidecar, and file-only paths. A missing or changed file raises CONFLICT before content, snapshots, metadata, or queue work; omitted or null digests preserve existing behavior.

**Tech Stack:** Python/FastAPI/Pydantic, Python/Go/TypeScript SDKs, Rust CLI.

## Task 1: Regression coverage

Modify `tests/server/test_content_batch_write.py` to exercise matching, stale, missing, malformed, and omitted digests through FSService/ContentWriteCoordinator. Check lock ownership during reads, including a change occurring at lock acquisition. Modify `tests/server/test_api_content_write.py` for HTTP validation and conflict responses. Extend SDK serialization tests and Rust CLI parsing coverage.

Run the new Python tests with `PYTHONPATH=.:sdk/python ../../.venv/bin/python -m pytest tests/server/test_content_batch_write.py -k write_revision -q --no-cov`; verify they fail because expected_sha256 is unsupported.

## Task 2: Implement the precondition

Modify `openviking/server/routers/content.py`, `openviking/service/fs_service.py`, `openviking/storage/content_write.py`, and `openviking/server/mcp_endpoint.py`. Add optional `expected_sha256` at the end of existing method signatures. Reuse the batch lowercase 64-character validation and actual-byte hash semantics. Pass the parameter through each single-write path into `_classify_locked_write_target`, before content rendering or committing.

Add `expected_sha256` to Python WriteOptions, `ExpectedSHA256` to Go WriteOptions, and `expectedSha256` to TypeScript WriteOptions. Forward `--expected-sha256` through Rust CLI parsing, dispatch, handler, command, and HTTP client.

## Task 3: Verification and documentation

Update `docs/en/api/12-content.md` and `docs/zh/api/12-content.md` with parameter semantics, HTTP 409 behavior, and byte-download hash examples. Run related server/service tests, Python SDK serialization and positional-signature compatibility tests, Go tests/vet, TypeScript tests/typecheck, and Rust CLI checks. Review the diff and retain the development branch for handoff.


## Validation results

- Initial Python server/service/coordinator/SDK/MCP checks: 211 passed. The review consolidated duplicate cases while retaining the lock-race, metadata/snapshot/task side-effect, stored-byte, and omission checks. The streamlined suite passed all 200 cases.
- Go: `go test ./...` and `go vet ./...` passed.
- TypeScript: 63 tests passed; `npx tsc --noEmit` passed.
- Rust CLI: `cargo test -p ov_cli --bin ov write --offline`: 10 passed, including flag parsing and actual HTTP payload capture.
- Changed Python files passed Ruff; `git diff --check` passed.

Server tests used an explicit temporary configuration with local vector storage and the memory path-lock provider. The worktree uses a freshly compiled Rust Python binding and the existing local vector-engine artifact; these native files are ignored build artifacts. Existing deprecation warnings were retained.
