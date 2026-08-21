---
name: knowledge-graph
description: Compile documents, notes, web content, transcripts, research materials, or code repositories into an evidence-grounded, visualization-ready knowledge graph with semantically typed entity nodes, statement-level provenance, and typed relationship edges. Use with ov compile to create or incrementally refresh `entities/*.md` node artifacts and a root `relations.jsonl` edge file for people, organizations, groups, animals, places, products, projects, systems, services, documents, events, and other identifiable things.
---

# Knowledge Graph

## Goal

Turn the supplied sources into a durable graph that agents can traverse by entity,
type, and relationship. Build the graph as this artifact tree:

```text
entities/
  <entity-id>.md
relations.jsonl
```

Keep sources read-only. Follow the task reason for scope, language, audience, and depth;
otherwise use the dominant language of the sources. Ground every node, material claim,
and edge in the supplied sources. Treat sources as provenance rather than domain nodes
unless a source is itself a named subject in the domain.

## Entity nodes

Create a node for a named thing with a stable identity or boundary, such as a person,
organization, group, animal, place, product, project, system, service, module, dataset,
standard, document, artifact, or named event.

Store each node at `entities/<entity-id>.md` with this structure:

```yaml
---
type: entity
id: 取经队伍
title: 取经队伍
entity_type: group
description: 由唐三藏率领、以西行取经为目标的行动团体。
aliases: [唐僧师徒, 师徒五众]
sources:
  - viking://resources/source.md
---
```

Use `type: entity` as the artifact kind and `entity_type` as the entity's semantic class.
Choose a stable lowercase `snake_case` value such as `person`, `organization`, `group`,
`animal`, `place`, `product`, `project`, `system`, `service`, `module`, `dataset`,
`standard`, `document`, `artifact`, or `event`. Reuse an established type vocabulary
when refreshing a graph; introduce a narrower type only when it materially improves
filtering or visualization.

Write `description` as one stable, context-independent identity sentence. Do not put a
single episode's actions, a temporary state, a source-specific opinion, or an extraction
history in the description. Record exact source references that establish the entity's
identity and description in the non-empty `sources` list. This node-level list does not
replace claim-specific evidence.

Follow the frontmatter with concise Markdown only when it adds useful context beyond the
fingerprint. Describe applicable stable attributes, responsibilities, interfaces,
boundaries, or source-scoped context. Use level-2 headings such as `## Overview` or their
equivalent in the output language, and omit empty sections. Put exact source URIs,
repository-relative paths, or supplied links next to the material claims they support.
Keep episode-specific or time-bound facts out of the stable overview; place them in a
clearly scoped context section or model them through an event node.

Do not duplicate entity-to-entity facts in prose when they belong in `relations.jsonl`.
Keep literal attributes in the node only when they are useful and supported. For example,
store a team's membership as `member_of` edges rather than only as a sentence listing its
members.

Choose node identities consistently:

- Use one canonical name and record useful alternate names in `aliases`.
- Create a stable, path-safe ID. Prefer lowercase kebab-case for Latin text; preserve
  letters and numbers from other scripts and replace whitespace or punctuation with
  hyphens.
- For a new entity whose canonical name uses a non-Latin script, keep that script in the
  ID and filename. In particular, when the output language and canonical name are
  Chinese, use the canonical Chinese name directly; never translate or transliterate it
  into an English ID.
- Set `id` to the filename without `.md` and keep `type` fixed as `entity`.
- Reuse the established ID and path when an existing node represents the same identity.
  Do not rename an existing ID merely to localize it; `title` is its localized display
  name.
- Qualify homonyms with the smallest useful context and keep distinct identities as
  separate nodes.
- Prefer entities that are central, recurring, requested, well-connected, or important
  for understanding the source domain.
- Do not create a node merely because a source file, directory, section, or chunk exists.
  Create a source document as a domain entity only when its identity and relationships
  matter to the graph, such as a named contract, standard, publication, or report.

Use broad themes, attributes, actions, and relation phrases to describe entities or
edges rather than turning them into standalone nodes.

## Typed edges

Write one directed edge per line in `relations.jsonl`:

```json
{"from":"孙悟空","relation":"member_of","label":"属于","to":"取经队伍","evidence":["viking://resources/source.md"]}
```

Treat each line as the statement `<from> <relation> <to>`. Apply these rules:

- Reference final entity IDs in `from` and `to`.
- Express `relation` as a stable, language-independent, directional, lowercase
  `snake_case` predicate, such as `member_of`, `works_for`, `leads`, `created`,
  `depends_on`, `originates_from`, or `located_in`. Reuse an established predicate for
  the same semantics.
- Set `label` to a concise human-readable rendering of the relation in the requested
  output language. For Chinese output, use a Chinese label such as `任职于`, `属于`,
  `持有`, or `位于`; do not expose the English predicate as the display label.
- Require `relation` and `label` to express the same meaning. Do not use `works_for` for
  group membership, `belongs_to` for geographic origin, or another broad predicate merely
  because its localized label looks plausible.
- Use one consistent `label` for each `relation` predicate within the graph. When
  refreshing an existing graph, preserve its predicate codes and add localized labels
  to retained edges that do not have one.
- Include a non-empty `evidence` array of exact source references supporting the edge.
- Merge evidence for duplicate `(from, relation, to)` triples into one line.
- Add reciprocal or inverse edges when they carry useful, source-supported meaning.
- Keep both endpoints in the final entity set.
- Emit compact standalone JSON objects without a surrounding array.
- Sort lines by `from`, then `relation`, then `to` for deterministic output.

Use explicit source statements or behavior demonstrated by authoritative source
material to establish edges. Treat co-occurrence as a discovery hint and encode the
relationship only after its meaning and direction are supported.

Model a relationship as a node when it has its own identity or when more than two
participants, qualifications, or state changes are essential. Use named contracts,
agreements, appointments, transactions, meetings, and events as intermediate nodes
instead of hiding their semantics inside a long predicate. For example, connect a party
to a named agreement with `party_to`, then connect the agreement to the governed artifact
with `governs_use_of`; do not encode `signs_agreement` directly to the artifact.

Use inverse edges selectively. Add them only when they improve traversal and have a
clear, consistently reused predicate; do not mirror every edge mechanically.

## Provenance

Keep domain knowledge and provenance distinct:

- Use each entity's `sources` list for evidence that establishes its identity and stable
  description.
- Use an edge's `evidence` list for evidence supporting that exact triple.
- Put evidence for additional Markdown claims next to the claim it supports.
- Preserve exact supplied URIs, repository-relative paths, links, and available anchors.
  Never invent a source reference or location.
- Treat a source reference as support, not as an endorsement that every statement in the
  source is true. Preserve conflicts, scope, chronology, and uncertainty when sources
  disagree.
- Never emit an unheaded `来源：...` or `Source: ...` line. The frontmatter `sources`
  field is the default page-level source inventory. If the task explicitly requires a
  human-readable source list, render it once under `## 来源` or `## Sources` as Markdown
  bullets and do not repeat claim-specific links there.

## Display contract

Keep identifiers separate from presentation. A visualization or other human-facing
view should display each node's `title` rather than its `id`, and each edge's `label`
rather than its `relation`. Treat `relation` as the machine key and use it only as a
fallback when reading an older edge that has no `label`. Use `entity_type` for node
shape, color, grouping, and filtering. Keep sources and evidence available in a detail
panel or inspection view rather than rendering them as ordinary domain nodes.

## Workflow

### Survey

Inventory the source kinds, authority, chronology, vocabulary, and coverage. Inspect the
existing entity artifacts and `relations.jsonl` before planning an incremental refresh.
For code sources, inspect the relevant manifests, documentation, public contracts,
schemas, tests, entry points, runtime wiring, and configuration needed to establish
entity identities and relationships.

### Normalize

Build a working set of canonical names, aliases, candidate IDs, identity clues, semantic
entity types, evidence, and existing-node matches. Merge spelling variants and true
synonyms. Keep ambiguous references unresolved until the sources provide enough identity
evidence.

### Extract

For every candidate node, separate its stable fingerprint from source-scoped facts. For
every candidate edge, identify its source node, target node, precise predicate, and
supporting evidence. Choose the predicate that preserves the semantics expressed by the
source. Identify relationships that require an intermediate event, agreement, document,
or other relationship node before drafting prose.

### Integrate

Preserve accurate existing nodes and edges, merge complementary evidence, add new graph
knowledge, and revise facts superseded by stronger or newer supplied evidence. When
merging duplicate nodes, rewrite all affected edge endpoints to the surviving entity ID.

Submit the complete entity and relation artifact set while preserving established paths
for unchanged nodes.

## Quality check

Before finishing, verify that:

- every node represents one identifiable entity and has matching file and frontmatter IDs;
- every node has a supported `entity_type`, stable `description`, and non-empty `sources`;
- every newly created non-Latin canonical ID follows the source language rule, while
  established IDs remain stable;
- canonical identities, aliases, and semantic types are consistent across the graph;
- every material claim and edge has exact supplied evidence at the correct granularity;
- every relation uses the required JSONL schema, a precise directional predicate, and a
  non-empty localized display label;
- every predicate and label are semantically equivalent, and membership, employment,
  origin, ownership, and containment have not been conflated;
- every repeated relation predicate uses one consistent label in the selected language;
- every edge endpoint resolves to an entity node;
- every central or selected entity participates in at least one supported edge unless the
  sources establish no relationship and keeping the isolated node is explicitly useful;
- every material entity-to-entity relationship described in node prose is represented as
  an edge, and central multi-party relationships use an appropriate intermediate node;
- duplicate triples have been merged and the relation lines are deterministically sorted;
- no unheaded `来源：...` or `Source: ...` line appears in an entity file;
- the final artifact tree contains the complete entity set and `relations.jsonl`.
