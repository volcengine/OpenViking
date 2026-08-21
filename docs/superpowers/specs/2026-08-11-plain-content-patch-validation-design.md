# Plain-Content Patch Validation Design

## Problem

The memory read tool exposes `MemoryFile.plain_content()` to the extraction model, so generated SEARCH text does not contain rendered Markdown links. The extract-loop pre-validation currently applies that SEARCH text to `MemoryFile.content`, which may contain rendered links. Valid patches are therefore rejected before the real updater runs, even though both updater paths already apply patches to `plain_content()`.

## Design

Align pre-validation with the established read and apply contract:

- Use `operation.old_memory_file_content.plain_content()` as the target text passed to `PatchOp.apply`.
- Search other previously read files through `memory_file.plain_content()` when populating `found_in_other_uris`.
- Keep persisted Markdown link rendering and link metadata unchanged. The updater continues to apply patches to plain content and render links during serialization.

No retry policy, patch algorithm, prompt, or storage format changes are included.

## Error Handling

Existing validation behavior remains unchanged: an exception or unchanged result is reported as a repairable patch error. Only the text representation used for validation changes.

## Testing

Add a regression test with stored content containing a rendered Markdown link and a patch whose SEARCH text matches the plain visible form. Assert that pre-validation returns no patch errors. Existing invalid-patch tests continue to prove that genuinely missing SEARCH text still triggers one repair attempt.
