# OpenViking 评测：先选对场景

> **测试 Memory 和测试 RAG 是两回事，不要用 RAG 测试记忆。**
>
> - **Memory**：历史对话 → session 写入与 commit → 提取持久记忆 → 检索 `viking://user/.../memories` → 回答。
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
uv pip install -e '.[benchmark]' -e ./sdk/python
export ROOT="$PWD"
export PYTHONPATH="$ROOT:$ROOT/sdk/python${PYTHONPATH:+:$PYTHONPATH}"
export OPENVIKING_URL="http://127.0.0.1:1933"   # 替换为专用测试服务
export DATA="/absolute/path/to/locomo10.json"  # 两条 Memory 路径使用原始 JSON
export RUN="$ROOT/result/evaluation/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN"
```

导入和问答必须使用同一服务、account 与数据版本。`ovcli.conf` 管 HTTP 连接；服务端 `ov.conf` 管提取与索引模型；第 2 条路径的本地问答进程还需要 `ov.conf` 中的 `vlm` 和可选 `rerank`。可用 `OPENVIKING_CLI_CONFIG_FILE` / `OPENVIKING_CONFIG_FILE` 指定独立配置。密钥勿提交到仓库。

## 1. 复现官网的 LoCoMo 结果

[官网报告（2026-05-29）](https://blog.openviking.ai/post/openviking-benchmark-results/)公布的是 **Agent 原生记忆 vs Agent + OpenViking**，不是下面第 2 条路径的单次检索成绩：

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

目标是控制**问答方式和评分口径**，观察提取出的记忆是否有效，而不是测 Agent 的工具调用。使用 [locomo/openviking](locomo/openviking/README.md)：

`import_to_ov.py` 提取记忆 → `run_eval.py` 单次检索并回答 → `judge.py` → `stat_judge_result.py`。

### 对齐哪些内容

参考 mem0 官方 [memory-benchmarks LoCoMo prompts](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/benchmarks/locomo/prompts.py)，本仓库对应实现是 [locomo_prompts.py](locomo/openviking/locomo_prompts.py)：

- **问答**：`get_answer_generation_prompt`，使用检索后的记忆及参考日期；固定回答模型、候选数、重排、上下文预算和记忆顺序。当前问答模板有标点差异，本地也没有上游可选的 `user_profile` 注入；严格对齐时应固定两边 prompt 版本，mem0 侧不启用该额外输入，不能把“口径对齐”说成逐字相同。
- **裁判**：默认 `get_judge_prompt` / `get_judge_prompt_with_evidence` 与上述固定版本的 mem0 宽松模板一致；**不要加 `--strict-prompt`**。本地 CSV 有 `evidence_text` 时自动带 evidence，mem0 runner 需设置 `--with-evidence` 对齐；若不带 evidence，双方都不能提供。测试集标注的参考答案/evidence 只用于评分，不得作为额外信息注入待测记忆或问答输入。
- **实验条件**：同一批问题、同一回答/裁判模型与参数，分别导入各自的记忆系统。`locomo/mem0` 是 OpenClaw + mem0 插件评测，不能直接当作这里的单次检索对照。

### 运行

先验证完整样本中的一题；导入阶段不要按测试题 evidence 裁剪历史。单独工作目录也用于隔离导入脚本的 `result/.ingest_record.json`：

```bash
mkdir -p "$RUN/memory"
cd "$RUN/memory"
python "$ROOT/benchmark/locomo/openviking/import_to_ov.py" \
  --input "$DATA" --openviking-url "$OPENVIKING_URL" \
  --sample 0 --parallel-samples 1 \
  --success-csv ./result/import_success.csv --error-log ./result/import_errors.log
python "$ROOT/benchmark/locomo/openviking/run_eval.py" "$DATA" \
  --openviking-url "$OPENVIKING_URL" --output ./result/answers.csv \
  --sample 0 --question-index 0 --threads 1 \
  --single-search-context-limit 50 --single-search-rerank-limit 10 \
  --single-search-max-context-chars 30000 --debug-print-model-input
```

核对导入错误日志、实际生成的 memories、CSV 中的 prompt 和检索轨迹。以上 50 / 10 / 30000 是示例预算，不是 mem0 默认值；两边需显式对齐。`--single-search-rerank-limit 0` **不能关闭重排**，无重排对照需关闭本地 `rerank` 配置并检查轨迹。

通过安全方式设置裁判凭据 `ARK_API_KEY` 或 `OPENAI_API_KEY`（前者优先），并设置模型和地址后评分：

```bash
export JUDGE_BASE_URL="https://your-provider.example/v1"
export JUDGE_MODEL="your-judge-model-id"
python "$ROOT/benchmark/locomo/openviking/judge.py" \
  --input ./result/answers.csv --provider openai \
  --base-url "$JUDGE_BASE_URL" --model "$JUDGE_MODEL" --parallel 1
python "$ROOT/benchmark/locomo/openviking/stat_judge_result.py" \
  --input ./result/answers.csv
```

全量时去掉导入和问答的 `--sample 0`，以及问答的 `--question-index 0`，使用新的答案文件；同一轮可复用已完成导入，换服务/提取配置则从独立空存储和新实验目录重来。LoCoMo 按数组下标映射用户，导入后不能重排数据。修改答案或裁判配置后重新评分，避免默认跳过旧 `result`。

结果写回答案 CSV，汇总为同目录 `summary.txt`。统计排除 category 5，`Accuracy = CORRECT / (CORRECT + WRONG)`；同时报告未评分/错误数和各类别结果，不能只报均值。

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
