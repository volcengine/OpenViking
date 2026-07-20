# Periodic report Resources

This example shows how an application can use the public `AsyncOpenViking`
filesystem API to save one generated report as two project Resources:

- `report.md` contains the readable report;
- `manifest.json` is written last and points to the report plus its digest.

The ordering is an application convention, not an OpenViking transaction or
extension ABI. The application still owns scheduling, report generation,
stable IDs, retries, and conflict policy. OpenViking provides the Resource
write and exact readback surfaces.

The manifest fields in this example are illustrative application metadata,
not a registered OpenViking report schema.

```bash
python examples/periodic-report-resource/archive_report.py \
  --path ./data \
  --report-id 2026-W29
```

The example uses `mode="create"`, so reusing an existing report ID fails
instead of overwriting history. A production retry handler can read the
existing Resources, compare their digests, and accept only an exact match.
