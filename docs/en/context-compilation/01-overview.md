# Context Compilation Overview

`ov compile` turns the raw material scattered across OpenViking — documents, notes, web pages, transcripts, research files, code repositories — into structured, retrievable knowledge that people and agents can reuse over and over.

## How it works

You supply three things:

- **Where it comes from (`--from`)**: one or more source directories/files;
- **Where it goes (`--to`)**: the target directory for the output;
- **Which Skill to use (`--skill`)**: a spec describing what the output should look like.

Plus an optional **`--reason`**: extra instructions for this run — scope, audience, language, emphasis, or date range. The Skill defines *what shape* to compile into; `--reason` tells the agent *what you want this particular time* on top of that.

OpenViking does the rest. Compile is powered by [VikingBot](../concepts/15-vikingbot.md): once a task is accepted, VikingBot loads the Skill you named, reads the sources under your identity, and works through them autonomously in a dedicated **agent loop** — reading, distilling, organizing, and writing pages, much like hiring someone to turn a pile of material into a clean knowledge base and hand you back the finished result. The whole thing runs asynchronously: you can wait for it, or grab the `task_id` and move on.

In other words: **you provide the material and the goal, the agent does the actual work of organizing the knowledge.** 

## Run it in one command

```bash
ov compile \
  --from viking://resources/research \
  --to viking://resources/research-wiki \
  --skill viking://agent/skills/llm-wiki \
  --reason "Organize the research into a knowledge base the team can search" \
  --wait
```

`--wait` polls until the task reaches a terminal state; drop it to get a `cmp_...` task ID back immediately, then use `ov task status <id>` to check progress and `ov task cancel <id>` to stop it. Full field reference, task lifecycle, and HTTP API are in [VikingBot API → compile()](../api/24-vikingbot.md#compile).

## Swap the Skill, get a different output

Compile itself does not decide *what* to compile into — the Skill does. The same sources, paired with different Skills, produce completely different knowledge artifacts. Here are the example Skills we ship; the first two also come with a visualization script you can run as-is:

| Skill | Output shape | Good for | Example |
|-------|-------------|----------|---------|
| **LLM Wiki** | A set of interlinked Markdown pages (entity, concept, method…) plus a navigation `index.md` | A knowledge base that both people and agents can quickly search, navigate, and reuse | [LLM Wiki example](./02-llm-wiki.md) |
| **Knowledge Graph** | `entities/*.md` nodes plus a `relations.jsonl` edge file | Structured knowledge you traverse by entity, type, and relationship | [Knowledge Graph example](./03-knowledge-graph.md) |
| **Daily Report** | One `<YYYY-MM-DD>.md` page per date | Reconstructing "what actually happened each day" from conversations, sessions, messages, and task records | [Daily Report example](./04-daily-report.md) |
| **Knowledge Distillation** | Topic-organized high-level conclusion pages | Distilling cross-source findings, trends, and changes out of one or more knowledge bases | [Knowledge Distillation example](./05-knowledge-distillation.md) |

The first two examples also give the complete `ov` commands from **importing sources → adding the Skill → running compile → visualizing the output**, ending in an interactive HTML graph.

## Prerequisites

- A running OpenViking service with Bot enabled (`--with-bot`). The default endpoint is `http://localhost:1933`; remote use needs an API Key — see [Authentication](../guides/04-authentication.md). No service yet? Start with the [Quick Start](../getting-started/02-quickstart.md).
- The `ov` CLI configured with a connection (`~/.openviking/ovcli.conf` or `OPENVIKING_*` environment variables).
- Python 3 for the visualization scripts; the LLM Wiki script also uses the `openviking` Python package to read Wiki pages straight from the service.

## Related docs

- [VikingBot concepts](../concepts/15-vikingbot.md) — the runtime behind Compile
- [VikingBot API](../api/24-vikingbot.md) — full reference for `compile()` / `compile_status()` / `compile_cancel()`
- [Skills API](../api/04-skills.md) — managing and customizing Skills
