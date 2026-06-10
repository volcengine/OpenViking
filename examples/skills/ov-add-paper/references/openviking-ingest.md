# OpenViking Ingest Contract

`ov-add-paper` must end by importing the generated paper artifact into OpenViking with `ov add-resource`.

## Recommended CLI Flow

```bash
python3 scripts/validate_ara.py <artifact-dir>
ov -o json stat viking://resources/papers/<slug>
ov add-resource <artifact-dir> --to viking://resources/papers/<slug> --wait
ov -o json stat viking://resources/papers/<slug>
ov tree viking://resources/papers/<slug>
```

If the user did not provide a target URI, derive a stable slug from the paper title, arXiv ID, DOI, or file stem.

Use `--timeout 300` or a larger value for medium-sized papers:

```bash
ov add-resource <artifact-dir> --to viking://resources/papers/<slug> --wait --timeout 300
```

The `stat` preflight should return NOT_FOUND for a new target. If it succeeds, ask before overwriting or choose a different target.

## Required Preconditions

- `ov` CLI is installed and configured.
- `~/.openviking/ovcli.conf` or equivalent environment config is present.
- The artifact directory exists locally.
- Validation passes or the user explicitly accepts the listed validation errors.

## Directory Upload Pitfall

The CLI zips a local directory before upload. `--include`/`--exclude` are request parameters and may not reduce the client-side ZIP payload. If directory upload repeatedly ends with `Could not reach OpenViking` while `ov health` succeeds and single-file imports work, suspect upload timeout or an unstable large directory payload.

Mitigations:

- Keep the OV upload artifact lean: required ARA Markdown/YAML plus filed figure/table PNG evidence.
- Do not duplicate large raw PDFs, source tarballs, or extraction scratch files inside the upload directory unless the user explicitly needs them in OV.
- Keep raw source files in a local `source/` or full `artifact/` working copy and record their paths in `src/environment.md`.
- Optimize evidence PNGs while preserving readability, for example by rendering full pages at 1.5x instead of 2x when that is still legible.
- If needed, create a separate upload copy such as `<artifact-dir>-ov/` rather than mutating the full local artifact.
- `upload.mode = "shared"` can help distributed deployments, but it does not make an oversized directory payload smaller.

If `ov add-resource --wait` exits with a connection error after the target appears, treat it as an interrupted wait, not necessarily a failed ingest. Check:

```bash
ov -o json stat viking://resources/papers/<slug>
ov wait --timeout 300
ov observer queue
ov tree viking://resources/papers/<slug>
```

If `stat` shows `isLocked=false`, `count` is nonzero, the queue is empty, and `tree`/`read` can access content, report the ingest as completed with a note that the original `--wait` connection broke. If the target is not visible, shrink the upload payload and retry.

## Reporting

Report:

- artifact directory path
- target URI or returned root URI
- whether `--wait` completed or required recovery checks
- validation summary
- exact error and recovery path if ingestion failed

## Boundaries

- Do not run `ov add-skill` for this workflow.
- Do not delete or overwrite local artifacts as cleanup.
- Do not hide asynchronous ingestion status. If `--wait` is not used, say processing continues in the background.
- Do not invent a successful OV URI when the CLI fails.
