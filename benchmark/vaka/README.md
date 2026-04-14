# VAKA 记忆评测

数据存储在飞书文档：https://bytedance.larkoffice.com/wiki/OUa4wRTVqikSEDkcEyBchgbAnfk

## 数据结构

| Sheet | 说明 |
|-------|------|
| samples | 原始对话数据（15 条对话，5 组 session） |
| queries | 查询问题 + 期望答案（15 个问题） |

### samples 表结构

| 字段 | 说明 |
|------|------|
| session_group | Session 组别编号 (1-5) |
| turn_id | 唯一turn ID，格式：session_group_sessionId_turn |
| conversation | JSON，对话消息列表 |

### queries 表结构

| 字段 | 说明 |
|------|------|
| question_id | 问题 ID |
| question | 查询问题 |
| ground_truth | 期望答案（长期记忆） |
| session_group | 对应的 session 组编号 |

## 文件结构

```
benchmark/vaka/
├── run_eval.py      # 评测执行脚本（从飞书读取数据）
├── judge.py         # LLM 评分脚本
├── run_full_eval.sh # 完整流程脚本
└── result/          # 输出结果
    ├── eval_result.csv     # run_eval 输出
    └── judged_result.csv   # judge 评分后输出
```

## 使用方法

### 方式一：完整流程

```bash
cd benchmark/vaka
./run_full_eval.sh
```

### 方式二：分步执行

```bash
# Step 1: 写入对话 + 查询召回
python3 run_eval.py --phase all

# Step 2: LLM 评分
python3 judge.py
```

### 方式三：单独执行

```bash
# 仅写入对话
python3 run_eval.py --phase ingest

# 仅查询召回
python3 run_eval.py --phase query

# 仅评分
python3 judge.py --input result/eval_result.csv
```

## 依赖

- OpenViking Server 运行中（默认 http://localhost:1934）
- lark-cli 已登录（有 sheets 读取权限）
- ARK API Key（用于 LLM 评分）
  - 设置方式：
    - 创建 `~/.openviking_benchmark_env` 文件，内容：`ARK_API_KEY=你的key`
    - 或通过 `--token` 参数传入

## 评测指标

使用 LLM 判断回答是否正确匹配标准答案：
- **CORRECT**: 回答正确涵盖了标准答案的核心信息
- **WRONG**: 回答遗漏了重要信息或理解错误

最终输出准确率 = CORRECT 数量 / 总数