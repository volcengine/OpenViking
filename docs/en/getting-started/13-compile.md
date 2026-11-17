# Context compilation

Turn the four Lighthouse sources into a small wiki, then open its body page to verify evidence and unknowns.

## About LLM Wiki

LLM Wiki uses a language model to organize information scattered across documents, meeting notes, and feedback into an interlinked Markdown knowledge base. Related information about a project or concept can be brought together on one page, reached through an index, and linked back to its original sources for readers and agents to inspect, verify, and reuse.

For example, the Lighthouse project's goals, feedback process, and preparation tasks are spread across four sources. Compilation can bring them together on a project page, identify undecided dates and budget, and cite the sources for key facts. Readers can start with that page and follow its references for details.

In OpenViking, the LLM Wiki Skill defines how to organize the material, and the Compile Runtime reads the sources and generates pages. The wiki remains available for reading and retrieval; check key conclusions against the original sources. The following walkthrough compiles the Lighthouse materials.

## Prepare sources and a Skill

Use a service with a configured Compile Runtime. The local default is VikingBot installed with `openviking[bot]` and enabled with `--with-bot`; see the [quickstart](02-quickstart.md) for model and connection setup.

Complete the source-import step in [Resource retrieval](08-resources.md#prepare-the-sources), and confirm `viking://resources/onboarding-en-v1` is readable. Download [Wiki Skill v1](/quickstart/llm-wiki-v1.zip) and install it into a shared skills directory you can write:

```bash
ov add-skill ./llm-wiki-v1.zip -p viking://agent/skills --wait
ov read viking://agent/skills/llm-wiki/SKILL.md
```

The package contains the project's LLM Wiki Skill. Compilation calls the model and writes output into a new directory.

## Submit compilation

Create a named configuration with the sources, target, Skill, and optional `--instruction` and `--args`.

```bash
ov compile create --compile-name lighthouse-wiki \
  --from viking://resources/onboarding-en-v1 \
  --to viking://resources/onboarding-wiki-en-v1 \
  --skill viking://agent/skills/llm-wiki \
  --instruction "Generate index.md and entity/lighthouse.md in English. Explain the isolated-network feedback pilot using the project, feedback, and meeting sources. Preserve full viking:// source URIs and identify undecided dates. Write only inside the target directory."
```

Create the configuration once. Submit each run by name:

```bash
ov compile --compile-name lighthouse-wiki
```

Execution reuses the configuration's saved `from`, `to`, `skill`, and `instruction`.

Record the returned `cmp_...` task ID and inspect progress:

```bash
ov task status <cmp_task_id>
```

Check every 5 seconds for up to 5 minutes initially. Read outputs after `completed`. If still running, check later or request cancellation with `ov task cancel <cmp_task_id>`. On failure, inspect the task error and Compile Runtime configuration.

After compilation, the target directory should contain an index and a project page. See the [sample Wiki page](/quickstart/wiki-output-en-v1.txt) for content organization and source citations, then follow the checks below to review your output.

The first run is full. Subsequent runs use the last successful baseline. The name is required, case-sensitive, and unique for the current user within an account; it contains Han characters, ASCII letters, digits, underscores, or hyphens. ASCII-only names allow up to 64 characters. A name containing any Han character allows up to 32 characters in total. The first character cannot be a hyphen. Different users can use the same name. See [configuration and execution](../api/23-agent-runtime.md) for schedules and invocation timestamps.

The direct `ov compile --from … --to … --skill …` form is deprecated and still performs a one-off full compile.

## Opt in to runtime conflict comparison

Save `"global_conflict_detection": true` in the configuration's `args`, for example
`--args '{"global_conflict_detection":true}'` on `ov compile create`. The value must
be a JSON boolean. Missing values remain false, preserving the current executor and
behavior for ordinary tasks. This option is intended for a resource Wiki with the
conflict-aware LLM Wiki Skill; it does not apply to memory consolidation or Skill
package generation.

Enabled VikingBot tasks use complex execution independently of the service default.
They retain Map, Shuffle and Reduce, and recall target candidates when the target has
content. The same worker receives format/protection errors within its existing tool
budget. Runtime publication preserves human records and their visible anchors.
Custom Skills still own factual comparison and evidence quality.

The effective option is frozen in the task. Changing it forces a full named run.
A run without an explicit complete-processing receipt does not advance the incremental
baseline. This receipt describes structural input processing and acknowledged delivery;
it does not prove complete semantic coverage, exhaustive conflict recall or completed
indexing. Check source citations and index state separately. Candidate bounds and
concurrency defaults remain unchanged; additional comparison can increase model work.

## Open the body page

```bash
ov tree viking://resources/onboarding-wiki-en-v1
ov read viking://resources/onboarding-wiki-en-v1/index.md
ov read viking://resources/onboarding-wiki-en-v1/entity/lighthouse.md
```

If filenames differ, use the tree and index to locate the body page. Check the isolated network, offline installer and local directory, Friday feedback exports, template and rehearsal, and undecided dates and budget. Open cited sources to verify each conclusion.

| Observation | Check |
| --- | --- |
| Runtime unavailable | Bot dependencies and the server's compile runtime configuration |
| Source or Skill missing | URI, read permissions, and processing results |
| Evidence missing | Actual read records and citations; refine the instruction and retry with a new target |

Completion means a readable, navigable body page with evidence for key facts. See the [LLM Wiki example](../context-compilation/02-llm-wiki.md) for structures, page types, and visualization. To clean up, identify the demo target in Studio, preserve any needed output, and delete the directory.

### Stage-specific Skill attachments

A Skill may declare `compile_stage_resources` in its YAML frontmatter:

```yaml
compile_stage_resources:
  map: [references/map.md]
  reduce: [references/conflicts.md]
```

The complex Markdown pipeline authorizes and snapshots these bounded attachments
once before planning, then injects the declared rules into generation workers. A Map draft omits
final-generation rules only when conflict mode guarantees a later Reduce;
ordinary Map keeps the union because it can publish directly or lose Reduce
after stage review. Missing or invalid required files fail before model planning; no shorter
fallback drops rules. Existing Skills without this declaration retain their full
text. Standalone agents must follow the Skill's reference links themselves.
The bundled LLM Wiki separates source-draft instructions from final conflict
format/human-protection rules, and uses file tools inside isolated workers while
retaining its script for standalone writing. No extra adjudication model is added. Planner instructions and stage-review
revisions remain intact; attachment selection never substitutes a fixed Reduce task.
Content-free call receipts identify the injected attachment paths and characters.

When global conflict detection is enabled, semantic routing uses bounded one-hop
comparison groups (at most 50 drafts) so a hub claim can be compared with related
pages that serve different reader purposes. It does not close transitive chains
or promise exhaustive recall. Normal routing, candidate windows, concurrency and
model selection defaults remain unchanged. Whole-Skill snapshots already cover
user Skill attachments. After upgrading bundled system Skills, start a full run
before relying on an old named incremental baseline; system-Skill asset versions
are not currently part of that baseline's identity.

Reserved conflict comments are excluded from Markdown summary inputs and routing
views before truncation. Raw source/draft bytes, citations, hashes and Reduce
evidence remain intact; existing stored summaries are not automatically rebuilt.
