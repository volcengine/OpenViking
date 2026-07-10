# VikingDB Content Backfill

This maintenance script backfills empty `content` fields in historical OpenViking
VikingDB records. It is intended for one-off operational use after the target
VikingDB collection already has the `content` text field and FullText configured.

## What It Does

- Enumerates source data from each account root in Local AGFS.
- Computes the deterministic OpenViking vector record ID for each expected
  L0/L1/L2 record.
- Fetches existing VikingDB records by ID.
- Updates only empty or missing `content` fields with `update_data`.
- Leaves vectors, sparse vectors, abstracts, tags, schema, and indexes unchanged.

## What It Does Not Do

- It does not create or update collection schema.
- It does not rebuild indexes.
- It does not re-vectorize data.
- It does not call `upsert_data`.
- It does not start or restart `openviking-server`.
- It does not process orphan vector records whose Local AGFS source data no
  longer exists.

## Environment Requirements

Run from the repository root with the project virtual environment:

```bash
./venv/bin/python scripts/maintenance/vikingdb_content_backfill/backfill_vikingdb_content.py
```

The script depends on the current OpenViking codebase and configuration:

- `ov.conf` must point to the target environment.
- `storage.vectordb.backend` must be `volcengine` or `vikingdb`.
- The target collection must already contain the `content` text field and
  FullText configuration.
- API key mode trusts that schema and FullText are already correct; the script
  does not perform management-plane schema validation for API key mode.
- Local AGFS must be readable from the machine or pod where the script runs.
- If encryption is enabled, the same encryption configuration and root key must
  be available so Local AGFS reads return plaintext.

## OpenViking Code Assumptions

The script is designed for OpenViking versions that use the current
deterministic vector ID rule:

```text
id = md5(f"{account_id}:{seed_uri}")
```

Where:

- L0 seed URI is `{directory_uri}/.abstract.md`
- L1 seed URI is `{directory_uri}/.overview.md`
- L2 seed URI is the file or memory chunk URI

It also assumes the current `Vectorize.full_text -> content` write semantics.


## Enumeration Scope

For each account directory under `/local`, the script traverses `viking://` with
`node_limit=None`, `level_limit=None`, and `show_all_hidden=True`. It no longer
maintains a hard-coded list such as `resources`, `agent/skills`, or user
`resources` / `memories` / `skills`, because that list can miss newer
namespaces such as peer-scoped content.

Directories generate L0/L1 candidates; files generate L2 candidates. Hidden
L0/L1 marker files (`.abstract.md` and `.overview.md`) are not emitted again as
L2 candidates. Existing VikingDB records are then checked by deterministic ID;
missing IDs are simply skipped.

## Recommended Usage

Start with a dry run:

```bash
./venv/bin/python scripts/maintenance/vikingdb_content_backfill/backfill_vikingdb_content.py \
  --limit 100
```

Inspect the generated files under:

```text
scripts/maintenance/vikingdb_content_backfill/result/<timestamp>/
```

Then execute the update:

```bash
./venv/bin/python scripts/maintenance/vikingdb_content_backfill/backfill_vikingdb_content.py \
  --execute
```

Optional throttling:

```bash
./venv/bin/python scripts/maintenance/vikingdb_content_backfill/backfill_vikingdb_content.py \
  --execute \
  --batch-size 100 \
  --limit 10000
```

## Output Files

Each run writes to a timestamped directory under `result/`.
The repository `.gitignore` already ignores `result/`, so run outputs should not
be committed.

- `summary.json`: final counters for the run.
- `progress.json`: latest counters, written after the initial root traversal and
  refreshed after each processed candidate.
- `updated.jsonl`: records actually updated by the script.
- `failed.jsonl`: records that failed to update.

By default, the script does not write full candidate or skipped-record details,
because those files can become very large on production datasets.

Optional detail files:

- `candidates.jsonl`: written only with `--record-candidates`.
- `skipped.jsonl`: written only with `--record-skipped`.

The script does not support checkpoint resume in the current version. Re-running
is safe because non-empty `content` is skipped by default.

### Summary Fields

- `candidate_count`: number of deterministic vector candidates generated from
  Local AGFS source data.
- `existing_records`: number of candidates that already had a VikingDB record.
- `processed_count`: number of records whose `content` was empty and for which
  replacement content was successfully resolved.
- `updated_count`: number of records updated through `update_data`. This is `0`
  in dry-run mode.
- `skipped_count`: number of records skipped for any reason.
- `failed_count`: number of records that failed during `update_data`.
- `missing_record_count`: number of candidates whose deterministic VikingDB
  record ID did not exist.
- `non_empty_content_count`: number of records skipped because `content` was
  already non-empty.
- `created_after_cutoff_count`: number of records skipped because their
  `created_at` / `create_time` was later than the script start time.
- `empty_resolved_content_count`: number of records skipped because the script
  could not resolve non-empty source content.
- `changed_during_run_count`: number of records skipped because `updated_at`
  changed between the first fetch and the pre-update fetch.

## Safety Notes

- Default mode is dry-run. The script writes VikingDB only when `--execute` is
  provided.
- Non-empty `content` is skipped by default.
- Use `--rewrite-non-empty` only when intentionally replacing existing content.
- Use `--record-candidates` or `--record-skipped` only for debugging or small
  datasets.
- Before updating, the script fetches the record again and skips it if `content`
  is no longer empty or `updated_at` changed during the run.
- The script uses Local AGFS directly and does not start `OpenVikingService`,
  queue workers, or transaction lock managers.
