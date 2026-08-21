# Example: LLM Wiki

Compile a set of heterogeneous sources into a Karpathy-style, evidence-grounded, interlinked **LLM Wiki**: every page has one clear retrieval purpose, opens with a direct summary, uses consistent terminology, makes relationships explicit, keeps evidence close to the claims it supports, and is fronted by an `index.md` navigation page.

The Skill picks the smallest page type that matches each page's retrieval purpose:

| Page type | Use for |
|-----------|---------|
| `entity` | A named thing with a stable identity (person, organization, product, project, system, dataset, standard, event…) |
| `concept` | A reusable idea, mechanism, pattern, protocol, or mental model |
| `method` | A reusable procedure with prerequisites, ordered steps, and a verifiable outcome |
| `comparison` | Two or more subjects evaluated side by side on explicit dimensions |
| `analysis` | A cross-source conclusion tied to a clear question |
| `summary` | A faithful digest of one source (only when `--reason` explicitly asks for it) |

`entity` and `concept` are the defaults; the others are promoted only when they pass their stricter tests. The result is a knowledge base, not a source-by-source pile of summaries.

Skill source: [examples/compile/ov-compile-skills/llm-wiki](https://github.com/volcengine/OpenViking/tree/main/examples/compile/ov-compile-skills/llm-wiki) · Visualization script: [examples/compile/graph-show/llm-wiki](https://github.com/volcengine/OpenViking/tree/main/examples/compile/graph-show/llm-wiki)

## Step 1: Prepare the sources

If the material is not in OpenViking yet, import it. Use `ov add-resource` for directories, `ov write` for a single file:

```bash
# Import a directory as a source
ov add-resource ./my-research --to viking://resources/research --wait

# Or write a single file
ov mkdir viking://resources/research
ov write viking://resources/research/notes.md \
  --from-file ./notes.md --mode create --wait
```

Confirm the source is in place:

```bash
ov ls -r viking://resources/research
```

## Step 2: Add the Skill

Install the LLM Wiki Skill. By default it lands in your user-private skills namespace; use `-p viking://agent/skills` to make it shared across the team:

```bash
ov add-skill examples/compile/ov-compile-skills/llm-wiki --wait
```

Find the installed Skill URI:

```bash
ov skills list
# → viking://agent/skills/llm-wiki  (or viking://user/<you>/skills/llm-wiki)
```

## Step 3: Run compile

```bash
ov compile \
  --from viking://resources/research \
  --to viking://resources/research-wiki \
  --skill viking://agent/skills/llm-wiki \
  --reason "Organize into a team-searchable Wiki, keeping the source of every claim" \
  --wait
```

- `--from` can be repeated or comma-separated to pass multiple sources at once.
- The `--to` directory is created automatically if it does not exist.
- Add `-o json` for machine-readable output; drop `--wait` to avoid blocking and poll with the returned `task_id`:

```bash
ov task status cmp_01abc      # progress and final result
ov task cancel cmp_01abc      # cooperative cancel
```

## Step 4: Inspect the output

When compile finishes, the target directory holds a Markdown knowledge base. Read the navigation page first, then drill in:

```bash
ov tree viking://resources/research-wiki
ov read viking://resources/research-wiki/index.md
```

Typical layout (page type maps to directory):

```text
research-wiki/
├── index.md            # navigation entry, type index
├── entity/
│   └── <title>.md
├── concept/
│   └── <title>.md
├── method/…  comparison/…  analysis/…
```

## Step 5: Visualize it as an interactive graph

`wiki_graph.py` connects **directly to the OpenViking service** to read the Wiki pages (no local download needed), colors pages by type, links them by their cross-references, and produces a standalone interactive HTML:

```bash
python examples/compile/graph-show/llm-wiki/wiki_graph.py \
  viking://resources/research-wiki \
  -o research-wiki-graph.html \
  --title "Research Knowledge Base"
```

Open `research-wiki-graph.html` in a browser. Nodes are pages (colored by `entity`/`concept`/`method`…), edges are links between pages, and clicking a node shows its body.

Connection settings resolve the same way as `ov`: command-line arguments → `OPENVIKING_*` environment variables → `~/.openviking/ovcli.conf`. Pass them explicitly for a remote service:

```bash
python examples/compile/graph-show/llm-wiki/wiki_graph.py \
  viking://resources/research-wiki \
  --url https://openviking.example.com \
  --api-key "$OPENVIKING_API_KEY" \
  -o research-wiki-graph.html --title "Research Knowledge Base"
```

Pass multiple Wikis to draw them on the same graph for comparison:

```bash
python examples/compile/graph-show/llm-wiki/wiki_graph.py \
  viking://resources/wiki-a viking://resources/wiki-b \
  -o combined.html --title "Two Knowledge Bases Side by Side"
```

## Related docs

- [Context Compilation Overview](./01-overview.md)
- [Knowledge Graph example](./03-knowledge-graph.md)
- [VikingBot API → compile()](../api/24-vikingbot.md#compile)
