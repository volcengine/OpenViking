# GitHub PR Radar User Guide

Radar runs with a source Watch, collects merged GitHub PRs, and produces Chinese summaries and historical reports.

## 1. Prerequisites

Start OpenViking with its models configured. On the server machine, sign in to GitHub CLI as the operating-system user running the service:

```bash
gh auth login
gh auth status
```

An existing login can be used directly. Radar calls `gh api`; GitHub CLI manages credentials.

Replace `<host>`, `<port>`, `<owner>`, and `<repo>` in the examples. If your OpenViking deployment requires authentication, include the authentication headers configured for that deployment.

## 2. Enable Radar

Enable `args.pr_radar` when importing a repository:

```bash
curl --fail-with-body -sS -X POST 'http://<host>:<port>/api/v1/resources' \
  -H 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{
  "path": "https://github.com/<owner>/<repo>",
  "processing_mode": "vectors_only",
  "watch_interval": 60,
  "wait": false,
  "args": {
    "pr_radar": {
      "enabled": true,
      "digest_interval_minutes": 1440,
      "backfill_days": 7
    }
  }
}
JSON
```

This example collects every **60 minutes**, uses **24-hour report windows**, and initially looks back **7 days**. These intervals are example values.

| Parameter | Meaning |
| --- | --- |
| `watch_interval` | Watch interval in minutes; must be positive when Radar is enabled |
| `processing_mode: vectors_only` | Processes source vectors without disabling Radar model summaries |
| `pr_radar.enabled` | Set to `true` to enable Radar |
| `pr_radar.digest_interval_minutes` | Report window in minutes; required when enabled, range 1–10080 |
| `pr_radar.backfill_days` | Initial lookback in days; default 7, range 1–90 |
| `pr_radar.summary_enabled` | Model summaries; defaults to `true` and can usually be omitted |

Save the response's `watch_id` to manage the Watch and locate reports. The separate `task_id` identifies the import task. Request acceptance does not mean the import or report is complete.

## 3. Read reports

Reports are stored under the Watch owner's user directory:

```text
viking://user/<user_id>/resources/radar/<watch_id>/
├── latest.md       Most recently published report
└── reports/        Historical reports
```

Read as the user who created the Watch, substituting the actual IDs:

```bash
curl --fail-with-body -sS --get 'http://<host>:<port>/api/v1/content/read' \
  --data-urlencode 'uri=viking://user/<user_id>/resources/radar/<watch_id>/latest.md'
```

The response's `result` field contains the report.

- The first report is generated after backfill collection completes, if it contains merged PRs.
- Later reports require a completed window and complete collection. PRs belong to windows by merge time.
- Empty windows produce no report and leave `latest.md` unchanged.
- Radar runs on every Watch trigger, including when the source HEAD is unchanged.
- Model failures can fall back to a factual template. Each report identifies its actual generation mode.

## 4. Manage the Watch

View Watch status and its owner:

```bash
curl -sS 'http://<host>:<port>/api/v1/watches/<watch_id>'
```

Trigger execution:

```bash
curl --fail-with-body -sS -X POST \
  'http://<host>:<port>/api/v1/watches/<watch_id>/trigger'
```

A manual trigger submits execution; it does not close a report window early.

Pause:

```bash
curl --fail-with-body -sS -X PATCH \
  'http://<host>:<port>/api/v1/watches/<watch_id>' \
  -H 'Content-Type: application/json' \
  --data '{"is_active": false}'
```

Use `true` to resume. Pausing affects both source refresh and Radar.

Delete the Watch:

```bash
curl --fail-with-body -sS -X DELETE \
  'http://<host>:<port>/api/v1/watches/<watch_id>'
```

Deletion stops future execution and keeps existing reports.

## 5. If no report appears

Confirm the Watch is active and check server logs for `Radar failed`. Radar errors do not change the source Watch's result, so its `last_error` alone cannot establish whether Radar succeeded.

For GitHub access failures, check `gh auth status` and repository access in the server environment. Collection may also be incomplete, the window may still be open, or it may contain no merged PRs.

Radar records errors and retries on subsequent Watch triggers. Source Watch processing continues.
