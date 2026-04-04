# OpenViking 健康检查工具

`ov-healthcheck.py` 端到端验证 OpenClaw + OpenViking 插件链路。通过一次真实 Gateway 会话，再从 OpenViking 侧检查结果。

## 快速开始

```bash
python examples/openclaw-plugin/ov-healthcheck.py
```

只依赖 Python 标准库。地址和 token 会从 `openclaw.json` 自动读取。

## 期望输出

正常运行结果如下：

```
OpenViking Plugin Healthcheck
Gateway: http://127.0.0.1:18789
OpenViking: http://127.0.0.1:1933
...

[PASS] OpenClaw config discovered (...)
[PASS] plugins.slots.contextEngine is openviking
[PASS] Gateway health check succeeded
[PASS] OpenViking health check succeeded

Phase 1: real conversation
[PASS] Chat turn 1 succeeded (reply_len=151)
[PASS] Chat turn 2 succeeded (reply_len=131)
[PASS] Chat turn 3 succeeded (reply_len=115)
[PASS] Chat turn 4 succeeded (reply_len=77)

Phase 2: OpenViking session inspection
[PASS] Probe session located in OpenViking (...)
[PASS] Captured session context contains the probe marker
[PASS] Captured session context contains seeded facts (go,postgresql,redis,70)

Phase 3: commit, context, and memory checks
[PASS] OpenViking commit accepted (accepted)
Waiting up to 300s for commit, archive, and memory extraction...
[PASS] Session commit_count is greater than zero (1)
[PASS] Memory extraction produced results (total=1)
[PASS] Context endpoint returned latest_archive_overview

Phase 4: follow-up through Gateway
[PASS] Same-session follow-up recalled earlier facts (go,postgresql,redis,70)
[PASS] Fresh-session recall returned seeded stack facts (...)

Summary
PASS=18 WARN=0 FAIL=0 SKIP=0

Healthcheck passed.
```

Phase 3 会等待异步 commit 完成（默认最多 300 秒）。这是正常的——commit 涉及 LLM 调用来做归档和记忆抽取。

所有测试消息都带有 `[OPENVIKING-HEALTHCHECK]` 前缀并标记为合成数据，不会干扰用户的真实对话。

## 工作原理

脚本通过注入一组受控对话，再追踪其在系统中的流转来验证插件链路。

**Probe 标记** — 每次运行生成一个唯一随机标记（如 `probe-a1b2c3d4`），嵌入第一条消息中。后续通过这个标记在 OpenViking 中精确定位本次测试的 session，不会和用户真实对话混淆。

**Phase 1：对话注入** — 脚本通过 Gateway `/v1/responses` 接口发送 4 条合成消息，模拟一次真实用户对话。消息中包含已知事实（技术栈、Kafka topic、服务地址等），作为后续验证的锚点。所有消息都带 `[OPENVIKING-HEALTHCHECK]` 前缀，让 LLM 和 OpenViking 能区分测试数据和真实对话。

**Phase 2：捕获验证** — 等待一小段时间（`--capture-wait`）后，脚本查询 OpenViking sessions API，逐个扫描 session 的 context 寻找 probe 标记。找到标记说明插件的 `afterTurn` 钩子成功将 Gateway 对话写入了 OpenViking。

**Phase 3：Commit 和记忆验证** — 脚本通过 OpenViking API 触发 commit，然后轮询（最多 `--commit-wait` 秒）直到三个条件同时满足：
- `commit_count > 0` — commit 已完成
- `latest_archive_overview` 存在 — 对话已归档
- `memories_extracted > 0` — 记忆抽取产生了结果

这确认了完整的异步流水线：对话归档、概要生成、记忆抽取。

**Phase 4：召回验证** — 通过 Gateway 再发两个问题：
1. 同 session 追问，询问之前对话中的事实。检查回复是否包含关键词（`go`、`postgresql`、`redis`、`70`）。验证 session 内的上下文连续性。
2. 新 session 追问（使用新 user ID），询问只有通过记忆召回才能获得的事实。检查回复是否包含关键词（`order_events_v2`、`payment-cb.internal:9443`）。验证 `autoRecall` 是否在新 session 中注入了存储的记忆。

**关键词匹配** — 脚本不要求精确复述。它将模型回复转为小写，检查目标关键词中是否至少命中 2 个。这容忍了模型的改写，同时能捕捉到完全的召回失败。

## 输出含义

- `PASS` — 确认正常
- `INFO` — 补充信息，不代表异常
- `WARN` — 主链路可用，但该项未稳定确认
- `FAIL` — 明确故障，脚本返回非 0

## 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--gateway <url>` | 自动 | Gateway 地址 |
| `--openviking <url>` | 自动 | OpenViking 地址 |
| `--token <token>` | 自动 | Gateway bearer token |
| `--openviking-api-key <key>` | 自动 | OpenViking API key |
| `--agent-id <id>` | `default` | OpenViking agent id |
| `--user-id <id>` | 随机 | 测试会话的 user id |
| `--openclaw-config <path>` | 自动 | `openclaw.json` 路径 |
| `--chat-timeout <秒>` | `120` | 每次 Gateway 聊天请求的超时 |
| `--commit-wait <秒>` | `300` | 等待 commit、归档、记忆抽取完成的最大时间 |
| `--capture-wait <秒>` | `4` | 聊天结束后等待 OpenViking 捕获的时间 |
| `--delay <秒>` | `1` | 聊天轮次间隔 |
| `--session-scan-limit <n>` | `0`（全部） | 扫描 session 的上限（0 = 扫描全部） |
| `--insecure` | 关 | 跳过 SSL 证书验证（自签证书场景） |
| `--strict-warnings` | 关 | 有 WARN 时也返回非 0 |
| `--json-out <path>` | — | 输出 JSON 报告 |
| `--verbose` / `-v` | 关 | 打印调试信息 |

## 故障处理

### `Gateway health check failed`

```bash
openclaw gateway status
curl http://127.0.0.1:<端口>/health
openclaw logs --follow
```

### `OpenViking health check failed`

```bash
curl http://127.0.0.1:<端口>/health
cat ~/.openviking/ov.conf
```

检查 `storage.workspace/log/openviking.log`。

### `Probe session not found in OpenViking`

会话已发出但插件未写入 OpenViking。

```bash
openclaw config get plugins.slots.contextEngine
openclaw config get plugins.entries.openviking.config
openclaw logs --follow
```

常见原因：插件未加载、`autoCapture` 关闭、路由或写入失败。

### `Session commit_count is still zero after waiting`

Commit 是异步的，涉及 LLM 调用。如果超时：

1. 检查 commit 任务是否还在运行：`curl http://127.0.0.1:<端口>/api/v1/tasks`
2. 如果还在运行，用 `--commit-wait 600` 重跑
3. 如果卡住，检查 `storage.workspace/log/openviking.log`
4. 确认 LLM 后端可达且正常响应

### `Context endpoint has no archive overview after waiting`

归档概要在 commit 过程中生成。如果 commit 成功但概要缺失：

```bash
curl "http://127.0.0.1:<端口>/api/v1/sessions/<session_id>/context?token_budget=128000"
```

如果手动请求也为空，问题在 OpenViking 侧；如果手动正常，检查脚本是否指向了错误实例。

### `Fresh-session recall was inconclusive`

通常不是完全故障。常见原因：`autoRecall` 关闭、记忆抽取未完成、模型本轮未命中。先重跑一次。

### `Direct backend memory search returned no results`

这是 `INFO`，不是失败。只要 fresh-session recall 能答对，插件链路就是正常的。

## 建议排查顺序

1. 检查 `plugins.slots.contextEngine` 是否为 `openviking`
2. 检查 Gateway `/health`
3. 检查 OpenViking `/health`
4. 看 `openclaw logs --follow`
5. 看 OpenViking 日志
6. 看脚本输出里失败的具体阶段
