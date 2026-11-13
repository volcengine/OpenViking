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

The package contains the project's LLM Wiki Skill. Compilation calls the model and writes output into a new directory. Use another target name for repeat runs.

## Submit compilation

```bash
ov compile --from viking://resources/onboarding-en-v1 \
  --to viking://resources/onboarding-wiki-en-v1 \
  --skill viking://agent/skills/llm-wiki \
  --instruction "Generate index.md and entity/lighthouse.md in English. Explain the isolated-network feedback pilot using the project, feedback, and meeting sources. Preserve full viking:// source URIs and identify undecided dates. Write only inside the target directory."
```

Record the returned `cmp_...` task ID and inspect progress:

```bash
ov task status <cmp_task_id>
```

Check every 5 seconds for up to 5 minutes initially. Read outputs after `completed`. If still running, check later or request cancellation with `ov task cancel <cmp_task_id>`. On failure, inspect the task error and Compile Runtime configuration.

After compilation, the target directory should contain an index and a project page. See the [sample Wiki page](/quickstart/wiki-output-en-v1.txt) for content organization and source citations, then follow the checks below to review your output.

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
