# Global Agent Evolution Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-user Agent Evolution setting with one deployment-level switch shared by every account and user in an OpenViking server instance.

**Architecture:** `ServerConfig.agent_evolution.enabled` is the only active Agent Evolution setting for HTTP server deployments. `SessionService` snapshots it into each `Session`; commit Phase 1 stores the effective value in archive metadata and Phase 2 consumes that snapshot. Embedded/local SDK clients preserve the historical enabled behavior because they do not load `ServerConfig`. The former user field remains parse-only for compatibility, while user-facing management APIs, clients, and CLI commands are removed.

**Tech Stack:** Python 3.10+, Pydantic v2, FastAPI, pytest, Rust/clap CLI.

---

### Task 1: Define the global configuration contract

**Files:**
- Modify: `openviking/server/config.py`
- Modify: `openviking/server/user_config.py`
- Test: `tests/server/test_agent_evolution_global_setting.py`

- [ ] **Step 1: Replace user-resolution tests with global configuration tests**

Add tests that assert:

```python
def test_agent_evolution_is_disabled_by_default():
    assert ServerConfig().agent_evolution.enabled is False


def test_agent_evolution_can_be_enabled_for_the_server():
    config = ServerConfig.model_validate(
        {"agent_evolution": {"enabled": True}}
    )
    assert config.agent_evolution.enabled is True


def test_deprecated_user_agent_evolution_value_is_ignored():
    user_config = UserConfig.model_validate(
        {"agent_evolution": {"enabled": True}}
    )
    assert user_config.agent_evolution.enabled is True
    assert "agent_evolution" not in user_config.model_dump(exclude_none=True)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
uv run pytest -q --no-cov --tb=short tests/server/test_agent_evolution_global_setting.py
```

Expected: failures because `ServerConfig.agent_evolution` does not exist and the user field is still active.

- [ ] **Step 3: Add the global model and make the old user field parse-only**

Implement:

```python
class AgentEvolutionConfig(BaseModel):
    enabled: bool = False

    model_config = {"extra": "forbid"}


class DeprecatedUserAgentEvolutionConfig(BaseModel):
    enabled: Optional[bool] = None

    model_config = {"extra": "forbid"}


class UserConfig(BaseModel):
    add_targets: AddTargetsConfig = Field(default_factory=AddTargetsConfig)
    agent_evolution: DeprecatedUserAgentEvolutionConfig = Field(
        default_factory=DeprecatedUserAgentEvolutionConfig,
        exclude=True,
    )


class ServerConfig(BaseModel):
    agent_evolution: AgentEvolutionConfig = Field(default_factory=AgentEvolutionConfig)
```

Remove `ResolvedMemorySettings`, `write_user_memory_settings()`,
`resolve_memory_settings()`, and `public_memory_settings()` from
`openviking/server/user_config.py`.

- [ ] **Step 4: Run the configuration tests**

Run:

```bash
uv run pytest -q --no-cov --tb=short tests/server/test_agent_evolution_global_setting.py
```

Expected: all remaining global configuration and compatibility tests pass.

- [ ] **Step 5: Commit**

```bash
git add openviking/server/config.py openviking/server/user_config.py tests/server/test_agent_evolution_global_setting.py
git commit -m "refactor(agent-evolution): use global configuration"
```

### Task 2: Apply the global switch to session commit

**Files:**
- Modify: `openviking/server/app.py`
- Modify: `openviking/service/session_service.py`
- Modify: `openviking/session/session.py`
- Modify: `tests/unit/session/test_agent_evolution_policy.py`
- Modify: `tests/unit/session/test_session_commit_resume.py`
- Modify: `tests/session/test_session_commit.py`

- [ ] **Step 1: Add failing global-switch session tests**

Cover these behaviors:

```python
def test_disabled_global_switch_removes_agent_memory_types():
    policy = _apply_agent_evolution_setting(
        MemoryPolicy.default(),
        agent_evolution_enabled=False,
    )
    assert AGENT_EVOLUTION_MEMORY_TYPES.isdisjoint(policy.memory_types)


async def test_commit_uses_global_enabled_value(session_with_messages):
    session_with_messages._agent_evolution_enabled = True
    result = await session_with_messages.commit_async()
    task = await _wait_for_task(result["task_id"])
    assert task["result"]["agent_evolution_enabled"] is True


async def test_phase2_uses_archived_global_value(session):
    archive_meta = {
        "agent_evolution": {
            "enabled": True,
            "skip_reason": None,
        }
    }
    session._agent_evolution_enabled = False
    await session.resume_queued_commit(
        _queued_commit_with_archive_meta(archive_meta)
    )
    assert (
        session._run_memory_extraction.await_args.kwargs[
            "agent_evolution_enabled"
        ]
        is True
    )
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest -q --no-cov --tb=short \
  tests/unit/session/test_agent_evolution_policy.py \
  tests/unit/session/test_session_commit_resume.py \
  tests/session/test_session_commit.py
```

Expected: failures because sessions still resolve per-user settings.

- [ ] **Step 3: Pass the deployment value into sessions**

In `SessionService`, replace `_user_config_defaults` with:

```python
# Embedded/local compatibility default. HTTP app setup overrides it from
# ServerConfig, whose default is false.
self._agent_evolution_enabled = True

def set_agent_evolution_config(self, config: AgentEvolutionConfig) -> None:
    self._agent_evolution_enabled = config.enabled
```

Pass `agent_evolution_enabled=self._agent_evolution_enabled` when constructing
`Session`, and pass the same value to manual extraction.

In `openviking/server/app.py`, configure the service with:

```python
sessions.set_agent_evolution_config(config.agent_evolution)
```

- [ ] **Step 4: Remove per-user resolution from commit**

In `Session.__init__`, store:

```python
self._agent_evolution_enabled = bool(agent_evolution_enabled)
```

In `commit_async`, derive the effective policy directly from that value:

```python
agent_evolution_enabled = self._agent_evolution_enabled
effective_policy = _apply_agent_evolution_setting(
    effective_policy,
    agent_evolution_enabled=agent_evolution_enabled,
)
```

Remove the user-config exception path and `user_config_error` from new archive
metadata and task results. Keep Phase 2 compatibility reads for old archive
metadata, but do not consult current configuration during resume.

- [ ] **Step 5: Run the focused session tests**

Run:

```bash
uv run pytest -q --no-cov --tb=short \
  tests/unit/session/test_agent_evolution_policy.py \
  tests/unit/session/test_session_commit_resume.py \
  tests/session/test_session_commit.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add openviking/server/app.py openviking/service/session_service.py \
  openviking/session/session.py tests/unit/session/test_agent_evolution_policy.py \
  tests/unit/session/test_session_commit_resume.py tests/session/test_session_commit.py
git commit -m "feat(agent-evolution): apply global commit switch"
```

### Task 3: Remove user-level management surfaces

**Files:**
- Modify: `openviking/server/routers/admin.py`
- Modify: `openviking/server/routers/user_settings.py`
- Modify: `openviking/async_client.py`
- Modify: `openviking/sync_client.py`
- Modify: `openviking/client/local.py`
- Modify: `openviking_cli/client/base.py`
- Modify: `sdk/python/openviking_sdk/client.py`
- Modify: `crates/ov_cli/src/commands/mod.rs`
- Delete: `crates/ov_cli/src/commands/user_settings.rs`
- Modify: `crates/ov_cli/src/main.rs`
- Modify: `crates/ov_cli/src/help_ui.rs`
- Delete: `tests/client/test_user_memory_settings.py`
- Modify: `tests/client/test_base_client_compatibility.py`
- Modify: `sdk/python/tests/test_async_client_behaviors.py`
- Modify: `tests/server/test_admin_api.py`

- [ ] **Step 1: Change API tests to assert the user-level endpoint is absent**

Add:

```python
async def test_agent_evolution_user_endpoint_is_not_registered(client):
    response = await client.get("/api/v1/user-settings/memory")
    assert response.status_code == 404
```

Remove tests that patch or retrieve the former user-level setting.

- [ ] **Step 2: Run API/client tests and verify they fail**

Run:

```bash
uv run pytest -q --no-cov --tb=short \
  tests/server/test_agent_evolution_global_setting.py \
  tests/client/test_base_client_compatibility.py \
  sdk/python/tests/test_async_client_behaviors.py
```

Expected: the endpoint and client methods still exist.

- [ ] **Step 3: Remove the Python and HTTP surfaces**

Delete the memory request model and `/user-settings/memory` routes. Remove
`get_memory_settings()` and `patch_memory_settings()` from embedded, async,
sync, CLI-base, and SDK clients. Remove Agent Evolution handling from account
and user creation; deprecated user input remains accepted by `UserConfig` but
is ignored.

- [ ] **Step 4: Remove the Rust CLI commands**

Delete the `user-settings` command group, dispatcher, help entries, parser
tests, and `commands/user_settings.rs`.

- [ ] **Step 5: Run Python and Rust tests**

Run:

```bash
uv run pytest -q --no-cov --tb=short \
  tests/server/test_agent_evolution_global_setting.py \
  tests/client/test_base_client_compatibility.py \
  sdk/python/tests/test_async_client_behaviors.py
cargo test --manifest-path crates/ov_cli/Cargo.toml user_settings
cargo test --manifest-path crates/ov_cli/Cargo.toml plain_help
```

Expected: Python tests pass; the filtered Rust suites complete without a
failure.

- [ ] **Step 6: Commit**

```bash
git add -A crates/ov_cli openviking sdk/python tests
git commit -m "refactor(agent-evolution): remove user settings surface"
```

### Task 4: Update public configuration documentation

**Files:**
- Delete: `docs/design/agent-evolution-user-configuration.md`
- Modify: `docs/en/guides/01-configuration.md`
- Modify: `docs/zh/guides/01-configuration.md`
- Modify or delete: `docs/en/api/12-user-settings.md`
- Modify or delete: `docs/zh/api/12-user-settings.md`
- Modify: `docs/en/api/01-overview.md`
- Modify: `docs/zh/api/01-overview.md`
- Modify: `docs/.vitepress/config.ts`
- Modify: `docs/scripts/check-api-reference.mjs`

- [ ] **Step 1: Replace user-setting examples with global server configuration**

Document:

```json
{
  "server": {
    "agent_evolution": {
      "enabled": false
    }
  }
}
```

State that the value applies to every account and user served by the process,
controls only future production, and does not hide or delete existing files.

- [ ] **Step 2: Remove API references for `/user-settings/memory`**

Keep existing add-location documentation only where it existed independently
of this branch. Remove navigation entries introduced solely for the deleted
memory-setting API.

- [ ] **Step 3: Run documentation checks**

Run:

```bash
npm --prefix docs run docs:check-api-reference
git diff --check
```

Expected: documentation validation and whitespace checks pass.

- [ ] **Step 4: Commit**

```bash
git add -A docs
git commit -m "docs(agent-evolution): document global switch"
```

### Task 5: Full regression and PR review

**Files:**
- Test only

- [ ] **Step 1: Run the focused Python suite**

```bash
uv run pytest -q --no-cov --tb=short \
  tests/unit/session/test_session_commit_resume.py \
  tests/storage/test_session_commit_processor_identity.py \
  tests/server/test_agent_evolution_global_setting.py \
  tests/unit/session/test_agent_evolution_policy.py \
  tests/unit/usage_reporter \
  tests/session/test_compressor_v3.py \
  tests/session/test_session_commit.py
```

- [ ] **Step 2: Run formatting and static checks**

```bash
uv run ruff check openviking tests sdk/python
uv run ruff format --check openviking tests sdk/python
cargo fmt --manifest-path crates/ov_cli/Cargo.toml --check
git diff --check
```

- [ ] **Step 3: Push and run `review-pr`**

Push the branch, review PR #3223 against `main`, fix every blocking finding
with a failing regression test first, and repeat until the current HEAD has no
blocking findings.
