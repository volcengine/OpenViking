# Cross-language contract fixtures

Language-neutral data pinning wire behaviour that every OpenViking memory plugin shares. The JavaScript library under `../../lib/` is asserted against these files by `../../contract-fixtures.test.mjs`; the Hermes plugin (Python) asserts the same files, so the two implementations cannot drift apart silently.

The files hold inputs and expected outputs only, never code. Inputs use neutral snake_case names (`api_key`, `trusted_identity`, `recall_limit`, ...); each language maps them onto its own config shape. If a fixture and the JavaScript library disagree, the library is the reference: fix the fixture only after re-reading the code.

| File | Pins |
| --- | --- |
| `headers.json` | `buildOvHeaders` in `lib/ov-http.mjs`: Bearer auth only, account and user headers only for a trusted server, the actor peer header, and the User-Agent shape from `buildUserAgent` in `lib/credentials.mjs`. |
| `retryable.json` | `isRetryableFailure` in `lib/retryable.mjs`, plus the 404/405 fallback from the batch endpoint to per-message sends in `lib/batch-send.mjs`. |
| `recall-request.json` | The common subset of the context-mode recall body built by `buildContextSearchBody` in `lib/recall-core.mjs`. |

## What `recall-request.json` leaves out

Only the fields named in `common_fields` are compared. A case's `expected` lists exactly those fields that must be present; every other common field must be absent from the body. The following are excluded on purpose:

- `query`: filled per turn by the caller.
- `rewrite`, `rewrite_max_bullets`: depend on whether the harness has a local compressor (`localCompressorAvailable`) and on the harness's rewrite setting. Harnesses without a local compressor do not share this decision.
- `query_expansion`: sent only when the harness's own config explicitly sets it.

A plugin that sends an excluded field is not violating the contract. A plugin that omits a common field, or fills it with a different meaning, is.

## Placeholders

`headers.json` uses `{plugin}` and `{version}` in the User-Agent. A case that carries `placeholders` substitutes them before comparing; the `user_agent.cases` list gives the fully resolved values.

## Adding a fixture

Add cases to the existing file when a behaviour is shared by all plugins; keep harness-specific behaviour out of here. Both the JavaScript test and the Python test must be extended in the same change, otherwise one side silently skips the new case.
