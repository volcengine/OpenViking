# Add-resource temporary tree lock design

## Goal

Remove the redundant encrypted `.encrypt_stage` path-lock CRUD from Markdown
add-resource parsing while preserving cooperative write serialization and
encrypted atomic replacement.

## Scope

The change applies only to the request-unique `viking://temp/.../<generated-id>`
tree created by `MarkdownParser.parse_content`. Normal writes, non-temp writes,
and writes without an active outer handle retain the existing dual-path
`[final, encrypted-stage]` lock.

## Design

`VikingFS` exposes an async context manager for locking a concrete temp tree.
It validates that the URI belongs to the `temp` scope, converts it to the AGFS
path, acquires one tree lock, and always releases it on exit.

`MarkdownParser` materializes the generated temp root, enters that context, and
replays the whole layout with the returned handle. Text sections, copied image
bytes, and the image-mapping sidecar all receive the same handle. Each encrypted
child write still enters `LockContext` for its final path, but the lock manager
reuses the owned ancestor tree lock and the existing `VikingFS` optimization
does not add the deterministic `.encrypt_stage` lock.

The tree remains private to one parse request. Final resource publication and
semantic processing retain their existing locking behavior.

## Safety properties

- A non-temp URI is rejected by the temp-tree lock API.
- A child write cannot bypass locking merely because its URI is under `temp`;
  it must receive the live handle returned by the outer tree-lock context.
- The outer tree lock is released on normal completion and exceptions.
- Writes without a handle continue locking both final and staging paths.
- Encrypted writes remain whole-envelope writes followed by atomic replace.

## Verification

Unit tests first demonstrate that the current parser does not open a temp-tree
lock or propagate a handle. After implementation they verify handle propagation
to text, binary image, and mapping writes, rejection of non-temp roots, and
release on exceptions. Existing encrypted write-lock tests must remain green.

The runtime validation is the same 40-request wave used for the baseline:
20 `add-resource` plus 20 `content/write`, with sibling target directories and
`wait=false`. Success requires 20/20 HTTP success for both groups, no loss of
landed resources, queue drain to zero, and a material reduction in add-resource
`.encrypt_stage` lock CRUD and parse latency.
