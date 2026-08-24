# Context Layers (L0/L1/L2)

OpenViking uses a three-layer information model to balance retrieval efficiency, navigation, and full-content fidelity.

## Overview

| Layer | Name | Storage | Default body limit | Purpose |
| --- | --- | --- | --- | --- |
| **L0** | Abstract | `.abstract.md` in a directory | 256 characters | Vector retrieval, quick filtering |
| **L1** | Overview | `.overview.md` in a directory | 4000 characters | Rerank, content navigation |
| **L2** | Detail | Original files and subdirectories | No uniform limit | Full content, on-demand loading |

L0 and L1 are **directory-level semantic sidecars**. They describe a directory; OpenViking does not create a matching L0/L1 sidecar for every ordinary file. File summaries are inputs aggregated into the containing directory's L1.

L0 and L1 are normally generated together, but either one may exist independently. For example, `mkdir()` initially creates only L0: it uses the directory name as the default body when `description` is omitted, or the provided description otherwise. A directory with `.abstract.md` but no `.overview.md` is therefore valid. Reads and vector rebuilds process only the levels that actually exist.

The body limits are configured by `semantic.abstract_max_chars` and `semantic.overview_max_chars`; the table shows their defaults. These limits apply to the Markdown body only and do not truncate sidecar metadata.

## L0: Abstract

L0 is the shortest representation of a directory, used for vector retrieval and quick relevance decisions.

```markdown
API authentication guide covering OAuth 2.0, JWT tokens, and API keys for secure access.
```

Semantic accessors return only the visible body:

```python
abstract = client.abstract(uri="viking://resources/docs/auth")
```

## L1: Overview

L1 provides a broader directory summary and navigation, supporting rerank and the decision to load L2.

```markdown
# Authentication Guide

This directory covers the primary API authentication methods.

## Quick Navigation

- `oauth.md`: OAuth 2.0 flow and examples
- `jwt.md`: token generation and validation
- `api-keys.md`: API key authentication
```

```python
overview = client.overview(uri="viking://resources/docs/auth")
```

L0 is extracted from the L1 body: the Brief Description paragraph after the H1 title and before the first `##` heading. YAML frontmatter is not part of this extraction.

## L2: Detail

L2 is the original or fully parsed content, loaded only when needed and retaining its source format and structure.

```python
content = client.read(uri="viking://resources/docs/auth/oauth.md")
```

## Directory Structure

A semantically processed directory commonly looks like this:

```text
viking://resources/docs/auth/
├── .abstract.md          # L0, hidden directory sidecar
├── .overview.md          # L1, hidden directory sidecar
├── oauth.md              # L2, full content
├── jwt.md                # L2, full content
└── api-keys.md           # L2, full content
```

Normal `ls` hides `.abstract.md` and `.overview.md`. The two files are not guaranteed to coexist, so callers should not assume every directory always has both sidecars.

## OKF Sidecar Format

New L0/L1 files use minimal OKF Markdown: YAML frontmatter followed by the visible Markdown body.

```markdown
---
directory: viking://resources/docs/auth/
source:
  kind: http
  uri: https://example.com/auth.pdf
generated_by:
  component: SemanticProcessor
  trigger: resource_ingest
freshness:
  total_entries: 3
  sampled_entries: 3
  unsampled_entries: 0
  pending_child_changes: 0
---

API authentication guide covering OAuth 2.0, JWT tokens, and API keys.
```

The initial metadata fields are:

| Field | Meaning |
| --- | --- |
| `directory` | URI of the directory represented by the sidecar |
| `source` | Optional import source, normally stored only on the import root |
| `generated_by` | Generator component and coarse trigger |
| `freshness` | Direct-child coverage and known pending changes |

Known fields are schema-validated. Unknown top-level fields and unknown nested keys inside known objects are silently dropped; they do not enter previews, embeddings, canonical writeback, or metadata-protection comparisons. Existing sidecars without frontmatter remain valid legacy Markdown. Malformed YAML, a missing required `directory`, or an invalid known-field type still fails explicitly.

## Behavior Across Read Surfaces

The same sidecar has different views depending on the access surface:

| Access | Returned content |
| --- | --- |
| `abstract()` / `overview()` | Markdown body only |
| `find` and search/rerank previews | Markdown body only |
| `ls output=agent` and tree agent output | Markdown body only |
| Direct `read(".../.abstract.md")` | Raw frontmatter and body |
| Normal `ls` | Hidden sidecars are omitted |

Parent-summary generation also consumes only child L0 bodies. `source`, `generated_by`, and `freshness` are not included in summarization prompts.

## Embedding Metadata Whitelist

L0/L1 embedding input contains the body plus explicitly whitelisted metadata. The initial whitelist contains only `directory`:

```markdown
---
directory: viking://resources/docs/auth/
---

API authentication guide covering OAuth 2.0, JWT tokens, and API keys.
```

`source`, `generated_by`, `freshness`, and unknown fields are excluded. Normal vectorization and admin `vectors_only` reindex use the same policy so rebuilding the index does not change retrieval input. The L1 rerank scalar remains the plain L1 body.

## Freshness and Stable Sampling

`freshness` counts a directory's **direct children**, not its entire recursive subtree:

- `total_entries`: total direct files and direct subdirectories contributing to directory semantics.
- `sampled_entries`: direct entries used for this summary.
- `unsampled_entries`: direct entries not sampled, with `sampled + unsampled = total`.
- `pending_child_changes`: known changed direct entries not yet reflected in the current body.

When the direct-entry count exceeds `semantic.overview_sample_limit` (32 by default), OpenViking uses deterministic, order-preserving stable sampling. Repeated refreshes of an unchanged tree choose the same sample, avoiding noisy body rewrites and Git diffs.

`pending_child_changes > 0` means the body is still readable but is known to lag behind lower-level changes. A successful parent refresh resets the value to 0 as part of the new coverage metadata.

Currently, every successful resource/skill semantic task schedules the next parent refresh and marks the parent pending before enqueue, continuing to the namespace-root boundary.

> **TODO: control bubbling frequency with freshness**
>
> The current implementation attempts to bubble after every successful resource/skill semantic task, even when the newly generated child summary is unchanged. This is not the intended final scheduling policy. A future implementation should use `freshness` to coalesce, threshold, or time-window parent refreshes—for example by considering `pending_child_changes`, sampling coverage, direct-child change volume, and recent refresh state. The goal is to reduce repeated refreshes and upward write amplification in hot directories while preserving eventual consistency.

## Write Protection

L0/L1 bodies can be updated through public `write` / `batch_write`, while metadata remains protected:

- The target sidecar must already exist; public APIs cannot directly create a new `.abstract.md` or `.overview.md`.
- A body-only request inherits the stored metadata and writes canonical OKF.
- A full-OKF request must preserve all known metadata; changing a protected field fails.
- Unknown metadata fields are silently dropped.
- `append` appends only to the body, never to frontmatter.
- A body update rebuilds only the directory levels that currently exist and does not regenerate semantics, preventing the newly written body from being overwritten.

## Generation

SemanticProcessor processes directories bottom-up:

```text
file summaries → leaf L1 → leaf L0 → parent directories → namespace boundary
```

Child-directory L0 bodies are aggregated into the parent L1. Memory directories use the same SemanticProcessor entry point, although the current parent-bubbling path applies only to resource/skill. Multimodal files first produce text summaries, which contribute to the containing directory's L0/L1 like other file summaries; OpenViking does not create per-file L0/L1 sidecars for each image, audio file, or video.

## Best Practices

| Scenario | Recommended layer |
| --- | --- |
| Quick relevance check | L0 |
| Understand a directory's scope | L1 |
| Detailed information extraction | L2 |
| Build initial LLM context | L1, then load L2 when needed |
| Inspect sidecar source or freshness | Read the raw sidecar directly |

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Types](./02-context-types.md) - Context types
- [Viking URI](./04-viking-uri.md) - URI specification
- [Context Extraction](./06-extraction.md) - L0/L1 generation flow
- [Retrieval Mechanism](./07-retrieval.md) - Retrieval details
