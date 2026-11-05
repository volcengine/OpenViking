# Version management

Save two versions in an isolated demo directory, inspect the difference, and restore the first version. Connect using [CLI basics](05-cli-setup.md). Processing uses the server's configured models and index.

## Save version one

```bash
ov mkdir viking://resources/onboarding-versions-v1
ov write viking://resources/onboarding-versions-v1/plan.md --mode create --content "Feedback is exported every Friday." --wait
ov snapshot commit -m "onboarding v1" --paths viking://resources/onboarding-versions-v1 -o json
```

Record `commit_oid` as `<v1>`. This tutorial operates only on the demo directory; use a new name for repeat runs. `snapshot commit` explicitly saves a snapshot. `session commit` archives conversation messages and starts memory extraction.

## Edit and compare

```bash
ov write viking://resources/onboarding-versions-v1/plan.md --content "Feedback is exported every Monday." --wait
ov snapshot commit -m "onboarding v2" --paths viking://resources/onboarding-versions-v1 -o json
```

Record the second `commit_oid` as `<v2>`. Replace the placeholders below with actual IDs:

```bash
ov snapshot show <v1> --path viking://resources/onboarding-versions-v1/plan.md
ov snapshot diff viking://resources/onboarding-versions-v1/plan.md --from <v1> --to <v2>
```

The old version should say Friday; the diff should show the change to Monday.

## Preview and restore

```bash
ov snapshot restore <v1> viking://resources/onboarding-versions-v1 --dry-run -o json
```

Inspect planned additions, changes, and deletions, confirming they belong to the demo directory. Restore writes changes back to that scope; preserve any newer work you need before applying it. Once the plan is confirmed:

```bash
ov snapshot restore <v1> viking://resources/onboarding-versions-v1 -m "restore onboarding v1" -o json
ov read viking://resources/onboarding-versions-v1/plan.md
ov snapshot log --paths viking://resources/onboarding-versions-v1 -o json
```

The content should say Friday again, with a new restore commit in history. `applied` means changes were applied; `noop` means the selected source tree matches HEAD. Read the file to confirm the working content.

## Check retrieval readiness

Restore schedules semantic and vector rebuilding as needed. Replace `<task_id>` with the ID returned by restore, then check retrieval after completion:

```bash
ov task status <task_id>
ov find "When is feedback exported?" --uri viking://resources/onboarding-versions-v1
```

If processing is ongoing, inspect its task and retry retrieval after completion. Use an initial 2-minute observation window and keep pending task IDs for later checks.

Snapshots store the selected source-file tree. Operations use current ACLs, and vectors are rebuilt from restored content. Account ignore rules and system pruning affect saved scope. See [Snapshot reference](../api/11-snapshot.md) and the [snapshot guide](../guides/15-snapshot.md). Identify the demo directory in Studio before cleaning it up; see the guide for history retention.
