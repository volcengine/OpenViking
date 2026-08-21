# SessionCommit Default 8 with Local Override 50 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make unconfigured SessionCommit workers default to 8 while configuring this developer's local OpenViking instance to use 50.

**Architecture:** `QueueWorkersConfig` remains the server-facing configuration source and explicit values continue through `OpenVikingService` into `QueueManager`. Repository fallbacks and documentation use 8; `~/.openviking/ov.conf` supplies a local explicit override of 50 without entering Git.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, Ruff, JSON configuration

## Global Constraints

- Change only the `SessionCommit` queue default; other queue defaults remain 4.
- `examples/ov.conf.example` explicitly shows 8.
- `~/.openviking/ov.conf` explicitly sets 50 and preserves every existing setting.
- Do not restart the running OpenViking service.

---

### Task 1: Repository defaults and documentation

**Files:**
- Modify: `tests/test_config_loader.py:125-146`
- Modify: `tests/storage/test_queue_manager.py:21-24`
- Modify: `openviking_cli/utils/config/queue_worker_config.py:20-29`
- Modify: `openviking/storage/queuefs/queue_manager.py:24-97`
- Modify: `openviking/service/core.py:146-157`
- Modify: `examples/ov.conf.example:85-89`
- Modify: `docs/en/configuration/01-server.md:217-221`
- Modify: `docs/zh/configuration/01-server.md:217-221`

**Interfaces:**
- Consumes: `OpenVikingConfig.from_dict(data: dict) -> OpenVikingConfig` and `QueueManager(..., max_concurrent_session_commit: int = 8)`.
- Produces: an unconfigured `session_commit.max_concurrent` of 8 and preservation of explicit values such as 50.

- [ ] **Step 1: Change the behavior tests to require default 8 and explicit 50**

In `tests/test_config_loader.py`, make the default assertion and explicit override read:

```python
assert config.queue_workers.session_commit.max_concurrent == 8

# In test_queue_worker_concurrency_accepts_separate_values:
"session_commit": {"max_concurrent": 50},
assert config.queue_workers.session_commit.max_concurrent == 50
```

In `tests/storage/test_queue_manager.py`, rename and update the focused test:

```python
def test_session_commit_concurrent_defaults_to_eight() -> None:
    manager = QueueManager(agfs=object(), max_concurrent_external_parse=9)

    assert manager._max_concurrent_for_queue(manager.SESSION_COMMIT) == 8
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run:

```bash
uv run pytest tests/storage/test_queue_manager.py tests/test_config_loader.py -q --no-cov
```

Expected: the two default-value assertions fail with actual value 50; the explicit value 50 assertion passes.

- [ ] **Step 3: Implement the repository default of 8**

Use 8 for `DEFAULT_MAX_CONCURRENT_SESSION_COMMIT` in `queue_manager.py`, for the `session_commit` default factory in `queue_worker_config.py`, and for `OpenVikingService._init_storage()`'s fallback. Keep the existing explicit-value data flow unchanged.

```python
DEFAULT_MAX_CONCURRENT_SESSION_COMMIT = 8

session_commit: QueueWorkerConfig = Field(
    default_factory=lambda: QueueWorkerConfig(max_concurrent=8)
)

max_concurrent_session_commit: int = 8,
```

- [ ] **Step 4: Update repository examples and documentation**

Set `examples/ov.conf.example` to:

```json
"session_commit": {"max_concurrent": 8}
```

Set the English and Chinese `queue_workers.session_commit.max_concurrent` default tables to `8`.

- [ ] **Step 5: Run focused tests and static checks**

Run:

```bash
uv run pytest tests/storage/test_queue_manager.py tests/test_config_loader.py tests/unit/service/test_core_consistency.py -q --no-cov
uv run ruff check openviking/storage/queuefs/queue_manager.py openviking/service/core.py openviking_cli/utils/config/queue_worker_config.py tests/storage/test_queue_manager.py tests/test_config_loader.py tests/unit/service/test_core_consistency.py
uv run ruff format --check openviking/storage/queuefs/queue_manager.py openviking/service/core.py openviking_cli/utils/config/queue_worker_config.py tests/storage/test_queue_manager.py tests/test_config_loader.py tests/unit/service/test_core_consistency.py
git diff --check
```

Expected: 44 tests pass, Ruff reports no errors or formatting changes, and Git reports no whitespace errors.

- [ ] **Step 6: Commit the repository change**

```bash
git add docs/en/configuration/01-server.md docs/zh/configuration/01-server.md examples/ov.conf.example openviking/service/core.py openviking/storage/queuefs/queue_manager.py openviking_cli/utils/config/queue_worker_config.py tests/storage/test_queue_manager.py tests/test_config_loader.py
git commit -m "perf(queue): default session commit concurrency to 8"
```

---

### Task 2: Local OpenViking override

**Files:**
- Modify outside Git: `~/.openviking/ov.conf`

**Interfaces:**
- Consumes: the root JSON object in `~/.openviking/ov.conf`.
- Produces: `queue_workers.session_commit.max_concurrent == 50` while every unrelated JSON value remains identical.

- [ ] **Step 1: Create a protected temporary copy and backup**

Run these exact commands. The first command prevents overwriting an earlier backup:

```bash
test ! -e /tmp/openviking-ovconf-session-commit-50
mkdir /tmp/openviking-ovconf-session-commit-50
cp ~/.openviking/ov.conf /tmp/openviking-ovconf-session-commit-50/ov.conf.edit
cp ~/.openviking/ov.conf /tmp/openviking-ovconf-session-commit-50/ov.conf.backup
```

Do not print either file's full contents.

- [ ] **Step 2: Insert the local override with apply_patch**

Patch the temporary `ov.conf.edit` immediately before the existing root-level `embedding` section:

```json
"queue_workers": {
    "session_commit": {"max_concurrent": 50}
},
```

- [ ] **Step 3: Validate the edit before copying it back**

Run:

```bash
/usr/bin/python3 -c 'import copy, json, pathlib; root=pathlib.Path("/tmp/openviking-ovconf-session-commit-50"); before=json.loads((root/"ov.conf.backup").read_text()); after=json.loads((root/"ov.conf.edit").read_text()); expected=copy.deepcopy(before); expected.setdefault("queue_workers", {}).setdefault("session_commit", {})["max_concurrent"]=50; assert after == expected; print("local_config_valid=true")'
```

Expected output: `local_config_valid=true`. This proves unrelated local settings were preserved without printing secrets.

- [ ] **Step 4: Install and verify the local configuration**

Copy the validated temporary edit to `~/.openviking/ov.conf`, preserving the original as `ov.conf.backup` in the temporary directory. Parse the installed file and print only `queue_workers.session_commit.max_concurrent`; expected output is `50`. Do not restart OpenViking.

```bash
cp /tmp/openviking-ovconf-session-commit-50/ov.conf.edit ~/.openviking/ov.conf
/usr/bin/python3 -c 'import json, pathlib; data=json.loads((pathlib.Path.home()/".openviking"/"ov.conf").read_text()); print(data["queue_workers"]["session_commit"]["max_concurrent"])'
```
