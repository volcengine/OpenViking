# OpenViking Ingest Contract

`ov-add-paper` must end by importing the generated paper artifact into OpenViking with `ov add-resource`.

## Default Command

```bash
ov add-resource <artifact-dir> --to viking://resources/papers/<slug> --wait
```

If the user did not provide a target URI, derive a stable slug from the paper title, arXiv ID, DOI, or file stem.

## Helper Script

Prefer:

```bash
python3 scripts/ingest_to_ov.py <artifact-dir> --to viking://resources/papers/<slug> --wait
```

The helper validates the artifact before calling `ov add-resource`.

## Required Preconditions

- `ov` CLI is installed and configured.
- `~/.openviking/ovcli.conf` or equivalent environment config is present.
- The artifact directory exists locally.
- Validation passes or the user explicitly accepts the listed validation errors.

## Reporting

Report:

- artifact directory path
- target URI or returned root URI
- whether `--wait` completed
- validation summary
- exact error and recovery path if ingestion failed

## Boundaries

- Do not run `ov add-skill` for this workflow.
- Do not delete or overwrite local artifacts as cleanup.
- Do not hide asynchronous ingestion status. If `--wait` is not used, say processing continues in the background.
- Do not invent a successful OV URI when the CLI fails.
