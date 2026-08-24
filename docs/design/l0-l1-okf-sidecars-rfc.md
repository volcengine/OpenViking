# RFC: Upgrade L0/L1 Semantic Sidecars to OKF Markdown

Status: Draft

## Summary

OpenViking today stores built-in context-layer summaries as directory-level Markdown sidecars:

- `.abstract.md` for L0
- `.overview.md` for L1

This RFC proposes keeping those filenames and the visible Markdown body, while upgrading their internal document format to minimal OKF-style Markdown: YAML frontmatter metadata followed by the summary body.

The change makes L0/L1 sidecars more self-describing without changing the public context-layer model described in `docs/zh/concepts/03-context-layers.md`.

## Background

OpenViking uses a three-layer context model:

| Layer | Current file | Role |
| --- | --- | --- |
| L0 | `.abstract.md` | short summary for fast filtering and vector retrieval |
| L1 | `.overview.md` | directory overview for navigation and rerank |
| L2 | source files and subdirectories | full content loaded on demand |

The current implementation treats `.abstract.md` and `.overview.md` as generated hidden sidecars. `SemanticProcessor` / `SemanticDagExecutor` generate them bottom-up, and the vector pipeline maps those two filenames to `ContextLevel.ABSTRACT` and `ContextLevel.OVERVIEW`.

## Goals

- Keep the existing L0/L1 file names and directory-level sidecar convention.
- Add a small structured metadata block to L0/L1 documents.
- Exclude metadata from embedding by default.
- Preserve current preview behavior: `find`, `ls`, tree, and agent previews should show only the visible summary body unless explicitly reading the file.
- Make metadata available through direct `read` / `get` access to the sidecar files.
- metadata is protected from directly `write`.
- Leave room for a future metadata embedding whitelist for index-able fields.

## Non-Goals

- Do not change L0/L1 into per-file sidecars.
- Do not expose hidden sidecars in normal directory listings.
- Do not redesign semantic generation prompts, metadata is hidden for re-generation.
- Do not make all Markdown frontmatter globally invisible to embedding.
- Do not require OKF parsing for ordinary user-authored Markdown resources.

## Proposed Format

L0 and L1 remain UTF-8 Markdown files with `.md` suffixes. Each generated file starts with YAML frontmatter:

### L0 Example Across Surfaces

Use one directory as the running example:

```text
viking://resources/images_2/
```

#### Raw Storage

The stored `.abstract.md` keeps metadata and body together:

```markdown
---
directory: viking://resources/images_2/
source:
  kind: http
  uri: http://demo.com/demo.pdf
generated_by:
  component: SemanticProcessor
  trigger: resource_ingest
freshness:
  total_entries: 161
  sampled_entries: 32
  unsampled_entries: 129
  pending_child_changes: 0
---

This directory contains a collection of visual assets including screenshots, diagrams, logos, and QR codes related to a context database for AI agents, AI coding assistant tools, and cloud-based AI model management.
```

#### Preview Reads

`abstract()`, `ls output=agent`, tree agent output, and `find` previews use the body only:

```markdown
This directory contains a collection of visual assets including screenshots, diagrams, logos, and QR codes related to a context database for AI agents, AI coding assistant tools, and cloud-based AI model management.
```

Normal `ls` still hides `.abstract.md` unless hidden files are explicitly requested.

#### Full Reads

`read()` / `get()` of the sidecar file return the raw stored document, including frontmatter and body. This is the path for callers that intentionally need metadata.

#### Embedding Text

Embedding uses the body plus whitelisted metadata. The initial whitelist contains `directory`, so the text sent to the embedder is equivalent to:

```text
---
directory: viking://resources/images_2/
---

This directory contains a collection of visual assets including screenshots, diagrams, logos, and QR codes related to a context database for AI agents, AI coding assistant tools, and cloud-based AI model management.
```

`source`, `generated_by`, and `freshness` are intentionally absent from this embedding text.

#### Regeneration Input

When a parent summary is regenerated from this L0, the semantic pipeline uses the body only:

```text
This directory contains a collection of visual assets including screenshots, diagrams, logos, and QR codes related to a context database for AI agents, AI coding assistant tools, and cloud-based AI model management.
```

Metadata is not prompt input for re-summarization. `freshness` may still be updated out of band to indicate that the stored body is stale relative to child changes.

L1 uses the same storage and access rules. Its body is longer and navigation-oriented, but metadata handling is identical.

The metadata block is intentionally narrower than general OKF wiki pages. L0/L1 are generated context abstractions, so metadata should only carry machine-facing fields that are not already available from file attributes such as `stat`, and not repeat the visible summary itself.

The initial metadata should stay small and deterministic. Timestamps are intentionally omitted at first to avoid rewriting sidecars when semantic content has not changed.

Existing L0/L1 content length limits apply to the Markdown body only. Metadata is outside those limits and should not be truncated by summary-size enforcement.

## Metadata Fields

The initial metadata set should stay small:

| Field | Purpose | Notes |
| --- | --- | --- |
| `directory` | Directory represented by this L0/L1 sidecar | Initial embedding whitelist field |
| `source` | Optional original source, when known | Top-level import root only unless a nested source boundary exists |
| `generated_by` | Component and coarse trigger that created or updated the sidecar | Operational metadata, not content provenance |
| `freshness` | Child counts, sampling coverage, and known pending child changes | A freshness signal; not a replacement for semantic refresh |

`freshness` counts direct child entries, not the total recursive subtree size. Files and child directories share the same counters because both contribute one direct input to the parent summary. For large directories, it should distinguish total input size from the subset actually read for summary generation. A directory with many entries may record `total_entries: 161`, `sampled_entries: 32`, and `unsampled_entries: 129`. `freshness.pending_child_changes > 0` means the body is still readable, but known to lag behind lower-level changes.

Sampling should be deterministic for a stable tree so repeated refreshes do not rewrite sidecars unnecessarily. The first policy can be simple: summarize all direct children up to a threshold, and for larger directories use a bounded sample that preserves useful ordering and diversity. Freshness metadata should make that choice visible without forcing the body to enumerate unsampled files.

Metadata should not duplicate information already available through `stat`, such as file name, file size, mode, modified time, or lock state.

## Embedding Behavior

The embedding input for generated L0/L1 sidecars should be the visible Markdown body plus explicitly whitelisted metadata fields.

The initial whitelist includes `directory`, because the directory URI helps retrieval connect a summary to its location in the context tree. Operational and provenance fields remain excluded by default, including `source`, `generated_by`, and `freshness`.

Conceptually:

```text
raw sidecar bytes
  -> parse OKF frontmatter
  -> body_markdown
  -> append whitelisted metadata fields (`directory` initially)
  -> embedding text
```

This keeps the semantic vector space focused on user-facing summaries and stable location context, while avoiding accidental ranking changes from source URLs, generator details, or freshness counters.

## Read and Preview Semantics

Direct file reads of sidecar files remain raw: callers receive frontmatter plus body.

Semantic accessors return body-only content. Preview surfaces also use body-only content:

- `find` result summaries
- `ls output=agent`
- tree output in agent mode
- search/rerank preview snippets

Normal `ls` behavior remains unchanged: hidden sidecars are not listed unless the caller explicitly asks for hidden entries.

## Compatibility

Existing sidecars without frontmatter remain valid. Readers should treat them as legacy Markdown and return the full content as body.

Writers should emit OKF-formatted sidecars after the feature lands. There is no need for an eager migration job. Existing directories migrate naturally when semantic refresh rewrites their L0/L1 files.

The current L0 extraction rule should continue to operate on the L1 body, not the raw OKF document. This preserves the current convention that L0 is derived from the brief paragraph before the first `##` section in L1.

## Implementation Sketch

Introduce a small semantic-sidecar document helper near the existing sidecar write/read code:

- `render_abstract_overview(level, dir_uri, body, metadata) -> str`
- `parse_abstract_overview(raw) -> {metadata, body}`
- `body_for_preview(raw) -> str`
- `body_for_embedding(raw, whitelist=()) -> str`

Use it in these paths:

| Area | Required change |
| --- | --- |
| Sidecar writeback | Render OKF frontmatter before writing `.abstract.md` / `.overview.md` |
| Source propagation | Carry optional source metadata to the import root sidecars when available |
| Generation metadata | Record the component and trigger that created or updated each sidecar |
| Freshness accounting | Write direct child input counters during semantic generation |
| Large directory sampling | Record total direct children, sampled children, and unsampled count when generation uses a bounded sample |
| Deferred changes | Increment pending child-change counters when filesystem changes do not immediately refresh the parent summary |
| `abstract()` / `overview()` | Strip OKF frontmatter before returning semantic accessor content |
| Incremental summary reuse | Parse `.overview.md` body before extracting file summaries |
| L0 derivation | Extract abstract from L1 body |
| Vectorization | Enqueue the body plus explicitly whitelisted metadata for L0/L1 embeddings |
| Preview formatting | Keep using semantic accessor/body-only content |

The parser should be tolerant: valid YAML object frontmatter is metadata; missing frontmatter means legacy body; malformed frontmatter should not silently enter embeddings. For generated sidecars, malformed frontmatter should be treated as a processing error.

Size-limit enforcement should parse the sidecar and apply existing L0/L1 limits only to the body before rendering metadata back around it. Metadata size should remain bounded by field policy rather than by the summary body limit.

## Test Plan

Format and compatibility:

- Unit-test rendering and parsing of OKF sidecars.
- Verify legacy sidecars without frontmatter still read as body-only content.
- Verify malformed generated sidecar frontmatter fails loudly instead of being embedded as body text.
- Verify ordinary user-authored Markdown frontmatter is not affected by semantic-sidecar parsing.

Read and preview behavior:

- Verify `abstract()` and `overview()` return body-only content.
- Verify direct `read_file()` returns raw frontmatter plus body.
- Verify `ls output=agent`, tree agent output, and `find` previews do not include metadata fields.
- Verify normal `ls` still hides `.abstract.md` / `.overview.md` unless hidden files are requested.

Metadata generation:

- Verify optional `source` metadata is preserved on the generated import root and not repeated in nested directories unless a nested source boundary exists.
- Verify `generated_by` records the component and trigger for generated sidecars.
- Verify L0/L1 size limits truncate only the body and preserve metadata.
- Verify direct writes cannot mutate protected metadata independently from semantic sidecar generation.

Freshness and sampling:

- Verify freshness counters match direct child entries used for generation.
- Verify large directories record deterministic sampling coverage, including total, sampled, and unsampled direct entries.
- Verify repeated refreshes on an unchanged large directory produce the same sample metadata and do not rewrite sidecars unnecessarily.
- Verify deferred child changes update `pending_child_changes` without changing the visible summary body.
- Verify a completed parent refresh resets `pending_child_changes` and updates freshness coverage.

Semantic reuse and embedding:

- Verify L0 extraction ignores frontmatter in L1.
- Verify incremental refresh can reuse summaries from an OKF-formatted `.overview.md`.
- Verify regeneration prompts use body-only child summaries and do not include metadata.
- Verify vectorization text for `.abstract.md` and `.overview.md` includes whitelisted `directory` metadata.
- Verify vectorization text excludes `source`, `generated_by`, and `freshness`.

## Decisions

- Do not add a dedicated `read_metadata()` API in the first version. Direct `read()` / `get()` of the sidecar file is enough for callers that intentionally need metadata.
- Use the metadata embedding policy described above: `directory` is included initially; `source`, `generated_by`, and `freshness` are excluded.
- Include OKF metadata in Git diffs as-is. Body-only diff rendering can be a future viewer feature, not part of the storage format.

## Future Work: Freshness-Aware Parent Bubbling

The current implementation schedules a parent refresh after every successful resource/skill semantic task, even when the newly generated child summary is unchanged. It marks the parent `pending_child_changes` before enqueue so freshness remains accurate, but unconditional bubbling is not the intended final scheduling policy.

Future work should use freshness state to control bubbling frequency. Candidate inputs include `pending_child_changes`, sampling coverage, direct-child change volume, whether the child L0 body actually changed, and recent parent-refresh state. The scheduler may coalesce changes, apply thresholds, or use a time window to reduce repeated refreshes and upward write amplification in hot directory trees while preserving eventual consistency.
