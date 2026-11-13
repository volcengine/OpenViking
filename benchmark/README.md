# Evaluating OpenViking: Choose the Right Scenario

English | [简体中文](README_zh.md)

> **Memory evaluation and RAG evaluation are different. Do not use RAG to evaluate memory.**
>
> - **Memory**: conversation history → session messages and commit → persistent memory extraction → recall by user / Peer → answer.
> - **RAG**: source material → `add_resource` → search `viking://resources` → read documents → answer.
>
> Even with the same LoCoMo dataset, importing raw conversations as resources through `benchmark/RAG` does not evaluate memory extraction or retention across sessions.

| Goal | Path |
| --- | --- |
| Reproduce the Memory evaluation workflow through VikingBot using our routine evaluation code | [1. VikingBot code reference](#1-memory-evaluation-code-reference-vikingbot) |
| Reproduce the published LoCoMo results for different Agent frameworks | [2. Agent integration evaluation](#2-reproduce-published-locomo-results-for-hermesopenclaw-and-other-frameworks) |
| Single-turn memory QA with mem0 QA and LLM-judge prompts (non-agentic) | [3. mem0 single-turn comparison](#3-mem0-single-turn-qa-comparison) |
| Evaluate knowledge-base retrieval and answering | [4. RAG evaluation](#4-rag-evaluation) |

## Before You Start

**You can reuse an existing OV service, including production; the key is to keep evaluation reads and writes out of production data.** Use a separate test account and its user API key, and verify that import, search, and deletion permissions cover only test data (see [authentication](../docs/en/guides/04-authentication.md)). The specific risks are:

- Memory evaluation writes test sessions and memories. RAG's default `all` / `del` recursively deletes the resource root accessible to the current identity. Mixing production data can contaminate results or delete real data; the RAG examples use only `gen` / `eval`.
- Some Agent scripts modify local configuration, restart services, or restore snapshots. A test account does not isolate these process/filesystem operations; those steps must target a separate test environment, never production.
- Batch evaluation adds load to the service and model providers. Limit concurrency when reusing production, and prefer an isolated instance for meaningful latency/throughput comparisons without interference from live traffic.

See [server/model setup](../docs/en/getting-started/02-quickstart.md), [CLI configuration](../docs/en/getting-started/05-cli-setup.md), and the individual benchmark READMEs for preparation.

From the repository root, with an isolated Python environment activated:

```bash
uv pip install -e '.[benchmark,bot]' -e ./sdk/python
export ROOT="$PWD"
export PYTHONPATH="$ROOT:$ROOT/sdk/python${PYTHONPATH:+:$PYTHONPATH}"
export OPENVIKING_URL="http://127.0.0.1:1933"   # Replace with an existing service or test instance
export DATA="$ROOT/benchmark/locomo/data/locomo10.json"  # Place the original JSON here first
export OPENVIKING_CONFIG_FILE="/absolute/path/to/ov.eval.conf"
export RUN="$ROOT/result/evaluation/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN"
```

Import and QA must use the same server, account/user, and dataset version. `ovcli.conf` controls HTTP connections; `ov.conf` controls server models and VikingBot. Follow the [VikingBot configuration guide](../docs/en/guides/17-vikingbot.md) to explicitly set `bot.ov_server.server_url` to the import target and avoid accidentally running standalone. The answer model comes from `bot.agents` or inherits the root `vlm`. Do not commit credentials.

## 1. Memory Evaluation Code Reference (VikingBot)

Use the [one-command runner](locomo/vikingbot/run_full_eval.sh) for routine Memory evaluation. Its implementation can also serve as a reference for reproducing papers or evaluating alternative approaches. Configure the environment above and judge credentials (`ARK_API_KEY` or `OPENAI_API_KEY`), then run:

```bash
cd "$ROOT/benchmark/locomo/vikingbot"
./run_full_eval.sh 0 --parallel-import-sessions 1 \
  --parallel-run-eval 1 --parallel-judge 1
# Remove positional argument 0 for a full run.
```

Use an isolated test data space: the runner re-imports data and retains only the latest 10 experiment directories by default. Do not add `--auto-commit`.

## 2. Reproduce Published LoCoMo Results for Hermes/OpenClaw and Other Frameworks

The [official report (May 29, 2026)](https://blog.openviking.ai/post/openviking-benchmark-results/) compares **Agent native memory against Agent + OpenViking**, and should not be mixed with routine VikingBot scores or the mem0 comparison in section 3:

| Agent | Published accuracy: native → OpenViking | Reproduction entry point |
| --- | --- | --- |
| OpenClaw | 24.20% → 82.08% | [Instructions](locomo/README.md), [runner](locomo/openclaw/run_full_eval.sh) |
| Hermes | 33.38% → 82.86% | [README](locomo/hermes/README.md): `native` / `e2e` / `preingest` |
| Claude Code | 57.21% → 80.32% | [README](locomo/claudecode/README.md): `prompted` / `sdk_iso` / `sdk_noiso` / `e2e` |

To reproduce:

1. Pin the report's OpenViking / Agent versions, models, data, plugin configuration, import mode, and Agent-specific QA/judge prompts. End-to-end capture and SDK pre-ingestion are different experiments; do not substitute one for the other.
2. Follow the corresponding README to prepare native-memory and OpenViking groups, importing identical history into fresh test state. Start with one sample, then run the full dataset.
3. Use **that Agent directory's** judge/stat pipeline for accuracy, latency, and tokens. Switching to the mem0 judging protocol in section 3 changes the experiment; it is no longer the published setup.

For example, a Hermes integration smoke test, after configuring its services and models:

```bash
cd "$ROOT/benchmark/locomo/hermes"
export LOCOMO_JSON="$DATA"
./run_full_eval.sh --suite native --sample 0 --count 1
./run_full_eval.sh --suite e2e --sample 0 --count 1
# Remove --sample 0 --count 1 for a full run; use preingest for a pre-ingested setup.
```

The report does not pin every runtime parameter, and current script defaults may change. This example demonstrates the integration flow; selecting `e2e` alone does not establish an exact reproduction of the published score. Save the commit, configuration, and mode, and check each run's per-question CSV and statistics log.

## 3. mem0 Single-Turn QA Comparison

This is a **single-turn, non-agentic memory QA comparison**. The workflow in [locomo/openviking](locomo/openviking/README.md) and its [QA/judge prompts](locomo/openviking/locomo_prompts.py) searches memories extracted from sessions once, reads and optionally reranks them, then generates an answer. It does not let an Agent choose tools over multiple turns. It is separate from routine VikingBot agentic evaluation and from RAG over raw conversation resources; do not change `locomo/vikingbot` to conform to this protocol.

**The mem0 prompt version used in this comparison has more permissive LLM-judge criteria than routine evaluation**: for example, it accepts partial list answers and tolerances in dates/durations that the routine VikingBot judge does not explicitly allow. Scores are not directly comparable; a higher score from more permissive judging does not establish better memory quality.

- Compare with a pinned version of the [mem0 LoCoMo prompts](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/benchmarks/locomo/prompts.py). Check the QA and judge prompts, reference date, evidence policy, and result parser separately; changing only the judge model is insufficient.
- Fix the questions, answer/judge models, retrieval budget, and context budget. Record local/upstream template differences rather than claiming byte-for-byte equivalence by default. Never inject test answers/evidence annotations as extra information into memories or QA input.
- Report these results separately. They neither replace the routine VikingBot baseline nor reproduce the published scores. `locomo/mem0` evaluates the OpenClaw + mem0 plugin and is another integration path.

## 4. RAG Evaluation

Use the [RAG README](RAG/README.md) to evaluate **resource-document retrieval and answering**. Existing configurations include Qasper, SyllabusQA, and FinanceBench. For example:

```bash
cd "$ROOT/benchmark/RAG"
python scripts/prepare_dataset.py --dataset Qasper --num-docs 2 --seed 42
cp config/qasper_config.yaml "$RUN/rag.yaml"
```

Edit the run's YAML before continuing: set `llm.model` / `base_url` / `api_key_env_var` (credentials through the environment), `execution.max_queries: 5`, and low concurrency. Point `paths.doc_output_dir`, `output_dir`, and `log_file` to isolated directories for this run. Relative data paths resolve from **`benchmark/RAG/`**, not the YAML's directory. This YAML selects the RAG answer and judge model.

```bash
python run.py --config "$RUN/rag.yaml" --step gen
python run.py --config "$RUN/rag.yaml" --step eval
```

- **Always specify `gen` / `eval` explicitly.** The default `all`, and `del`, recursively delete the `viking://resources` tree accessible to the current identity. Do not use them on a shared instance.
- `max_queries` limits QA, not ingestion. Sample the data first, then fix the split/question set before scaling up.
- Inspect `generated_answers.json`, `qa_eval_detailed_results.json`, and `benchmark_metrics_report.json` for counts, F1, evidence Recall, judge scores, latency, and tokens. This Recall is not standard document Recall@k; Qasper's normalized 0–4 judge score is not Memory's binary Accuracy.
- **Do not use results from `RAG/config/locomo_config.yaml` as memory-quality results**: that path treats conversations as resources and does not run session memory extraction.

For every path, report data/code/prompt versions, models and budgets, actual graded counts, per-question results, and cost definitions. A successful smoke test proves the flow works, not that the published results have been reproduced.
