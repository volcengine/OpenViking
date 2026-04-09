# LoCoMo 评测脚本使用指南

本目录包含 LoCoMo（Long-Term Conversation Memory）评测脚本，用于评估对话记忆系统的性能。

## 目录结构

```
benchmark/locomo/
├── vikingbot/          # VikingBot 评测脚本
│   ├── run_eval.py     # 运行 QA 评估
│   ├── judge.py        # LLM 裁判打分
│   ├── import_to_ov.py # 导入数据到 OpenViking
│   ├── import_and_eval_one.sh  # 单题/批量测试脚本
│   ├── stat_judge_result.py    # 统计评分结果
│   ├── run_full_eval.sh        # 一键运行完整评测流程
│   ├── data/           # 测试数据目录
│   └── result/         # 评测结果目录
└── openclaw/           # OpenClaw 评测脚本
    ├── import_to_ov.py # 导入数据到 OpenViking
    ├── eval.py         # OpenClaw 评估脚本 (ingest/qa)
    ├── judge.py        # LLM 裁判打分（适配 OpenClaw）
    ├── stat_judge_result.py    # 统计评分结果和 token 使用
    ├── run_full_eval.sh        # 一键运行完整评测流程
    ├── data/           # 测试数据目录
    └── result/         # 评测结果目录
```

---

## VikingBot 评测流程

### 完整一键评测

使用 `run_full_eval.sh` 可以一键运行完整评测流程：

```bash
cd benchmark/locomo/vikingbot
bash run_full_eval.sh        # 完整流程
bash run_full_eval.sh --skip-import  # 跳过导入，仅评测
```

该脚本会依次执行以下四个步骤：

### 单题/批量测试

使用 `import_and_eval_one.sh` 可以快速测试单个问题或批量测试某个 sample：

```bash
cd benchmark/locomo/vikingbot
```

**单题测试：**
```bash
./import_and_eval_one.sh 0 2          # sample 索引 0, question 2
./import_and_eval_one.sh conv-26 2    # sample_id conv-26, question 2
./import_and_eval_one.sh conv-26 2 --skip-import  # 跳过导入
```

**批量测试单个 sample：**
```bash
./import_and_eval_one.sh conv-26       # conv-26 所有问题
./import_and_eval_one.sh conv-26 --skip-import
```

### 分步使用说明

#### 步骤 1: 导入对话数据

使用 `import_to_ov.py` 将 LoCoMo 数据集导入到 OpenViking：

```bash
python import_to_ov.py --input <数据文件路径> [选项]
```

**参数说明：**
- `--input`: 输入文件路径（JSON 或 TXT 格式），默认 `./data/locomo10.json`
- `--sample`: 指定样本索引（0-based），默认处理所有样本
- `--sessions`: 指定会话范围，例如 `1-4` 或 `3`，默认所有会话
- `--parallel`: 并发导入数，默认 5
- `--force-ingest`: 强制重新导入，即使已导入过
- `--clear-ingest-record`: 清除所有导入记录
- `--openviking-url`: OpenViking 服务地址，默认 `http://localhost:1933`

**示例：**
```bash
# 导入第一个样本的 1-4 会话
python import_to_ov.py --input ./data/locomo10.json --sample 0 --sessions 1-4

# 强制重新导入所有数据
python import_to_ov.py --input ./data/locomo10.json --force-ingest
```

#### 步骤 2: 运行 QA 评估

使用 `run_eval.py` 运行问答评估：

```bash
python run_eval.py <输入数据> [选项]
```

**参数说明：**
- `input`: 输入 JSON/CSV 文件路径，默认 `./data/locomo10.json`
- `--output`: 输出 CSV 文件路径，默认 `./result/locomo_qa_result.csv`
- `--sample`: 指定样本索引
- `--count`: 运行的 QA 问题数量，默认全部
- `--threads`: 并发线程数，默认 5

**示例：**
```bash
# 使用默认参数运行
python run_eval.py

# 指定输入输出文件，使用 20 线程
python run_eval.py ./data/locomo_qa_1528.csv --output ./result/my_result.csv --threads 20
```

#### 步骤 3: LLM 裁判打分

使用 `judge.py` 对评估结果进行打分：

```bash
python judge.py [选项]
```

**参数说明：**
- `--input`: QA 结果 CSV 文件路径，默认 `./result/locomo_qa_result.csv`
- `--token`: API Token（也可通过 `ARK_API_KEY` 或 `OPENAI_API_KEY` 环境变量设置）
- `--base-url`: API 基础 URL，默认 `https://ark.cn-beijing.volces.com/api/v3`
- `--model`: 裁判模型名称，默认 `doubao-seed-2-0-pro-260215`
- `--parallel`: 并发请求数，默认 5

**示例：**
```bash
python judge.py --input ./result/locomo_qa_result.csv --token <your_token> --parallel 10
```

#### 步骤 4: 统计结果

使用 `stat_judge_result.py` 统计评分结果：

```bash
python stat_judge_result.py --input <评分结果文件>
```

**参数说明：**
- `--input`: 评分结果 CSV 文件路径

**输出统计信息包括：**
- 正确率（Accuracy）
- 平均耗时
- 平均迭代次数
- Token 使用情况

---

## OpenClaw 评测流程

### 完整一键评测

使用 `openclaw/run_full_eval.sh` 可以一键运行完整评测流程：

```bash
cd benchmark/locomo/openclaw
bash run_full_eval.sh                      # 只导入 OpenViking（跳过已导入的）
bash run_full_eval.sh --with-claw-import   # 同时导入 OpenViking 和 OpenClaw（并行执行）
bash run_full_eval.sh --skip-import        # 跳过导入步骤，直接运行 QA 评估
bash run_full_eval.sh --force-ingest       # 强制重新导入所有数据
bash run_full_eval.sh --sample 0           # 只处理第 0 个 sample
```

**脚本参数说明：**

| 参数 | 说明 |
|------|------|
| `--skip-import` | 跳过导入步骤，直接运行 QA 评估 |
| `--with-claw-import` | 同时导入 OpenViking 和 OpenClaw（并行执行） |
| `--force-ingest` | 强制重新导入所有数据（忽略已导入记录） |
| `--sample <index>` | 只处理指定的 sample（0-based） |

**脚本执行流程：**
1. 导入数据到 OpenViking（可选同时导入 OpenClaw）
2. 等待 60 秒确保数据导入完成
3. 运行 QA 评估（`eval.py qa`，输出到 `result/qa_results.csv`）
4. 裁判打分（`judge.py`，并行度 40）
5. 统计结果（`stat_judge_result.py`，同时统计 QA 和 Import 的 token 使用）

**脚本内部配置参数：**

在 `run_full_eval.sh` 脚本顶部可以修改以下配置：

| 变量 | 说明 | 默认值                       |
|------|------|---------------------------|
| `INPUT_FILE` | 输入数据文件路径 | `../data/locomo10.json`   |
| `RESULT_DIR` | 结果输出目录 | `./result`                |
| `GATEWAY_TOKEN` | OpenClaw Gateway Token | 需要设置为实际 openclaw 网关 token |

### 分步使用说明

OpenClaw 评测包含以下脚本：
- `import_to_ov.py`: 导入数据到 OpenViking
- `eval.py`: OpenClaw 评估脚本（ingest/qa 两种模式）
- `judge.py`: LLM 裁判打分
- `stat_judge_result.py`: 统计评分结果和 token 使用

---

#### import_to_ov.py - 导入对话数据到 OpenViking

```bash
python import_to_ov.py [选项]
```

**参数说明：**
- `--input`: 输入文件路径（JSON 或 TXT），默认 `../data/locomo10.json`
- `--sample`: 指定样本索引（0-based）
- `--sessions`: 指定会话范围，如 `1-4`
- `--question-index`: 根据 question 的 evidence 自动推断需要的 session
- `--force-ingest`: 强制重新导入
- `--no-user-agent-id`: 不传入 user_id 和 agent_id 给 OpenViking 客户端
- `--openviking-url`: OpenViking 服务地址，默认 `http://localhost:1933`
- `--success-csv`: 成功记录 CSV 路径，默认 `./result/import_success.csv`
- `--error-log`: 错误日志路径，默认 `./result/import_errors.log`

**示例：**
```bash
# 导入所有数据（跳过已导入的）
python import_to_ov.py

# 强制重新导入，不使用 user/agent id
python import_to_ov.py --force-ingest --no-user-agent-id

# 只导入第 0 个 sample
python import_to_ov.py --sample 0
```

---

#### eval.py - OpenClaw 评估脚本

该脚本有两种模式：

##### 模式 1: ingest - 导入对话数据到 OpenClaw

```bash
python eval.py ingest <输入文件> [选项]
```

**参数说明：**
- `--sample`: 指定样本索引
- `--sessions`: 指定会话范围，如 `1-4`
- `--force-ingest`: 强制重新导入
- `--agent-id`: Agent ID，默认 `locomo-eval`
- `--token`: OpenClaw Gateway Token

**示例：**
```bash
# 导入第一个样本的 1-4 会话到 OpenClaw
python eval.py ingest locomo10.json --sample 0 --sessions 1-4 --token <token>
```

##### 模式 2: qa - 运行 QA 评估

- 该评测指定了 `X-OpenClaw-Session-Key`，确保每次 OpenClaw 使用相同的 session_id
- Token 计算统计 `session.jsonl` 文件中的所有 assistant 轮次的 Token 消耗
- 每道题目执行完后会归档 session 文件
- 支持并发运行（`--parallel` 参数）
- 问题会自动添加时间上下文（从最后一个 session 提取）

```bash
python eval.py qa <输入文件> [选项]
```

**参数说明：**
- `--output`: 输出文件路径（不含 .csv 后缀）
- `--sample`: 指定样本索引
- `--count`: 运行的 QA 问题数量
- `--user`: 用户 ID，默认 `eval-1`
- `--parallel`: 并发数，默认 10，最大 40
- `--token`: OpenClaw Gateway Token（或设置 `OPENCLAW_GATEWAY_TOKEN` 环境变量）

**示例：**
```bash
# 运行所有 sample 的 QA 评估
python eval.py qa locomo10.json --token <token> --parallel 15

# 只运行第 0 个 sample
python eval.py qa locomo10.json --sample 0 --output qa_results_sample0
```

---

#### judge.py - LLM 裁判打分

```bash
python judge.py [选项]
```

**参数说明：**
- `--input`: QA 结果 CSV 文件路径
- `--parallel`: 并发请求数，默认 40

**示例：**
```bash
python judge.py --input ./result/qa_results.csv --parallel 40
```

---

#### stat_judge_result.py - 统计结果

同时统计 QA 结果和 OpenViking Import 的 token 使用：

```bash
python stat_judge_result.py [选项]
```

**参数说明：**
- `--input`: QA 结果 CSV 文件路径，默认 `./result/qa_results_sample0.csv`
- `--import-csv`: Import 成功 CSV 文件路径，默认 `./result/import_success.csv`

**输出统计包括：**
- QA 结果统计：正确率、token 使用（no-cache、cacheRead、output）
- OpenViking Import 统计：embedding_tokens、vlm_tokens、total_tokens

**示例：**
```bash
python stat_judge_result.py --input ./result/qa_results_sample0.csv --import-csv ./result/import_success.csv
```

---

## 测试数据格式

### LoCoMo JSON 格式

```json
[
  {
    "sample_id": "sample_001",
    "conversation": {
      "speaker_a": "Alice",
      "speaker_b": "Bob",
      "session_1": [
        {
          "speaker": "Alice",
          "text": "你好，我是 Alice",
          "img_url": [],
          "blip_caption": ""
        }
      ],
      "session_1_date_time": "9:36 am on 2 April, 2023"
    },
    "qa": [
      {
        "question": "Alice 叫什么名字？",
        "answer": "Alice",
        "category": "1",
        "evidence": []
      }
    ]
  }
]
```

### CSV 格式（QA 数据）

必须包含字段：
- `sample_id`: 样本 ID
- `question`: 问题
- `answer`: 标准答案

---

## 输出文件说明

| 文件 | 说明 |
|------|------|
| `result/locomo_qa_result.csv` | QA 评估原始结果 |
| `result/judge_result.csv` | 包含裁判打分的结果 |
| `result/summary.txt` | 统计摘要 |
| `result/import_success.csv` | 导入成功记录 |
| `result/import_errors.log` | 导入错误日志 |

---

## 环境变量

| 变量名 | 说明 |
|--------|------|
| `ARK_API_KEY` | 火山引擎 API Key（用于 judge.py） |
| `OPENAI_API_KEY` | OpenAI API Key（备选） |
| `OPENCLAW_GATEWAY_TOKEN` | OpenClaw Gateway Token |

---

## 常见问题

### Q: 如何中断后继续评测？
A: 所有脚本都支持断点续传，重新运行相同命令会自动跳过已处理的项目。

### Q: 如何强制重新运行？
A: 使用 `--force-ingest`（导入）或删除结果 CSV 文件。

### Q: 评测速度慢怎么办？
A: 增加 `--threads`（run_eval.py）或 `--parallel`（其他脚本）参数值。
