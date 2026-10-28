# OpenViking RAG 与 Memory 评测指南

本文介绍如何用仓库中的脚本评测 OpenViking：准备数据、导入、运行问答、评分，再检查质量与成本。先跑小样本验证链路，再做完整实验；下面的参数是起跑配置，不是最优配置或性能承诺。

## 1. 选择评测路径

| 目标 | 入口 | 实际评测的链路 |
| --- | --- | --- |
| 知识库 RAG：从资料中找到依据并回答 | [RAG](RAG/README_zh.md) | 文档 → `add_resource` → `find(viking://resources)` → 读取上下文 → 生成答案 → 评分 |
| 长对话 Memory：跨会话保留并使用信息 | [LoCoMo / OpenViking](locomo/openviking/README.md) | 对话 → session 消息与 commit → 记忆提取 → 按用户检索 memories → 回答 → 评分 |
| 更长历史中的 Memory 问答 | [LongMemEval / OpenViking](longmemeval/openviking/README.md) | 导入 haystack sessions → 记忆提取 → 按用户检索 → 回答 → 按问题类型统计 |

RAG 支持 Qasper、SyllabusQA、FinanceBench 和 LoCoMo。**`RAG` 中的 LoCoMo 把对话当作资源文档，并不经过 session 记忆提取；不能把它的结果称为 Memory 评测结果。** 本文用 Qasper 演示 RAG，用 LoCoMo 演示 Memory。

上面两条 Memory 路径都是单次检索问答，不等于 Agent 的多轮工具调用效果。需要评测真实 Agent 接入时，参见 [LoCoMo Agent 入口](locomo/README.md)、[Hermes](locomo/hermes/README.md)、[Claude Code](locomo/claudecode/README.md)；需要看任务执行与经验学习，参见 [tau2 VikingBot](tau2/vikingbot/README.md) 和 [tau2 训练流程](tau2/train/README.md)。不要直接对比不同 Agent、模型或上下文预算下的分数。

## 2. 公共准备

### 2.1 环境与独立服务

从**仓库根目录**开始，在本 worktree 的独立 Python 环境中安装依赖；已有环境可跳过 `uv venv`：

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e '.[benchmark]' -e ./sdk/python

export ROOT="$PWD"
export PYTHONPATH="$ROOT:$ROOT/sdk/python${PYTHONPATH:+:$PYTHONPATH}"
export RUN_ROOT="$ROOT/result/evaluation/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_ROOT"
git rev-parse HEAD > "$RUN_ROOT/commit.txt"
```

源码构建的工具链要求见[贡献指南](../docs/repository/CONTRIBUTING_CN.md)。`PYTHONPATH` 保证后续切换工作目录时仍使用本 worktree 的代码。

准备一个**专用的 OpenViking 测试服务和独立存储目录**，不要连接生产或日常使用的实例。服务启动方法见[快速开始](../docs/zh/getting-started/02-quickstart.md)，模型和存储设置见[配置指南](../docs/zh/guides/01-configuration.md)。记录服务端与评测端各自的代码版本，避免只升级脚本而误认为服务端也已升级。

在评测终端设置连接；替换示例路径和地址后执行：

```bash
export OPENVIKING_URL="http://127.0.0.1:1933"
export OPENVIKING_CLI_CONFIG_FILE="/absolute/path/to/ovcli.eval.conf"
export OPENVIKING_CONFIG_FILE="/absolute/path/to/ov.eval.conf"
curl --fail "$OPENVIKING_URL/health"
```

- `ovcli.eval.conf`：HTTP 连接及身份配置（`url`、`api_key`、`account` 等），见 [CLI 配置](../docs/zh/getting-started/05-cli-setup.md)。导入与问答必须连接同一个服务、同一个 account；启用鉴权时确认测试凭据允许访问对应用户空间，见[身份认证](../docs/zh/guides/04-authentication.md)。
- `ov.eval.conf`：OpenViking 模型配置。服务端使用 Embedding / VLM 提取和索引；Memory 评测进程还会从本地配置读取 `vlm` 生成答案，以及可选的 `rerank` 做重排。**只配远程服务的模型，不足以运行本地 Memory 问答。**
- RAG 的回答和裁判模型来自 benchmark YAML 的 `llm`，不是服务端 VLM。Memory 裁判模型由 `judge.py` 的参数单独指定。
- `health` 成功仅说明服务可响应；后面的小样本导入和问答才验证模型、权限与数据链路。

模型调用、数据下载和完整评测可能产生费用，先设低并发。密钥通过环境变量或本地配置提供，不提交配置原文、数据、答案和日志；分享结果前脱敏。

### 2.2 隔离每轮实验

- `RUN_ROOT`、结果文件和服务端存储都应能对应到同一轮实验；换模型、数据或服务时新建实验目录。
- Memory 导入脚本还在**当前工作目录**下的 `result/` 保存隐藏导入记录。仅更换 `--success-csv` 不足以隔离缓存，所以下文为每种评测单独 `cd` 到实验目录。
- 本地导入记录不证明新服务上已有数据。更换服务/存储后不要沿用旧记录；反过来，仅清理本地记录也不会删除服务端记忆。
- `--force-ingest` 会重新导入，不是重置服务。只有明确需要重导时才使用；不要用它掩盖身份或存储不一致。

## 3. RAG：以 Qasper 为例

### 3.1 下载与抽样

```bash
cd "$ROOT/benchmark/RAG"
python scripts/prepare_dataset.py --dataset Qasper --num-docs 2 --seed 42
```

原始数据放在 `raw_data/Qasper/`，抽样结果及 `sampling_metadata.json` 放在 `datasets/Qasper/`。正式实验可以使用 `--full`，或固定同一份子集；保存数据版本、文件校验和、抽样参数及实际问题列表。

| 数据集 | 配置模板 | 模板中的数据路径（相对 `benchmark/RAG/`） |
| --- | --- | --- |
| Qasper | [qasper_config.yaml](RAG/config/qasper_config.yaml) | `datasets/Qasper/` |
| SyllabusQA | [syllabusqa_config.yaml](RAG/config/syllabusqa_config.yaml) | `datasets/SyllabusQA/` |
| FinanceBench | [financebench_config.yaml](RAG/config/financebench_config.yaml) | `datasets/FinanceBench/financebench_open_source.jsonl`，并保留准备脚本生成的配套文件 |
| LoCoMo（资源模式） | [locomo_config.yaml](RAG/config/locomo_config.yaml) | `datasets/Locomo/locomo10.json` |

准备脚本的 `--dataset` 大小写与上表一致。不要直接使用占位模板 `config/config.yaml`。Qasper 目录可能包含多个 split，适配器会读取其中的 JSON；如果报告某个特定 split 的成绩，应让 `dataset_path` 指向该 split 文件，并固定相应语料范围，不能把混合目录的成绩当作官方测试集成绩。

### 3.2 生成本轮配置

以下命令从 Qasper 模板生成独立配置，不修改仓库模板。运行前通过安全方式设置 `EVAL_LLM_API_KEY`，并把 `EVAL_LLM_MODEL`、`EVAL_LLM_BASE_URL` 换成可用模型及其兼容接口地址：

```bash
: "${EVAL_LLM_API_KEY:?请先设置 EVAL_LLM_API_KEY}"
export EVAL_LLM_MODEL="your-model-id"
export EVAL_LLM_BASE_URL="https://your-provider.example/v1"

python - <<'PY'
import os
from pathlib import Path
import yaml

root = Path(os.environ["ROOT"])
run = Path(os.environ["RUN_ROOT"]) / "rag"
run.mkdir(parents=True, exist_ok=True)
config = yaml.safe_load((root / "benchmark/RAG/config/qasper_config.yaml").read_text())
config["execution"].update(max_workers=1, ingest_workers=1, max_queries=5,
                           retrieval_topk=5, skip_ingestion=False)
config["paths"].update(doc_output_dir=str(run / "processed_docs"),
                       output_dir=str(run / "output"),
                       log_file=str(run / "output/benchmark.log"))
config["llm"].update(model=os.environ["EVAL_LLM_MODEL"],
                     base_url=os.environ["EVAL_LLM_BASE_URL"], api_key="",
                     api_key_env_var="EVAL_LLM_API_KEY")
(run / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True))
PY
```

`paths` 中的相对路径统一相对 **`benchmark/RAG/`** 解析，不是相对 YAML 文件位置。上例保持模板的数据路径，并将产物写入绝对路径。若 `benchmark/RAG/ov.conf` 存在，`run.py` 会用它覆盖进程的 `OPENVIKING_CONFIG_FILE`，运行前也要核对该文件。

### 3.3 分阶段运行

```bash
python run.py --config "$RUN_ROOT/rag/config.yaml" --step gen
python run.py --config "$RUN_ROOT/rag/config.yaml" --step eval
```

**不要在共享实例上省略 `--step`，也不要随手使用 `--step all` 或 `--step del`。** 当前默认值是 `all`，它在生成和评分后调用 `rm("viking://resources", recursive=True)`，递归删除当前身份可访问的资源根目录，而不仅是本轮数据。上例明确拆成 `gen` 和 `eval`，不执行删除。

- `gen`：准备并导入文档 → 检索 → 生成答案。`max_queries: 5` 只限制问答数量，**不会限制导入文档量**，所以应先抽样数据。
- `eval`：读取同一输出目录中的 `generated_answers.json` 再评分。先确认生成文件存在且问题数非零；不要只凭退出码判断评测完成。
- 小样本通过后，将本轮配置的 `max_queries` 设为 `null`、调整并发，使用完整或固定子集重新评测；正式结果使用新输出目录。若只改回答/裁判且确认索引、数据、模型与身份均未改变，才可用 `skip_ingestion: true` 复用索引。
- 当前检索范围是 `viking://resources`，不是单个样本的资源子树；测试服务不能混入其他实验或私人资料。

### 3.4 查看结果与指标

结果在 `$RUN_ROOT/rag/output/`：

| 文件 | 用途 |
| --- | --- |
| `generated_answers.json` | 实际问题、参考答案、检索 URI、生成答案、检索耗时和 token 估算 |
| `qa_eval_detailed_results.json` | 逐题 F1、Recall、裁判分数及理由 |
| `benchmark_metrics_report.json` | 汇总质量、导入效率和查询效率 |
| `benchmark.log` | 定位导入、生成和评分错误 |

指标以[实现](RAG/src/core/metrics.py)和[流水线](RAG/src/pipeline.py)为准：

- **F1**：答案归一化后的词级重合度；有多个参考答案时取最大值。
- **Recall**：参考 evidence 在检索文本中的命中比例。先做子串匹配，较长 evidence 再按词覆盖率软匹配；没有 evidence 时返回 0。它不是按文档 ID 计算的标准 Recall@k，也不是 MRR / nDCG。
- **Accuracy**：裁判给出的 0–4 分；汇总的 `Average Accuracy (normalization)` 是均分除以 4。Qasper 等分级评分**不是答对题数占比**；RAG LoCoMo 的裁判 prompt 使用 0 / 4 二值评分，也不能与 Memory 的独立裁判口径直接混用。另外存在双方都拒答时的启发式满分处理。
- **效率**：导入耗时与 token、平均检索耗时、回答输入/输出 token。检索计时只包围 `find`，不含后续读取和生成；问答 token 为本地 tokenizer 估算，不能当作含裁判在内的完整 API 账单。

比较生成题数、评分题数和日志。部分评分异常会被记录后继续，裁判失败也可能留下默认 0 分；缺失评分不能被当作有效的模型质量下降。当前每条检索文本最多取前 8000 字符用于回答，而 Recall 在截断前计算，排障时要区分“检索到证据”和“答案模型看到了证据”。

## 4. Memory：以 LoCoMo 为例

### 4.1 准备原始对话

```bash
cd "$ROOT/benchmark/RAG"
python scripts/download_dataset.py --dataset Locomo
export LOCOMO_DATA="$ROOT/benchmark/RAG/raw_data/Locomo/locomo10.json"

mkdir -p "$RUN_ROOT/locomo"
cd "$RUN_ROOT/locomo"
```

直接使用下载的原始 JSON，不要使用 RAG 生成的 Markdown。导入和问答按**数据数组下标**生成 `sample_0` 等用户身份，因此必须使用同一份数据、同一顺序。需要抽样时优先使用 `--sample` / `--count`，不要导入后再重排或裁剪数据文件。

### 4.2 先导入一个完整样本并验证单题

```bash
python "$ROOT/benchmark/locomo/openviking/import_to_ov.py" \
  --input "$LOCOMO_DATA" --openviking-url "$OPENVIKING_URL" \
  --sample 0 --parallel-samples 1 \
  --success-csv ./result/import_success.csv --error-log ./result/import_errors.log

python "$ROOT/benchmark/locomo/openviking/run_eval.py" "$LOCOMO_DATA" \
  --openviking-url "$OPENVIKING_URL" --output ./result/smoke.csv \
  --sample 0 --question-index 0 --threads 1 \
  --single-search-context-limit 50 --single-search-rerank-limit 10 \
  --single-search-max-context-chars 30000 --debug-print-model-input
```

导入脚本创建 session、写入消息并 commit，等待返回的后台任务。核对成功 CSV、错误日志以及实际生成的记忆，再开始问答；进程退出不等于所有 session 都导入成功。LoCoMo 默认把 `blip_caption` 作为图片的文本证据；仅在明确做多模态实验时启用 `--use-image-url`，并在报告中注明。

`run_eval.py` 对 `viking://user/sample_0/memories` 做一次检索，读取记忆后回答。检查 `smoke.csv` 中的 `context_uris`、`retrieved_uris_by_iteration` 和 `model_input_prompt`，确认用户、内容和上下文预算符合预期。完整 prompt 可能含原始记忆，不要直接公开调试 CSV。

**导入阶段不要使用基于测试答案的筛选。** 例如导入器的 `--question-index` 会根据该题 evidence 选择 session，适合定位单题问题，不适合作为完整 Memory 成绩。上例的 `--question-index` 只传给问答脚本；导入的是该用户的完整历史。

### 4.3 全量导入、问答与评分

保持相同目录、数据和服务，去掉导入的 `--sample` 后继续；同轮实验可复用已完成的导入记录：

```bash
python "$ROOT/benchmark/locomo/openviking/import_to_ov.py" \
  --input "$LOCOMO_DATA" --openviking-url "$OPENVIKING_URL" \
  --parallel-samples 4 \
  --success-csv ./result/import_success.csv --error-log ./result/import_errors.log

python "$ROOT/benchmark/locomo/openviking/run_eval.py" "$LOCOMO_DATA" \
  --openviking-url "$OPENVIKING_URL" --output ./result/answers.csv --threads 4 \
  --single-search-context-limit 50 --single-search-rerank-limit 10 \
  --single-search-max-context-chars 30000
```

裁判与回答模型可不同，但 A/B 实验必须使用同一裁判模型和 prompt。通过安全方式设置 `OPENAI_API_KEY`（脚本读取兼容接口凭据）或 `ARK_API_KEY`，再指定裁判地址和模型；**两者都存在时脚本优先使用 `ARK_API_KEY`**，应确认实际使用的凭据与地址匹配：

```bash
export JUDGE_BASE_URL="https://your-provider.example/v1"
export JUDGE_MODEL="your-judge-model-id"

python "$ROOT/benchmark/locomo/openviking/judge.py" \
  --input ./result/answers.csv --provider openai \
  --base-url "$JUDGE_BASE_URL" --model "$JUDGE_MODEL" --parallel 4
python "$ROOT/benchmark/locomo/openviking/stat_judge_result.py" \
  --input ./result/answers.csv
```

`judge.py` 将 `result` 写回原 CSV；默认跳过已有评分，修改答案、裁判模型或 prompt 后，需要新的结果文件或明确使用 `--force` 重评。默认是宽松 prompt，`--strict-prompt` 切换严格口径；比较时不能混用。

统计输出到终端，并写入 CSV 所在目录的 `summary.txt`（同目录再次统计会覆盖它）：

- **Accuracy = CORRECT / (CORRECT + WRONG)**；未评分或异常值不进入该分母。同步报告总题数、已评分数、异常/未评分数，不能只报 Accuracy。
- LoCoMo 的 judge/stat 排除 category 5；其余按 multi-hop、temporal、open-domain、single-hop 分组。若使用 `--errors` 标记无效题，还应说明无效题列表版本及有效题子集口径，不要与全体口径混报。
- 同时查看平均耗时、prompt / completion tokens、`memory_prompt_tokens` 和 `memory_chars`。Memory 上下文成本不等于包括导入、Embedding、重排及裁判在内的完整成本。

### 4.4 重排和上下文预算的实际语义

- `--single-search-context-limit`：检索与读取的候选数量上限。
- `--single-search-rerank-limit`：**有可用 reranker 且重排成功时**保留的条数；未配置或重排出错时，不保证裁成这个数量。以调试轨迹中的启用状态、错误和最终 URI 为准。
- 当前实现中，**`--single-search-rerank-limit 0` 不是关闭重排开关**；成功重排后使用 `max(1, limit)` 截取。做无重排对照应使用不启用 `rerank` 的独立本地配置，并检查轨迹，而不是仅设置 0。
- `--single-search-max-context-chars` 按**整份记忆文件**预算：放不下就跳过，不是逐字符截断；小于等于 0 取消预算。若命中但上下文为空，先检查单个文件是否超过预算。

## 5. 扩展到 LongMemEval

从作者发布的 [LongMemEval cleaned 数据集](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/tree/main)下载 `longmemeval_s_cleaned.json`，保存到本地。固定版本及校验和，勿将 oracle、S、M 等不同数据文件的结果混为一个口径。

原始 JSON 需保留 `question_id`、`question`、`answer`、`question_type`、`question_date` 以及 `haystack_sessions`、`haystack_dates`、`haystack_session_ids`；导入与问答都使用原始 `question_id` 派生的 `lm_user_<hash>` 用户空间。

```bash
export LONGMEMEVAL_DATA="/absolute/path/to/longmemeval_s_cleaned.json"
mkdir -p "$RUN_ROOT/longmemeval"
cd "$RUN_ROOT/longmemeval"

python "$ROOT/benchmark/longmemeval/openviking/import_to_ov.py" \
  --input "$LONGMEMEVAL_DATA" --openviking-url "$OPENVIKING_URL" \
  --sample 0 --parallel 1 --submit-parallel 1 --wait-mode deferred \
  --success-csv ./result/import_success.csv --error-log ./result/import_errors.log

python "$ROOT/benchmark/longmemeval/openviking/run_eval.py" "$LONGMEMEVAL_DATA" \
  --openviking-url "$OPENVIKING_URL" --output ./result/smoke.csv \
  --sample 0 --count 1 --threads 1 --timeout 900 \
  --single-search-context-limit 50 --single-search-rerank-limit 10 \
  --single-search-max-context-chars 30000 --debug-print-model-input
```

`deferred` 将提交与等待分开，但仍需等导入命令完成，并核对任务与错误日志后再问答。不要仅导入答案所在的 session，或先读取测试答案再生成记忆。

小样本通过后：重新执行导入命令，去掉 `--sample 0`；随后执行问答命令，去掉 `--sample 0 --count 1 --debug-print-model-input`，输出改为 `./result/answers.csv`。按服务限流适当增加并发。最后使用上一节设置的裁判环境：

```bash
python "$ROOT/benchmark/longmemeval/openviking/judge.py" \
  --input ./result/answers.csv --provider openai \
  --base-url "$JUDGE_BASE_URL" --model "$JUDGE_MODEL" --parallel 4
python "$ROOT/benchmark/longmemeval/openviking/stat_judge_result.py" \
  --input ./result/answers.csv
```

这里同样是 CSV 原地评分、按已评分题计算 Accuracy，并按 `question_type` 汇总；统计打印到终端，并写入 CSV 所在目录的 `longmemeval_summary.txt`（重复统计会覆盖）。重排、上下文预算和实验隔离原则与 LoCoMo 相同，细节见 [LongMemEval README](longmemeval/openviking/README.md)。

## 6. 怎样得到可比较的结论

### 6.1 固定变量与对照

| 对照 | 保持不变 | 只改变 |
| --- | --- | --- |
| RAG 检索参数 A/B | 数据、索引、回答/裁判模型、prompt、并发 | `retrieval_topk` 或检索指令中的一项 |
| Memory 提取策略 A/B | 原始历史、问题、回答/裁判、检索与上下文预算 | 提取配置；每组从独立空存储重新导入 |
| Memory 重排 A/B | 相同记忆快照、候选数量、回答/裁判 | 本地 `rerank` 配置；核对实际启用状态 |
| 有记忆与无记忆 / 全上下文 | 相同问题、回答模型、裁判与统计口径 | 输入上下文来源及其预算 |

最后一行是实验设计，不是上述脚本已有的模式开关；无记忆或全上下文基线需要另行准备对应 harness。不要把空召回、导入失败当作无记忆基线。调参用开发集，最终成绩用预先固定的保留集；禁止把 QA 参考答案、裁判结果或测试题答案定向写入待测记忆。

### 6.2 报告最少保留什么

- **版本与数据**：服务端/评测端 commit、数据来源与校验和、split、抽样 seed、实际样本/问题 ID、排除项。
- **配置**：脱敏后的模型 ID、Embedding 维度、提取配置、重排、top-k、prompt 版本、上下文预算、并发和是否复用索引。
- **质量**：RAG F1 / evidence Recall / 归一化裁判分；Memory 总体及各类别 Accuracy；明确分母与评分完成率。
- **成本与稳定性**：导入耗时、问答耗时、token 口径、异常/超时/重试数；需要总费用或 P50/P95 时另外采集相应调用记录，不将现有平均值当作这些指标。
- **证据**：逐题答案、检索 URI、裁判理由、导入成功记录和错误日志。抽查成功题和失败题，区分提取遗漏、检索未命中、预算丢弃、回答错误与裁判问题。
- **重复性**：质量对照使用相同问题列表，成本对照保持相同并发与缓存条件；多次运行报告波动。小样本 smoke 通过只能证明链路可用，不能替代完整成绩。

## 7. 常见问题

| 现象 | 先检查 |
| --- | --- |
| 导入全部被跳过，但服务里没有记忆 | 当前目录的隐藏导入记录及 success CSV 是否来自另一服务；为新服务使用新实验目录 |
| 检索为空或串入别的用户 | URL、account、权限、LoCoMo 数组顺序 / LongMemEval question_id、实际 memories URI、后台任务状态 |
| RAG 分数异常高或 Recall 与答案不一致 | 资源根目录是否混入其他资料；evidence 是否只出现在被截掉的文本中；是否混入参考答案 |
| 换了模型，分数却没变 | 是否重跑生成；judge 是否跳过已有结果；RAG 是否指向旧输出目录；Memory 本地模型配置是否真的切换 |
| 成功进程但结果不完整 | 非零题数、导入错误日志、CSV 的未评分项、RAG 生成/评分题数是否一致 |
| 声称重排 10 条，实际条数不符 | reranker 是否配置可用、是否报错、字符预算是否跳过整份文件 |

更多参数以对应脚本的 `--help` 和当前实现为准；基准分数应来自记录完整配置与证据的实际运行。
