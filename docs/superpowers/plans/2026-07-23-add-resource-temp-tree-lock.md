# Add-resource Temporary Tree Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse one request-owned temp-tree lock during Markdown add-resource parsing so encrypted child writes do not create locks under `.encrypt_stage`.

**Architecture:** `VikingFS` owns validation and acquisition of the temp tree lock. `MarkdownParser` owns its request lifecycle and passes the acquired handle through every write performed while materializing that tree.

**Tech Stack:** Python 3.11, asyncio, pytest, OpenViking `LockContext`, Kubernetes.

## Global Constraints

- Only request-generated `viking://temp/...` parser trees use the new context.
- Normal and handle-less encrypted writes retain dual-path locking.
- No API or configuration changes.
- The production change is limited to `VikingFS` and `MarkdownParser`.

---

### Task 1: Temp-tree lock API

**Files:**
- Modify: `openviking/storage/viking_fs.py`
- Test: `tests/storage/test_viking_fs_write_locking.py`

**Interfaces:**
- Produces: `VikingFS.lock_temp_tree(uri, ctx=None) -> AsyncIterator[LockHandle]`

- [ ] **Step 1: Write failing tests**

Add tests that enter `lock_temp_tree("viking://temp/request-id")`, assert a
tree-mode `LockContext` receives the converted path, assert its yielded handle
is returned, and assert `viking://resources/...` raises `InvalidArgumentError`.

- [ ] **Step 2: Verify RED**

Run:
`python -m pytest tests/storage/test_viking_fs_write_locking.py -k temp_tree -vv`

Expected: failure because `VikingFS.lock_temp_tree` does not exist.

- [ ] **Step 3: Implement the minimal API**

Import `asynccontextmanager`, validate `VikingURI(uri).scope == "temp"`, acquire
`LockContext(get_lock_manager(), [path], lock_mode="tree")`, and yield its
handle.

- [ ] **Step 4: Verify GREEN**

Run:
`python -m pytest tests/storage/test_viking_fs_write_locking.py -vv`

Expected: all encrypted write and temp-tree lock tests pass.

### Task 2: Markdown handle propagation

**Files:**
- Modify: `openviking/parse/parsers/markdown.py`
- Modify: `tests/parse/test_markdown_link_rewrite.py`
- Test: `tests/parse/test_markdown_temp_tree_locking.py`

**Interfaces:**
- Consumes: `VikingFS.lock_temp_tree`
- Produces: `_apply_layout(..., lock_handle=None)`, `_write_section(..., lock_handle=None)`, and `_ingest_local_images(..., lock_handle=None)`

- [ ] **Step 1: Write failing parser test**

Use a recording fake VikingFS whose `lock_temp_tree` yields a sentinel. Parse a
small Markdown resource and assert the text write receives that sentinel.
Exercise image ingestion and assert binary and mapping writes receive it too.

- [ ] **Step 2: Verify RED**

Run:
`python -m pytest tests/parse/test_markdown_temp_tree_locking.py -vv`

Expected: failure because the parser neither opens `lock_temp_tree` nor passes
`lock_handle` to child writes.

- [ ] **Step 3: Implement minimal propagation**

Create the generated temp root, enter `lock_temp_tree`, replay the layout, and
thread the handle through text, binary-image, and mapping writes. Update the
existing test fake to accept the new optional keyword.

- [ ] **Step 4: Verify GREEN and focused regression**

Run:
`python -m pytest tests/parse/test_markdown_temp_tree_locking.py tests/parse/test_markdown_link_rewrite.py tests/storage/test_viking_fs_write_locking.py -vv`

Expected: all selected tests pass.

### Task 3: Runtime hot update and mixed benchmark

**Files:**
- Reuse: `/Users/bytedance/ov_perf_patch.py`
- Reuse: `/Users/bytedance/ov_mixed_probe.py`

**Interfaces:**
- Consumes: modified `viking_fs.py` and `markdown.py`
- Produces: a fresh Pod and a mixed-wave JSON result

- [ ] **Step 1: Run formatting and focused tests**

Run Ruff format/check on changed Python files and the focused pytest set.

- [ ] **Step 2: Update the target Deployment**

Extend the existing startup hot patch to install the two modified runtime
modules, preserve PERF instrumentation, annotate the pod template, and wait for
rollout completion.

- [ ] **Step 3: Verify Pod health**

Verify Ready=true, restartCount=0, `/health` returns healthy, and runtime source
contains `lock_temp_tree` plus parser handle propagation.

- [ ] **Step 4: Run the unchanged 20+20 wave**

Run `/Users/bytedance/ov_mixed_probe.py` through a new port-forward. Record HTTP
success, landed resources, wall time, per-interface latency, queue drain, and
cleanup results.

- [ ] **Step 5: Compare lock evidence**

Parse DEBUG/PERF logs by telemetry ID. Compare add-resource parse duration and
`.encrypt_stage` lock create/delete totals with the 2026-07-22 baseline, and
report any remaining bottleneck without attributing unrelated queue work to API
latency.
