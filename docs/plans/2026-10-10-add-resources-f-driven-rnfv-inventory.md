# Add-resources F-driven RNFV Inventory Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make RNFV snapshots created by `add_resources` query only vector records whose URIs still exist in F, while retaining a one-million-node safety cap and bounded exact-query RPCs.

**Architecture:** `build_rnfv_snapshot()` gains an opt-in formal-tree inventory strategy. It first obtains N and F, derives the target root plus every F node URI, enforces the add-resources inventory cap, and then loads V using exact `account_id + uri IN (...) + level IN (...)` filters. The shared exact inventory backend uses one bounded filter query per URI chunk, rather than a count-plus-scroll scan. Existing write/batch-write callers retain their explicit vector inventory behavior.

**Tech Stack:** Python async I/O, OpenViking RNFV snapshots, VikingDB scalar filters, pytest.

---

### Task 1: Specify exact inventory query behavior in backend tests

**Files:**
- Modify: `tests/storage/test_vector_transfer.py`
- Modify: `openviking/storage/viking_vector_index_backend.py`

**Step 1: Write the failing test**

Assert `get_incremental_inventory_by_uris()` uses one `filter()` query for one URI chunk, requests only its inventory projection, and does not invoke strict count or cursor scroll.

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest --no-cov -q tests/storage/test_vector_transfer.py -k incremental_inventory_by_uris`

Expected: FAIL because the implementation calls `_strict_scan()`.

**Step 3: Write minimal implementation**

Use one exact scalar `filter()` call per URI input chunk. Bound its result to three L0/L1/L2 records per URI plus one sentinel; reject a saturated result instead of silently accepting a truncated response.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest --no-cov -q tests/storage/test_vector_transfer.py -k incremental_inventory_by_uris`

Expected: PASS.
### Task 2: Add formal-tree-driven RNFV V inventory

**Files:**
- Modify: `tests/storage/test_resource_diff_snapshot.py`
- Modify: `openviking/storage/resource_diff.py`

**Step 1: Write failing tests**

Cover: (a) the formal strategy queries root plus formal F paths exactly and omits V-only orphan records, (b) an absent F target performs no V query, and (c) more than the configured inventory cap raises a clear error before a V query.

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest --no-cov -q tests/storage/test_resource_diff_snapshot.py -k formal_inventory`

Expected: FAIL because the strategy and cap do not exist.

**Step 3: Write minimal implementation**

Define `RNFV_INVENTORY_LIMIT` with default `1_000_000` and a separate exact-query chunk size. Add an opt-in snapshot V strategy that derives exact URIs from F and calls the backend exact inventory reader only after F is available. Keep the existing subtree strategy as the default for all other callers.

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest --no-cov -q tests/storage/test_resource_diff_snapshot.py -k formal_inventory`

Expected: PASS.
### Task 3: Opt add_resources into the formal strategy and run regressions

**Files:**
- Modify: `tests/utils/test_resource_processor_processing_mode.py`
- Modify: `openviking/utils/resource_processor.py`

**Step 1: Write the failing test**

Assert the add-resources RNFV call selects the formal F-driven strategy and passes the two add-resources inventory controls, while direct content-write call sites remain unchanged.

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest --no-cov -q tests/utils/test_resource_processor_processing_mode.py -k rnfv`

Expected: FAIL because `ResourceProcessor` still uses subtree V inventory.

**Step 3: Write minimal implementation**

Pass the opt-in strategy and configuration values from `ResourceProcessor.finish_prepared_resource()` into `build_rnfv_snapshot()`.

**Step 4: Run focused and cross-path regression tests**

Run:
`.venv/bin/pytest --no-cov -q tests/storage/test_vector_transfer.py tests/storage/test_resource_diff_snapshot.py tests/utils/test_resource_processor_processing_mode.py tests/storage/test_content_write_processing_mode.py tests/server/test_content_batch_write.py`

Expected: PASS.

<!-- End of implementation plan. -->
