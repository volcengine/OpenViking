# Filesystem Processing Mode Implementation Plan

> Apply the executing-plans workflow, then run the relevant regression checks.

**Goal:** Make VLM and SemanticQueue side effects controllable for generic filesystem mutations.

**Architecture:** Reuse semantic_and_vectors and vectors_only. Keep native vector transfer/deletion and existing byte-write paths; file-only to indexed transfers use target-scoped durable reindex. No new dependency or namespace policy.

**Tech stack:** Python/FastAPI, QueueFS, Python/TypeScript/Go SDKs.

## Work

1. Add FS mutation, protocol, and SDK contract tests and confirm failures.
2. Implement rm/cp/mv mode guards, structured mv status, and postcommit target reindex.
3. Add single-file snapshot restore mode; retain directory vectors-only behavior.
4. Add WebDAV processing header and MCP write/edit/forget options.
5. Update SDK remove/move options with compatible default omission and Go MoveWithOptions.
6. Update bilingual API, MCP and SDK docs; verify with native local offline tests and review.

## Acceptance

- vectors_only makes no SemanticMsg and no VLM request, while URI/L2 vector consistency holds.
- file-only to indexed transfers return a durable reindex task; failure leaves committed bytes and reports vector_error.
- WebDAV invalid mode fails before any mutation; direct single-file indexing stores its real parent URI.
- snapshot omitted/null retains defaults; directory semantic_and_vectors is rejected before write with INVALID_ARGUMENT (HTTP 400).
- Defaults remain compatible with existing calls and older servers.

## Boundaries

mkdir, tags, ACL, commit, memory-specific refresh and skill lifecycle are unchanged. defer_processing remains write/batch-write only. Existing SDK result types and missing cp/single-file snapshot methods are unchanged. Existing summaries may stay stale in vectors_only mode.
