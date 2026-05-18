# OpenViking Server 压测脚本使用指南

本目录包含面向 OpenViking 本地 Server 的自定义压测脚本。当前主要脚本是：

- `session_contention_benchmark.py`：通用 Server 压测框架，覆盖 SDK、CLI HTTP 封装和真实 `ov` 子进程三种调用路径。

## 压测目标

`session_contention_benchmark.py` 用来请求已经启动的 OpenViking Server，验证多类接口在并发和混合负载下的吞吐、延迟、失败率和后台任务积压情况。

脚本不会启动或停止 Server，只负责：

1. 生成本地 Markdown 测试文档。
2. 每次运行前默认清空上一次压测写入的数据目录。
3. 并发请求资源添加、检索、session 写入、session commit、任务轮询和观测接口。
4. 输出中文压测报告和机器可读明细文件。

## 前置条件

先在另一个终端启动 OpenViking Server：

```bash
openviking-server
```

如果 Server 启用了 API Key 或多租户，请准备好以下信息：

- Server 地址，例如 `http://127.0.0.1:1935`
- API Key，默认参数会使用 `test-root-api-key`
- Account，默认 `default`
- User，默认 `default`

真实 CLI 子进程模式还要求当前环境能执行 `ov`：

```bash
ov health
```

## 快速开始

在仓库根目录运行 smoke 压测：

```bash
.venv/bin/python benchmark/custom/session_contention_benchmark.py \
  --server-url http://127.0.0.1:1935 \
  --profile smoke
```

只测试 Python SDK 路径：

```bash
.venv/bin/python benchmark/custom/session_contention_benchmark.py \
  --server-url http://127.0.0.1:1935 \
  --profile smoke \
  --adapters sdk
```

同时测试 SDK、CLI HTTP 封装和真实 `ov` 子进程：

```bash
.venv/bin/python benchmark/custom/session_contention_benchmark.py \
  --server-url http://127.0.0.1:1935 \
  --profile standard \
  --adapters sdk,cli-http,cli-subprocess
```

## 调用路径说明

脚本支持三种 adapter：

| Adapter | 含义 | 适用场景 |
| --- | --- | --- |
| `sdk` | 通过 `openviking.AsyncHTTPClient` 请求 Server | 评估 Python SDK 的 HTTP 调用表现 |
| `cli-http` | 直接使用 `openviking_cli.client.http.AsyncHTTPClient` | 评估 CLI 共用 HTTP client 层表现 |
| `cli-subprocess` | 每次请求真实执行一次 `ov ... --output json` | 评估真实 CLI 进程启动、配置读取、上传和输出解析成本 |

默认会同时运行三种路径：

```bash
--adapters sdk,cli-http,cli-subprocess
```

如果只关心 Server 吞吐，建议先跑 `sdk` 或 `cli-http`。如果关心用户实际执行 CLI 命令的端到端成本，再加入 `cli-subprocess`。

## 压测场景

每个 adapter 会依次执行以下阶段：

| 阶段 | 内容 |
| --- | --- |
| `warmup` | 健康检查预热 |
| `add_resources` | 并发添加多个生成的 Markdown 文档 |
| `session_messages` | 并发向不同 session 写入多轮 user / assistant 消息 |
| `retrieval` | 并发执行 `find`、`search`、`grep`、`glob` |
| `session_commit` | 并发 commit 不同 session，并轮询后台任务 |
| `mixed` | 混合资源添加、检索、session 写入、commit、观测接口和任务轮询 |

这些阶段不是只压单个接口，目的是观察 OpenViking 在真实组合负载下的退化情况。

## 数据清理策略

默认每次运行前会清理：

- Server 侧资源目录：`viking://resources/bench/load_test`
- 旧 session：所有 `bench-load-` 前缀的 session
- 本地生成数据目录：`benchmark/results/openviking_server_load/data`

默认运行结束后保留本次写入的数据，方便人工复查。下一次运行前会再次清空。

如果不想在运行前清理：

```bash
--no-clear-before-run
```

如果希望运行结束后也清理：

```bash
--cleanup-at-end
```

## 常用参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--server-url` | `http://127.0.0.1:1935` | OpenViking Server 地址 |
| `--api-key` | `test-root-api-key` | 请求使用的 API Key |
| `--account` | `default` | 请求使用的 account |
| `--user` | `default` | 请求使用的 user |
| `--adapters` | `sdk,cli-http,cli-subprocess` | 要测试的调用路径 |
| `--profile` | `standard` | 压测规模：`smoke`、`standard`、`stress` |
| `--resource-count` | 跟随 profile | 每个 adapter 添加的初始文档数 |
| `--session-count` | 跟随 profile | 每个 adapter 使用的 session 数 |
| `--phase-seconds` | 跟随 profile | 单类持续压测阶段时长 |
| `--mixed-seconds` | 跟随 profile | 混合压测阶段时长 |
| `--drain-timeout` | `60` | 等待后台任务完成的最大秒数 |
| `--data-root-uri` | `viking://resources/bench/load_test` | Server 侧压测资源根目录 |
| `--output-dir` | 自动生成 | 报告输出目录 |
| `--ov-bin` | `ov` | 真实 CLI 子进程使用的可执行文件 |

## Profile 说明

| Profile | 用途 | 特点 |
| --- | --- | --- |
| `smoke` | 快速验证脚本、配置和 Server 可用性 | 时间短、并发低、数据少 |
| `standard` | 常规压测 | 默认推荐配置 |
| `stress` | 高压力压测 | 并发和数据量更高，耗时更长 |

建议先跑 `smoke`，确认报告正常生成后再跑 `standard` 或 `stress`。

## 报告输出

默认输出目录类似：

```text
benchmark/results/openviking_server_load/20260511T120000Z/
```

主要文件：

| 文件 | 内容 |
| --- | --- |
| `summary_zh.md` | 中文压测报告，优先阅读 |
| `run_summary.json` | 汇总结果，便于自动分析 |
| `request_events.jsonl` | 每次请求的明细事件 |
| `task_events.jsonl` | 后台任务完成和积压明细 |
| `request_summary.csv` | 按 adapter / 阶段 / 接口聚合的 QPS、成功率、延迟 |
| `request_windows.csv` | 按时间窗口聚合的请求表现 |
| `adapter_comparison.csv` | SDK / CLI 路径对比 |
| `errors.csv` | 错误 Top 明细 |

报告会重点展示：

- 总请求量、失败数和成功率。
- 各接口 p50 / p95 / p99 / max 延迟。
- `retrieval` 阶段到 `mixed` 阶段的检索延迟变化。
- SDK、CLI HTTP 封装、真实 CLI 子进程之间的差异。
- commit / add_resource 后台任务是否积压。
- Top 错误类型和发生位置。

## 示例：指定认证信息

```bash
.venv/bin/python benchmark/custom/session_contention_benchmark.py \
  --server-url http://127.0.0.1:1935 \
  --api-key your-root-api-key \
  --account default \
  --user default \
  --profile standard
```

也可以通过环境变量传入：

```bash
export OPENVIKING_SERVER_URL=http://127.0.0.1:1935
export OPENVIKING_API_KEY=your-root-api-key
export OPENVIKING_ACCOUNT=default
export OPENVIKING_USER=default

.venv/bin/python benchmark/custom/session_contention_benchmark.py --profile smoke
```

## 示例：降低真实 CLI 子进程开销

真实 `cli-subprocess` 会为每个请求启动一次 `ov` 进程，开销明显高于 SDK 路径。若只想压 Server 本身，可以先排除它：

```bash
.venv/bin/python benchmark/custom/session_contention_benchmark.py \
  --profile standard \
  --adapters sdk,cli-http
```

若必须测试真实 CLI，但本地 `ov` 不在 PATH 中，可以指定可执行文件：

```bash
.venv/bin/python benchmark/custom/session_contention_benchmark.py \
  --profile smoke \
  --adapters cli-subprocess \
  --ov-bin .venv/bin/ov
```

## 常见问题

### 1. Server 连接失败

先确认 Server 已启动：

```bash
curl http://127.0.0.1:1935/health
```

如果端口不同，请设置 `--server-url`。

### 2. 认证失败

确认 `--api-key`、`--account`、`--user` 与当前 Server 配置一致。多租户模式下，root key 请求通常还需要 account 和 user。

### 3. `cli-subprocess` 失败

确认 `ov` 可执行：

```bash
ov health
```

如果不可用，使用 `--ov-bin .venv/bin/ov` 或只运行 `sdk,cli-http`。

### 4. 后台任务没有在 drain 内完成

这通常说明资源处理或 session commit 的后台任务积压。可以：

- 增大 `--drain-timeout`
- 降低并发参数
- 查看 `task_events.jsonl`
- 查看 Server 日志和 observer queue 状态

### 5. 多次运行结果差异较大

压测会受本机 CPU、磁盘、模型配置、队列积压和真实 CLI 进程启动开销影响。建议：

1. 先跑 `smoke` 验证环境。
2. 连续跑多次 `standard`。
3. 对比 `adapter_comparison.csv` 和 `request_windows.csv`。
4. 避免在已有大量后台任务未完成时启动下一轮。
