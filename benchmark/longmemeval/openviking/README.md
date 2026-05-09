# LongMemEval Benchmark 使用说明

这个目录用于把 LongMemEval 数据集导入 OpenViking，并用 VikingBot 的 `single-search-context` 模式做问答、judge、统计和稳定性分析。

当前推荐主链路是：

1. 启动 OpenViking 服务。
2. 先导入单个 sample，确认 memory 生成正常。
3. 跑单个 sample 的评测和 judge，确认检索、回答、评分链路正常。
4. 全量导入数据集。
5. 全量跑评测、judge、统计。
6. 对 timeout/error/parse error 做补跑回填。
7. 需要研究波动时跑 repeat eval 和 stability。
8. 需要分析检索是否拖累回答时跑 evidence judge。

下面命令默认在仓库根目录执行。

## 数据和空间约定

默认数据集路径：

```bash
<LONGMEMEVAL_JSON>
```

默认 OpenViking 服务地址：

```bash
http://127.0.0.1:1933
```

LongMemEval sample 默认按 `question_id` 派生独立 user/agent：

- user id: `lm_user_<hash>`
- agent id: `lm_<hash>`

导入和评测固定使用一题一个 user/agent space；不再支持 shared space 参数。`run_eval.py` 会按同样规则派生检索空间。

## 推荐接入流程

### 1. 启动 OpenViking

```bash
./.venv/bin/openviking-server --withbot
```

如果改了服务端代码，通常重启服务即可。只有改了安装包、依赖、生成产物或需要重新安装 entrypoint 时，才需要重新安装或重新构建。

### 2. 单个 sample 导入测试

先导入一条，确认 ingest 能跑通：

```bash
python benchmark/longmemeval/openviking/import_to_ov.py \
  --input <LONGMEMEVAL_JSON> \
  --sample 0 \
  --openviking-url http://127.0.0.1:1933 \
  --wait-mode deferred \
  --parallel 4 \
  --success-csv ./result/longmemeval_import_success_debug.csv \
  --error-log ./result/longmemeval_import_errors_debug.log
```

如果需要只导入某几个 session：

```bash
python benchmark/longmemeval/openviking/import_to_ov.py \
  --input <LONGMEMEVAL_JSON> \
  --sample 0 \
  --sessions 1-3 \
  --openviking-url http://127.0.0.1:1933
```

### 3. 单个 sample 评测测试

评测单条并打印给模型的输入，适合检查 prompt、召回 URI、memory token 口径：

```bash
python benchmark/longmemeval/openviking/run_eval.py \
  <LONGMEMEVAL_JSON> \
  --sample 0 \
  --output result/longmemeval_sample0_debug.csv \
  --threads 1 \
  --timeout 300 \
  --debug-print-model-input
```

然后 judge 和统计：

```bash
python benchmark/longmemeval/openviking/judge.py \
  --input result/longmemeval_sample0_debug.csv \
  --parallel 1

python benchmark/longmemeval/openviking/stat_judge_result.py \
  --input result/longmemeval_sample0_debug.csv
```

### 4. 全量导入

推荐用单进程内并行，不要再用 `xargs -P` 同时启动多个 `import_to_ov.py` 进程。多进程会竞争写 `result/.longmemeval_ingest_record.json`，容易丢记录。

```bash
python benchmark/longmemeval/openviking/import_to_ov.py \
  --input <LONGMEMEVAL_JSON> \
  --openviking-url http://127.0.0.1:1933 \
  --wait-mode deferred \
  --parallel 24 \
  --success-csv ./result/longmemeval_import_success_full_parallel.csv \
  --error-log ./result/longmemeval_import_errors_full_parallel.log \
  2>&1 | tee ./result/longmemeval_import_full_parallel.log
```

`--parallel 24` 会作为默认并发数传给 sample 提交和后台 task wait。只需要单独限制提交并发时，可以加：

```bash
--submit-parallel 16
```

### 5. 全量评测、judge、统计

当前推荐回答模式是 `single-search-context`：每题只做一次 OpenViking find，读取 top-k memory 文件，把内容拼到 LongMemEval answer prompt 中单轮回答。

```bash
python benchmark/longmemeval/openviking/run_eval.py \
  <LONGMEMEVAL_JSON> \
  --output result/longmemeval_single_search_context.csv \
  --threads 4 \
  --timeout 300 \
  --single-search-context-limit 10

python benchmark/longmemeval/openviking/judge.py \
  --input result/longmemeval_single_search_context.csv \
  --parallel 8

python benchmark/longmemeval/openviking/stat_judge_result.py \
  --input result/longmemeval_single_search_context.csv
```

`run_eval.py` 输出的关键列：

- `response`: 模型回答。
- `token_usage`: 回答模型 token，用 JSON 存储。
- `memory_prompt_tokens`: 在 `token_usage` 内，只统计检索后读取的 memory 文件正文 token。
- `time_cost`: 单题耗时。
- `iteration`: `single-search-context` 固定为 1。
- `tools_used_names`: 固定包含 `single_search/read/context_answer`。
- `retrieved_uris_by_iteration`: 记录单轮检索 trace。`retrieved_uris` 是 rerank 前候选，`context_uris` 是最终进入 prompt 的 URI；如果配置了 rerank，还会记录 `rerank_enabled`、`rerank_limit`、`rerank_scores`。
- `result`: judge 后写入 `CORRECT` 或 `WRONG`。

## 文件说明

### `openviking/import_to_ov.py`

把 LongMemEval 的 `haystack_sessions` 导入 OpenViking memory。

常用参数：

- `--input`: LongMemEval JSON。
- `--sample`: 只导入第 N 个 sample，0-based。
- `--sessions`: 只导入某个 sample 的部分 session，例如 `1-3` 或 `4`。
- `--openviking-url`: OpenViking 服务地址。
- `--wait-mode deferred`: 先提交所有 session，再统一等后台 ingest task 完成。全量导入推荐这个模式。
- `--wait-mode immediate`: 每个 session 提交后立即等待完成，适合调试。
- `--parallel`: sample 处理和后台 task wait 的共享默认并发。
- `--submit-parallel`: 同时提交多少个 session。
- `--force-ingest`: 即使已有成功记录也重新导入。
- `--clear-ingest-record`: 清空 `result/.longmemeval_ingest_record.json`。
- `--success-csv`: 成功导入记录。
- `--error-log`: 导入错误日志。

注意：`result/.longmemeval_ingest_record.json` 是导入去重记录。单进程并发是安全的；不要对同一个 record 用多个 `import_to_ov.py` 进程并发写。

### `openviking/run_eval.py`

对已导入 OpenViking 的 LongMemEval question 做回答。

当前使用固定的 `single-search-context` 模式：

1. 用 question 作为 query。
2. 在 `viking://user/<sample_user_id>/memories` 下 search。
3. 直接按 `--single-search-context-limit` 召回候选 URI，默认 10。
4. 逐个 `read` 文件内容。
5. 如果 OpenViking 配置了 `rerank`，用 memory 正文做 rerank，并固定保留 rerank 后 top10 的 memory 进入 prompt。
6. 把最终 context memory 内容拼入 LongMemEval answer prompt。
7. 单轮调用回答模型。
8. 记录 `retrieved_uris_by_iteration` 和 `memory_prompt_tokens`。

常用参数：

- `input`: LongMemEval JSON，或包含 QA 的 CSV。
- `--output`: 输出 CSV。
- `--sample`: 只跑某个 sample。
- `--count`: 只跑前 N 条。
- `--threads`: 并发题目数。
- `--timeout`: OpenViking client timeout。
- `--single-search-context-limit`: 初始 search 召回的候选 memory 文件数；配置 rerank 时，最终进入 prompt 的是 rerank 后 top10。
- `--debug-print-model-input`: 打印完整模型输入和 memory token，用于单题调试。

### `openviking/judge.py`

对 `run_eval.py` 输出的 CSV 做自动判分，并把 `result` / `reasoning` 回写到同一个 CSV。

常用参数：

- `--input`: 需要 judge 的 CSV。
- `--parallel`: judge 请求并发，默认 8。
- `--provider`: `openai` 或 `azure`。
- `--base-url`: judge 模型 base URL。
- `--token`: API key。
- `--api-version`: Azure API version。
- `--model`: judge 模型。
- `--force`: 重新 judge 所有行，否则只 judge `result` 为空的行。

Azure 示例：

```bash
python benchmark/longmemeval/openviking/judge.py \
  --input result/longmemeval_single_search_context.csv \
  --provider azure \
  --base-url https://search.bytedance.net/gpt/openapi/online/v2/crawl/openai/deployments/gpt_openapi \
  --token "$LONGMEMEVAL_JUDGE_API_KEY" \
  --api-version 2024-03-01-preview \
  --model gpt-4o-2024-11-20 \
  --parallel 8
```

### `openviking/stat_judge_result.py`

统计一份 judge 后 CSV 的总体准确率、分类型准确率、平均耗时、平均 iteration 和 token 用量。

```bash
python benchmark/longmemeval/openviking/stat_judge_result.py \
  --input result/longmemeval_single_search_context.csv
```

同时会在输入 CSV 所在目录写入：

```bash
longmemeval_summary.txt
```

### `openviking/repeat_eval.py`

连续跑多次 eval + judge + stat，用于观察稳定性和平均 token/准确率。

```bash
python benchmark/longmemeval/openviking/repeat_eval.py \
  --input <LONGMEMEVAL_JSON> \
  --output-prefix result/longmemeval_single_search_context_repeat10 \
  --runs 10 \
  --threads 6 \
  --timeout 300 \
  --judge-parallel 8
```

输出：

```bash
result/longmemeval_single_search_context_repeat10.run1.csv
result/longmemeval_single_search_context_repeat10.run2.csv
...
```

每次 run 后会打印单次准确率、平均耗时、平均 token。最后打印多次平均统计。

### `openviking/stat_stability.py`

读取多份已经 judge 过的 repeat CSV，统计每道题的稳定性。

```bash
python benchmark/longmemeval/openviking/stat_stability.py \
  --inputs result/longmemeval_single_search_context_repeat10.run*.csv \
  --output result/longmemeval_single_search_context_stability.csv \
  --output-json result/longmemeval_single_search_context_stability.json
```

输出含义：

- `stable_correct`: 每次都答对。
- `stable_wrong`: 每次都答错。
- `flaky`: 有时答对、有时答错。
- `wrong_count`: N 次实验中答错次数。
- `correct_count`: N 次实验中答对次数。

如果只是跑完 `repeat_eval.py` 后立刻统计，后续可以把这段逻辑接到 `repeat_eval.py` 末尾；但保留独立脚本有价值，因为它可以分析历史 CSV，不需要重新跑评测。

### `openviking/rerun_timeouts_and_backfill.py`

补跑已有结果 CSV 中 timeout、命令错误、parse error 或空 response 的行，并把新结果回填回原 CSV。

```bash
python benchmark/longmemeval/openviking/rerun_timeouts_and_backfill.py \
  --inputs result/longmemeval_single_search_context.csv \
  --dataset <LONGMEMEVAL_JSON> \
  --threads 4 \
  --timeout 600 \
  --judge-parallel 8 \
  --include-missing
```

默认认为这些 response 前缀是失败：

- `[TIMEOUT]`
- `[CMD ERROR]`
- `[PARSE ERROR]`

`--include-missing` 会把 CSV 中缺失的 dataset sample 也补跑。

### `openviking/judge_retrieved_evidence.py`

判断 `run_eval.py` 召回的 URI 内容是否足够支撑标准答案，并估计证据首次出现在 top-k 的哪个位置。

用途：区分错题原因是检索没召回证据，还是证据已召回但回答模型没用好。

```bash
python benchmark/longmemeval/openviking/judge_retrieved_evidence.py \
  --input result/longmemeval_single_search_context.csv \
  --output result/longmemeval_single_search_context_evidence.csv \
  --data-root <OPENVIKING_DATA_ROOT> \
  --max-topk 30 \
  --parallel 4 \
  --provider azure \
  --base-url https://search.bytedance.net/gpt/openapi/online/v2/crawl/openai/deployments/gpt_openapi \
  --token "$LONGMEMEVAL_EVIDENCE_JUDGE_API_KEY" \
  --api-version 2024-03-01-preview \
  --model gpt-4o-2024-11-20
```

输出关键列：

- `evidence_sufficient`: 召回内容是否足够回答标准答案。
- `evidence_topk`: 第几个 top-k 前缀首次足够。
- `evidence_checked_k`: 实际检查到第几个 k。
- `evidence_uris`: 被判定所需的 URI 前缀。
- `evidence_supporting_uris`: judge 认为真正支撑答案的 URI。
- `evidence_reason`: 判断理由。

### `openviking/import_and_eval_one.py`

导入一个 sample 并立刻跑该 sample 的一条评测。适合非常早期的冒烟测试。

```bash
python benchmark/longmemeval/openviking/import_and_eval_one.py 0 \
  --input <LONGMEMEVAL_JSON> \
  --wait-seconds 3 \
  --force-ingest
```

注意：这个脚本封装较薄，输出文件使用 `run_eval.py` 默认路径。需要精细控制输出文件、debug prompt 或 context limit 时，建议直接分别调用 `import_to_ov.py` 和 `run_eval.py`。

### `openviking/longmemeval_prompts.py`

集中存放 LongMemEval answer generation prompt 和 judge prompt。

被这些脚本使用：

- `run_eval.py`: 构造回答 prompt。
- `judge.py`: 构造 judge prompt。

如果要调整回答策略或 judge 宽严程度，优先改这里，而不是在多个脚本里复制 prompt。

## 常见实验配方

### 只跑前 10 条检查全链路

```bash
python benchmark/longmemeval/openviking/run_eval.py \
  <LONGMEMEVAL_JSON> \
  --output result/longmemeval_first10.csv \
  --count 10 \
  --threads 2 \
  --timeout 300

python benchmark/longmemeval/openviking/judge.py \
  --input result/longmemeval_first10.csv \
  --parallel 2

python benchmark/longmemeval/openviking/stat_judge_result.py \
  --input result/longmemeval_first10.csv
```

### 对比 context limit

limit 10：

```bash
python benchmark/longmemeval/openviking/run_eval.py \
  <LONGMEMEVAL_JSON> \
  --output result/longmemeval_single_search_context_limit10.csv \
  --threads 4 \
  --timeout 300 \
  --single-search-context-limit 10
```

limit 20：

```bash
python benchmark/longmemeval/openviking/run_eval.py \
  <LONGMEMEVAL_JSON> \
  --output result/longmemeval_single_search_context_limit20.csv \
  --threads 4 \
  --timeout 300 \
  --single-search-context-limit 20
```

limit 30：

```bash
python benchmark/longmemeval/openviking/run_eval.py \
  <LONGMEMEVAL_JSON> \
  --output result/longmemeval_single_search_context_limit30.csv \
  --threads 4 \
  --timeout 300 \
  --single-search-context-limit 30
```

每个结果都需要继续跑：

```bash
python benchmark/longmemeval/openviking/judge.py --input <csv> --parallel 8
python benchmark/longmemeval/openviking/stat_judge_result.py --input <csv>
```

### 后台跑长实验

```bash
nohup python benchmark/longmemeval/openviking/repeat_eval.py \
  --input <LONGMEMEVAL_JSON> \
  --output-prefix result/longmemeval_single_search_context_repeat10 \
  --runs 10 \
  --threads 6 \
  --timeout 300 \
  --judge-parallel 8 \
  > result/longmemeval_single_search_context_repeat10.out 2>&1 &
```

查看进度：

```bash
tail -f result/longmemeval_single_search_context_repeat10.out
```

## 新接入一个 LongMemEval 数据集时怎么做

1. 确认 JSON 字段存在：
   - `question_id`
   - `question`
   - `answer`
   - `question_type`
   - `question_date`
   - `haystack_sessions`
   - `haystack_dates`
   - `haystack_session_ids`

2. 先跑数据规模检查：

   ```bash
   python - <<'PY'
   import json
   path = "<LONGMEMEVAL_JSON>"
   data = json.load(open(path, encoding="utf-8"))
   print("samples:", len(data))
   print("first question_id:", data[0].get("question_id"))
   print("first sessions:", len(data[0].get("haystack_sessions", [])))
   PY
   ```

3. 启动 OpenViking。

4. 单 sample 导入：

   ```bash
   python benchmark/longmemeval/openviking/import_to_ov.py \
     --input /path/to/longmemeval.json \
     --sample 0 \
     --openviking-url http://127.0.0.1:1933 \
     --wait-mode deferred \
     --parallel 4
   ```

5. 单 sample 评测并打印 prompt：

   ```bash
   python benchmark/longmemeval/openviking/run_eval.py \
     /path/to/longmemeval.json \
     --sample 0 \
     --output result/longmemeval_new_sample0.csv \
     --threads 1 \
     --debug-print-model-input
   ```

6. judge 单 sample：

   ```bash
   python benchmark/longmemeval/openviking/judge.py \
     --input result/longmemeval_new_sample0.csv \
     --parallel 1
   ```

7. 如果单 sample 通过，再全量导入。

8. 全量评测。

9. 补跑 timeout/error。

10. 用 `stat_judge_result.py` 看总体和分类型表现。

11. 如果分数波动明显，用 `repeat_eval.py` + `stat_stability.py`。

12. 如果想判断错题是否是检索问题，用 `judge_retrieved_evidence.py`。

## 排错建议

- 导入很快结束但没有明显结果：先看 `--error-log` 和 OpenViking server log。
- 导入后 eval 检索为空：确认导入和 eval 是否使用同一个 per-sample user id 规则。
- CSV 里有 timeout/error/parse error：用 `rerun_timeouts_and_backfill.py` 补跑。
- judge 不跑：确认 API key、provider、base URL、model 参数。
- `memory_prompt_tokens` 异常：用 `--debug-print-model-input` 单条检查，它只应统计检索后读取的 memory 正文 token。
- 多次实验结果不一致：先比较 `retrieved_uris_by_iteration` 里的 `retrieved_uris` 和 `context_uris`，再用 `judge_retrieved_evidence.py` 判断证据是否已进入 prompt；加 `--include-unread` 可检查 rerank 前候选。
