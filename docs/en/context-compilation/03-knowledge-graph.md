# Example: Knowledge Graph

Compile a set of sources into an **evidence-grounded, visualization-ready** knowledge graph: semantically typed entity nodes, statement-level provenance, and typed directed relationship edges. The output is this artifact tree:

```text
entities/
  <entity-id>.md      # one file per node; frontmatter carries type/id/title/entity_type/description/sources
relations.jsonl       # one directed edge per line
```

Each edge is a compact JSON line, readable as the statement `<from> <relation> <to>`:

```json
{"from":"sun-wukong","relation":"member_of","label":"belongs to","to":"pilgrimage-team","evidence":["viking://resources/source.md"]}
```

`relation` is a stable, language-independent machine predicate (`member_of`, `leads`, `located_in`…), `label` is its localized display name, and `entity_type` drives node color, shape, and filtering in the visualization. The graph refreshes incrementally: existing nodes and edges are preserved, evidence is merged, and new knowledge is appended.

Skill source: [examples/compile/ov-compile-skills/knowledge-graph](https://github.com/volcengine/OpenViking/tree/main/examples/compile/ov-compile-skills/knowledge-graph) · Visualization script: [examples/compile/graph-show/knowledge-graph](https://github.com/volcengine/OpenViking/tree/main/examples/compile/graph-show/knowledge-graph)

## Step 1: Prepare the sources

```bash
ov add-resource ./journal-to-the-west --to viking://resources/journal --wait
ov ls -r viking://resources/journal
```

## Step 2: Add the Skill

```bash
ov add-skill examples/compile/ov-compile-skills/knowledge-graph --wait
ov skills list
# → viking://agent/skills/knowledge-graph
```

## Step 3: Run compile

```bash
ov compile \
  --from viking://resources/journal \
  --to viking://resources/journal-kg \
  --skill viking://agent/skills/knowledge-graph \
  --reason "Extract characters, places, artifacts and their relationships into a traversable graph" \
  --wait
```

Drop `--wait` to get a `task_id` back immediately, then:

```bash
ov task status cmp_01abc      # progress and final result
ov task cancel cmp_01abc      # cooperative cancel
```

## Step 4: Inspect the output

```bash
ov tree viking://resources/journal-kg
ov read viking://resources/journal-kg/relations.jsonl
ov read viking://resources/journal-kg/entities/sun-wukong.md
```

## Step 5: Visualize it as an interactive graph

Unlike the LLM Wiki script, `knowledge_graph.py` reads from a **local directory** (it needs both `entities/` and `relations.jsonl` on disk). So download the output first, then generate the HTML.

Download the whole artifact tree. `ov get` downloads one file at a time; combine it with `ov ls -r -s` to pull every path:

```bash
SRC="viking://resources/journal-kg"
DST="./journal-kg"
mkdir -p "$DST"
ov ls -r -s "$SRC" | while read -r uri; do
  # only download files (entities/*.md and relations.jsonl), skip directories
  case "$uri" in
    */entities|"$SRC") continue ;;
  esac
  rel="${uri#$SRC/}"
  mkdir -p "$DST/$(dirname "$rel")"
  ov get "$uri" "$DST/$rel"
done
```

> `ov get` requires the local target path to not exist yet, so clear the old directory (`rm -rf ./journal-kg`) before re-downloading.

Confirm the local layout is correct:

```bash
find ./journal-kg          # should show entities/*.md and relations.jsonl
```

Generate the interactive HTML:

```bash
python examples/compile/graph-show/knowledge-graph/knowledge_graph.py \
  ./journal-kg \
  -o journal-kg.html \
  --title "Journey to the West Knowledge Graph"
```

Open `journal-kg.html` in a browser. The script validates first — `relations.jsonl` must be valid JSON, every entity file must have a stable `id` and `title`, and both ends of every edge must resolve to an entity node — and fails with the offending line if not, so it doubles as a quality check on the output. Nodes are colored and shaped by `entity_type`, edges show their localized `label`, and clicking a node reveals that entity's body, aliases, and sources.

## Related docs

- [Context Compilation Overview](./01-overview.md)
- [LLM Wiki example](./02-llm-wiki.md)
- [VikingBot API → compile()](../api/24-vikingbot.md#compile)
