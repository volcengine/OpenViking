# OpenViking Qdrant Online Blue-Green Migration Design

## Status

Proposed follow-up design for `volcengine/OpenViking#4458`.

The scope is deliberately one-way:

```text
pre-#3872 legacy source -> current-format target
```

The long copy runs while the legacy deployment continues to serve the source.
Only the final reconciliation and application rollout require a short write
barrier.

## Goal

Migrate a legacy Qdrant data collection and its pre-`#3872` metadata sidecar to
a new physical data/metadata pair that the current-format adapter can read and
write. The source remains unchanged and retained for audit and a
barrier-held rollback.

The migration must provide:

- no mutation of the legacy data or metadata collections;
- bounded-batch, resumable copying;
- reconciliation of source upserts, deletes, and payload changes that occur
  during the long copy, plus fail-closed detection of sparse-map drift;
- exact final verification of IDs, payloads, vectors, indexes, metadata, and
  ACL state;
- a current-format application rollout that is validated before the write
  barrier is released;
- explicit recovery for interrupted phases;
- no new third-party dependency.

The upstream `volcengine/OpenViking/main` branch does not contain the
current-format Qdrant adapter or migration utility. At design time,
`upstream/main` was `a843ab6bf220b2b3bc82321576d623d1c55c6598` and did not
contain:

```text
openviking/storage/vectordb/collection/qdrant_collection.py
openviking/storage/vectordb/collection/qdrant_rest.py
openviking/storage/vectordb_adapters/qdrant_adapter.py
scripts/maintenance/qdrant_migrate.py
```

Those files exist only in the fork/PR work, so current-format-source migration
is not part of this change.

## Non-goals

- Migration from a current-format source.
- Application dual-write or a second current-format generation.
- Qdrant collection aliases. They add an adapter/configuration surface without
  reducing the legacy cutover window; the target physical pair is activated by
  one barrier-held application rollout.
- Transparent rollback after the current-format target has accepted data
  writes. That requires a separate reverse migration.
- Automatic deletion of the legacy source.
- Automatic adoption of an unmarked, ambiguous, or shared collection.
- A generic migration framework for other vector backends.
- A server-side RRF redesign. The existing client-side weighted rank contract
  is retained; changing it is a separate benchmark/design.
- A distributed scheduler. One controller owns a migration ID at a time; the
  operator supplies the external per-source lock that prevents distinct target
  generations from being copied concurrently.

## Current-state constraints

The current Qdrant implementation in the fork:

- binds `QdrantCollection` to a collection reference;
- derives the physical data name from `project_name` and `name` unless an
  explicit physical name is configured;
- derives the metadata sidecar from the data name unless overridden;
- stores the current marker and sparse dictionary in the metadata sidecar;
- preserves migration-owned marker fields when normal adapter schema writes
  rewrite the marker;
- treats `setup_complete=false` as an adapter read gate;
- uses the standard-library `QdrantRestClient`, whose default request timeout
  is 10 seconds.

The legacy source has a different marker format and physical-ID encoding. The
current adapter must not load it. The migration therefore reads the source
through raw Qdrant REST and writes only the new current-format target.

### Public adapter boundary

Original PR comment `5529502476`, item 5, is part of this consolidated change.
The shared `_AsyncVectorAdapter` must not call Qdrant's protected
`_build_default_index_meta` hook or read `_distance_metric` / `_sparse_weight`.
Qdrant schema/index updates belong in a concrete public
`QdrantCollectionAdapter.update_collection_schema(fields, scalar_index, index_name)`.
The async facade delegates to that method in its existing worker thread.
Preserve missing-index creation with configured Qdrant defaults, additive schema
and scalar-index behavior, custom field/index metadata, retry behavior, and all
non-Qdrant paths. Do not expose private configuration getters or add a generic
adapter framework to implement this boundary.

## Terminology

- **Logical collection**: the OpenViking identity, such as `default/context`.
- **Legacy source**: the pre-`#3872` physical data collection plus its legacy
  metadata sidecar.
- **Target pair**: the immutable-name current-format data collection and its
  current-format metadata sidecar.
- **Write barrier**: an operator-owned pause that disables every legacy data
  and metadata write, including deletes and partial updates. `cutover` requires
  an explicit `--barrier-held` acknowledgement plus a final source fingerprint
  check; the controller cannot infer that an application has stopped writing.
- **Migration manifest**: a temporary standard-library SQLite database keyed by
  target point ID. It stores only IDs and fingerprints, not full vectors.
- **Target marker**: the current-format marker written only to the target
  metadata sidecar.

Source and target counts always mean user records in the data collections.
Marker and sparse-dictionary points in the metadata sidecar are counted and
verified separately.

## Architecture

### 1. Target physical pair and configuration

The target data and metadata names must be pairwise distinct from each other,
the legacy data collection, and the legacy metadata sidecar. A target metadata
sidecar is never shared by two logical collections.

The current-format rollout adds explicit physical-name overrides:

```yaml
qdrant:
  url: https://qdrant.example
  timeout_seconds: 60
  data_collection_name: default__context__gen_20260908_abc
  metadata_collection_name: default__context__gen_20260908_abc__openviking_meta
```

`data_collection_name` and `metadata_collection_name` are physical Qdrant
names, not aliases. Existing configurations that omit them keep the current
`project_name`/`name` derivation. If only `data_collection_name` is set, the
metadata name defaults to `{data_collection_name}__openviking_meta`; if an
explicit metadata name is set, it must be pairwise distinct. The adapter must
pass both references to `QdrantCollection` and must not silently derive a
different target name. The explicit Qdrant fields take precedence over
`custom_params`; legacy configurations keep their current precedence.
`data_collection_name` is an optional `QdrantConfig` field with no non-null
model default, so omitted and explicitly supplied values remain distinguishable.

The target marker keeps the adapter's standard `collection_name` equal to the
target physical data name and adds:

```json
{
  "logical_collection": "default/context",
  "collection_name": "default__context__gen_20260908_abc",
  "metadata_collection_name": "default__context__gen_20260908_abc__openviking_meta",
  "source_collection": "legacy__context",
  "source_metadata_collection": "__openviking_meta",
  "dense_vector_name": "vector",
  "sparse_vector_name": "sparse_vector",
  "vector_dimension": 1536,
  "distance": "Cosine",
  "dense_datatype": "float32",
  "sparse_enabled": true,
  "sparse_datatype": "float16",
  "sparse_weight": 0.5,
  "source_fingerprint": "...",
  "transformed_source_fingerprint": "...",
  "target_content_fingerprint": "...",
  "source_count": 0,
  "target_count": 0,
  "metadata_fingerprint": "...",
  "sparse_map_fingerprint": "...",
  "migration_id": "20260908_abc",
  "migrator_version": "...",
  "migration_state": "building",
  "last_source_cursor": null,
  "backfill_complete": false,
  "setup_complete": false
}
```

The adapter validates the physical names and logical identity from this marker.
It does not accept a legacy marker as a current-format marker.
Explicit physical-name overrides require a matching logical identity. Ordinary
current-format markers without a logical identity remain readable through the
default-derived physical names for backward compatibility. Migration markers
require a non-empty migration ID and a valid state; any duplicate target-name
fields must be complete and match the canonical physical pair.

`source_fingerprint` and the source count in the reviewed plan are the most
recent raw-source observations, not immutable online-copy inputs. Each
successful reconciliation round replaces them with the fingerprint and count
of the source scan that round transformed. `verified_source_fingerprint` is the
raw-source receipt captured by exact verification. The independent
`transformed_source_fingerprint` and `target_content_fingerprint` are the
canonical content receipts and must match after a successful audit. The
metadata and sparse-map fingerprints are pinned inputs: the controller
recomputes them before and after every round and fails closed if either changes.

The legacy metadata sidecar may be the pre-`#3872` global sidecar shared by
several logical collections. The controller selects and fingerprints only the
documents for the requested source collection; it never writes that shared
sidecar.

### 2. Migration state

Allowed target states are:

```text
building       target is being created or copied
ready          target passed a point-in-time verification
cutting_over   barrier-held final verification and rollout are in progress
active         current-format rollout passed its read-only smoke gate
retained       target is retained for audit or later cleanup
rolled_back    rollout was reverted while the barrier was held
failed         operator-visible failure requiring resume or cleanup
```

`building`, `failed`, and `rolled_back` have `setup_complete=false`.
`ready`, `cutting_over`, `active`, and `retained` have
`setup_complete=true`. A persisted target marker is never adopted by another
migration ID. A `rolled_back` target remains available for raw audit only; the
current adapter must refuse to serve it.
`ready` is not a publication signal: no application may route to it until
`cutover` holds the barrier.

Successful phases have these transitions:

```text
prepare                         -> building
verify (outside cutover)        -> ready
cutover start                   -> cutting_over
final verify + rollout smoke    -> active
barrier-held rollback           -> rolled_back
retention completion             -> retained
```

Mutating phases (`backfill` and `reconcile`) are allowed only before
`active`/`retained`/`rolled_back`; they may temporarily keep the marker in
`building` and must never make an already active target incomplete. `verify` on
an active or retained target is read-only and does not change its state.

### 3. Migration controller

`scripts/maintenance/qdrant_migrate.py` becomes an explicit phase controller:

```text
preflight
prepare
backfill
reconcile
verify
cutover
rollback
retire
```

Every phase receives the logical collection identity explicitly (for example,
`--logical-collection default/context`) in addition to the physical source and
target names. The controller validates that identity against the rollout's
`project_name`/`name` configuration and stores it in the target marker.
Every phase also receives an explicit `--migration-id`; the controller never
silently generates a new ID during resume.

The existing `apply` command may remain as an offline compatibility wrapper for
`prepare + backfill + verify`. It retains its existing full-window source-write
freeze semantics, must not perform application cutover, and must not pretend
that the source is continuously dual-written. It refuses an `active`, `retained`,
or `rolled_back` target rather than mutating data after cutover. The new online
phases are explicit.

All commands accept `--timeout-seconds`, pass it to every
`QdrantRestClient` request, and record it in the plan. `cutover` and automatic
`rollback` additionally require `--barrier-held`; this is an operator
acknowledgement, not a claim that the controller can detect stopped application
writers. The operator acquires and releases the barrier; the controller has no
release method.
Every target point upsert/delete and marker write uses Qdrant's
acknowledged/waited write mode (`wait=true` on REST) with `ordering=strong`; if
the endpoint cannot honor strong ordering, preflight fails instead of silently
downgrading. Collection/index readiness polling and the final verify do not
treat an accepted-but-not-visible write as complete.

#### `preflight`

Read-only checks record:

- legacy source and target physical names;
- logical collection identity used by the current-format rollout;
- source metadata sidecar (default `__openviking_meta`) and target sidecar;
- collection layouts, scalar indexes, and vector names;
- selected legacy metadata and sparse-map fingerprints;
- exact source and target counts;
- ACL-incomplete count;
- migration ID, migrator version, batch size, and the exact
  `--timeout-seconds` passed to every REST request;
- whether the target is absent or already owned by this migration ID.

The plan JSON contains counts, fingerprints, names, and non-secret configuration
only. It never contains the Qdrant URL or API key, a full point map, a full
target ID set, vectors, or payloads.
An existing target or metadata collection is plan-eligible only when its marker
already belongs to the same migration ID; an unmarked or foreign collection
fails preflight rather than being adopted.

#### `prepare`

Creates the target data and metadata collections, required indexes, and target
sparse dictionary. It writes the incomplete marker only after both collections
are owned by this migration ID. A pre-existing unmarked collection, a
different marker, a target metadata sidecar shared by another logical
collection, or a source/target name collision fails closed.
Collection-creation races are handled by re-reading a 409 response and
accepting only the same migration-owned marker; the controller never overwrites
a foreign collection.

#### `backfill`

Scrolls the legacy source in bounded batches, transforms each page, and
upserts the target. After each successful page write it persists the opaque
Qdrant `next_page_offset` in `last_source_cursor` and sets
`backfill_complete` only when that cursor is `null`. The cursor may be an
integer or string; a malformed or repeated cursor fails closed. A failed or
unconfirmed page write never advances the cursor.

Only one source page, one target write batch, and bounded transform state are
held in memory. A failed batch is retried by rerunning that batch; the source
is never written.

Backfill's `source_count`, ACL/distinct-term counts, and fingerprints remain
the most recent full-source scan observations. Its durable progress fields are
the cursor, completion bit, and target count; per-page progress is not
substituted for canonical whole-source observations.

#### `reconcile`

Each reconciliation round:

1. creates a fresh temporary SQLite migration manifest;
2. scans the source from the beginning, transforms records, upserts changed
   target records in bounded batches, and stores
   `(target_id, transformed_fingerprint)` in the manifest;
3. streams the target and compares it with the manifest, deleting target
   records absent from the source and rechecking records whose fingerprints
   differ;
4. verifies sparse dictionary membership and writes missing terms in chunks;
5. recomputes the pinned legacy metadata and sparse-map fingerprints before and
   after the round, failing if either changed;
6. records the round's raw-source fingerprint/count and independent canonical
   transformed-source/target-content fingerprints and counts in the target
   marker.

When invoked by `cutover`, reconciliation preserves `migration_state=cutting_over`
and `setup_complete=true`; it never exposes an intermediate incomplete marker
during the barrier-held final pass.

The manifest is rebuilt after an interrupted round and is deleted after a
successful verification. It is not included in plan JSON or target metadata.
This makes exact extra detection disk-backed rather than an unbounded Python
set. If the source keeps changing and a bounded number of rounds does not
converge, or if the legacy metadata/sparse-map fingerprint changes during a
round, the controller fails closed and reports non-convergence or source
metadata drift. The implementation uses a fixed maximum of three rounds; it
does not add another operator tuning knob.

Fingerprints are only bounded candidate filters. Final point verification
compares canonical IDs, payloads, and dense/sparse vector values directly; a
hash match alone never proves equality. The manifest remains an invocation-
local SQLite file keyed by IDs/fingerprints, not a second public points store.

#### `verify`

Verification is streamed and exact:

- source and target counts;
- raw source and verified-source fingerprints;
- independently computed `transformed_source_fingerprint` and
  `target_content_fingerprint` receipts, which must be equal;
- target ID coverage and absence of extras;
- logical-ID, physical-ID, and payload identity;
- dense vector values, sparse vectors, vector names, dimensions, and distance;
- collection/index readiness, scalar indexes, and metadata schema;
- sparse dictionary completeness and collision-free term IDs;
- ACL completeness and the target marker;
- migration ID and migrator version.

Verification before the barrier is point-in-time only. A successful
non-cutover verify changes `building` to `ready`; it never changes an active or
retained target. `cutover` must run a fresh `reconcile` and `verify` while the
barrier is held. The final verify records success without changing
`cutting_over` back to `ready`.

#### `cutover`

The controller:

1. requires `migration_state=ready`, `--barrier-held`, and a held write
   barrier;
2. validates all ten required deployment-hook argv arrays before changing
   state, then changes the target marker to `cutting_over`;
3. drains in-flight legacy writes and waits for their Qdrant requests to
   complete before taking the final source snapshot;
4. removes the legacy deployment from the serving path so old and
   current-format replicas cannot serve mixed generations;
5. runs final `reconcile` and explicit-barrier `verify`;
6. rolls out the current-format application with both explicit target physical
   names and the configured Qdrant request timeout;
7. waits for every new replica to pass readiness and a read-only marker,
   count, and representative dense/sparse query smoke test;
8. checks that no current-format data write was accepted unexpectedly and
   changes the target marker to `active`.

The read-only smoke gate must not upsert, delete, or partially update data.
Source and target remain available for audit; the source is not rewritten.
The operator owns barrier acquisition and release; there is no release hook.
The operator releases the barrier only after the active receipt and rollout
checks are accepted.

The concrete deployment-hook JSON has exactly these required keys, each a
non-empty argv list of validated strings:

```text
drain_legacy_writes
remove_legacy_from_serving_path
rollout_current
wait_current_ready
smoke_current_read_only
remove_current_from_serving_path
restore_legacy
verify_legacy_read_path
current_target_has_accepted_writes
assert_target_not_served
```

Only `current_target_has_accepted_writes` consumes captured stdout, and it
accepts only exact lowercase `true` or `false`. All other hooks require exit
status zero. Hooks run with `shell=False`, a finite timeout, and non-secret
`OV_*` bindings; inherited operator environment is never logged.

If any step fails while the barrier is held, the controller leaves the target
in `cutting_over` or `failed` and requires `rollback` or an explicit resume.
It never releases the barrier automatically. The barrier procedure must also
drain in-flight legacy requests; `--barrier-held` is an acknowledgement, not
proof that writers have stopped.

#### `rollback`

Automatic rollback is allowed only while the write barrier is held. It accepts
only a `cutting_over` or barrier-held `active` target and requires
`--confirm`, `--lock-held`, `--barrier-held`,
`--no-current-format-writes-accepted`, and the strict accepted-write hook.

1. removes the current-format serving path and asserts the target is not served;
2. restores the legacy deployment's direct source configuration and serving
   path;
3. verifies the legacy marker and source read path;
4. marks the target `rolled_back` with `setup_complete=false` and retains it.

After the barrier is released, the target may contain writes that do not exist
in the legacy source. The controller therefore refuses automatic rollback and
requires a separately designed reverse migration.

#### `retire`

Deletion is explicit and requires the migration ID, exact target pair,
external lock, `--confirm`, a reviewed plan, and the concrete deployment-hook
runner proving that the target is not served. Only `active -> retained`,
owned retained-pair retries, and owned retained metadata-only retries are
ordinary retire states; `ready`, `building`, `failed`, `cutting_over`, and
`rolled_back` are rejected. After the retention window, it first writes
`migration_state=retained`, re-reads that marker, deletes target data, verifies
absence, and only then deletes target metadata. If a process died before its
first marker write, the reviewed plan's `target_absent=true` plus the external
per-source lock permits a separate orphan cleanup only after both names are
rechecked as unmarked and empty. An unmarked collection that was not absent in
the reviewed plan is never deleted by the tool. Normal migration and rollback
never delete the legacy source.

### 4. Legacy-to-current transform contract

The transform must:

- require `_openviking_original_id`; it is the authoritative logical ID;
- validate the raw legacy ID type before normalizing it to the current
  string-valued target payload;
- preserve that normalized logical ID in the target payload;
- validate the legacy physical point ID against the pre-`#3872` encoding:
  uint64 integers pass through, canonical UUID strings remain UUIDs, and other
  values use the legacy uuid5 namespace;
- derive the current target point ID with the current `to_qdrant_point_id`
  rule;
- fail closed on duplicate logical IDs, target-ID collisions, missing URI,
  invalid/non-finite vector values, or payload loss;
- preserve documented URI/owner normalization and all ACL fields without
  silently backfilling ACL grants;
- require an authoritative old sparse-index-to-term map and fail closed on
  missing terms or stable-index collisions.

### 5. Failure and recovery

- Source reads and target writes are separate; a target failure never mutates
  the source.
- Backfill resumes from the persisted cursor. Reconciliation restarts its
  temporary manifest from the beginning.
- A collection created before its first marker is persisted is cleaned up only
  when it is still migration-owned; a foreign marker is never deleted.
- A process-death orphan is recoverable only through the reviewed
  `target_absent=true` plan, the external per-source lock, an exact-name
  recheck, and `--confirm`; it is never auto-adopted.
- Cleanup catches `BaseException` only for pre-marker orphan cleanup, then
  re-raises `KeyboardInterrupt` or `SystemExit`.
- A completed target is never changed back to `setup_complete=false` by a
  same-fingerprint read-only rerun; an active, retained, or rolled-back target
  rejects mutating reruns.
- Pinned metadata/sparse-map fingerprints, names, counts, ACL, and vector
  mismatches fail before cutover; final verification requires the independent
  `transformed_source_fingerprint` and `target_content_fingerprint` to match.
- A process death during `cutting_over` leaves the barrier held. Recovery
  inspects the marker and deployment state, then resumes or rolls back; it
  never assumes that a partial application rollout succeeded.
- Same-target controllers with different migration IDs fail closed. Distinct
  target names cannot be detected without a distributed scheduler, so the
  operator must hold one external per-source migration lock and must not run
  two controllers for the same logical collection.

## Configuration and compatibility

Direct-name configuration remains the default. The only new current-format
configuration needed for cutover is the explicit target physical data and
metadata names plus the migration REST timeout. No alias or secondary
collection configuration is added.

For physical names, resolution is exact: an explicitly present
`qdrant.data_collection_name` or `qdrant.metadata_collection_name` wins;
otherwise the corresponding `custom_params` value is used; otherwise the
existing derived name is used. For every pre-existing option, preserve the
current resolver and fallback order exactly; this migration must not silently
change legacy `qdrant`/top-level/`custom_params` behavior. Only the new
physical-name fields need omitted-versus-explicit detection.

The legacy adapter is never pointed at the target. The current adapter is
never pointed at the legacy marker. The adapter checks the target marker's
physical names, logical collection, vector layout, and sparse mode/weight
against runtime configuration. The migration controller separately checks the
migration ID and migrator version against the reviewed plan and its code-owned
contract before cutover; these are not additional runtime configuration knobs.
A successful adapter attachment is not a substitute for controller verification.
`migrator_version` is a stable code constant combined with the transform-schema
version; changing either requires a new migration ID and target.

The migration exposes the existing `--allow-acl-fail-open` acknowledgement.
Without it, incomplete or malformed ACL records block `verify` and `cutover`.
With it, the command prints a warning and records the incomplete count in the
target marker; it does not claim that those records are protected. Enabling ACL
after this migration therefore does not retroactively protect records that were
accepted through this gate.

Hybrid search remains the existing client-side weighted rank fusion. Qdrant
supports server-side `prefetch` + `fusion: RRF` from v1.10, and
[weighted RRF from v1.17](https://qdrant.tech/documentation/search/hybrid-queries/#weighted-rrf).
Weighted RRF is therefore not available across the entire supported `>=1.16`
range. The current adapter performs sidecar term lookup before two
data-collection queries; that encoding step does not prevent server-side
prefetch. Switching fusion implementations still requires score and pagination
contract validation, so this change preserves existing behavior and leaves
server-side fusion to a separate benchmark/design.

## Testing and documentation

### REST and controller tests

- opaque integer/string cursor persistence and repeated-cursor failure;
- timeout propagation through `QdrantRestClient`;
- waited writes and strong ordering are sent for every target mutation;
- collection/index readiness is awaited before verify and smoke;
- explicit Qdrant physical-name precedence over `custom_params` and derived
  names;
- target creation ordering and foreign-marker refusal;
- pre-marker orphan cleanup requires a reviewed absent-target plan;
- bounded batch upsert/delete and temporary-manifest reconciliation;
- cutover reconciliation preserves `cutting_over` and the read gate;
- resume after a failed batch or interrupted reconciliation;
- completed-target rerun preserves `setup_complete=true`;
- active/retained/rolled-back targets reject mutating reruns;
- migrator-version, source-fingerprint, vector, metadata, ACL, and sparse-map
  mismatch failures;
- exact legacy ID mapping, duplicate/collision rejection, and payload
  fail-closed decoding;
- cutover failure keeps the barrier held;
- source-write drain and final source snapshot occur before final verify;
- read-only rollout smoke gate and barrier-held rollback;
- rollback refusal after target writes;
- retire refuses a serving target and marks a non-serving target retained
  before deletion.

### Adapter tests

- existing direct-name mode remains unchanged;
- explicit data/metadata physical-name overrides route every operation to the
  configured pair;
- target marker physical/logical fields round-trip;
- vector layout and sparse configuration must match the target marker;
- migration fingerprints and state survive normal adapter marker rewrites;
- required logical collection identity must match rollout configuration;
- the adapter refuses legacy markers and mismatched target names;
- missing `_openviking_original_id` never fabricates a record ID.

### CI and documentation

The conditional Qdrant CI job runs:

```text
tests/maintenance/test_qdrant_migrate.py
tests/storage/test_qdrant_adapter.py
tests/storage/test_qdrant_sparse.py
tests/maintenance/test_qdrant_sparse_upgrade.py
tests/storage/test_qdrant_migration_integration.py
tests/storage/test_qdrant_integration.py
tests/storage/test_collection_schemas.py
```

Fake-REST tests are mandatory. Live Qdrant tests run only when `QDRANT_URL`
is configured and never migrate Monster or delete a user collection.

The implementation updates `scripts/maintenance/README.md`,
`openviking/storage/vectordb_adapters/README.md`, and the bilingual
configuration guides so they describe the phase commands, the short final
barrier, the exact ACL flag, the timeout option, the physical-name override,
the post-release rollback boundary, and the disk-space budget for the
temporary manifest. Existing text that says to freeze the whole long copy
window for the online phases must be removed; the compatibility `apply` path
must retain its documented full-window freeze semantics.

## Operational runbook

1. Confirm logical collection identity, source data name, legacy metadata name
   (default `__openviking_meta`), target pair, sparse map, timeout, and
   migration ID;
   acquire the external per-source migration lock.
2. Run read-only `preflight` and review the compact plan.
3. Run `prepare`, `backfill`, `reconcile`, and point-in-time `verify` while
   the legacy deployment serves the source.
4. Acquire the write barrier. Stop legacy upserts, deletes, partial updates,
   and metadata writes; drain in-flight requests and keep the barrier held.
5. Run final `reconcile --barrier-held` and
   `verify --final --barrier-held --confirm --lock-held`, using
   `--allow-acl-fail-open` only if the recorded ACL risk is explicitly
   accepted.
6. Run `cutover --confirm --lock-held --barrier-held --deployment-hooks`; keep
   the barrier held until every current-format replica passes read-only smoke.
   The representative sparse query uses already migrated dictionary terms (or
   pre-encoded indices), so the smoke gate cannot create metadata points as a
   side effect.
7. The operator releases the barrier after the controller returns `active`, and
   performs the first normal write/read acceptance check.
8. Retain the legacy source and target marker for the agreed audit window.
9. Run `retire` only after the retention window and explicit confirmation.

## Comment coverage

This design closes the previously reported findings as follows:

- M1: bounded batches plus a disk-backed manifest; no full ID map in plan JSON;
- M2: completed targets remain readable during same-fingerprint reruns;
- M3: marker-bound migrator version;
- M4: interrupt-safe pre-marker cleanup;
- L5: explicit REST timeout configuration and CLI propagation;
- L6: compact plan JSON;
- L10: one-controller ownership and concurrent-run refusal;
- legacy compatibility: exact pre-`#3872` ID validation and transformation;
- ACL: explicit `--allow-acl-fail-open` gate and recorded warning;
- RRF: weighted client-side ranking is retained intentionally;
- payload fallback: missing logical IDs fail closed;
- metadata sharing: target pair names and sidecars are unique;
- private adapter boundary: Qdrant schema updates stay behind the concrete
  adapter's public method;
- CI/docs: maintenance/storage tests and operator documentation are in scope.

The pre-existing random-vector behavior is intentionally not changed here; it
belongs to a separate adapter-wide contract change.

## References

- Qdrant migration guidance and persisted scroll offsets:
  <https://qdrant.tech/documentation/tutorials-operations/embedding-model-migration/>
- Qdrant collection operations and write ordering:
  <https://qdrant.tech/documentation/manage-data/collections>
  <https://qdrant.tech/documentation/scaling/consistency-guarantees/>
