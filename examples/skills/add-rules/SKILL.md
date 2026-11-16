---
name: add-rules
description: Compile durable constraints into OpenViking Rules with rules/compile. Use when the user asks to add rules or import a policy. Do not use for preferences, facts, or rule search.
---

# Add Rules

## Goal

Use `POST /api/v1/rules/compile` to compile user-authorized durable constraints into
OpenViking Rules, then verify the task result and saved Rules.

## Workflow

1. Identify the user's rule text or policy sources and the intended OpenViking connection.
   Use the same instance and identity for upload, compilation, task polling, and verification.
2. Choose exactly one invocation mode for the entire workflow:
   - **Python SDK mode**: use when the Agent runs in Python and the OpenViking SDK is
     available. Use SDK methods for source upload, compilation, task polling, and reading.
   - **HTTP mode**: use for other languages or general-purpose Agents. Use HTTP endpoints
     for source upload, compilation, task polling, and reading.
   Do not mix SDK calls and direct HTTP calls within one compilation workflow.
3. Build a request with top-level `instruction` and `sources`; at least one must be non-empty.
   Preserve the source's obligations, applicability conditions, exceptions, thresholds,
   and identifiers without inventing policy.
4. Continue only with the selected mode and use its exact parameter shape:

   **Python SDK mode**

   `options` supports `sources`. Each source may be a local file or directory path, a
   remote URL, a Resource URI, or an advanced source dictionary. The SDK uploads local
   paths automatically.

   ```python
   task = client.rule_compile(
       instruction="Compile the supplied policy into durable Rules.",
       options={
           "sources": [
               "/local/path/policy.md",
               "https://example.org/policy.md",
               "viking://resources/team-policy",
           ]
       },
   )
   task_id = task["task_id"]
   ```

   Poll with `client.get_task(task_id)` and read saved content with
   `client.read_raw(uri)`.

   **HTTP mode**

   HTTP has no `options` wrapper. Put `instruction` and `sources` directly in the JSON
   body. Use `resource_uri` for an existing Resource, `path` for a remote text source,
   or `temp_file_id` returned by `POST /api/v1/resources/temp_upload` for local content.

   ```json
   {
     "instruction": "Compile the supplied policy into durable Rules.",
     "sources": [
       {"resource_uri": "viking://resources/team-policy"},
       {"path": "https://example.org/policy.md"},
       {"temp_file_id": "tmp_xxx", "source_name": "policy.md"}
     ]
   }
   ```

   Submit this body to `POST /api/v1/rules/compile`. Never send a client-local path to
   that endpoint. Poll `GET /api/v1/tasks/{task_id}` and read saved content through
   `GET /api/v1/content/read?uri=...&raw=true`.

   Save the returned `task_id`; acceptance does not mean compilation has completed.
5. Poll using the selected mode until the task is completed, failed, or cancelled.
   Query the existing task rather than resubmitting the request.
6. Inspect `result.operations`, `written_uris`, and `write_result`. Distinguish saved
   Rules from NOOP, UNCERTAIN, and REJECT results; completed does not guarantee new Rules.
7. Read representative saved Rules and their Scenarios using the selected mode. Check the
   policy text and evidence, and verify `rules/<scenario_id>/abstract.md` holds Scenario
   metadata while Rule files sit directly beside it as `<atomic_id>.md`.
   Report the task ID, actual changes, representative URIs, and unresolved decisions.

## Boundaries

- Compile durable constraints only; do not promote preferences, facts, or one-task advice.
- Prefer faithful, non-empty `applies_when` conditions supported by the source.
  Empty `applies_when` means applicable in every situation except `exception`, not unknown.
- Request lifecycle changes only when the user explicitly authorizes them.
  Use ARCHIVE only when the user's instruction explicitly asks to delete, discard, or
  move a Rule to the recycle bin. Use PAUSE for pause, disable, deactivate, retire,
  stop-applying, or stop-using requests without deletion intent. Archived Rules are
  logically deleted: they are not compiler candidates and cannot be restored through
  Rule compilation.
- Use client connection configuration, not service-side model credentials. Do not switch
  instances or expose credentials to resolve a failed request.
- The API accepts at most 16 sources and 64000 instruction characters; source collection
  reads at most 128 files and 128000 characters. Split oversized material into self-contained
  policies with their conditions and exceptions rather than silently omitting content.
- Rule compilation requires readable text. Do not submit image, audio, or video sources.
- If a request fails or its status is unknown, check the task and saved files before retrying.
  Previously imported Resources are not rolled back when later Rule compilation fails.
- Do not treat an immediate empty search result as proof that writing failed; semantic
  indexing may still be pending.
