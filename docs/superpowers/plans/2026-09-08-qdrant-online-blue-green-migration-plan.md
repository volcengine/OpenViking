# Qdrant Online Blue-Green Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved one-way migration from a pre-`#3872` legacy Qdrant source to an explicitly named current-format target pair, with bounded resumable copy, exact reconciliation, barrier-held cutover, and recoverable failure states.

**Architecture:** Keep the migration controller in `scripts/maintenance/qdrant_migrate.py` and keep the adapter responsible only for loading and serving a current-format physical pair. The controller reads the legacy pair through raw REST, writes the target with acknowledged strong ordering, persists progress in the target marker, and uses a temporary standard-library SQLite manifest for exact reconciliation. Application cutover uses one concrete validated argv runner, not a platform-specific client or a one-implementation hook protocol. The operator owns both barrier acquisition and release.

**Tech Stack:** Python standard library (`argparse`, `hashlib`, `json`, `sqlite3`, `tempfile`, `subprocess`, `urllib`), Pydantic configuration models, OpenViking `Collection`/`ICollection`, Qdrant REST API, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-08-qdrant-online-blue-green-migration-design.md`

## Global Constraints

- Scope is exactly `pre-#3872 legacy source -> current-format target`; no current-format-source migration, dual-write, aliases, reverse migration, or generic backend framework.
- The legacy data collection and legacy metadata sidecar are read-only and are never deleted by normal migration, rollback, or retire.
- Target data and metadata names are physical Qdrant names and must be pairwise distinct from each other and both source names.
- `migration_id`, `logical_collection`, `migrator_version`, `timeout_seconds`, and the target physical names are explicit inputs; resume never silently creates a new migration ID.
- Mutating phases require the operator's external per-source lock acknowledgement; the controller does not pretend to be a distributed scheduler.
- Target point mutations and target marker writes use `wait=true` and `ordering=strong`; unsupported strong ordering fails closed.
- `building`, `failed`, and `rolled_back` use `setup_complete=false`; `ready`, `cutting_over`, `active`, and `retained` use `setup_complete=true`.
- A target already owned by another migration ID is never adopted or overwritten.
- Reconciliation uses exactly three maximum rounds and a temporary SQLite manifest containing IDs and fingerprints only.
- Final equality compares canonical IDs, payloads, dense vectors, and sparse vectors directly; a fingerprint is only a bounded candidate filter.
- Final verification stores independently computed `transformed_source_fingerprint` and `target_content_fingerprint`; raw `source_fingerprint` and `verified_source_fingerprint` remain separate drift receipts.
- Dense and sparse datatypes are part of the pinned layout; the approved sparse index datatype is `float16` and has no new runtime tuning flag.
- ACL-incomplete records require `--allow-acl-fail-open`; the marker records the incomplete count and the command prints the risk.
- The operator acquires and releases the write barrier; the controller never invokes a release operation or claims that writes have resumed.
- No new third-party dependency is added; client-side weighted rank fusion remains unchanged.
- Every implementation change starts with a failing focused test and ends with a focused test run, `ruff`, and `git diff --check`.

## File Map

**Configuration and adapter**

- Modify `openviking_cli/utils/config/vectordb_config.py`: add the optional explicit Qdrant data physical-name field without changing old resolver precedence.
- Modify `openviking/storage/vectordb_adapters/qdrant_adapter.py`: resolve physical names, logical identity, timeout, and marker compatibility.
- Modify `openviking/storage/viking_vector_index_backend.py`: delegate Qdrant schema/index updates through the concrete adapter's public method, without reading its private configuration.
- Modify `openviking/storage/vectordb/collection/qdrant_collection.py`: validate the target marker, preserve migration-owned fields, and send strong-ordered writes.
- Modify `openviking/storage/vectordb/collection/qdrant_rest.py`: expose the configured timeout and a single request path used by mutation/readiness checks.

**Migration controller and tests**

- Modify `scripts/maintenance/qdrant_migrate.py`: add stateful phase methods, compact plans, resumable cursors, SQLite reconciliation, exact verification, deployment hooks, rollback, and retire.
- Modify `tests/maintenance/test_qdrant_migrate.py`: extend the fake REST server and add phase/state/recovery tests.
- Modify `tests/storage/test_qdrant_adapter.py`: add physical-name, marker, timeout, and strong-ordering tests.
- Modify `tests/storage/test_qdrant_migration_integration.py`: exercise the explicit target pair and phase API against an opt-in live Qdrant.

**CI and documentation**

- Modify `.github/workflows/pr.yml`: add a conditional Qdrant test job and include all required fake-REST tests.
- Modify `.github/workflows/_test_lite.yml` only to accept optional
  `test_paths_json`; its default remains the existing cuVS suite.
- Modify `scripts/maintenance/README.md`: document online phases, barrier ownership, reviewed plans, recovery, and retire.
- Modify `openviking/storage/vectordb_adapters/README.md`: document explicit physical-name configuration and the current-format marker gate.
- Modify `docs/en/guides/01-configuration.md` and `docs/zh/guides/01-configuration.md`: add the new fields, phase commands, ACL acknowledgement, timeout, rollback boundary, and manifest disk budget.

---

### Task 1: Add explicit physical-name configuration and adapter binding

**Files:**
- Modify: `openviking_cli/utils/config/vectordb_config.py:52-68`
- Modify: `openviking/storage/vectordb_adapters/qdrant_adapter.py:18-116`
- Modify: `openviking/storage/vectordb/collection/qdrant_collection.py:56-85,168-270`
- Test: `tests/storage/test_qdrant_adapter.py`

**Interfaces:**
- `QdrantConfig.data_collection_name: str | None` has no non-null model default.
- `QdrantCollectionAdapter.__init__(url: str, api_key: str | None, timeout_seconds: float, project_name: str, collection_name: str, index_name: str, distance_metric: str, dimension: int, sparse_weight: float, dense_vector_name: str, sparse_vector_name: str, data_collection_name: str | None, metadata_collection_name: str | None, logical_collection: str)`.
- `QdrantCollectionAdapter.from_config(config)` resolves `qdrant.data_collection_name` and `qdrant.metadata_collection_name` before `custom_params`, then falls back to the existing derived names.
- `QdrantCollection(client: QdrantRestClient, collection_name: str, metadata_collection_name: str, dense_vector_name: str, sparse_vector_name: str, vector_dim: int, distance: str, sparse_enabled: bool, sparse_weight: float, logical_collection: str | None = None)` receives the already-resolved physical pair and never recomputes it from a logical name.

- [ ] **Step 1: Write failing configuration and routing tests**

```python
def test_explicit_qdrant_physical_names_override_custom_params():
    config = VectorDBBackendConfig(
        backend="qdrant",
        project="default",
        name="context",
        dimension=2,
        custom_params={
            "data_collection_name": "custom-data",
            "metadata_collection_name": "custom-meta",
        },
        qdrant={
            "url": "http://qdrant.local",
            "data_collection_name": "generation-data",
            "metadata_collection_name": "generation-meta",
        },
    )
    adapter = QdrantCollectionAdapter.from_config(config)
    collection = adapter._new_collection()
    assert collection._collection_name == "generation-data"
    assert collection._metadata_collection_name == "generation-meta"


def test_data_name_only_derives_metadata_sidecar():
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={
            "url": "http://qdrant.local",
            "data_collection_name": "generation-data",
        },
        dimension=2,
    )
    adapter = QdrantCollectionAdapter.from_config(config)
    collection = adapter._new_collection()
    assert collection._collection_name == "generation-data"
    assert collection._metadata_collection_name == "generation-data__openviking_meta"


def test_omitted_physical_names_keep_project_name_derivation():
    config = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={"url": "http://qdrant.local"},
        project="project",
        name="docs",
        dimension=2,
    )
    adapter = QdrantCollectionAdapter.from_config(config)
    assert adapter._new_collection()._collection_name == "project__docs"
```

- [ ] **Step 2: Run the new tests and confirm the expected failure**

Run: `uv run --project . pytest -q tests/storage/test_qdrant_adapter.py -k 'physical_names or derives_metadata or project_name_derivation'`

Expected: collection/configuration failures because `data_collection_name` and the explicit adapter route do not exist yet.

- [ ] **Step 3: Implement the minimum resolver and pass both physical names through**

Add the optional Pydantic field, retain the current URL/API/vector fallback order, and set:

```python
physical_data = (
    qdrant_cfg.data_collection_name
    if qdrant_cfg and qdrant_cfg.data_collection_name is not None
    else custom.get("data_collection_name")
    or f"{project_name}__{collection_name}"
)
physical_meta = (
    qdrant_cfg.metadata_collection_name
    if qdrant_cfg and qdrant_cfg.metadata_collection_name is not None
    else custom.get("metadata_collection_name")
    or f"{physical_data}__openviking_meta"
)
logical_collection = f"{project_name or 'default'}/{collection_name or 'context'}"
```

Reject empty or pairwise-equal physical names before constructing `QdrantCollection`. Keep an omitted `data_collection_name` distinguishable from an explicitly supplied empty value so the Pydantic validator can reject the latter.

- [ ] **Step 4: Add marker binding checks without breaking direct-name mode**

Store `logical_collection` in newly written markers, preserve the existing `vector_dim` field for old current-format markers, and also write/read `vector_dimension` for migration markers. `_ensure_loaded()` must reject:

```text
setup_complete != true
collection_name != configured data physical name
metadata_collection_name != configured metadata physical name when present
logical_collection != configured logical identity when present
migration_state == rolled_back
```

A marker without `_openviking_meta_version` remains a legacy/unmarked collection and fails closed. Normal `update()`, `create_index()`, `update_index()`, and `drop_index()` calls must preserve `migration_id`, `migration_state`, source fingerprints, target counts, and logical/physical identity fields.

- [ ] **Step 5: Run the focused adapter tests**

Run: `uv run --project . pytest -q tests/storage/test_qdrant_adapter.py -k 'physical_names or marker or migration_provenance or unmarked or incomplete'`

- [ ] **Step 6: Commit the configuration/adapter boundary**

```bash
git add openviking_cli/utils/config/vectordb_config.py \
  openviking/storage/vectordb_adapters/qdrant_adapter.py \
  openviking/storage/vectordb/collection/qdrant_collection.py \
  tests/storage/test_qdrant_adapter.py
git commit -m "feat: bind qdrant adapter to explicit physical names"
```

---

### Task 2: Enforce timeout, acknowledged writes, and strong ordering

**Files:**
- Modify: `openviking/storage/vectordb/collection/qdrant_rest.py:32-117`
- Modify: `openviking/storage/vectordb/collection/qdrant_collection.py:113-207,330-465,458-465`
- Modify: `scripts/maintenance/qdrant_migrate.py:555-629,1975-2052`
- Test: `tests/storage/test_qdrant_adapter.py`
- Test: `tests/maintenance/test_qdrant_migrate.py`

**Interfaces:**
- `QdrantRestClient.timeout_seconds -> float` exposes the validated timeout.
- `QdrantRestClient.request(method, path, body=None, *, params=None)` keeps the existing injectable transport and passes the configured timeout on every call.
- `QdrantCollection._strong_point_params() -> dict[str, str]` returns `{"wait": "true", "ordering": "strong"}` for point and marker writes; collection/index readiness writes use `{"wait": "true"}` because Qdrant does not accept point ordering on those endpoints.
- `QdrantMigration._request(method: str, path: str, body: dict[str, Any] | None = None, *, params: dict[str, Any] | None = None, mutation: bool = False) -> dict[str, Any]` adds the same parameters for target mutations and accepts an explicit `timeout_seconds` in the controller constructor.
- `QdrantMigration._assert_strong_ordering_support() -> None` raises `MigrationError` when a target mutation explicitly reports that `ordering=strong` is unsupported.

- [ ] **Step 1: Add failing transport and mutation-parameter assertions**

Extend `_ScriptedTransport` and `FakeQdrant` to retain query parameters, then add:

```python
def test_rest_client_exposes_and_applies_timeout():
    transport = _ScriptedTransport((200, {"result": True}))
    client = QdrantRestClient("http://qdrant.local", timeout_seconds=17, opener=transport)
    client.request("GET", "/collections/demo")
    assert client.timeout_seconds == 17.0
    assert transport.requests[0]["timeout"] == 17.0


def test_target_mutations_use_wait_and_strong_ordering():
    # create_remote_collection, create_index, upsert_data, and delete_data
    # are called against the scripted transport.
    assert parse_qs(urlsplit(request["url"]).query)["ordering"] == ["strong"]
    assert parse_qs(urlsplit(request["url"]).query)["wait"] == ["true"]
```

Add a migration test that constructs `QdrantMigration(timeout_seconds=37)` and asserts every target `PUT /points`, `POST /points/delete`, and marker write carries both parameters, while collection creation and index creation carry `wait=true` and no unsupported point-ordering parameter.

- [ ] **Step 2: Run the tests and record the expected failures**

Run:

```bash
uv run --project . pytest -q tests/storage/test_qdrant_adapter.py -k 'timeout or mutation or rest_client'
uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'strong_ordering or timeout'
```

Expected: `timeout_seconds` is not exposed and existing writes only send `wait=true`.

- [ ] **Step 3: Implement one mutation helper and use it everywhere**

Validate `timeout_seconds > 0` in `QdrantRestClient`. Add the property, use `wait=true&ordering=strong` for Qdrant collection point upserts, deletes, marker writes, and sparse-term writes, and use `wait=true` for collection/index creation. Keep read requests free of write-ordering parameters.

The migration controller must construct its REST client with the CLI timeout when invoked from `main`, and its `_request(method, path, body, params=params, mutation=True)` must add `wait=true&ordering=strong` only for target point upsert/delete and marker paths; target collection/index setup uses `wait=true`. Source reads remain read-only.

- [ ] **Step 4: Add the strong-ordering capability gate and readiness gate**

Call `_assert_strong_ordering_support()` during `preflight` to record that the plan requires the strong-ordering contract, then probe the endpoint on the first acknowledged target point write (the incomplete marker in `prepare`). The fake server returns a Qdrant 400 response containing `ordering`/`strong` when it cannot honor the option; the controller reports that exact failure and refuses to continue rather than retrying without `ordering=strong`. Qdrant has no separate read-only ordering-discovery endpoint, so the first target write is the capability receipt.

Implement `QdrantMigration._wait_collection_ready(collection: str)` by polling `GET /collections/{name}` until the response reports a ready/green state, while rejecting an explicit red/failed state and a timeout. The fake server returns `{"status": "green"}` immediately; the method is called after target collection/index creation and before `verify`, `cutover`, or smoke.

- [ ] **Step 5: Run focused REST and collection tests**

Run:

```bash
uv run --project . pytest -q tests/storage/test_qdrant_adapter.py -k 'rest_client or lifecycle or index or delete'
uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'strong_ordering or timeout or readiness'
```

- [ ] **Step 6: Commit the REST consistency boundary**

```bash
git add openviking/storage/vectordb/collection/qdrant_rest.py \
  openviking/storage/vectordb/collection/qdrant_collection.py \
  scripts/maintenance/qdrant_migrate.py \
  tests/storage/test_qdrant_adapter.py \
  tests/maintenance/test_qdrant_migrate.py
git commit -m "feat: require acknowledged strongly ordered qdrant writes"
```

---

### Task 3: Add migration identity, compact plans, state machine, and CLI phases

**Files:**
- Modify: `scripts/maintenance/qdrant_migrate.py:105-185,552-608,1267-1552,2649-2848`
- Test: `tests/maintenance/test_qdrant_migrate.py`
- Test: `tests/storage/test_qdrant_migration_integration.py`

**Interfaces:**
- Constants:

```python
MIGRATOR_VERSION = "qdrant-blue-green-v1"
MAX_RECONCILIATION_ROUNDS = 3
MIGRATION_STATES = frozenset(
    {"building", "ready", "cutting_over", "active", "retained", "rolled_back", "failed"}
)
```

- `QdrantMigration.__init__(client: Any, source_collection: str, target_collection: str, source_metadata_collection: str | None, target_metadata_collection: str | None, batch_size: int, dense_vector_name: str | None, sparse_vector_name: str | None, sparse_map: Mapping[Any, Any] | None, logical_collection: str, migration_id: str, timeout_seconds: float = 10.0, migrator_version: str = MIGRATOR_VERSION)`.
- `QdrantMigration.preflight() -> MigrationPlan`.
- `MigrationPlan.to_dict()` emits names, identity, layout (including dense and sparse datatype), counts, raw-source/metadata/sparse fingerprints, ACL count, `batch_size`, `timeout_seconds`, `migration_id`, `migrator_version`, `target_absent`, and current target state, but never `id_map`, `existing_target_ids`, full vectors, payloads, or the Qdrant URL/API key.
- `QdrantMigration._transition(target_state: str, *, setup_complete: bool | None = None) -> dict[str, Any]` validates allowed states and writes/read-verifies the target marker.
- CLI subcommands are `preflight`, `prepare`, `backfill`, `reconcile`, `verify`, `cutover`, `rollback`, and `retire`; `apply` remains an offline wrapper for `prepare + backfill + verify`.

- [ ] **Step 1: Add failing state and plan-schema tests**

```python
def test_preflight_plan_is_compact_and_binds_identity():
    plan = _migration(
        _legacy_fixture(sparse=False),
        logical_collection="legacy/context",
        migration_id="mig-1",
        timeout_seconds=23,
    ).preflight()
    value = plan.to_dict()
    assert value["logical_collection"] == "legacy/context"
    assert value["migration_id"] == "mig-1"
    assert value["timeout_seconds"] == 23.0
    assert "id_map" not in value
    assert "existing_target_ids" not in value
    assert "url" not in value
    assert "api_key" not in value


def test_foreign_target_marker_is_rejected():
    qdrant = _legacy_fixture(sparse=False)
    _add_current_marker(qdrant, migration_id="other", migration_state="building")
    with pytest.raises(MigrationError, match="migration ID"):
        _migration(qdrant, logical_collection="legacy/context", migration_id="mig-1").preflight()


def test_state_transition_preserves_setup_gate():
    migration = _migration(
        _legacy_fixture(sparse=False),
        logical_collection="legacy/context",
        migration_id="mig-1",
    )
    assert migration._transition("building")["setup_complete"] is False
    assert migration._transition("ready")["setup_complete"] is True
```

Add CLI parser tests that require `--logical-collection`, `--migration-id`, and `--timeout-seconds` on every phase and reject a phase-specific missing `--plan`, `--confirm`, or `--barrier-held`.

- [ ] **Step 2: Run the plan/state tests and confirm the expected failure**

Run: `uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'compact or identity or foreign_target or state_transition or cli_phase'`

Expected: constructor/plan/CLI failures because the current plan contains an unbounded ID map and only has `preflight`/`apply`.

- [ ] **Step 3: Replace plan serialization and add explicit state validation**

Remove `id_map` and `existing_target_ids` from serialized plans. Keep duplicate/collision information in bounded scan state only; later reconciliation uses SQLite. Validate the exact plan field set in `_load_plan`, compare the reviewed plan with a fresh preflight, and reject a different migration ID, logical identity, target pair, timeout, batch size, layout, metadata fingerprint, or sparse-map fingerprint.

Add marker fields:

```json
{
  "logical_collection": "default/context",
  "collection_name": "<target data name>",
  "metadata_collection_name": "<target metadata name>",
  "migration_id": "<explicit id>",
  "migrator_version": "qdrant-blue-green-v1",
  "migration_state": "building",
  "last_source_cursor": null,
  "backfill_complete": false,
  "source_fingerprint": "<raw-source drift receipt>",
  "transformed_source_fingerprint": "",
  "target_content_fingerprint": "",
  "source_count": 0,
  "target_count": 0,
  "setup_complete": false
}
```

Use the stable transform-schema version in `migrator_version`; a change requires a new migration ID and target.

- [ ] **Step 4: Implement the phase parser and offline compatibility wrapper**

Add shared arguments (`--url`, `--api-key`, source/target names, `--logical-collection`, `--migration-id`, `--batch-size`, `--timeout-seconds`, vector overrides, sparse map) and phase-specific flags. `--lock-held` is required for `prepare`, `backfill`, `reconcile`, `cutover`, `rollback`, and `retire`; `preflight` remains read-only and may be run before the external lock is acquired.

```text
prepare/backfill: --plan PATH --confirm --lock-held [--allow-acl-fail-open]
reconcile: --plan PATH --confirm --lock-held [--barrier-held] [--allow-acl-fail-open]
verify: --plan PATH [--final --barrier-held --confirm --lock-held] [--allow-acl-fail-open]
cutover: --plan PATH --confirm --barrier-held --lock-held [--resume] [--allow-acl-fail-open] --deployment-hooks PATH
rollback: --confirm --barrier-held --lock-held --no-current-format-writes-accepted --deployment-hooks PATH
retire: --plan PATH --confirm --lock-held --deployment-hooks PATH
```

`apply` invokes the offline sequence with its existing full-window source-write freeze semantics, never changes application routing, and refuses `active`, `retained`, or `rolled_back` targets.

- [ ] **Step 5: Run all existing maintenance tests plus the new phase tests**

Run: `uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py`

- [ ] **Step 6: Commit the controller identity/state boundary**

```bash
git add scripts/maintenance/qdrant_migrate.py \
  tests/maintenance/test_qdrant_migrate.py \
  tests/storage/test_qdrant_migration_integration.py
git commit -m "feat: add explicit qdrant migration phases and states"
```

---

### Task 4: Implement safe prepare, ownership checks, and sparse dictionary setup

**Files:**
- Modify: `scripts/maintenance/qdrant_migrate.py:1367-1775,1975-2052`
- Test: `tests/maintenance/test_qdrant_migrate.py`

**Interfaces:**
- `QdrantMigration.prepare(*, confirm: bool, plan: MigrationPlan, allow_acl_fail_open: bool = False) -> dict[str, Any]`.
- `QdrantMigration._create_target_pair(layout: CollectionLayout, metadata: LegacyMetadata) -> None`.
- `QdrantMigration._cleanup_pre_marker_orphan(*, reviewed_plan: MigrationPlan, confirm: bool, lock_held: bool) -> None`.

- [ ] **Step 1: Add failing prepare and orphan tests**

Cover these exact tests:

- `test_prepare_creates_both_collections_before_marker_write`
- `test_prepare_rejects_source_target_name_collision`
- `test_prepare_rejects_foreign_marker_and_shared_metadata_sidecar`
- `test_pre_marker_orphan_cleanup_requires_target_absent_review_and_confirm`
- `test_prepare_race_re_reads_409_and_accepts_only_same_migration_marker`
- `test_sparse_dictionary_write_is_chunked_and_verified`

The fake server records operation order. It must show both `PUT /collections/<target>` and `PUT /collections/<target-meta>` before the first target marker point write.

- [ ] **Step 2: Run the failing prepare tests**

Run: `uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'prepare or orphan or shared_metadata or sparse_dictionary'`

Expected: the current `apply()` writes a metadata marker before creating the data collection and has no reviewed-orphan gate.

- [ ] **Step 3: Implement target pair creation and foreign-marker refusal**

Create the target data collection with the discovered dense/sparse layout, create the target metadata sidecar with the one-dimensional `meta` vector, wait for both collections to be ready, create required scalar indexes, and write the incomplete marker only after both collections exist and match this migration ID. Re-read a 409 creation response and accept it only when the existing marker has the same migration ID, logical identity, physical pair, and `setup_complete=false`; otherwise fail closed.

Before any marker exists, an unmarked pair is never adopted. Cleanup is permitted only when the reviewed plan had `target_absent=true`, the operator supplied the external per-source lock acknowledgement and `--confirm`, and an exact-name recheck still shows both names unmarked. Catch `BaseException` only in this pre-marker cleanup and re-raise `KeyboardInterrupt`/`SystemExit` after best-effort deletion.

- [ ] **Step 4: Make sparse setup authoritative and idempotent**

Validate the supplied legacy sparse map, write missing term points in `batch_size` chunks with strong ordering, and read them back before reporting success. Reject missing terms, stable-index collisions, duplicate term points, and a target dictionary point that belongs to another migration.

- [ ] **Step 5: Run focused prepare tests**

Run: `uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'prepare or orphan or shared_metadata or sparse_dictionary or creation_race'`

- [ ] **Step 6: Commit the prepare boundary**

```bash
git add scripts/maintenance/qdrant_migrate.py tests/maintenance/test_qdrant_migrate.py
git commit -m "feat: make qdrant target preparation ownership-safe"
```

---

### Task 5: Implement bounded resumable backfill with an opaque cursor

**Files:**
- Modify: `scripts/maintenance/qdrant_migrate.py:785-827,1177-1274,1975-2052,2332-2635`
- Test: `tests/maintenance/test_qdrant_migrate.py`

**Interfaces:**
- `QdrantMigration._scroll_page(collection: str, *, offset: int | str | None, with_vectors: bool, filter: Mapping[str, Any] | None = None) -> tuple[list[dict[str, Any]], int | str | None]`.
- `QdrantMigration.backfill(*, confirm: bool, plan: MigrationPlan) -> dict[str, Any]`.
- `QdrantMigration._upsert_target_batch(points: list[dict[str, Any]]) -> tuple[int, int]`.

- [ ] **Step 1: Add failing cursor and bounded-memory tests**

Add these exact tests:

- `test_backfill_persists_integer_cursor_after_each_batch`
- `test_backfill_persists_string_cursor_without_coercion`
- `test_backfill_rejects_malformed_or_repeated_cursor`
- `test_failed_batch_can_be_retried_without_source_mutation`
- `test_backfill_holds_one_page_and_one_write_batch`

Instrument the fake server and migration marker writes so the test can assert the cursor is persisted only after a successful target write and that a failed write leaves the same cursor for retry.

- [ ] **Step 2: Run the failing backfill tests**

Run: `uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'backfill or cursor or batch'`

Expected: the current generator consumes the full source snapshot and `apply()` has no cursor marker.

- [ ] **Step 3: Replace generator-only scrolling with page-at-a-time scrolling**

Implement `_scroll_page` with the opaque Qdrant `next_page_offset`. Validate that an offset is `None`, an integer, or a string; reject booleans, mappings, lists, and a repeated serialized offset. Transform only the current page, flush target writes in `batch_size` chunks, then write the new `last_source_cursor` and `backfill_complete` bit to the marker with `migration_state=building` and `setup_complete=false`. A null cursor is completion only when the bit is true; it never aliases an unstarted page.

- [ ] **Step 4: Preserve idempotence and source immutability**

For each transformed point, require `_openviking_original_id`, validate the legacy uint64/UUID physical-ID encoding, preserve the normalized logical ID and URI/owner/ACL payload, reject duplicate logical IDs and target-ID collisions, reject missing/non-finite vectors and missing sparse-map terms, then retrieve the target ID, compare the logical ID and canonical payload/vector, and upsert only a missing or changed point. Never call a source `PUT`, `POST /points/delete`, or partial-update route. A failed target batch is retried from the persisted cursor page; no cursor is advanced before the acknowledged target write completes. Marker source/ACL/distinct-term summaries remain the latest full-source scan observations, not page-progress substitutes.

- [ ] **Step 5: Run maintenance regression and resume tests**

Run:

```bash
uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py
uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'backfill or resume or cursor'
```

- [ ] **Step 6: Commit resumable backfill**

```bash
git add scripts/maintenance/qdrant_migrate.py tests/maintenance/test_qdrant_migrate.py
git commit -m "feat: add cursor-based qdrant backfill"
```

---

### Task 6: Implement exact three-round SQLite reconciliation

**Files:**
- Modify: `scripts/maintenance/qdrant_migrate.py:279-310,1177-1274,1561-1775,2053-2195,2590-2635`
- Test: `tests/maintenance/test_qdrant_migrate.py`

**Interfaces:**
- `QdrantMigration._open_manifest() -> sqlite3.Connection`.
- `QdrantMigration._reconcile_round(*, layout: CollectionLayout, schema: Mapping[str, Any], metadata: LegacyMetadata, state: str) -> SourceSnapshot`.
- `QdrantMigration.reconcile(*, confirm: bool, plan: MigrationPlan, barrier_held: bool = False, allow_acl_fail_open: bool = False) -> dict[str, Any]`.

The temporary `_ScanManifest` reuses the controller's disk-backed tables for
source IDs, logical IDs, target IDs, canonical content fingerprints, and sparse
terms. Their private SQLite names are an implementation detail; there is no
required public physical table name and no duplicate full-points store.

- [ ] **Step 1: Add failing reconciliation tests**

Cover these exact tests:

- `test_reconcile_upserts_source_payload_and_vector_changes`
- `test_reconcile_deletes_target_extras`
- `test_reconcile_uses_sqlite_manifest_not_an_unbounded_id_set`
- `test_reconcile_rechecks_fingerprint_candidates_with_direct_payload_vector_compare`
- `test_reconcile_fails_closed_on_metadata_or_sparse_map_drift`
- `test_reconcile_fails_after_three_non_converging_rounds`
- `test_cutover_reconcile_preserves_cutting_over_and_setup_gate`
- `test_interrupted_reconcile_rebuilds_and_deletes_manifest`

The fake source mutates between rounds; the test verifies that only the final source snapshot is published to the marker and that the temporary SQLite file is absent after success or failure cleanup.

- [ ] **Step 2: Run the failing reconciliation tests**

Run: `uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'reconcile or manifest or non_converging or metadata_drift'`

Expected: the current code has no SQLite manifest, target-extra deletion, or bounded reconciliation rounds.

- [ ] **Step 3: Implement a disk-backed source manifest**

Open a temporary SQLite file, insert the bounded ID/fingerprint rows in
batches, and keep only one source page, one transform batch, and one SQLite
transaction in memory. Delete the manifest in `finally`; do not serialize it
into plan JSON or the target marker.

- [ ] **Step 4: Apply source changes and remove target extras**

Scan the source from the beginning each round, transform records, and upsert missing/changed target points. Stream the target collection; for each target ID, query the manifest for existence and delete absent IDs in acknowledged strong-ordered batches. For fingerprint candidates, retrieve vectors and compare canonical payload and vector values directly before deciding to write. Recheck sparse dictionary membership and write missing terms in chunks.

- [ ] **Step 5: Add pinned metadata/sparse drift and fixed convergence rules**

Recompute the selected legacy metadata and authoritative sparse-map fingerprints before and after every round. If either changes, write `migration_state=failed` and raise `MigrationError`. Run at most `MAX_RECONCILIATION_ROUNDS == 3`; if the rolling raw-source fingerprint and counts do not converge, fail closed with the round number and source/target counts. Independently persist canonical `transformed_source_fingerprint` and `target_content_fingerprint` receipts. When `barrier_held=True`, preserve `migration_state=cutting_over` and `setup_complete=true` throughout the final pass.

- [ ] **Step 6: Run focused and full maintenance tests**

Run:

```bash
uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'reconcile or manifest or non_converging or metadata_drift'
uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py
```

- [ ] **Step 7: Commit reconciliation**

```bash
git add scripts/maintenance/qdrant_migrate.py tests/maintenance/test_qdrant_migrate.py
git commit -m "feat: reconcile qdrant migration with sqlite manifest"
```

---

### Task 7: Implement streamed exact verification and state-safe reruns

**Files:**
- Modify: `scripts/maintenance/qdrant_migrate.py:2071-2331`
- Test: `tests/maintenance/test_qdrant_migrate.py`

**Interfaces:**
- `QdrantMigration.verify(*, plan: MigrationPlan, allow_acl_fail_open: bool = False, final: bool = False, barrier_held: bool = False, confirm: bool = False, lock_held: bool = False) -> dict[str, Any]`.
- `QdrantMigration._verify_point_pair(source_point: Mapping[str, Any], target_point: Mapping[str, Any], schema: Mapping[str, Any], layout: CollectionLayout, allow_acl_fail_open: bool) -> None`.
- `QdrantMigration._verify_target_marker(marker: Mapping[str, Any], *, expected_state: str | None = None) -> None`.

- [ ] **Step 1: Add failing exact-verification tests**

Cover these exact tests:

- `test_verify_detects_dense_vector_value_mismatch_even_when_fingerprint_matches`
- `test_verify_detects_sparse_vector_value_and_name_mismatch`
- `test_verify_detects_payload_and_acl_mismatch`
- `test_verify_detects_target_extra_and_missing_id`
- `test_verify_requires_matching_indexes_metadata_and_marker_layout`
- `test_non_cutover_verify_changes_building_to_ready`
- `test_final_verify_does_not_reset_cutting_over_to_ready`
- `test_active_retained_and_rolled_back_targets_reject_mutating_reruns`

- [ ] **Step 2: Run the failing verification tests**

Run: `uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'verify or mismatch or active or retained or rolled_back'`

Expected: current `_validate_final_target()` is tied to the frozen `apply()` snapshot and has no explicit `final` state semantics.

- [ ] **Step 3: Implement streamed exact comparison**

Use the invocation-local SQLite manifest for source target IDs when needed,
compare source/target counts, logical IDs, normalized payloads, URI sidecars
(`uri_depth`, `scope_roots`), ACL fields, dense vector names/dimensions/values,
sparse indices/values, and target payload indexes. Validate the marker's raw
source/metadata/sparse fingerprints, migration ID, migrator version,
logical/physical names, vector layout (including sparse datatype), sparse
policy, ACL-incomplete count, and independent transformed-source and
target-content receipts.

Never treat a hash match as final equality. A missing `_openviking_original_id`, duplicate logical ID, target-ID collision, invalid/non-finite vector, malformed payload, missing sparse term, or incomplete ACL without the explicit acknowledgement raises `MigrationError`.

- [ ] **Step 4: Implement state-safe completion**

`verify(final=False)` transitions only `building -> ready`; it leaves `active`,
`retained`, and `rolled_back` unchanged. `verify(final=True)` requires
`cutting_over`, `barrier_held=True`, `confirm=True`, and `lock_held=True`, and
records success without writing `ready`. A same-fingerprint read-only rerun
never resets `setup_complete=true`, while mutating phases reject `active`,
`retained`, and `rolled_back`.

- [ ] **Step 5: Run all maintenance verification tests**

Run: `uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py`

- [ ] **Step 6: Commit exact verification**

```bash
git add scripts/maintenance/qdrant_migrate.py tests/maintenance/test_qdrant_migrate.py
git commit -m "feat: verify qdrant migration state and data exactly"
```

---

### Task 8: Add barrier-held cutover, rollback, and explicit retire recovery

**Files:**
- Modify: `scripts/maintenance/qdrant_migrate.py:222-253,2772-2848`
- Test: `tests/maintenance/test_qdrant_migrate.py`

**Interfaces:**

- `DeploymentHooks(commands: Mapping[str, Any])` validates one exact JSON
  document and runs the ten required operations with `shell=False`, captured
  output, and the migration timeout. It is one concrete runner, not a
  `Protocol` or an implicit no-op.
- Required keys are
  `drain_legacy_writes`, `remove_legacy_from_serving_path`,
  `rollout_current`, `wait_current_ready`, `smoke_current_read_only`,
  `remove_current_from_serving_path`, `restore_legacy`,
  `verify_legacy_read_path`, `current_target_has_accepted_writes`, and
  `assert_target_not_served`. Only the accepted-writes hook consumes stdout,
  and it must be exact lowercase `true` or `false`; all other hooks assert
  exit status zero.
- `QdrantMigration.cutover(*, confirm: bool, plan: MigrationPlan, barrier_held: bool = False, hooks: Any, allow_acl_fail_open: bool = False, lock_held: bool = False, resume: bool = False) -> dict[str, Any]`.
- `QdrantMigration.rollback(*, confirm: bool, barrier_held: bool = False, no_current_format_writes_accepted: bool = False, hooks: Any, lock_held: bool = False) -> dict[str, Any]`.
- `QdrantMigration.retire(*, confirm: bool, plan: MigrationPlan, lock_held: bool = False, hooks: Any) -> dict[str, Any]`.

- [ ] **Step 1: Add failing cutover/rollback/retire tests**

Add these exact tests:

- `test_cutover_requires_ready_target_and_barrier`
- `test_cutover_drains_legacy_before_final_source_snapshot`
- `test_cutover_removes_old_serving_path_before_current_rollout`
- `test_cutover_readiness_and_smoke_are_read_only`
- `test_cutover_failure_leaves_barrier_held_and_cutting_over_marker`
- `test_rollback_requires_barrier_and_no_accepted_target_writes`
- `test_rollback_restores_legacy_and_marks_target_rolled_back`
- `test_rollback_refuses_after_target_write_or_released_barrier`
- `test_retire_refuses_a_serving_target`
- `test_retire_marks_retained_before_deleting_non_serving_pair`
- `test_retire_orphan_cleanup_requires_absent_plan_exact_names_and_confirm`

The fake deployment hook records ordering and whether the smoke callback attempted any Qdrant mutation. The cutover test changes the source after the pre-cutover verify and confirms the final reconciliation sees only the post-drain snapshot.

- [ ] **Step 2: Run the failing orchestration tests**

Run: `uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'cutover or rollback or retire or barrier'`

Expected: the current script has no deployment hook, barrier acknowledgement, rollback state, or serving-target guard.

- [ ] **Step 3: Implement cutover ordering and barrier semantics**

Require `migration_state=ready`, `--confirm`, `--lock-held`, and
`--barrier-held`. Validate all ten hooks, write `cutting_over`, drain
in-flight legacy writes, remove the legacy deployment from the serving path,
run `reconcile(barrier_held=True)` and
`verify(final=True, barrier_held=True, confirm=True, lock_held=True)`, invoke
the current rollout/readiness/read-only smoke hooks, inspect accepted writes,
then write and re-read `active`. The operator, not the controller, releases
the barrier. On any exception, leave `cutting_over` or `failed` and re-raise
without an automatic release.

The CLI loads a JSON deployment-hook specification containing command arrays for
the ten required operations. Execute each argv array with
`subprocess.run(argv, check=True, shell=False, capture_output=True, text=True,
timeout=timeout_seconds, env=env)` and these non-secret environment variables:

```text
OV_LOGICAL_COLLECTION
OV_MIGRATION_ID
OV_SOURCE_COLLECTION
OV_SOURCE_METADATA_COLLECTION
OV_TARGET_COLLECTION
OV_TARGET_METADATA_COLLECTION
OV_TIMEOUT_SECONDS
OV_MIGRATOR_VERSION
```

The file shape is exact; each value is an argv array, and the write-acceptance
hook must print only `true` or `false` on stdout:

```json
{
  "drain_legacy_writes": ["./ops/drain-legacy.sh"],
  "remove_legacy_from_serving_path": ["./ops/remove-legacy.sh"],
  "rollout_current": ["./ops/rollout-current.sh"],
  "wait_current_ready": ["./ops/wait-current-ready.sh"],
  "smoke_current_read_only": ["./ops/smoke-current-readonly.sh"],
  "remove_current_from_serving_path": ["./ops/remove-current.sh"],
  "restore_legacy": ["./ops/restore-legacy.sh"],
  "verify_legacy_read_path": ["./ops/verify-legacy-read.sh"],
  "current_target_has_accepted_writes": ["./ops/current-writes-accepted.sh"],
  "assert_target_not_served": ["./ops/assert-target-not-served.sh"]
}
```

An absent or malformed hook specification is a hard error for `cutover`,
`rollback`, and `retire`, not an implicit no-op.

- [ ] **Step 4: Implement barrier-held rollback**

Accept only `cutting_over` or barrier-held `active`, require
`--barrier-held`, `--lock-held`, `--confirm`, and
`--no-current-format-writes-accepted`, and ask the strict hook whether a
current-format write was accepted. Remove current serving, assert the target is
not served, restore the legacy path, verify its read path, write
`migration_state=rolled_back` with `setup_complete=false`, and retain both
collections. After barrier release or any accepted target write, refuse
automatic rollback.

- [ ] **Step 5: Implement explicit retire**

Require migration ID, exact target pair, external per-source lock acknowledgement,
a reviewed plan, `--confirm`, and the concrete hooks runner. Refuse deletion
while a deployment serves the target. For `active`, write `retained`, read it
back, delete target data first, verify absence, and delete target metadata last.
Support owned retained-pair and retained metadata-only retries. For a
pre-marker process-death orphan, permit deletion only when the reviewed plan
had `target_absent=true`, exact-name recheck finds both names still unmarked,
and the pair is empty; never delete the legacy source. Do not widen ordinary
retire to `ready`, `building`, `failed`, `cutting_over`, or `rolled_back`.

- [ ] **Step 6: Run focused orchestration tests**

Run:

```bash
uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'cutover or rollback or retire or barrier'
uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py
```

- [ ] **Step 7: Commit orchestration and recovery**

```bash
git add scripts/maintenance/qdrant_migrate.py tests/maintenance/test_qdrant_migrate.py
git commit -m "feat: add barrier-held qdrant cutover and recovery"
```

---

### Task 9: Extend adapter and live integration coverage

**Files:**
- Modify: `tests/storage/test_qdrant_adapter.py`
- Modify: `tests/storage/test_qdrant_migration_integration.py`

**Interfaces:**
- The adapter tests use `VectorDBBackendConfig(qdrant={"data_collection_name": "generation-data", "metadata_collection_name": "generation-meta"})`.
- The integration test constructs `QdrantMigration` with the current adapter's
  `logical_collection=f"{project}/context"`, explicit source/target physical
  pairs, `migration_id=suffix`, and matching `timeout_seconds=30`; it runs
  `preflight`, `prepare`, `backfill`, `reconcile`, and `verify` with the real
  reviewed-plan/confirm/lock arguments, then creates
  `QdrantCollectionAdapter` with the target physical names. The fixture
  preserves dense `float32` and sparse index datatype `float16`.

- [ ] **Step 1: Add failing adapter marker and routing coverage**

Add these exact tests:

- `test_explicit_pair_routes_create_upsert_fetch_delete_to_target_names`
- `test_target_marker_round_trips_logical_and_physical_identity`
- `test_vector_layout_and_sparse_policy_mismatch_is_rejected`
- `test_migration_fields_survive_normal_marker_rewrites`
- `test_legacy_marker_is_not_loadable_by_current_adapter`
- `test_missing_original_id_does_not_fabricate_a_record_id`

- [ ] **Step 2: Run the focused adapter tests**

Run: `uv run --project . pytest -q tests/storage/test_qdrant_adapter.py`

- [ ] **Step 3: Update the opt-in live test to use the new phases**

Keep `@pytest.mark.skipif(not QDRANT_URL, reason="QDRANT_URL not set")` and unique names. Create a pre-`#3872` fixture, preserve a full source/metadata snapshot, run all non-cutover phases, assert the target marker and direct adapter round trip, and delete only the four test collections in a `finally` block. The live test never migrates Monster and never deletes a user-named collection.

- [ ] **Step 4: Run integration tests when a disposable Qdrant is available**

Run without a live server:

```bash
uv run --project . pytest -q tests/storage/test_qdrant_migration_integration.py
```

Run with an explicitly disposable server:

```bash
QDRANT_URL=http://127.0.0.1:6333 \
uv run --project . pytest -q tests/storage/test_qdrant_migration_integration.py -m integration
```

- [ ] **Step 5: Commit adapter/integration coverage**

```bash
git add tests/storage/test_qdrant_adapter.py tests/storage/test_qdrant_migration_integration.py
git commit -m "test: cover qdrant migration target binding and phases"
```

---

### Task 10: Add conditional CI and operator documentation

**Files:**
- Modify: `.github/workflows/pr.yml`
- Modify: `.github/workflows/_test_lite.yml` (only the minimal
  `test_paths_json` input extension; preserve cuVS defaults and callers)
- Modify: `scripts/maintenance/README.md`
- Modify: `openviking/storage/vectordb_adapters/README.md`
- Modify: `docs/en/guides/01-configuration.md`
- Modify: `docs/zh/guides/01-configuration.md`
- Test: `tests/maintenance/test_qdrant_migrate.py`

- [ ] **Step 1: Add the failing CI/configuration assertions**

Add `test_qdrant_ci_workflow_lists_required_suites` to the maintenance tests.
Parse both workflow files with PyYAML from
`Path(__file__).resolve().parents[2]`, then assert the actual
`check-deps.outputs.qdrant_changed` mapping, change-detector shell assignment
and matching paths, `qdrant-tests.needs`/`if`/`uses`/inputs, reusable-workflow
pytest command, and the empty `QDRANT_URL`/`QDRANT_API_KEY` environment. The
Qdrant CI input contains exactly:

```text
tests/maintenance/test_qdrant_migrate.py
tests/storage/test_qdrant_adapter.py
tests/storage/test_qdrant_migration_integration.py
tests/storage/test_qdrant_integration.py
tests/storage/test_collection_schemas.py
```

The parsed assertions also check that live integration remains environment-gated
by `QDRANT_URL`:

```python
REQUIRED_QDRANT_TESTS = (
    "tests/maintenance/test_qdrant_migrate.py",
    "tests/storage/test_qdrant_adapter.py",
    "tests/storage/test_qdrant_migration_integration.py",
    "tests/storage/test_qdrant_integration.py",
    "tests/storage/test_collection_schemas.py",
)
workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/pr.yml").read_text()
for path in REQUIRED_QDRANT_TESTS:
    assert path in workflow
assert "qdrant_changed" in workflow
assert "qdrant-tests" in workflow
assert "QDRANT_URL" in workflow
```

- [ ] **Step 2: Run the assertion and record its expected failure**

Run: `uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py -k 'ci or workflow'`

Expected: `.github/workflows/pr.yml` currently has no Qdrant change detector or conditional job.

- [ ] **Step 3: Implement the conditional Qdrant job**

Extend `check-deps` with a `qdrant_changed` output whose pattern covers the
REST client, collection, sparse helpers, adapter, config, migration script,
maintenance/storage tests, `pyproject.toml`/`uv.lock`, and both workflow files.
Add a `qdrant-tests` job conditioned on that output. Reuse `_test_lite.yml` only
through its minimal `test_paths_json` input extension, preserving the default
cuVS paths and all existing callers; run the five required paths with
`uv run pytest -q -o addopts=''`. The test step explicitly clears
`QDRANT_URL` and `QDRANT_API_KEY`, so fake-REST tests are mandatory and live
tests skip without production credentials.

- [ ] **Step 4: Update English and Chinese runbooks**

Document:

```text
preflight -> prepare -> backfill -> reconcile -> verify
barrier acquisition and in-flight write drain
cutover --confirm --lock-held --barrier-held --plan PATH --deployment-hooks PATH
rollback only before accepted target writes and while the barrier is held
retire --confirm after the retention window
```

Show `qdrant.data_collection_name`, `qdrant.metadata_collection_name`, `timeout_seconds`, `logical_collection`, and `migration_id`. State that the temporary SQLite manifest stores IDs/fingerprints only and requires disk space for one source scan. Remove language that says online phases require freezing the entire long copy; retain the full-window freeze description only for compatibility `apply`. Keep the explicit `--allow-acl-fail-open` warning and the post-release reverse-migration boundary.

- [ ] **Step 5: Run documentation/CI checks**

Run:

```bash
python - <<'PY'
from pathlib import Path
for path in (
    Path("scripts/maintenance/README.md"),
    Path("openviking/storage/vectordb_adapters/README.md"),
    Path("docs/en/guides/01-configuration.md"),
    Path("docs/zh/guides/01-configuration.md"),
):
    text = path.read_text()
    for required in ("data_collection_name", "migration_id", "allow-acl-fail-open"):
        assert required in text, (path, required)
PY
git diff --check
```

- [ ] **Step 6: Commit CI and documentation**

```bash
git add .github/workflows/pr.yml .github/workflows/_test_lite.yml \
  scripts/maintenance/README.md \
  openviking/storage/vectordb_adapters/README.md \
  docs/en/guides/01-configuration.md docs/zh/guides/01-configuration.md
git add -f docs/superpowers/specs/2026-09-08-qdrant-online-blue-green-migration-design.md \
  docs/superpowers/plans/2026-09-08-qdrant-online-blue-green-migration-plan.md
git commit -m "docs: document online qdrant migration operations"
```

---

### Task 11: Final verification and implementation handoff

**Files:**
- Modify only files listed in the previous tasks.

- [ ] **Step 1: Run focused unit suites**

```bash
uv run --project . pytest -q tests/maintenance/test_qdrant_migrate.py
uv run --project . pytest -q tests/storage/test_qdrant_adapter.py
uv run --project . pytest -q tests/storage/test_qdrant_migration_integration.py
```

- [ ] **Step 2: Run the required static checks**

```bash
uv run --project . ruff check \
  scripts/maintenance/qdrant_migrate.py \
  openviking/storage/vectordb/collection/qdrant_rest.py \
  openviking/storage/vectordb/collection/qdrant_collection.py \
  openviking/storage/vectordb_adapters/qdrant_adapter.py \
  openviking_cli/utils/config/vectordb_config.py \
  tests/maintenance/test_qdrant_migrate.py \
  tests/storage/test_qdrant_adapter.py \
  tests/storage/test_qdrant_migration_integration.py
uv run --project . ruff format --check \
  scripts/maintenance/qdrant_migrate.py \
  openviking/storage/vectordb/collection/qdrant_rest.py \
  openviking/storage/vectordb/collection/qdrant_collection.py \
  openviking/storage/vectordb_adapters/qdrant_adapter.py \
  openviking_cli/utils/config/vectordb_config.py
git diff --check
```

- [ ] **Step 3: Run the compact-plan and placeholder checks**

```bash
python - <<'PY'
from pathlib import Path
text = Path("docs/superpowers/plans/2026-09-08-qdrant-online-blue-green-migration-plan.md").read_text()
for token in ("T" + "BD", "FIX" + "ME", "TO" + "DO", "implementation " + "later", "fill in " + "details"):
    assert token not in text, token
PY
```

Confirm that a plan JSON contains no URL, API key, full ID map, vectors, or payloads, and that a migration marker contains the explicit migration ID, logical identity, target pair, layout, fingerprints, state, cursor, and setup gate.

- [ ] **Step 4: Re-read the approved spec against the implementation**

Check each spec requirement: source immutability, physical-name precedence, metadata/sparse pinning, cursor resume, exactly three reconciliation rounds, direct final comparison, ACL acknowledgement, state transitions, write drain, read-only smoke, barrier-held rollback, explicit retire, CI paths, and bilingual documentation. Any missing requirement is fixed before claiming completion.

- [ ] **Step 5: Report evidence and offer execution mode**

Do not claim live migration, deployment rollout, barrier release, or CI success without the corresponding receipt. Report focused test counts, static-check output, live-test skip/pass status, and the final worktree state. The plan is complete and saved at `docs/superpowers/plans/2026-09-08-qdrant-online-blue-green-migration-plan.md`.

## Spec coverage self-review

| Approved-spec requirement | Plan coverage |
| --- | --- |
| Explicit target physical pair, precedence, logical identity, marker fields | Tasks 1, 3, 4, 9, 10 |
| Seven migration states and `setup_complete` read gate | Tasks 3 and 7 |
| Explicit phase CLI, migration ID, timeout, lock acknowledgement | Tasks 2, 3, 8, 10 |
| Read-only preflight and compact reviewed plan | Task 3 |
| Safe prepare ordering, foreign-marker refusal, sparse dictionary setup | Task 4 |
| Opaque integer/string cursor and bounded resumable backfill | Task 5 |
| Temporary SQLite manifest, exact extras, pinned metadata/sparse fingerprints, three rounds | Task 6 |
| Direct payload/vector/ACL/index/metadata verification | Task 7 |
| Drain, serving-path removal, final barrier reconciliation, rollout smoke | Task 8 |
| Barrier-held rollback boundary and retained target | Task 8 |
| Explicit retire and reviewed pre-marker orphan cleanup | Tasks 4 and 8 |
| Legacy ID/payload/owner/ACL/sparse transform contract | Tasks 5 and 7 |
| Failure recovery, source immutability, no automatic source deletion | Tasks 4–8 and Global Constraints |
| Client-side weighted rank fusion and no new dependency | Global Constraints and Task 10 |
| Fake REST, adapter, integration, CI, bilingual runbook coverage | Tasks 2, 9, 10, 11 |

The placeholder scan is clean, all implementation signatures used by later
tasks are defined above, and no task requires a new third-party package.

## Execution preflight corrections (2026-09-08)

The approved spec is authoritative over the pseudocode above. Execution keeps
real red/green tests, no no-op phase stubs, keyword-only compatibility defaults,
and the following safety clarifications:

- Read-only preflight cannot demonstrate a write by making one. Check the
  official server capability/version using read-only metadata, reject unknown
  or unsupported endpoints, and still require every target point write to
  complete with strong ordering; never retry without that option.
- Disk-backed uniqueness and fingerprint aggregation apply to preflight as
  well as reconciliation; hiding an in-memory map from JSON is insufficient.
- Cursor completion must distinguish a completed final page from the initial
  null cursor. Resume never skips a page whose target write is unconfirmed.
- Online source counts/fingerprints roll forward; offline `apply` retains its
  frozen snapshot guard. Pinned metadata and sparse-map fingerprints do not roll.
- Missing original IDs fail in the shared adapter decoder (Task 1), covering
  every fetch/query/update path rather than only adding a Task 9 test.
- Runtime migration markers require logical/physical binding and consistent
  vector dimension fields; sparse index datatype is also pinned; non-migration
  direct-name markers remain compatible.
- Runtime receipts keep raw `source_fingerprint` /
  `verified_source_fingerprint` separate from canonical
  `transformed_source_fingerprint` / `target_content_fingerprint`; final
  verification requires the latter pair to match.
- Deployment hook checks use one concrete runner with exactly ten argv keys:
  drain/remove legacy, rollout/readiness/read-only smoke, remove current,
  restore/verify legacy, accepted-write inspection, and target-not-served
  assertion. The operator owns barrier acquisition and release; no release
  hook exists. Explicit interrupted-cutover resume checks accepted writes
  before source-authoritative repair and before active publication. No
  production hook is invoked by implementation or local tests.
- Existing baseline on this worktree: 215 passed, 3 live tests skipped, five
  existing Python/Pydantic warnings. No live server was used.

The per-task ledger under this plan's `.superpowers/sdd/` directory contains
the detailed shared-file/dependency scan and all execution rulings.

### Task 12: Close the original shared-layer private adapter comment

This corrects an omission from the original whole-PR-comment scope, not a new
migration mode. It executes after Task 11 preparation and before final reviews.

**Files:**
- Modify: `openviking/storage/vectordb_adapters/qdrant_adapter.py`
- Modify: `openviking/storage/viking_vector_index_backend.py`
- Test: `tests/storage/test_qdrant_adapter.py`

**Interfaces:**
- Add concrete synchronous
  `QdrantCollectionAdapter.update_collection_schema(self, fields: list[dict[str, Any]], scalar_index: list[str], index_name: str) -> None`.
- `_AsyncVectorAdapter.update_collection_schema` retains its signature and
  `asyncio.to_thread` boundary. Only its Qdrant branch delegates publicly.
- Preserve existing missing-index defaults, schema/index scalar union, field
  metadata, custom indexes, retries, and all non-Qdrant behavior.
- Do not add a base-class interface, configuration getters, or duplicate merge
  logic. Existing private hooks remain internal to their owning adapter.

- [ ] **Step 1: Add and run the public-boundary regression**

```python
@pytest.mark.asyncio
async def test_qdrant_schema_update_uses_public_adapter_method():
    calls = []

    class PublicAdapter:
        mode = "qdrant"

        def update_collection_schema(self, fields, scalar_index, index_name):
            calls.append((fields, scalar_index, index_name))

    fields = [{"FieldName": "acl_enabled", "FieldType": "bool"}]
    await _AsyncVectorAdapter(PublicAdapter()).update_collection_schema(
        fields, ["acl_enabled"], "custom-index"
    )
    assert calls == [(fields, ["acl_enabled"], "custom-index")]
```

Run with live endpoint variables unset:

```bash
rtk proxy env -u QDRANT_URL -u QDRANT_API_KEY uv run --project . pytest -q -o addopts= tests/storage/test_qdrant_adapter.py -k qdrant_schema_update_uses_public_adapter_method
```

Expected RED: the old facade tries to call `get_collection` on an adapter
that deliberately exposes only the public operation.

- [ ] **Step 2: Move the existing Qdrant branch into its adapter**

Move the current Qdrant branch body intact into the public method, changing
`self._adapter` references to `self`; its collection comes from
`self.get_collection()`. In the existing facade worker closure, replace that
branch with:

```python
if self._adapter.mode == "qdrant":
    self._adapter.update_collection_schema(fields, scalar_index, index_name)
else:
    collection = self._adapter.get_collection()
    # Keep the existing non-Qdrant branch body unchanged.
```

Remove the shared pre-branch `get_collection()` call so the public method owns
Qdrant collection access. Keep all merge/default-index logic in the adapter,
not in both locations.

- [ ] **Step 3: Preserve behavioral regression coverage**

Adapt the existing schema-update tests to use a real
`QdrantCollectionAdapter` with a fake collection, rather than fake adapters
that reimplement its private defaults. Retain assertions for field metadata,
schema and per-index scalar unions, custom index metadata, configured distance
and sparse weight, and retry after a failed index operation. Cover the
non-Qdrant local/cuVS branch without requiring the new Qdrant-only method.
No live endpoint is needed for these tests.

- [ ] **Step 4: Run focused and shared verification**

```bash
rtk proxy env -u QDRANT_URL -u QDRANT_API_KEY uv run --project . pytest -q -o addopts= tests/storage/test_qdrant_adapter.py tests/storage/test_collection_schemas.py
rtk proxy uv run --project . ruff check openviking/storage/viking_vector_index_backend.py openviking/storage/vectordb_adapters/qdrant_adapter.py tests/storage/test_qdrant_adapter.py
rtk git diff --check
```

Then run the required five-path suite from Task 11, inspect every caller, and
confirm no `_adapter._` accesses remain in the facade. Record real RED/GREEN
output; do not substitute static string checks for behavioral assertions.

- [ ] **Step 5: Commit and independently review**

Commit only the three implementation/test files and the two scope-corrected
spec/plan files. The same consolidated PR receives this commit. No production
operation, upstream PR, or change to the server-side fusion implementation is
part of this task.

## Approved PR #4458 sparse-ownership amendment (2026-09-09)

The user approved raising the Qdrant floor to 1.16 and handling existing
term-keyed dictionaries. This supersedes the earlier term-ID write protocol;
the public data-vector format and migration marker version do not change.
Spec: `docs/superpowers/specs/2026-08-20-qdrant-integration-design.md`, Data model.

- [ ] Add failing shared primitive tests in `tests/storage/test_qdrant_sparse.py`
  for the real `69235` / `95303` collision, both canonical point-ID forms,
  malformed bindings, missing readback, and the 1.16 version boundary.
- [ ] Implement shared `sparse_owner_point_id(index)`, `parse_sparse_point(point)`,
  `validate_qdrant_version(version)`, and cached `ensure_supported_version()`.
- [ ] Runtime: dual-read immutable legacy rows and owner rows; new registrations
  use a native `update_filter` of `{"must_not": [{"has_id": owner_ids}]}` with
  `wait=true` and `ordering=strong`, then validate the owner readback. Do not use
  `update_mode=insert_only`: Qdrant 1.16 silently ignores it. Add interleaving/retry
  tests. Existing-term reads must not write metadata.
- [ ] Migration: emit owner IDs, require owner completeness, preserve legacy
  bindings, reject foreign provenance, and retain existing mutation/state gates.
- [ ] Add optional `qdrant_sparse_upgrade.py` and behavioral tests: read-only
  preflight; conversion gated by confirm, lock, barrier, and stopped old writers;
  complete validation before writes, insert-only seeding, final validation,
  resumable partial completion, and no deletion or data-vector mutation.
- [ ] Wire new test/script paths into the existing Qdrant CI lane and update
  configuration/runbook docs. Do not modify server-side fusion.
- [ ] Run affected pytest suites with `.venv/bin/python` (never rebuild this
  environment), then real integration tests on disposable local Qdrant 1.16.0
  and 1.19.0. Run Ruff, `git diff --check`, correctness and ponytail reviews;
  fix actionable findings and repeat. No commit, push, merge, or production
  migration without a separate request.
