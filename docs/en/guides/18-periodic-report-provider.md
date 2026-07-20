# Optional periodic-report archive provider

OpenViking ships an optional Resource provider for reports that another product has
already selected and rendered. It is designed for LoopX `periodic-report`, but the
provider contract is domain-neutral: release summaries, research reports, operational
reports, and issue-fix reports use the same archive surface.

The extension is **disabled by default**. Importing it only makes the provider
available; it does not activate reporting or the archive sink. The formal sink entry
point requires a verified LoopX `periodic_report_activation_v0` whose profile is
enabled and binds this exact provider.

This keeps one product state: LoopX owns capability activation and the project profile;
OpenViking does not create a second reporting switch that can drift from it.

## Ownership boundary

The provider owns only:

- `report.archive.write`
- `report.archive.readback`
- `report.query`

It does not decide when a report is due, what evidence belongs in it, how Markdown or
HTML is rendered, or whether a Lark message is sent. An archive failure is local to the
optional archive sink; the caller chooses whether its primary delivery can continue.

## Archive layout and receipts

Each report uses a stable identity under:

```text
viking://resources/periodic-reports/{project_key}/{period_start}/{report_id}/
```

The directory contains `report.md`, `report.html.txt`, and `manifest.json`. HTML remains
UTF-8 text; the manifest declares `media_type=text/html`. This preserves the exact HTML
without widening OpenViking's bounded `content/write` create-extension allowlist.

`manifest.json` is the commit marker and is written last. Its deterministic
`bundle_digest` and `result_id` bind the identity, metadata, Markdown, and HTML. An
identical retry is a no-op, a retry after a partial write completes the missing files,
and a different payload at the same identity fails closed. Every successful write ends
with exact manifest and payload readback.

## Python usage

```python
from openviking import AsyncOpenViking
from openviking.extensions.periodic_report import (
    PeriodicReportArchiveProvider,
    PeriodicReportBundle,
)

client = AsyncOpenViking(path="./data")
provider = PeriodicReportArchiveProvider(client)

bundle = PeriodicReportBundle(
    project_key="my-project",
    profile_id="my_project_weekly",
    profile_version="v1",
    report_id="weekly-2026-07-20",
    period_start="2026-07-13",
    period_end="2026-07-19",
    generated_at="2026-07-20T09:00:00+08:00",
    markdown="# Weekly report\n\n- Shipped the provider",
    html="<h1>Weekly report</h1><ul><li>Shipped the provider</li></ul>",
    metadata={"trigger": "cadence"},
)

# Exact periodic_report_activation_v0 returned by LoopX inspect-profile.
activation_receipt = load_loopx_activation_receipt()
readiness = provider.readiness(
    activation_receipt=activation_receipt,
    sink_id="project_archive",
    bundle=bundle,
)
assert readiness["status"] == "ready"

result = await provider.archive_sink(
    bundle,
    sink_id="project_archive",
    idempotency_key="report_sink_0123456789abcdef",
    activation_receipt=activation_receipt,
)
assert result["readback_verified"]

history = await provider.query(project_key="my-project", limit=8)
```

## Optional LoopX binding

The extension manifest is available through `get_extension_manifest()`. Its
`report.archive.write@v0` capability and `periodic_report_sink_v0` protocol match the
version-pinned LoopX sink contract:

```json
{
  "schema_version": "periodic_report_sink_binding_v0",
  "sink_id": "project_archive",
  "sink_kind": "project_resource",
  "sink_role": "archive",
  "dependency_policy": "optional",
  "capability": {
    "capability_id": "report.archive.write",
    "capability_version": "v0"
  },
  "extension": {
    "extension_id": "openviking.periodic-report.archive",
    "extension_version": "0.1.0",
    "protocol": "periodic_report_sink_v0"
  }
}
```

Pass the LoopX activation receipt to `provider.readiness(...)`, then pass that provider
receipt to LoopX extension-readiness evaluation. After generation, call
`provider.archive_sink(...)` with the same activation receipt, sink id, and LoopX-derived
idempotency key. A disabled capability, missing binding, disabled dependency, or version
mismatch fails closed before any Resource write.

A LoopX project should add this optional binding only after explicitly enabling its
periodic-report profile. If OpenViking is absent, provider-neutral generation remains
usable and the optional sink degrades without taking over scheduling or chat delivery.

## Experience modes

| Configuration | Experience |
| --- | --- |
| Capability disabled, extension installed | No report run and no archive write |
| Capability enabled | Triggering, normalized evidence, Markdown/HTML generation, and configured primary delivery |
| Capability + optional OpenViking extension | Everything above plus durable history, exact retry/readback, and query/visualization input; archive absence degrades locally |
| Capability + required OpenViking extension | Durable mode; formal completion fails closed until the archive is verified |

For the fullest team experience, use one enabled profile with both the primary Lark
delivery sink and this OpenViking archive sink. The same report identity then backs the
group message and historical report view without giving either provider scheduling
authority.
