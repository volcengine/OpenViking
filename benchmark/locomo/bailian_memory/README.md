# 百炼记忆库（ModelStudio Memory）LoCoMo 评测

本目录使用阿里云百炼记忆库对 LoCoMo 长期对话记忆数据集进行评测。

## 目录结构

```
bailian_memory/
├── ingest.py        # 将对话数据导入百炼记忆库
├── eval.py          # QA 评测（SearchMemory + Qwen LLM 回答）
├── delete_user.py   # 删除指定用户的全部记忆节点
└── result/          # 评测结果输出目录
```

## 环境准备

### 1. 获取 API Key 和记忆库 ID

- **DASHSCOPE_API_KEY**：在[百炼控制台](https://bailian.console.aliyun.com)获取 DashScope API Key（`sk-xxx` 格式）
- **BAILIAN_MEMORY_LIBRARY_ID**：在百炼控制台「记忆库」页面创建记忆库后，从记忆库卡片获取 ID
- **BAILIAN_PROFILE_SCHEMA_ID**：可选，见下方「用户画像说明」

推荐写入 `~/.openviking_benchmark_env`：

```bash
DASHSCOPE_API_KEY=sk-xxx
BAILIAN_MEMORY_LIBRARY_ID=xxx
# BAILIAN_PROFILE_SCHEMA_ID=xxx  # 可选，不建议在 LoCoMo 场景下开启，见下方说明
ARK_API_KEY=xxx                  # 裁判模型（可选，doubao）
```

### 2. 安装依赖

```bash
pip install requests python-dotenv
```

---

## 用户画像说明

百炼记忆库支持两类数据：**事件记忆**（memory nodes）和**用户画像**（user profile）。

### LoCoMo 场景下不建议开启画像

LoCoMo 是双人群聊数据集（Alice & Bob 互相对话），而百炼画像设计上针对**单用户**场景：

- ingest 时所有消息均为 `role: user`，API 无法区分两个说话人
- 画像会把两个人的属性混在同一个 `user_id` 下，导致错误归属

**实测结果**（全 `role: user` + `profile_schema`）：

| 画像字段 | 提取值 | 实际归属 |
|---------|--------|---------|
| 性别 | 女性 | Caroline（transgender woman） |
| 爱好 | 绘画，尤其是日出主题 | **Melanie**（串了） |
| 人生理想 | 学习心理学或心理咨询 | Caroline |

事件记忆提取正常（两个人的事件都能正确提取），但画像是乱的。

**结论：`BAILIAN_PROFILE_SCHEMA_ID` 不设置，只走事件记忆。**

### 如需测试画像功能

设置 `BAILIAN_PROFILE_SCHEMA_ID` 后，ingest 时会自动同时提取事件记忆和用户画像：

```bash
# ~/.openviking_benchmark_env
BAILIAN_PROFILE_SCHEMA_ID=your_schema_id
```

### 画像数据的限制

- **无法通过 API 删除**：`GetUserProfile` 是只读接口，没有删除/更新画像的公开 API
- **无法删除整个用户**：`DELETE /entities/{user_id}` 接口存在但尚未对外稳定开放（返回 500）
- 如需清理画像数据，只能在百炼控制台页面手动操作

---

## 评测流程

### 步骤一：导入对话数据

将 LoCoMo 对话导入百炼记忆库，每个 sample 以 `sample_id` 作为 `user_id` 隔离记忆空间：

```bash
# 导入所有数据
python ingest.py

# 只导入第 0 个 sample
python ingest.py --sample 0

# 指定会话范围（1-4 session）
python ingest.py --sample conv-26 --sessions 1-4

# 强制重新导入
python ingest.py --force-ingest
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--input` | locomo10.json 路径 | `../data/locomo10.json` |
| `--sample` | 样本索引（int）或 sample_id | 全部 |
| `--sessions` | 会话范围，如 `1-4` 或 `3` | 全部 |
| `--force-ingest` | 强制重新导入（忽略已导入记录） | false |
| `--clear-ingest-record` | 清空所有导入进度记录 | false |
| `--limit` | 最多处理样本数 | 全部 |

### 步骤二：运行 QA 评测

对每道题执行 SearchMemory 检索 → Qwen LLM 生成答案：

```bash
# 运行全部评测
python eval.py

# 只测第 0 个 sample 的前 10 题
python eval.py --sample 0 --count 10

# 指定模型和检索数量
python eval.py --model qwen-max --top-k 15 --threads 20
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--input` | locomo10.json 路径 | `../data/locomo10.json` |
| `--output` | 输出 CSV 路径 | `./result/qa_results.csv` |
| `--model` | 回答用 Qwen 模型 | `qwen-plus` |
| `--top-k` | 每题检索的记忆条数 | `10` |
| `--threads` | 并发线程数 | `10` |
| `--sample` | 指定样本 | 全部 |
| `--count` | 最多处理题目数 | 全部 |

> 评测支持断点续传，重新运行会自动跳过已完成的题目。

### 步骤三：裁判打分

使用上层目录的 judge 脚本打分：

```bash
python ../vikingbot/judge.py --input ./result/qa_results.csv --token $ARK_API_KEY

# 统计结果
python ../vikingbot/stat_judge_result.py --input ./result/qa_results.csv
```

---

## 清理数据

```bash
# 删除指定用户的全部记忆节点
python delete_user.py conv-26

# 删除多个用户
python delete_user.py conv-26 conv-31 conv-45

# 删除 locomo10.json 中的所有用户
python delete_user.py --from-data

# 只删前 2 个
python delete_user.py --from-data --limit 2
```

> **注意**：`delete_user.py` 只能删除 memory nodes（事件记忆），用户画像数据无法通过 API 删除，需要在百炼控制台手动清理。

---

## 输出文件

| 文件 | 说明 |
|------|------|
| `result/qa_results.csv` | QA 评测结果（含检索到的记忆、模型回答、token 消耗） |
| `result/.ingest_record.json` | 导入进度记录（断点续传用） |
| `result/ingest_errors.log` | 导入错误日志 |

## 环境变量

| 变量名 | 说明 |
|--------|------|
| `DASHSCOPE_API_KEY` | 百炼 DashScope API Key（`sk-xxx`），必填 |
| `BAILIAN_MEMORY_LIBRARY_ID` | 百炼记忆库 ID，必填 |
| `BAILIAN_PROFILE_SCHEMA_ID` | 画像模板 ID，可选，LoCoMo 场景不建议设置 |
| `ARK_API_KEY` | 裁判模型 API Key（Volcengine doubao），可选 |
