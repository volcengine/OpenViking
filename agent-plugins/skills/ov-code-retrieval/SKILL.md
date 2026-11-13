---
name: ov-code-retrieval
description: Find and explain code stored in OpenViking using scoped search and source evidence. Use for code lookup, behavior tracing, or repository questions. Do not use for indexing or modifying resources.
---

# OpenViking Code Retrieval

Use this procedure to answer from an ingested repository. Answer in the user's
language.

## Establish the resource and tool boundary

- Use the repository URI supplied by the user or returned by OpenViking.
  `viking://resources/<repo>/` is an example, not a guaranteed mapping.
  Keep the returned URI; never translate it into a local checkout path.
- Use the connected OpenViking read-only tools. Names may have a harness prefix,
  such as `mcp__openviking__grep`; inspect the exposed schema for parameters.
- If the harness permits only `grep` and `read`, stay within that contract.
  Do not enable discovery tools, local search, shell access, or another retrieval
  provider to bypass it. Report a missing repository URI if it cannot be resolved
  with the allowed tools.
- If discovery is permitted and the resource is unknown, use `list` on the
  authorized resource root or scoped `find`. Use `.abstract.md` and
  `.overview.md` to navigate; verify code claims against source files.
- This skill does not add, index, delete, or modify resources.

## Turn the question into evidence requirements

Before searching, identify:

1. The object being asked about: class, instance, test, handler, configuration,
   or another specific entity.
2. The behavior and conditions: input, branch, state, output, version, or failure.
3. The relationship to establish: caller to callee, test to implementation,
   producer to consumer, or representation to reconstruction.

Preserve exact names and quoted strings. Treat an inferred identifier as a
hypothesis: a phrase such as “class name” does not by itself select `__class__`
over `__name__`. Do not silently drop a qualifier while translating prose.

## Choose the first lookup

| Available clue | First lookup |
|---|---|
| Symbol, identifier, error message, distinctive source term | `grep` within the repository |
| Known file name or path | `read` the known URI, or `glob` if discovery is allowed |
| Concept only, with no credible lexical anchor | Scoped `find` if allowed, then inspect candidate files |
| Repository unknown | Authorized `list` or `find`, if allowed |

Prefer `grep` once a lexical anchor exists. Keep the first query simple. Use a
small set of alternative anchors rather than a broad regex intended to encode
the whole question.

Example tool arguments, after confirming the repository URI:

```json
{"uri":"viking://resources/pylint/","pattern":["__name__"]}
```

Multiple patterns are alternative searches merged into one result; they do not
require all conditions to occur in the same function. Check conjunctions in the
source yourself. Escape regex metacharacters when searching literal text.

## Read and discriminate candidates

1. Use the returned URI and line numbers to open a source window.
2. For this MCP `read`, `offset` is zero-based; grep source lines are one-based.
   For a match at line `L`, use `offset = max(0, L - 1 - context_before)`.
   Verify the connected schema if the deployment uses a different interface.
3. Start with roughly 40–120 lines around the match. Keep each read at most
   400 lines, extend only to finish the relevant function, branch, or assertion.
4. When plausible candidates compete, read their distinguishing statements.
   Compare the required object, input, condition, and asserted result.
   A matching function name or a high retrieval score is not sufficient.
5. Retain the candidate that explains the question's conditions. If none does,
   revise the query instead of explaining the closest-looking candidate.

Example:

```json
{"uris":["viking://resources/pylint/tests/functional/a/access/access_to__name__.py"],"offset":0,"limit":40}
```

## Extend only the missing part of the evidence

- Search newly observed exact names to follow the necessary caller, callee,
  helper, configuration value, or test assertion.
- Narrow to a directory or file after evidence identifies it. If a scoped
  lookup fails, widen to the parent or repository before claiming absence.
- Check the relevant guard, return value, exception handling, and state update.
  For test questions, connect the fixture and assertion to the implementation.
- For protocol-driven calls such as `list(x)` invoking `__iter__`, inspect the
  method body and call site; do not require an explicit textual call.
- Cite the returned file URI, symbol, and one-based source line range. Quote
  only the lines needed to establish the behavior.

## Recover from a miss or a contradiction

- Distinguish “no matches” from invalid regex, permission failure, missing
  resource, service failure, or a partial multi-pattern failure.
- Bounded or index-assisted search is not proof that code does not exist.
  Adjust one factor at a time: exact spelling, alias, scope, then result limit
  where exposed. Read a known file directly when the URI is already established.
- After two searches add no relevant evidence, reconsider the assumed object
  or mechanism. Try a different credible anchor or compare another candidate;
  do not keep elaborating the same unverified interpretation.
- When source contradicts the working explanation, revise the explanation.
  When a required branch is still unverified, state the gap.

## Finish with an evidence-backed answer

Answer once the requested behavior and its decisive conditions are supported.
Lead with the result, give the shortest necessary causal chain, and attach
source references. Separate directly observed code behavior from inference.
Report unresolved evidence only where it affects the answer. Do not fill gaps
with plausible repository-specific claims.

For controlled evaluations, also follow the harness's answer language and
output format. Keep reference answers, scores, and previous trajectories out
of the solving context.
