# First Failing Patch Block Diagnostic Design

## Context

`ExtractLoop._validate_patch_operations()` currently applies every SEARCH/REPLACE block in a field as one `StrPatch`. If any later block fails, the validator catches the field-level failure and then reports the first non-empty block instead of the block that actually failed.

This produces misleading repair prompts. In observed traces, an early block matched exactly once while a later `- Values friendship and compassion` block matched twice. The repair prompt identified the valid early block, so the model modified that block and left the duplicate SEARCH unchanged. The second validation failed for the same underlying reason.

## Goal

Report the first block that actually fails in patch execution order, with enough structured information for the repair model to correct it.

The change must preserve:

- `PatchOp` matching and application behavior, including fuzzy fallback;
- sequential block semantics, where an earlier replacement can affect a later SEARCH;
- plain-content validation introduced for rendered Markdown links;
- the existing single repair retry limit;
- existing empty-SEARCH and SEARCH-equals-REPLACE handling;
- cross-file SEARCH diagnostics.

## Selected Approach

Validate each active block sequentially through the existing `PatchOp` implementation.

For each operation field containing a `StrPatch`:

1. Initialize `working_content` from `operation.old_memory_file_content.plain_content()`.
2. Iterate over blocks in output order.
3. Skip blocks that the existing patch implementation treats as inactive, including empty SEARCH and SEARCH equal to REPLACE.
4. Wrap the current block in a one-block `StrPatch` and apply it to `working_content` with `PatchOp.apply()`.
5. If it succeeds and changes the content, retain the returned value as the next `working_content` and continue.
6. If it raises or leaves the content unchanged, report that block as the first actual failure and stop validating the field.
7. If all active blocks apply, return no validation error for the field.

This mirrors real patch execution without duplicating the patch handler's exact, fuzzy, line-based, marker-unescaping, or sequencing behavior inside `ExtractLoop`.

## Error Shape

The existing error fields remain, with these additions:

```json
{
  "uri": "viking://user/default/peers/conv-26/memories/profile.md",
  "page_id": 1,
  "field": "content",
  "block_index": 8,
  "search": "- Values friendship and compassion",
  "reason": "non_unique",
  "match_count": 2,
  "found_in_other_uris": []
}
```

`block_index` is one-based so it is easy to relate to model output.

`reason` is classified as:

- `non_unique` when the current working content contains the effective SEARCH more than once;
- `not_found` when it contains the effective SEARCH zero times and application makes no change;
- `not_applied` for any other unchanged result;
- `apply_error` for an exception that cannot be classified by the exact match count.

The effective SEARCH used for `match_count` is `unescape_markers(block.search)`, matching the exact-search preprocessing in the patch handler.

`match_count` is calculated against the current sequential `working_content`, not the original file. It is diagnostic metadata; `PatchOp.apply()` remains authoritative for whether a block succeeds.

`found_in_other_uris` continues to compare against each read file's `plain_content()`. It is most useful for `not_found`, but remains present for a stable repair-prompt schema.

## Failure and Retry Behavior

Only the first actual failure for a field is reported because real patch execution cannot reliably evaluate later blocks after an earlier block fails. The repair prompt continues to request a complete regenerated operations object.

The retry policy is unchanged:

- the first failed validation adds the repair instruction and grants one extra iteration;
- a second failed validation is logged but does not grant another repair iteration.

With accurate block diagnostics, the repair model receives the SEARCH it must change instead of an unrelated earlier block.

## Testing

Add focused tests to `TestExtractLoopPatchRepair`:

1. A valid first block followed by a duplicate second block reports the second block, `block_index = 2`, `reason = non_unique`, and `match_count = 2`.
2. The repair response makes the duplicate SEARCH unique and completes after one repair iteration.
3. A missing SEARCH reports `reason = not_found` and `match_count = 0`.
4. Sequential validation applies an earlier successful replacement before checking the next block.
5. All valid blocks complete without a repair retry.
6. Existing Markdown-link plain-content regressions continue to pass.

## Non-Goals

- Changing the number of allowed repair retries.
- Changing prompt wording beyond exposing the new structured fields.
- Reimplementing or simplifying `PatchOp` matching.
- Changing memory storage, rendered links, or link metadata.
- Reporting speculative errors in blocks after the first actual failure.
