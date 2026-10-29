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
| 参考日常评测代码，通过 VikingBot 复现 Memory 评测流程 | [1. VikingBot 代码参考](#1-memory-评测代码参考vikingbot) |
| 复现官网公布的各种 Agent 框架的 LoCoMo 结果 | [2. Agent 接入评测](#2-复现官网-hermesopenclaw-等框架的-locomo-结果) |
| 基于 mem0 问答与 LLM-judge prompt 的单轮记忆问答（非 Agentic） | [3. mem0 单轮问答对照](#3-mem0-单轮问答对照实验) |
| 评测知识库检索与回答 | [4. RAG 评测](#4-rag-相关评测) |

## 开始前

**可以复用已有 OV 服务，包括生产实例；关键是不要让评测读写生产数据空间。** 使用独立测试 account 及其用户 API Key，确认导入、检索和删除权限仅覆盖测试数据（见[身份认证](../docs/zh/guides/04-authentication.md)）。具体风险是：

- Memory 评测会写入测试会话和记忆；RAG 默认 `all` / `del` 会递归删除当前身份可访问的资源根目录。混用生产数据既会污染结果，也可能误删数据；RAG 示例仅运行 `gen` / `eval`。
- 部分 Agent 脚本会修改本地配置、重启服务或恢复快照。测试 account 不能隔离这些进程/文件操作；相关步骤必须指向独立测试环境，不能作用于生产。
- 批量评测会增加服务和模型调用负载；复用生产服务时控制并发，准确比较时延/吞吐则优先使用独立测试实例，避免与线上流量互相干扰。

准备方法见[服务与模型配置](../docs/zh/getting-started/02-quickstart.md)、[CLI 连接配置](../docs/zh/getting-started/05-cli-setup.md)及各评测目录 README。

在仓库根目录、已激活的独立 Python 环境中执行：

```bash
uv pip install -e '.[benchmark,bot]' -e ./sdk/python
export ROOT="$PWD"
export PYTHONPATH="$ROOT:$ROOT/sdk/python${PYTHONPATH:+:$PYTHONPATH}"
export OPENVIKING_URL="http://127.0.0.1:1933"   # 替换为已有服务或测试实例地址
export DATA="$ROOT/benchmark/locomo/data/locomo10.json"  # 预先将原始 JSON 放到此处
export OPENVIKING_CONFIG_FILE="/absolute/path/to/ov.eval.conf"
export RUN="$ROOT/result/evaluation/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN"
```

导入和问答必须使用同一服务、account/user 与数据版本。`ovcli.conf` 管 HTTP 连接；`ov.conf` 管服务端模型和 VikingBot 配置。按 [VikingBot 配置指南](../docs/zh/guides/17-vikingbot.md)显式设置 `bot.ov_server.server_url`，确保与导入地址一致，避免误跑 standalone；问答模型由 `bot.agents` 配置或继承根级 `vlm`。密钥勿提交到仓库。

## 1. Memory 评测代码参考（VikingBot）

日常 Memory 评测使用[一键脚本](locomo/vikingbot/run_full_eval.sh)；论文复现或方案评测也可参考这一版评测代码实现。配置好上文环境及裁判凭据（`ARK_API_KEY` 或 `OPENAI_API_KEY`）后执行：

```bash
cd "$ROOT/benchmark/locomo/vikingbot"
./run_full_eval.sh 0 --parallel-import-sessions 1 \
  --parallel-run-eval 1 --parallel-judge 1
# 去掉位置参数 0 跑全量。
```

注意：使用隔离的测试数据空间；脚本默认重导且只保留最近 10 个实验目录，不要加 `--auto-commit`。

## 2. 复现官网 Hermes/OpenClaw 等框架的 LoCoMo 结果

[官网报告（2026-05-29）](https://blog.openviking.ai/post/openviking-benchmark-results/)公布的是 **Agent 原生记忆 vs Agent + OpenViking**，不能与日常 VikingBot 或第 3 节的 mem0 对照分数混用：

| Agent | 官网 Accuracy：原生 → OpenViking | 复现入口 |
| --- | --- | --- |
| OpenClaw | 24.20% → 82.08% | [操作说明](locomo/README.md)、[运行脚本](locomo/openclaw/run_full_eval.sh) |
| Hermes | 33.38% → 82.86% | [README](locomo/hermes/README.md)：`native` / `e2e` / `preingest` |
| Claude Code | 57.21% → 80.32% | [README](locomo/claudecode/README.md)：`prompted` / `sdk_iso` / `sdk_noiso` / `e2e` |

复现步骤：

1. 固定报告对应的 OpenViking / Agent 版本、模型、数据、插件配置、导入模式和该 Agent 自带的问答/裁判 prompt。`e2e` 与 SDK 预导入不是同一种实验，不要互换。
2. 按对应 README 分别准备原生记忆与 OpenViking 实验组，从空的测试状态导入相同历史；先跑一个样本，再跑全量。
3. 用**该 Agent 目录下**的 judge/stat 流程统计 Accuracy、耗时和 token；不要换用第 3 节的 mem0 裁判后仍称为官网结果。

例如 Hermes 的端到端接入 smoke（前置服务与模型按其 README 配好）：

```bash
cd "$ROOT/benchmark/locomo/hermes"
export LOCOMO_JSON="$DATA"
./run_full_eval.sh --suite native --sample 0 --count 1
./run_full_eval.sh --suite e2e --sample 0 --count 1
# 验证后去掉 --sample 0 --count 1 跑全量；如复现预导入方案，选 preingest。
```

官网文章没有逐项固定完整运行参数，当前脚本默认值也可能变化；上例演示接入流程，不能仅因选了 `e2e` 就声称精确复现已公布分数。保留所用 commit、配置和模式，结果从对应实验目录的逐题 CSV 与统计日志核对。

## 3. mem0 单轮问答对照实验

这是**单轮、非 Agentic 的记忆问答对照**。参考 [locomo/openviking](locomo/openviking/README.md) 及其 [问答与裁判 prompt](locomo/openviking/locomo_prompts.py)：对 session 提取后的记忆做一次检索、读取与可选重排，再生成答案，不由 Agent 多轮自主调用工具。它与日常 VikingBot 的 Agentic 评测独立，也不是把原始对话当资源的 RAG 评测；不要修改 `locomo/vikingbot` 来迁就这套口径。

**相比日常评测，本对照版本的 mem0 prompt 在 LLM-judge 判分上相对宽松**：例如接受列表答案的部分命中，以及日期/时长的一定误差；日常 VikingBot judge 没有这些显式放宽规则。两者分数不能直接比较，也不能将判分更宽松带来的高分视为记忆能力提升。

- 对照固定版本的 [mem0 LoCoMo prompts](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/benchmarks/locomo/prompts.py)，分别核对问答 prompt、judge prompt、参考日期、evidence 使用方式与结果解析，不能只换裁判模型。
- 固定问题集、回答/裁判模型、检索和上下文预算；记录本地模板与上游的差异，不宣称默认逐字一致。测试集答案/evidence 不得额外注入待测记忆或问答输入。
- 对照结果单独报告，不替代日常 VikingBot 基线，也不标为官网复现成绩。`locomo/mem0` 是 OpenClaw + mem0 插件评测，属于另一条接入路径。

## 4. RAG 相关评测

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
