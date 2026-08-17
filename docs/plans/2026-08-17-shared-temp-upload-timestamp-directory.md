# Shared Temp Upload Timestamp Directory Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Store each shared temporary upload in a timestamp-prefixed directory so cleanup can determine expiry from one root listing without filesystem modification times.

**Architecture:** Generate shared upload IDs as a fixed-width Unix-millisecond timestamp followed by a UUID. Use that ID as the directory name under `viking://upload`, with `content` written before `meta`. Consumers construct both paths directly from `temp_file_id`; cleanup parses timestamps from first-level directory names and removes expired directories recursively.

**Tech Stack:** Python async server, VikingFS, pytest, Markdown documentation.

---

### Task 1: Define directory-layout cleanup behavior

**Files:**
- Modify: `tests/server/test_temp_upload_store.py`

**Step 1: Write the failing test**

Replace the flat-object cleanup test with a test that returns first-level timestamp directories and verifies only expired valid directories are removed recursively without `modTime`.

**Step 2: Run test to verify it fails**

Run: `uv run --active pytest tests/server/test_temp_upload_store.py -q`

Expected: FAIL because cleanup still expects `.content` and `.meta` objects.

**Step 3: Implement minimal cleanup behavior**

Parse a fixed-width millisecond timestamp from directory names, skip malformed entries, and remove only expired directories.

**Step 4: Run test to verify it passes**

Run: `uv run --active pytest tests/server/test_temp_upload_store.py -q`

Expected: PASS.

### Task 2: Switch shared upload save and resolve paths

**Files:**
- Modify: `openviking/server/temp_upload_store.py`
- Modify: `tests/server/test_api_resources.py`

**Step 1: Write the failing test**

Assert uploaded shared files are stored in `viking://upload/<timestamp>-<uuid>/content` and `meta`, while the returned `temp_file_id` stays `shared_<upload_id>`.

**Step 2: Run test to verify it fails**

Run: `uv run --active pytest tests/server/test_api_resources.py -q -k shared_temp_upload`

Expected: FAIL because the implementation writes flat `.content` and `.meta` objects.

**Step 3: Implement minimal storage behavior**

Generate timestamp-prefixed IDs, construct directory child URIs, write content before metadata, and clean up the directory recursively after a partial write failure.

**Step 4: Run test to verify it passes**

Run: `uv run --active pytest tests/server/test_api_resources.py -q -k shared_temp_upload`

Expected: PASS.

### Task 3: Update user-deletion cleanup

**Files:**
- Modify: `openviking/service/user_deletion.py`
- Modify: `tests/server/test_temp_upload_store.py`

**Step 1: Write the failing test**

Make user deletion list upload directories, read each directory’s `meta`, and remove only directories owned by the deleted user.

**Step 2: Run test to verify it fails**

Run: `uv run --active pytest tests/server/test_temp_upload_store.py -q`

Expected: FAIL because deletion still scans root-level `.meta` objects.

**Step 3: Implement minimal deletion behavior**

Read `viking://upload/<upload_id>/meta` for each valid upload directory and recursively remove the owned directory.

**Step 4: Run test to verify it passes**

Run: `uv run --active pytest tests/server/test_temp_upload_store.py -q`

Expected: PASS.

### Task 4: Document and verify the layout

**Files:**
- Modify: `docs/en/api/02-resources.md`
- Modify: `docs/zh/api/02-resources.md`
- Modify: `docs/en/guides/01-configuration.md`
- Modify: `docs/zh/guides/01-configuration.md`

**Step 1: Update documentation**

Describe the timestamp-prefixed directory layout and timestamp-based one-listing cleanup, without referring to object modification times.

**Step 2: Run focused verification**

Run:

```bash
uv run --active pytest tests/test_config_loader.py tests/server/test_temp_upload_store.py -q
uv run --active pytest tests/server/test_api_resources.py -q -k shared_temp_upload
uv run --active ruff check openviking/server/temp_upload_store.py openviking/service/user_deletion.py tests/server/test_temp_upload_store.py tests/server/test_api_resources.py
```

Expected: all commands pass.
