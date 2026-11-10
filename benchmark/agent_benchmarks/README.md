# SkillFlow / EvoAgentBench 接入 OV train

这两个适配器使用 `openviking.session.train` 的统一流程：

`baseline eval → train（执行任务并 session.commit 学习）→ epoch/final eval`

benchmark 服务负责原生任务环境、agent 执行与原生判分。训练和评测均在独立 worker 中复用本仓库的
`bot/vikingbot/agent/loop.py:AgentLoop` 和 `VLMProviderAdapter`。OV 在训练阶段
提交真实轨迹，评测阶段只读已学经验。经验召回目前发生在**任务入口一次**，不会在
每次工具调用前重新检索。baseline 完全不调用经验检索。

任务执行循环、模型调用和工具注册复用仓库已有实现；SkillFlow 使用 Harbor 容器
工具桥接，Evo 的代码/知识工作使用 VikingBot 的文件与 shell 工具，检索使用本地
corpus search 工具。所有任务只暴露所选 benchmark 工具，不加载个人 bot 配置、
本地技能、后台子 agent 或 bot 自动经验写入。学习统一由 train 的 session.commit 执行。

## 准备运行环境和清单

OV 服务使用本分支的 Python 环境。另准备各 benchmark 的 Python 环境：安装本仓库 VikingBot（`pip install -e ".[bot]"`）及其依赖，
以及 SkillFlow 所需 Harbor/Docker 或 EvoAgentBench 官方 harness 依赖。代码和数据下载、OV 模型及 embedding 配置仍按各项目正常方式准备。本适配器
不自动下载全部数据，也不修改模型权重。

复制 `skillflow-runtime.example.json` / `evoagentbench-runtime.example.json`，填写实际
路径和模型；`agent_runtime` 固定为 `vikingbot`，无需外部 agent 源码目录。`provider` 使用 OV 的
模型 provider 名称（默认 `volcengine`）；API key 通过 `api_key_env` 对应的环境变量提供，可用 `env_file` 加载本地
环境文件。运行配置不应包含明文 API key。每个服务对应一个冻结 manifest。

SkillFlow 可提供多个 `test_tasks` 根目录，优先使用排在前面的修复副本。每个 family
内部按固定种子划分本地 train/test；只有一题的 family 只进入 test。这是本地 held-out
划分，**不是官方 lifelong 难度顺序协议**。同一 `(family, task_id)` 不会跨 train/test。

```bash
python -m benchmark.agent_benchmarks.prepare \
  --dataset skillflow --runtime-config /path/skillflow-runtime.json \
  --tasks-root /path/repaired/test_tasks --tasks-root /path/original/test_tasks \
  --image-pins /path/validated/image-pins.json \
  --seed 20261106 --train-fraction 0.5 --output /path/skillflow.json
```

`--image-pins` 可省略；提供时，只有单行 `FROM <已验收父镜像>` 的任务才挂载对应 pin。
这些任务每次运行都会校验父镜像 ID，并核对实际容器镜像的文件系统与运行配置。
未携带 pin 的任务使用其自身 Harbor Dockerfile，不能声称已通过此前的镜像验收。

EvoAgentBench 从本地已准备的 task 列表生成清单，每条必须有 `domain`、`task_id`、
`official_split`。也接受此前 `eligible-pools.json` 的 `evoagentbench` 列表。校验官方
split 文件及 manifest 的 SHA256，保留官方 train/test 边界。当前覆盖已准备的
`code_implementation`、`information_retrieval`、`knowledge_work`；software engineering
需要另行准备执行环境并适配，当前明确拒绝，避免静默漏题。

```bash
python -m benchmark.agent_benchmarks.prepare \
  --dataset evoagentbench --runtime-config /path/evo-runtime.json \
  --selection /path/eligible-pools.json --output /path/evoagentbench.json
```

清单已存在时 prepare 拒绝覆盖。数据路径可位于仓库外；manifest 中写绝对路径。
两种清单也可手动编写（`version: 1`, `dataset`, `runtime`, `splits.train/test`），服务
启动时仍会校验重复、交叉和任务完整性。SkillFlow 任务条目含 `domain`（family）、
`task_id`、`path`；Evo 条目含 `domain`、`task_id`。

## 启动 benchmark 服务

```bash
python -m benchmark.agent_benchmarks.service \
  --manifest /path/skillflow.json --output result/skillflow/runtime \
  --port 1945 --concurrency 20

# 第二个终端；也可以在不同时间使用同一个端口
python -m benchmark.agent_benchmarks.service \
  --manifest /path/evoagentbench.json --output result/evoagentbench/runtime \
  --port 1946 --concurrency 20
```

`--check` 仅检查清单和本地文件，不调用模型、Docker 或 OV，也不证明全部运行依赖
可用。服务默认绑定 localhost；每个服务的 `--concurrency` 是全部客户端共享的上限。
每次执行的 worker 日志、模型参数、完整工具轨迹、产物、判分和经验召回证据保存在
独立 UUID 目录。worker 出错会让远程任务失败，不把基础设施错误转换成 reward=0。
IR judge 降级为 exact-match 时也拒绝当作有效判分。worker 超时会终止进程组并给
原生 runner 留出容器清理时间。

## 统一 train 命令

先以本分支代码启动一个已配置好模型和 embedding 的 OV 服务，通过服务的运行时配置或账号配置明确开启
`agent_evolution.enabled: true`，并确认账号的有效状态。该字段属于运行时配置，不能仅凭
`ov.conf` 中写了 true 就认为已开启；可通过 `GET /api/v1/admin/agent-evolution` 确认。
默认开关关闭时，归档
成功不等于经验学习成功；train 会在可查询时提前检查，并把提交结果中的学习跳过
标记当作训练错误。使用独立实验 user，
避免复用已有经验影响比较；SkillFlow 和 EvoAgentBench 应分别使用不同 user。

```bash
python -m openviking.session.train.run_batch_train_eval \
  --dataset skillflow --domain all \
  --config /path/ov.conf --server-url http://127.0.0.1:1933 \
  --user-id skillflow-exp-001 --benchmark-service-url http://127.0.0.1:1945 \
  --epochs 2 --train-cases 8 --eval-cases 20 --sample-seed 20261106 \
  --batch-size 20 --concurrency 20 --commit-concurrency 4 \
  --train-trials 1 --trials 5 --serial-eval-trials --eval-each-epoch \
  --model YOUR_MODEL_ID --reasoning-effort low --temperature 0.7 \
  --max-iterations 30 --max-tokens 16384 \
  --no-clean-result --output result/skillflow/exp-001/report.json
```

Evo 使用相同命令，将 dataset 改为 `evoagentbench`、服务 URL 改为 1946，user 和
输出路径也分别指定。`--domain all` 汇总 manifest 中的全部 family/domain；也可传
一个确切 family/domain 名称。OV 仍在该实验 user 的同一个经验目录中学习。

参数口径：

- `--epochs`：训练 epoch 数；0 可只评测。
- `--train-cases` / `--eval-cases`：每轮最多选择多少**不同题目**，不是 rollout 次数。
  省略即使用对应 split 的全部题目，实际数量不足时使用全部可用题。
- `--train-index` / `--eval-index`：0-based 索引或逗号列表，先按 domain 过滤，再按
  manifest 顺序解释索引。索引先应用，然后才进行抽样/数量截断。
- `--sample-seed`：固定随机选择，所有 epoch、baseline 和 final eval 使用同一子集；
  省略则按数据顺序取前 N 个。它不设置 LLM 随机种子。
- `--concurrency 1`：顺序执行 case；大于 1 即并行。`--batch-size` 决定每批 rollout
  数量与训练更新边界，`--commit-concurrency` 单独控制提交/学习并发。
- `--trials`：每个评测 case 重复多少次；`--train-trials`：每个训练 case 每 epoch
  重复多少次。默认分别为 8 和 1。
- `--serial-eval-trials`：完整跑完第 n 次评测再开始第 n+1 次；省略时重复 case 在
  同一个受并发限制的队列中执行，不承诺严格轮间屏障。
- `--eval-split train`：允许显式做训练集检查；未给 eval 索引/数量时继承 train 选择。
  `--eval-split test` 默认保持独立选择；`none` 关闭评测。
- `--model` / `--api-base` / `--reasoning-effort` / `--temperature` / `--max-tokens`：
  可覆盖 runtime 中的 agent 模型设置；原生 LLM judge 使用同一个模型及 reasoning
  设置，但保留 benchmark 原有的判分提示词和输出预算。
- `--skip-baseline-eval`、`--eval-each-epoch`、`--skip-final-eval` 控制评测阶段。

模型、运行时指纹、数量、种子、索引、并发等均进入结果/缓存配置。服务 `/health`
发布 manifest、task/checker 内容、执行代码及本地数据文件标识组成的指纹，train 在
查缓存前校验。大型外部数据缓存用文件路径/大小/mtime 标识，不应把这一缓存标识
当作全部数据的密码学完整性证明。修改服务代码/数据后应重启服务。

epoch-0 训练 rollout 缓存默认关闭；仅在明确需要复用同一初始经验状态时开启。
baseline 使用空召回，最终评测不写经验。结果报告包含各阶段准确率、训练错误及
原生轨迹位置，不能把训练集得分当作 held-out 泛化效果。
