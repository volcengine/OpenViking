# Read-only artifact inspection

These examples use the `ov` CLI. Confirm commands with the installed version's `--help`; MCP equivalents use their registered schemas. Replace uppercase placeholders before use.

## Connection and errors

```bash
ov health -o json
```

For a user-specified CLI config, prefix **every** command (including helper invocations) with:

```bash
OPENVIKING_CREDENTIAL_SOURCE=cli OPENVIKING_CLI_CONFIG_FILE=/absolute/path/to/ovcli.conf ov health -o json
```

Keep stderr and check the exit status and response status before parsing. Do not use `2>/dev/null` or a `sed` filter that turns authentication/network failures into empty evidence. If the CLI adds diagnostics around JSON, capture stdout/stderr separately and inspect the envelope first. Do not dump configs to diagnose identity. A second config is useful only for a defined comparison of identity, scope, or path behavior.

## Candidate search

```bash
ov grep 'MEMORY_URI_PATTERN' -u 'VERIFIED_SESSION_ROOT' -n 200 -o json
```

`grep` is pattern search: escape regex metacharacters in the URI for literal matching. Inspect the response envelope, errors, and result limits before extracting candidate URIs. The current envelope permits:

```jq
.result.matches[]?.uri | select(endswith("/memory_diff.json"))
```

Deduplicate candidates, then read the selected files. If capped results contain only messages, enumerate diffs within likely sessions using available list/glob capabilities; do not repeatedly increase the limit over the entire library. For local exports, `rg --files EXPORT_ROOT -g memory_diff.json` enumerates diff files without searching source conversations.

## Read and select an exact operation

```bash
ov read 'DIFF_URI' -o json
ov read 'ARCHIVE_URI/messages.jsonl' -o json
ov read 'ARCHIVE_URI/.meta.json' -o json
ov session get 'SESSION_ID' -o json
ov session get-session-context 'SESSION_ID' -o json
```

Read `.overview.md`, completion markers, failure records, and traces only when relevant and available. Missing optional artifacts are not evidence of failed extraction. Inspect existing directory entries before guessing trace filenames.

After verifying success, save the decoded `.result` string from `ov read` as a local JSON file. On that decoded diff, select changes by URI equality, not a substring match:

```bash
jq --arg uri 'EXACT_MEMORY_URI' '
  {archive_uri, trace_id, extracted_at, summary,
   operations: {
     adds: [.operations.adds[]? | select(.uri == $uri)],
     updates: [.operations.updates[]? | select(.uri == $uri)],
     deletes: [.operations.deletes[]? | select(.uri == $uri)]
   },
   skipped_operations}
' /path/to/decoded-memory-diff.json
```

Inspect `skipped_operations` separately using its actual schema; they are not applied changes. Keep archive/trace identifiers when reducing output. Current diffs group operations into `adds`, `updates`, `deletes`; other revisions must be inspected before adapting this query.

Diffs may be merged across batches or training outputs. Their `extracted_at` and `trace_id` are correlation aids, not guaranteed per-operation timestamps or complete execution histories. Stored metadata may contain multiple source extraction IDs. Follow only the IDs relevant to the symptom, and retain their session scope.

## Evidence checks before concluding

| Observed artifacts | Supported conclusion | Still unproven |
| --- | --- | --- |
| Python SDK update in trace; apply error; no matching diff change | Update proposed; recorded attempt failed | Whether a later retry or another writer changed the file |
| Completed archive; empty diff | No changes recorded in that diff | Whether the proposal was empty, unchanged, skipped, or evidence is missing |
| URI appears in message or diff body only | URI was mentioned | That this archive changed that URI |
| Matching diff UPDATE; current file differs | Archive recorded one version; present state differs | Which subsequent operation caused the difference |
| User claim absent from archive | Inspected archive lacks the claim | Whether capture omitted it or another archive contains it |
