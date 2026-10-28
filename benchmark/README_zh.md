# OpenViking 评测：先选对场景

[English](README.md) | 简体中文

> **测试 Memory 和测试 RAG 是两回事，不要用 RAG 测试记忆。**
>
> - **Memory**：历史对话 → session 写入与 commit → 提取持久记忆 → 按用户 / Peer 召回记忆 → 回答。
> - **RAG**：资料 → `add_resource` → 检索 `viking://resources` → 读取文档 → 回答。
>
> 即使使用同一份 LoCoMo 数据，把原始对话作为资源导入 `benchmark/RAG`，也没有评测记忆提取与跨会话保留能力。

| 你想做什么 | 走哪条路径 |
| --- | --- |
| 复现官网公布的 LoCoMo 结果 | [1. Agent 接入评测](#1-复现官网的-locomo-结果) |
| 对齐 mem0 的问答与 LLM-judge prompt 做 Memory 实验 | [2. Prompt 对齐实验](#2-对齐-mem0-prompt-的-memory-实验) |
| 评测知识库检索与回答 | [3. RAG 评测](#3-rag-相关评测) |

## 开始前

使用**专用测试服务、独立存储和独立 Agent 配置**，不要连接生产实例。准备方法见[服务与模型配置](../docs/zh/getting-started/02-quickstart.md)、[CLI 连接配置](../docs/zh/getting-started/05-cli-setup.md)及各评测目录 README。Agent 脚本可能修改配置、重启服务或操作快照，运行前先核对目标。

在仓库根目录、已激活的独立 Python 环境中执行：

```bash
uv pip install -e '.[benchmark,bot]' -e ./sdk/python
export ROOT="$PWD"
export PYTHONPATH="$ROOT:$ROOT/sdk/python${PYTHONPATH:+:$PYTHONPATH}"
export OPENVIKING_URL="http://127.0.0.1:1933"   # 替换为专用测试服务
export DATA="$ROOT/benchmark/locomo/data/locomo10.json"  # 预先将原始 JSON 放到此处
export OPENVIKING_CONFIG_FILE="/absolute/path/to/ov.eval.conf"
export RUN="$ROOT/result/evaluation/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN"
```

导入和问答必须使用同一服务、account/user 与数据版本。`ovcli.conf` 管 HTTP 连接；`ov.conf` 管服务端模型和 VikingBot 配置。按 [VikingBot 配置指南](../docs/zh/guides/17-vikingbot.md)显式设置 `bot.ov_server.server_url`，确保与导入地址一致，避免误跑 standalone；问答模型由 `bot.agents` 配置或继承根级 `vlm`。密钥勿提交到仓库。

## 1. 复现官网的 LoCoMo 结果

[官网报告（2026-05-29）](https://blog.openviking.ai/post/openviking-benchmark-results/)公布的是 **Agent 原生记忆 vs Agent + OpenViking**，不是第 2 节的 prompt 对齐实验：

| Agent | 官网 Accuracy：原生 → OpenViking | 复现入口 |
| --- | --- | --- |
| OpenClaw | 24.20% → 82.08% | [操作说明](locomo/README.md)、[运行脚本](locomo/openclaw/run_full_eval.sh) |
| Hermes | 33.38% → 82.86% | [README](locomo/hermes/README.md)：`native` / `e2e` / `preingest` |
| Claude Code | 57.21% → 80.32% | [README](locomo/claudecode/README.md)：`prompted` / `sdk_iso` / `sdk_noiso` / `e2e` |

复现步骤：

1. 固定报告对应的 OpenViking / Agent 版本、模型、数据、插件配置、导入模式和该 Agent 自带的问答/裁判 prompt。`e2e` 与 SDK 预导入不是同一种实验，不要互换。
2. 按对应 README 分别准备原生记忆与 OpenViking 实验组，从空的测试状态导入相同历史；先跑一个样本，再跑全量。
3. 用**该 Agent 目录下**的 judge/stat 流程统计 Accuracy、耗时和 token；不要换用第 2 节的 mem0 裁判后仍称为官网结果。

例如 Hermes 的端到端接入 smoke（前置服务与模型按其 README 配好）：

```bash
cd "$ROOT/benchmark/locomo/hermes"
export LOCOMO_JSON="$DATA"
./run_full_eval.sh --suite native --sample 0 --count 1
./run_full_eval.sh --suite e2e --sample 0 --count 1
# 验证后去掉 --sample 0 --count 1 跑全量；如复现预导入方案，选 preingest。
```

官网文章没有逐项固定完整运行参数，当前脚本默认值也可能变化；上例演示接入流程，不能仅因选了 `e2e` 就声称精确复现已公布分数。保留所用 commit、配置和模式，结果从对应实验目录的逐题 CSV 与统计日志核对。

## 2. 对齐 mem0 prompt 的 Memory 实验

**记忆评测使用 [locomo/vikingbot](locomo/vikingbot/)，不使用 `locomo/openviking` 的单次检索流程。** 执行链路为：

[import_to_ov.py](locomo/vikingbot/import_to_ov.py) 写入 session 并 commit → [run_eval.py](locomo/vikingbot/run_eval.py) 调用 `vikingbot chat` 召回记忆并回答 → [judge.py](locomo/vikingbot/judge.py) → [stat_judge_result.py](locomo/vikingbot/stat_judge_result.py)。默认按样本 Peer 隔离记忆，群聊模式则按 speaker Peer；导入与问答必须使用同一模式。

### mem0 prompt 对齐要求

参考固定版本的 [mem0 LoCoMo prompts](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/benchmarks/locomo/prompts.py)。**当前 VikingBot 脚本的默认问答和裁判 prompt 与该模板不同，也没有自动切换开关；跑通脚本不等于完成 mem0 对齐。**

要报告对齐实验，需要先在 VikingBot 评测中显式接入选定版本的问答与 judge prompt，并记录差异：

- 问答包括 system/user prompt、参考日期和记忆输入；固定模型、检索预算及工具策略。不能把 mem0 的检索结果变量直接当作 VikingBot 的用户问题。
- 裁判包括 prompt、模型参数、是否提供 evidence 和结果解析格式；只换裁判模型不等于对齐。测试集答案/evidence 不得作为额外信息注入待测记忆或问答输入。
- 两边使用相同问题和评分口径；`locomo/mem0` 是 OpenClaw + mem0 插件评测，也不能直接冒充上述 prompt 已对齐的对照。

本文只说明脚本入口与对齐条件，不新增 prompt 切换功能。下面先跑通 VikingBot 的**默认评测链路**。

### 运行与结果

将原始数据放在 `benchmark/locomo/data/locomo10.json`，配置好 VikingBot 和裁判凭据 `ARK_API_KEY` 或 `OPENAI_API_KEY`（前者优先），从一个完整样本开始：

```bash
cd "$ROOT/benchmark/locomo/vikingbot"
./run_full_eval.sh 0 --parallel-import-sessions 1 \
  --parallel-run-eval 1 --parallel-judge 1
# 验证后去掉位置参数 0 跑全量，并按服务限流调整并发。
```

一键脚本先检查配置、服务和身份，再执行导入、问答、评分、汇总；可能提示补充测试凭据后重跑。需自定义裁判模型时，分步调用 `judge.py --base-url ... --model ...`，不要假设一键脚本读取 `JUDGE_MODEL`。

- **不要用 `0 2` 这类单题导入或 `--retry-wrong` 作为正式评测**：它们会按测试题 evidence 选择历史，仅用于排障。示例只传 sample `0`，导入完整历史。
- 默认会重导；重复实验应使用新的测试存储，不能只换输出目录。`--skip-import` 仅用于确定对应记忆已经导入的情况。
- 不要加 `--auto-commit`，它会提交工作区改动；脚本默认只保留最近 **10** 个实验目录，重要结果先备份，按需设置 `--keep-runs`。

结果位于 `result/locomo/runs/<timestamp>/`：`locomo_result.csv`、`import_success.csv`、`summary.txt` 和 Bot 日志。先核对导入错误、Peer 和实际记忆召回，再看分数。统计排除 category 5，`Accuracy = CORRECT / (CORRECT + WRONG)`；同时报告未评分/错误数、有效题范围、耗时和 token，不能只报均值。

## 3. RAG 相关评测

使用 [RAG README](RAG/README_zh.md)，评测**资源文档的检索与回答**。当前配置包括 Qasper、SyllabusQA、FinanceBench；下面以 Qasper 为例：

```bash
cd "$ROOT/benchmark/RAG"
python scripts/prepare_dataset.py --dataset Qasper --num-docs 2 --seed 42
cp config/qasper_config.yaml "$RUN/rag.yaml"
```

编辑本轮 YAML 后再运行：配置 `llm.model` / `base_url` / `api_key_env_var`（密钥走环境变量）；设置 `execution.max_queries: 5` 和低并发；将 `paths.doc_output_dir`、`output_dir`、`log_file` 指向本轮独立目录。数据相对路径从 **`benchmark/RAG/`** 解析，不是从 YAML 所在目录解析。RAG 的回答/裁判模型由这个 YAML 指定。

```bash
python run.py --config "$RUN/rag.yaml" --step gen
python run.py --config "$RUN/rag.yaml" --step eval
```

- **始终显式指定 `gen` / `eval`。** 默认 `all` 和 `del` 会递归删除当前身份可访问的 `viking://resources`，不要在共享实例上使用。
- `max_queries` 只限制问答，不限制导入量；先抽样，正式实验再固定 split/问题集并扩大规模。
- 输出目录中查看 `generated_answers.json`、`qa_eval_detailed_results.json` 和 `benchmark_metrics_report.json`：核对题数、F1、evidence Recall、裁判分及耗时/token。这里的 Recall 不是标准文档 Recall@k，Qasper 的归一化 0–4 裁判分也不是 Memory 的二值 Accuracy。
- **不要用 `RAG/config/locomo_config.yaml` 的成绩代表记忆效果**：它把对话当资源，没有经过 session 记忆提取。

无论哪条路径，报告都应附数据/代码/prompt 版本、模型与预算、实际评分题数、逐题结果和成本口径。小样本通过只证明链路可用，不等于复现官网成绩。
