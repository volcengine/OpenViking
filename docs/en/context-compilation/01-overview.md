# Context Compilation Overview

`ov compile` reads documents, notes, web pages, or session records from OpenViking and uses a Skill to organize them into a Wiki, knowledge graph, daily report, or other output stored in OpenViking.

## How it works

Compilation with an installed Skill needs:

- `--from`: one or more source directories or files.
- `--to`: the output directory.
- `--skill`: the URI of an installed Skill that defines the output's content and structure.

Use the optional `--instruction` to specify this run's scope, audience, language, emphasis, or date range.

The configured [Agent Runtime](../api/23-agent-runtime.md) runs the compilation; local deployments can use the built-in [VikingBot](../concepts/15-vikingbot.md). It reads the sources and Skill under the requesting user's identity, then organizes and writes content in a dedicated agent loop. The task runs asynchronously and returns a `task_id` for checking progress and results.

### Image sources and models

For PNG, JPEG, GIF, and WebP sources, the built-in VikingBot retains the original image, source URI, and content hash and delivers image evidence on demand. It does not compile image bytes as text. Choose a model that accepts images; tool-calling support alone does not imply vision support.

The VLM backend declares the image transport; provider and model names do not grant support. Anthropic protocols and OpenAI Codex use native multimodal tool results. OpenAI Chat Completions backends, including the Ark, Azure, Kimi, and GLM subclasses, keep tool results as text and attach images in the same request, linked by tool-call ID and image number.

Failure handling is layered: **a declared protocol with unknown model vision capability permits the normal image-bearing request**, without a model allowlist or an extra probe. An explicit provider rejection fails compilation. **An undeclared protocol/route or incomplete image delivery stops compilation**; images are never skipped to report success. An attempted request can incur failure latency and cost, and a successful request alone does not prove the model understood the image correctly.

LiteLLM's explicit `provider` takes priority. With `provider: "litellm"`, image inputs require a recognized `provider/model` route, such as `openai/future-vision-model` or `anthropic/claude-sonnet-4-5`. A bare model name or `api_base` alone does not declare an image protocol; name fragments such as `claude` and `qwen` cannot grant image transport support. Text requests retain legacy automatic detection. Existing LiteLLM image-compilation configurations relying on bare-name detection must add a protocol prefix.

LiteLLM checks the protocol adapter for its actual local route: OpenAI-compatible protocols (including DashScope, Moonshot, Zhipu `zai`, OpenRouter, NVIDIA NIM, and vLLM), Gemini/Vertex Gemini, Ollama, and Bedrock Converse use image messages; Anthropic protocols retain native tool results. The legacy Zhipu `zhipu/` prefix is normalized to LiteLLM's current `zai/` route. Capability checks do not invoke credential resolution or login. Text Completion APIs and unverified protocols continue to reject image evidence instead of falling back to binary text.

Image messages follow all tool results from the same turn. Conversion happens only at the provider request boundary, preserving stored evidence and conversation history. Every backend in a fallback or credential chain must support image evidence delivery; mixed chains containing an image-message transport use image messages throughout.

This adaptation adds no model request, download, or dependency, only local protocol metadata checks and small attribution labels. Image inputs still incur the model's image-token cost. Existing format, count, byte-budget, and source-hash checks remain in force. Image transport support does not imply that a particular model accepts images, especially for text-only models.

## Run it in one command

First import the sources and install the Skill using the [LLM Wiki example](02-llm-wiki.md), then run:

```bash
ov compile \
  --from viking://resources/research \
  --to viking://resources/research-wiki \
  --skill viking://agent/skills/llm-wiki \
  --instruction "Organize the research into a knowledge base the team can search"
```

The command returns a `cmp_...` task ID immediately. Use `ov task status <id>` to check progress and `ov task cancel <id>` to stop it. The full field reference, task lifecycle, and HTTP API are in the [Agent Runtime API](../api/23-agent-runtime.md).

## Use Web Studio

Studio provides a Compile list at `/compile`, a form at `/compile/new`, and task details at `/compile/tasks/<task_id>`. Select and preview a Skill, browse source directories, submit multiple sources, inspect or cancel a task, and open its output directory. The Filesystem terminal also supports `compile` and `task`; it can hand parameters to the form.

Task-list cursors are signed with a process-local key and bound to the caller and filters. Restarting the service invalidates old cursors; refresh the list to start again. A cursor from one replica is not portable to another. Pagination scans stored task records rather than using an index, so read cost grows with retained history.

For HTTP submission retries, reuse the same `Idempotency-Key` and request parameters; a changed request with the same key returns `409`. Recovery lasts only as long as the task record: completed/cancelled records expire 24 hours after their last update, failed records after 7 days. This is not permanent submission history or a cross-instance exactly-once guarantee. See [Agent Runtime](../api/23-agent-runtime.md) for submission recovery and [Tasks](../api/17-tasks.md) for task operations.

## Swap the Skill, get a different output

The Skill defines the output. The repository includes these examples; LLM Wiki and Knowledge Graph also include visualization scripts:

| Skill | Output shape | Good for | Example |
|-------|-------------|----------|---------|
| **LLM Wiki** | A set of interlinked Markdown pages (entity, concept, method…) plus a navigation `index.md` | A knowledge base that both people and agents can quickly search, navigate, and reuse | [LLM Wiki example](./02-llm-wiki.md) |
| **Knowledge Graph** | `entities/*.md` nodes plus a `relations.jsonl` edge file | Structured knowledge you traverse by entity, type, and relationship | [Knowledge Graph example](./03-knowledge-graph.md) |
| **Daily Report** | One `<YYYY-MM-DD>.md` page per date | Reconstructing "what actually happened each day" from conversations, sessions, messages, and task records | [Daily Report example](./04-daily-report.md) |
| **Knowledge Distillation** | Topic-organized high-level conclusion pages | Distilling cross-source findings, trends, and changes out of one or more knowledge bases | [Knowledge Distillation example](./05-knowledge-distillation.md) |

The first two examples also give the complete `ov` commands from **importing sources → adding the Skill → running compile → visualizing the output**, ending in an interactive HTML graph.

## Not only new artifacts: consolidating existing memories

Besides compiling source material into new knowledge artifacts with a Skill, `ov compile` has a special mode — set `--skill` to `memory` to consolidate an existing **memory** directory in place (dedup, merge, split, compact), without going through VikingBot. See [Memory Consolidation](./06-memory-consolidation.md).

## Prerequisites

- For the Skill-based examples, a running OpenViking service with a Compile Runtime configured. For local use, install the `openviking[bot]` dependencies and enable the built-in VikingBot with `--with-bot`. The `--skill memory` mode runs in-process without a Compile Runtime and takes no `--from`. The default endpoint is `http://localhost:1933`; remote use needs an API Key — see [Authentication](../guides/04-authentication.md). No service yet? Start with the [Quick Start](../getting-started/02-quickstart.md).
- The `ov` CLI configured with a connection (`~/.openviking/ovcli.conf`, or a file selected by `OPENVIKING_CLI_CONFIG_FILE`).
- Paths beginning with `examples/...` are relative to the repository. Download the [OpenViking repository](https://github.com/volcengine/OpenViking) and run the commands from its root.
- Python 3 for the visualization scripts. The [LLM Wiki script](https://github.com/volcengine/OpenViking/blob/main/examples/compile/graph-show/llm-wiki/wiki_graph.py) also requires the `openviking` Python package.

## Related docs

- [VikingBot concepts](../concepts/15-vikingbot.md) — the built-in Compile runtime
- [Agent Runtime API](../api/23-agent-runtime.md) — full reference for creating, inspecting, and cancelling Compile tasks
- [Skills API](../api/04-skills.md) — managing and customizing Skills
