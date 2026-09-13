# Qdrant maintenance

All Qdrant server nodes must be >=1.16 before running these tools or enabling the
new sparse writer. Version checks fail closed; there is no ordinary-upsert fallback.

## Migrate a pre-`#3872` collection

`qdrant_migrate.py` copies a legacy Qdrant data collection and its legacy
metadata sidecar into a new current-format target pair. The legacy source is
read-only and remains available for audit and the barrier-held rollback
boundary. The target data and metadata names are physical names and must be
new, pairwise distinct names unless the same `migration_id` is being resumed.

The controller is deliberately one-way:

```text
preflight -> prepare -> backfill -> reconcile -> verify
```

The long online copy does **not** freeze the entire window. It requires an
external per-source lock and leaves legacy serving available while `backfill`
and rolling `reconcile` run. `apply` is the compatibility **offline** wrapper;
it retains the older full-window source and legacy-metadata freeze semantics
for `prepare + backfill + verify`.

Run from the repository root:

```bash
./.venv/bin/python scripts/maintenance/qdrant_migrate.py \
  --url https://qdrant.example \
  --source-collection legacy__context \
  --target-collection current__context \
  --source-metadata-collection __openviking_meta \
  --logical-collection default/context \
  --migration-id migration-2026-09-08 \
  --timeout-seconds 30 \
  --sparse-map /path/to/legacy-sparse-map.json \
  preflight
```

Every command requires the explicit `--logical-collection`, `--migration-id`,
and `--timeout-seconds` arguments. The reviewed plan records the target pair,
source metadata name, logical identity, `batch_size`, timeout, vector layout,
metadata/sparse fingerprints, ACL-incomplete count, and migration identity.
It never contains the Qdrant URL, API key, vectors, payloads, or a full ID map.

### Configuration and sparse map

The current-format application must be configured with the target physical
pair. For example:

```yaml
qdrant:
  data_collection_name: current__context__migration_20260908
  metadata_collection_name: current__context__migration_20260908__openviking_meta
  timeout_seconds: 30
```

`data_collection_name` and `metadata_collection_name` are physical Qdrant
names, not aliases. A migration target marker also binds `logical_collection`
and `migration_id`; the controller never adopts a migration marker owned by
another migration or missing the current identity. The migrator version is
code-owned. Required new marker fields are validated fail-closed; this
unpublished branch has no intermediate marker-schema upgrader.

Runtime attachment validates the physical/logical binding and vector policy.
For migration markers it also requires a complete migration identity/state.
The controller separately validates the migration ID and code-owned migrator
version against the reviewed
plan; these are not runtime configuration options. Successful attachment alone
does not prove that migration verification or cutover completed. Explicit
physical-name overrides require a logical identity in the marker; older ordinary
current-format markers without it remain compatible through default-derived
physical names.

`--source-metadata-collection` defaults to the pre-`#3872` global
`__openviking_meta`. The target sidecar defaults to
`{target_collection}__openviking_meta`; pass
`--target-metadata-collection` only when that physical name is explicitly
reserved for this migration. A target metadata sidecar is never shared by two
logical collections.

Pre-`#3872` sparse vectors contain numeric indexes without a reliable term
dictionary. Supply an authoritative JSON object in either direction:

```json
{"111": "hello", "222": "world"}
```

or:

```json
{"hello": 111, "world": 222}
```

If an index or term is missing, or a stable-index collision is found,
preflight fails closed. A source with multiple named sparse vectors also needs
`--sparse-vector-name`. The target layout records both dense and sparse
datatypes (the approved sparse index policy is currently `float16`); there is
no extra datatype tuning flag.

### Online procedure

1. Confirm the source and target physical pair, legacy metadata sidecar,
   `logical_collection`, `migration_id`, sparse map, timeout, and ACL risk.
   Acquire the external per-source migration lock.
2. Run read-only `preflight`; review and retain its compact JSON plan.
3. With legacy serving the source, run the reviewed `prepare`, then
   `backfill`, `reconcile`, and point-in-time `verify`. Each mutating command
   uses `--plan`, `--confirm`, and `--lock-held`. `reconcile` and final
   `verify` additionally acknowledge `--barrier-held` when run in the final
   window.
   Review ownership normalization: for `/user/alice/memories/a.md`, a missing
   `owner_user_id` is derived as `alice`; ownerless roots `/user` and
   `/resources` remain ownerless when the source value is null or absent.
   Malformed or URI-inconsistent owners fail closed.
4. Acquire the write barrier at the application boundary. Stop all legacy
   data and metadata writes, including deletes and partial updates, and drain
   in-flight requests. Keep the barrier held; the controller cannot infer that
   writers stopped.
5. Run final `reconcile --barrier-held` and
   `verify --final --barrier-held --confirm --lock-held`. Final verification
   independently compares canonical transformed-source and target-content
   fingerprints and exact IDs, payloads, dense/sparse vectors, metadata,
   indexes, and ACL state. `source_fingerprint` remains the raw-source drift
   receipt; it is not a target-content proof.
6. Run `cutover` with `--confirm --lock-held --barrier-held --plan` and
   `--deployment-hooks`. The controller invokes the validated idempotent hooks
   for drain, legacy serving removal, current rollout/readiness, read-only
   smoke, and accepted-write inspection in the required order. The complete
   runner also validates the target-not-served, current-removal, and
   legacy-read hooks used by rollback and retire. The operator owns both
   barrier acquisition and release; there is no release hook. Release the
   barrier only after the controller returns the `active` receipt and the
   current read-only smoke passes.
7. Perform the first normal read/write acceptance check, then retain both
   source and target for the agreed audit window.

The deployment-hook JSON must contain exactly these ten non-empty argv arrays:

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

The accepted-write hook must print exactly lowercase `true` or `false`;
other hooks are exit-code assertions. Commands run with `shell=False`, a
finite `timeout_seconds`, captured output, and non-secret `OV_*` bindings.
Hook output and inherited secrets are never placed in the JSON receipt.

Each reconciliation round uses a temporary SQLite manifest containing only
target IDs and canonical fingerprints. It is deleted after verification and
needs disk space for approximately one source scan. Backfill's marker
`source_count`, ACL/distinct-term counts, and fingerprints remain the most
recent full-source scan observations; the cursor, completion bit, and target
count are durable page progress. A page write must complete before its opaque
integer/string cursor advances. This is not a claim that online page reads
form one source snapshot.

### ACL and recovery boundaries

Records with missing or malformed `acl_mode`, `acl_direct_grants`, or
`acl_inherited_grants` remain fail-open. Do not expose the target until they
are repaired, or explicitly accept that risk with `--allow-acl-fail-open`.
That flag records the incomplete count and prints a warning; it never fakes
ACL protection and does not retroactively protect those records.

`acl_mode` must be `none`, `inherit`, or `restricted`. Legacy `acl_enabled`
booleans alone do not satisfy this current contract, including `true`: the
current application does not use them for protection. Migration preserves
payloads and does not infer or backfill modes. Review and repair ACL state
before exposing the target, or explicitly acknowledge the risk. The same
review is required before deploying the new application against existing
Qdrant collections that only contain the legacy boolean.

If cutover fails, the marker remains `cutting_over` (or `failed`) and the
operator keeps the barrier held. An interrupted cutover requires explicit
`--resume`; the controller checks for accepted current-format writes before
source-authoritative repair and again before publishing `active`. Before repair,
it removes any partially rolled-out current deployment from the serving path
and requires `assert_target_not_served` to succeed. It never silently overwrites
accepted target writes.

Rollback is allowed only while the barrier is held, before any accepted
current-format target write, with `--confirm --lock-held
--barrier-held --no-current-format-writes-accepted` and the deployment hooks.
It removes the current serving path, restores and verifies the legacy read
path, and retains the target as `rolled_back` for raw audit. After barrier
release or any accepted target write, automatic rollback is refused; use a
separate reverse migration.

`retire --confirm --lock-held --plan ... --deployment-hooks ...` is allowed
only after the retention window and when the target is not served. Normal
retire supports `active -> retained`, retries for an owned retained pair, and
an owned retained metadata-only retry. It deletes target data first, verifies
absence, then deletes target metadata. A reviewed, unmarked pre-marker orphan
is a separate cleanup path requiring the exact absent plan, external lock,
exact-name recheck, and confirmation. `ready`, `building`, `failed`,
`cutting_over`, and `rolled_back` are not ordinary retire states. The legacy
source and legacy metadata sidecar are never deleted.

The source is authoritative before cutover. A newer target timestamp does not
justify preserving divergent target values: reconciliation corrects them from
the authorized source or verification fails closed.

### Offline compatibility apply

For an explicitly offline migration, the compatibility wrapper keeps the
full-window freeze around both the source collection and legacy metadata
sidecar:

```bash
./.venv/bin/python scripts/maintenance/qdrant_migrate.py \
  --url https://qdrant.example \
  --source-collection legacy__context \
  --target-collection current__context \
  --source-metadata-collection __openviking_meta \
  --logical-collection default/context \
  --migration-id migration-2026-09-08 \
  --timeout-seconds 30 \
  --sparse-map /path/to/legacy-sparse-map.json \
  apply --plan /path/to/preflight.json --confirm --lock-held \
  --allow-acl-fail-open
```

Set `QDRANT_API_KEY` in the environment when authentication is required; do
not put secrets in command-line arguments. Remove
`--allow-acl-fail-open` after every source record has complete
`acl_mode`, `acl_direct_grants`, and `acl_inherited_grants` fields. Grant
values must be encoded ACL tokens. The flag never rewrites or retroactively
protects incomplete records.

After either path, configure the application with the target physical pair,
perform the normal deployment rollout separately, and retain the legacy
source/sidecar for the agreed audit window. This script never updates
application configuration, restarts services, or deletes legacy data.

## Upgrade an existing current-format sparse dictionary

`qdrant_sparse_upgrade.py` is only for an existing current-format data/metadata
pair. For a pre-`#3872` source, use `qdrant_migrate.py` above; its new targets
already receive per-index owner points.

The new sparse writer atomically claims one immutable owner point per stable
numeric index using Qdrant's native conditional upsert (`update_filter`).
Existing term-keyed rows remain readable and are never deleted or rewritten.
The converter only seeds missing owner points; it does not rewrite data
vectors, change the marker format, or repair conflicting dictionaries.

1. Upgrade **every Qdrant server node to >=1.16** before enabling the new
   writer. The CLI checks the contacted endpoint's version; that is not proof
   that every replica has been upgraded.
2. Confirm the exact physical data/metadata pair and take a recoverable
   backup. Acquire the external collection maintenance lock and application
   write barrier, stop **all old application writers**, and drain in-flight
   requests. Mixed old/new writers are unsupported, even after conversion:
   old writers do not honor index ownership. The CLI acknowledgement flags
   cannot acquire or verify these external controls.
3. Run read-only preflight and review its counts:

   ```bash
   ./.venv/bin/python scripts/maintenance/qdrant_sparse_upgrade.py \
     --url https://qdrant.example \
     --data-collection current__context \
     --metadata-collection current__context__openviking_meta \
     --timeout-seconds 30 \
     preflight
   ```

4. With the same lock and barrier still held, convert:

   ```bash
   ./.venv/bin/python scripts/maintenance/qdrant_sparse_upgrade.py \
     --url https://qdrant.example \
     --data-collection current__context \
     --metadata-collection current__context__openviking_meta \
     --timeout-seconds 30 \
     convert --confirm --lock-held --barrier-held --old-writers-stopped
   ```

   Set `QDRANT_API_KEY` in the environment when required. The converter uses
   bounded batches and temporary SQLite manifests, requiring local disk space
   proportional to the dictionary. It validates canonical IDs, stable hashes,
   provenance, and the legacy/owner union before writing; every inserted owner
   is read back with `consistency=all`. Final verification requires complete
   owner coverage, unchanged original bindings, and an unchanged marker.
   On any conflict or validation failure it stops without deleting partial
   progress. Keep the barrier held, investigate, then rerun the same command;
   a retry may only add still-missing matching owners.
5. Re-run preflight and require `missing_owner_count: 0`. Deploy only the new
   writer, check existing sparse reads, then release the barrier and perform
   a normal read/write acceptance check. Retain the backup and legacy rows.
   Starting old writers again is not a supported rollback.

Conversion is optional for read compatibility: existing-term lookups remain
read-only and accept both canonical row formats. A runtime-only marker need
not have migration provenance. A dictionary row with migration provenance
must carry both `logical_collection` and `migration_id` matching its marker;
an incomplete or mismatched pair fails closed.
