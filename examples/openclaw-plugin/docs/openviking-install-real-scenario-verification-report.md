# OpenViking 本机安装真实场景验证报告

> 日期：2026-06-05
> 分支：`feat/sharClawWithOpenViKing`
> 仓库：`iaasng/arkclaw-openviking-plugin`
> 本机 OpenClaw：`OpenClaw 2026.5.28 (e932160)`
> 安装插件版本：`2026.6.2`
> OpenViking 服务：`https://api.vikingdb.cn-beijing.volces.com/openviking`，健康检查版本 `v0.3.21`
> 结论：安装、配置、真实 agent 调用、HTTP 接口、WebSocket RPC、JSONL 召回轨迹、边界用例均已完成验证；发现并修复了 HTTP route 注册兼容问题与 Gateway 查询参数大小写/别名问题。

## 1. 验证目标

本次验证基于本机已安装的 `openclaw` 和本仓库构建出的 OpenViking 插件包，覆盖以下目标：

1. 安装包能真实安装到 `~/.openclaw/extensions/openviking`，并被 OpenClaw Gateway 加载。
2. 插件配置符合预期：remote 模式、API key 可用、`traceRecall` 和 `traceRecallPersist` 开启、资源召回范围配置正确。
3. OpenClaw agent 真实场景可调用 `ov_search`，返回 OpenViking `viking://` URI。
4. Recall Trace HTTP API 注册并可访问，成功响应和边界错误响应符合预期。
5. OpenClaw Gateway WebSocket RPC 能发现 OpenViking 工具，并可通过 `tools.invoke` 调用 `ov_search`、`ov_read`、`memory_recall` 等插件工具。
6. Recall trace JSONL 能写入本地磁盘，结构符合 `RecallTraceEntry` schema，条数符合配置上限。
7. 单测、类型检查、构建流程通过，避免回归。

## 2. 关键实现与修复位置

| 项目 | 说明 | 代码位置 |
|---|---|---|
| 兼容 OpenClaw 新 HTTP route API | 同时支持 legacy `registerRoute` 与现代 `registerHttpRoute` | `index.ts:885`、`index.ts:887`、`index.ts:888` |
| HTTP route 注册 | 注册 `/api/openviking/recall-traces`、`:traceId`、`uri-detail`、`latest-ov-search-list` | `index.ts:1144`、`index.ts:1187`、`index.ts:1198` |
| HTTP 响应包装 | 统一 `Content-Type: application/json`，非 GET 返回 405 | `index.ts:1164`、`index.ts:1173` |
| Gateway session key 参数别名 | 兼容 `sessionkey`、`session_key`、`session-key`，避免 HTTP 层对 camelCase/保留字段处理导致查不到 trace | `index.ts:917`、`index.ts:1049`、`index.ts:1135` |
| Gateway RPC trace 默认身份 | `ov_recall_trace` 默认使用外层 `params.sessionKey` 查询当前 session trace；仅显式传 `args.sessionKey/sessionId/ovSessionId/traceId` 时按显式过滤条件查询 | `index.ts:829`、`index.ts:855`、`index.ts:893` |
| 历史 JSONL 身份兼容 | 默认 `sessionKey` 查询未命中时，fallback 到当前 session 的 `sessionId/ovSessionId`，兼容旧版本或 web session 只按 UUID 落盘的 trace | `index.ts:901`、`index.ts:908`、`index.ts:911` |
| JSONL 持久化 | 每天一个 JSONL 文件，按 schema 写入并支持查询回退 | `recall-trace.ts:256`、`recall-trace.ts:284`、`recall-trace.ts:431` |
| 配置归一化 | `traceRecall`、`traceRecallPersist`、`traceRecallDir`、`traceRecallMaxEntries` 等配置归一化 | `config.ts:525`、`config.ts:526`、`config.ts:527`、`config.ts:543` |
| 回归测试 | HTTP route 注册、session key alias 查询新增测试覆盖 | `tests/ut/tools.test.ts:1317`、`tests/ut/tools.test.ts:1446` |

## 3. 安装与配置验证

### 3.1 构建与安装

执行：

```bash
./build.sh
./output/install.sh --source local --tarball ./output/openviking.tgz --openclaw-state-dir "$HOME/.openclaw"
```

结果：

- `./build.sh` 成功生成：
  - `output/openviking.tgz`
  - `output/install.sh`
  - `output/volcengine-install.sh`
- 安装脚本成功部署到：`/Users/bytedance/.openclaw/extensions/openviking`
- `openclaw openviking status --json` 返回健康：
  - `health.ok=true`
  - `health.version=v0.3.21`
  - `health.pluginVersion=2026.6.2`
  - `slotActive=true`

### 3.2 插件运行时注册

执行：

```bash
openclaw plugins inspect openviking --runtime --json
```

结果：

- `status=loaded`
- `contextEngineIds=["openviking"]`
- `services=["openviking"]`
- `commands=["add-resource","add-skill","ov-search","ov-query-config","ov-recall-trace"]`
- `toolNames` 包含 `ov_search`、`ov_read`、`memory_recall`、`ov_recall_trace`
- `httpRouteCount=4`，符合预期。

### 3.3 验证配置

执行：

```bash
openclaw config get plugins.entries.openviking.config --json
```

关键配置已恢复并验证：

```json
{
  "mode": "remote",
  "traceRecall": true,
  "traceRecallPersist": true,
  "traceRecallDir": "/Users/bytedance/.openclaw/openviking/recall-traces",
  "traceRecallMaxEntries": 200,
  "traceRecallMaxResultsPerSearch": 20,
  "recallTargetTypes": ["resource"],
  "autoRecall": true,
  "autoCapture": true,
  "logFindRequests": true,
  "emitStandardDiagnostics": true,
  "commitTokenThresholdRatio": 0
}
```

## 4. 测试用例与结果

| ID | 测试项 | 步骤 | 预期 | 结果 |
|---|---|---|---|---|
| TC-01 | 插件包构建 | 执行 `./build.sh` | typecheck、全量单测、build、tgz 生成成功 | 通过：29 个测试文件、564 个测试通过 |
| TC-02 | 本地安装 | `output/install.sh --source local --tarball ./output/openviking.tgz` | 安装到 `~/.openclaw/extensions/openviking`，Gateway 重启 | 通过 |
| TC-03 | 健康检查 | 安装脚本内执行 setup/status | OpenViking 服务连接成功 | 通过：`version=v0.3.21` |
| TC-04 | Runtime 注册 | `openclaw plugins inspect openviking --runtime --json` | tools/services/context engine/routes 均注册 | 通过：`httpRouteCount=4` |
| TC-05 | 真实 agent 场景 | `openclaw agent --agent main --session-key agent:main:ov-install-verify-jsonl-20260605 --message ... --json` | agent 调用 `ov_search` 并返回 URI | 通过：返回 `viking://resources/openclaw-plugin-config/.abstract.md` |
| TC-06 | Recall traces 列表 API | `GET /api/openviking/recall-traces?turn=all&limit=10` | 返回 JSON，包含 trace entries | 通过 |
| TC-07 | traceId 查询 API | `GET /api/openviking/recall-traces/<traceId>?limit=1` | 返回指定 trace | 通过：返回 `ov_search-1780628249097-lpxfpu9b` |
| TC-08 | latest ov_search 清单 API | `GET /api/openviking/recall-traces/latest-ov-search-list?limit=5` | 返回最近 ov_search 的扁平 item 列表 | 通过：返回 5 个 item |
| TC-09 | URI detail API | `GET /api/openviking/uri-detail?uri=...&includeContent=true&contentLimit=120` | 返回内容片段和分页元数据 | 通过：`readStatus=ok`、`returnedChars=120` |
| TC-10 | 无效 URI 边界 | `GET /api/openviking/uri-detail?uri=not-viking` | HTTP 400，`error.code=invalid_uri` | 通过 |
| TC-11 | 无效 limit 边界 | `GET /latest-ov-search-list?limit=0` | HTTP 400，`error.code=invalid_param` | 通过 |
| TC-12 | 非 GET 边界 | `POST /api/openviking/recall-traces` | HTTP 405，`error.code=method_not_allowed` | 通过 |
| TC-13 | JSONL 文件存在 | 检查 `~/.openclaw/openviking/recall-traces/2026-06-05.jsonl` | 文件存在并有新增 trace | 通过：7 行 |
| TC-14 | JSONL schema | 逐行校验 required fields、source、operationType、stats、selected URI | 0 个 schema 错误 | 通过 |
| TC-15 | JSONL 配置上限 | line count <= `traceRecallMaxEntries=200` | 不超过上限 | 通过：7 <= 200 |
| TC-16 | session key HTTP alias | 使用 `sessionkey`/`session_key` 查询 trace | 可从 persistent layer 查到对应 session trace | 通过；并新增单测覆盖 |
| TC-17 | Gateway RPC health | `openclaw gateway call health --json` | Gateway 返回健康状态 | 通过：`ok=true`、`runtimeVersion=2026.5.28` |
| TC-18 | Gateway RPC status | `openclaw gateway call status --json` | 返回运行时版本、默认 agent、session 状态 | 通过：`defaultAgentId=main`、`eventLoop.degraded=false` |
| TC-19 | Gateway RPC system-presence | `openclaw gateway call system-presence --json` | 返回 Gateway/CLI 节点 presence | 通过：返回 macOS gateway 节点与 CLI probe 节点 |
| TC-20 | Gateway RPC tools.catalog | `openclaw gateway call tools.catalog --params '{"agentId":"main"}' --json` | 工具目录包含 OpenViking 插件工具 | 通过：14 个 group，12 个 OpenViking 工具 |
| TC-21 | Gateway RPC tools.effective | 使用已存在 sessionKey `agent:main:ov-install-verify-jsonl-20260605` | 当前 session 可用工具包含 OpenViking 工具 | 通过：2 个 group，12 个 OpenViking 工具 |
| TC-22 | Gateway RPC tools.invoke/ov_search | `tools.invoke` 调用 `ov_search` 搜索 `openclaw plugin config` | 返回 `ok=true` 与 `viking://` URI | 通过：返回 2 条资源，首条为 `viking://resources/openclaw-plugin-config/.abstract.md` |
| TC-23 | Gateway RPC tools.invoke/ov_read | `tools.invoke` 调用 `ov_read` 读取首条 URI | 返回完整内容文本 | 通过：`ok=true`、返回 335 字符内容 |
| TC-24 | Gateway RPC tools.invoke/memory_recall | `tools.invoke` 调用 `memory_recall` | 返回召回结果且工具执行成功 | 通过：`ok=true`、`total=19` |
| TC-25 | Gateway RPC 未知 method 边界 | `openclaw gateway call does.not.exist --params '{}' --json` | Gateway 拒绝未知 RPC 方法 | 通过：`unknown method: does.not.exist` |
| TC-26 | Gateway RPC 无效 session 边界 | `tools.effective` 传不存在 sessionKey | Gateway 返回 unknown session key | 通过：`unknown session key` |
| TC-27 | Gateway RPC 未知工具边界 | `tools.invoke` 调用 `not_a_tool` | `payload.ok=false`/`ok=false`，错误码 `not_found` | 通过：`Tool not available: not_a_tool` |
| TC-28 | Gateway RPC 工具参数边界 | `ov_search` 缺少必填 `query`；`ov_read` 传 `not-viking` | 工具调用失败且不影响 Gateway | 通过：均返回 `ok=false`、`error.code=internal_error` |
| TC-29 | Gateway RPC trace 最新查询 | `tools.invoke` 调用 `ov_recall_trace`，`args={"turn":"latest","limit":5}` | 返回最近 trace | 通过：`count=1`，返回 `memory_recall-1780635643409-v243scn9` |
| TC-30 | Gateway RPC trace source 过滤 | `ov_recall_trace`，`turn=all`、`source=ov_search`、`limit=10` | 返回 ov_search trace | 通过：返回 `ov_search-1780635606119-h2fl11l5` |
| TC-31 | Gateway RPC traceId 精确查询 | `ov_recall_trace`，`traceId=ov_search-1780635606119-h2fl11l5` | 返回指定 trace | 通过：`count=1`、`source=ov_search` |
| TC-32 | Gateway RPC trace includeContent | `ov_recall_trace`，`traceId=...`、`includeContent=true` | selected 条目带内容预览 | 通过：`selected[0]` 包含 `contentPreview` |
| TC-33 | Gateway RPC trace sessionKey 过滤 | `ov_recall_trace`，`turn=all`、`sessionKey=agent:main:ov-install-verify-jsonl-20260605` | 返回该 session 的 trace | 通过：`count=1`、`lookupLayer=memory` |
| TC-34 | Gateway RPC trace 空结果边界 | `ov_recall_trace` 查询不存在 traceId / 不匹配 sessionKey / 不匹配 source | 返回空结果而非错误 | 通过：`ok=true`、`count=0`、`entries=[]` |
| TC-35 | Gateway RPC trace limit 边界 | `ov_recall_trace`，`turn=all`、`limit=0` | 参数被安全归一化或回退，不影响 Gateway | 通过：返回最近 1 条，`ok=true` |

## 5. 真实场景验证记录

### 5.1 Agent 调用结果

执行：

```bash
openclaw agent \
  --agent main \
  --session-key agent:main:ov-install-verify-jsonl-20260605 \
  --message "请使用 OpenViking 的 ov_search 搜索 openclaw plugin config 相关资源，并返回最相关的 viking URI；这是安装验收测试。" \
  --json \
  --timeout 600
```

结果摘要：

- `status=ok`
- `toolSummary.calls=1`
- `toolSummary.tools=["ov_search"]`
- `toolSummary.failures=0`
- agent 返回：`viking://resources/openclaw-plugin-config/.abstract.md`

### 5.2 JSONL 验证结果

目标文件：

```text
/Users/bytedance/.openclaw/openviking/recall-traces/2026-06-05.jsonl
```

校验输出摘要：

```text
line_count 7
within_max_entries True
latest_traceId memory_recall-1780635643409-v243scn9
latest_source memory_recall
latest_sessionId null
latest_sessionKey agent:main:ov-install-verify-jsonl-20260605
latest_searches 1
latest_selected 2
source_counts {"auto_recall":3,"ov_search":3,"memory_recall":1}
errors 0
```

## 6. 接口边界验证记录

| API | 场景 | 状态码 | 关键字段 | 结论 |
|---|---|---:|---|---|
| `/api/openviking/recall-traces/<traceId>` | 指定 traceId | 200 | `ok=true`、`entries.length=1` | 通过 |
| `/api/openviking/recall-traces/latest-ov-search-list` | 最近 ov_search | 200 | `ok=true`、`items.length=5` | 通过 |
| `/api/openviking/uri-detail` | 正常 URI + content | 200 | `readStatus=ok`、`uriType=resource` | 通过 |
| `/api/openviking/uri-detail` | 非 `viking://` URI | 400 | `error.code=invalid_uri` | 通过 |
| `/api/openviking/recall-traces/latest-ov-search-list` | `limit=0` | 400 | `error.code=invalid_param` | 通过 |
| `/api/openviking/recall-traces` | POST | 405 | `error.code=method_not_allowed` | 通过 |

备注：HTTP Gateway 对 `sessionKey` 这类 camelCase/query 保留字段的转发存在不稳定表现，真实 HTTP 验证中 `sessionkey` 和 `session_key` 可稳定命中。已在插件中加入 alias 兼容，并新增回归单测。

## 7. WebSocket RPC 协议验证记录

### 7.1 Gateway RPC 基础接口

本轮使用 `openclaw gateway call <method> --params '<json>' --json` 覆盖 OpenClaw Gateway WebSocket RPC。该 CLI 底层连接 Gateway WebSocket，使用与外部客户端一致的 RPC 方法与参数对象。

| 方法 | 参数 | 结果摘要 | 结论 |
|---|---|---|---|
| `health` | `{}` | `ok=true`、`runtimeVersion=2026.5.28`、`eventLoop.degraded=false` | 通过 |
| `status` | `{}` | `runtimeVersion=2026.5.28`、`defaultAgentId=main`、`mainHeartbeatEnabled=true`、`eventLoopDegraded=false` | 通过 |
| `system-presence` | `{}` | 返回 `SuperOpsByteDance.local` gateway 节点与 `cli` probe 节点 | 通过 |
| `tools.catalog` | `{"agentId":"main"}` | `group_count=14`，OpenViking 工具 12 个 | 通过 |
| `tools.effective` | `{"sessionKey":"agent:main:ov-install-verify-jsonl-20260605"}` | `agentId=main`、`profile=full`、`group_count=2`，OpenViking 工具 12 个 | 通过 |

`tools.catalog` 与 `tools.effective` 均确认 OpenViking 插件工具已经进入 Gateway 工具系统，工具清单包括：

```text
add_skill, memory_forget, memory_recall, memory_store,
openviking_tool_result_list, openviking_tool_result_read, openviking_tool_result_search,
ov_archive_expand, ov_archive_search, ov_read, ov_recall_trace, ov_search
```

### 7.2 OpenViking 工具 RPC 调用

| 方法 | 工具 | 参数摘要 | 结果摘要 | 结论 |
|---|---|---|---|---|
| `tools.invoke` | `ov_search` | `query="openclaw plugin config"`、`limit=2`、`sessionKey=agent:main:ov-install-verify-jsonl-20260605` | `ok=true`，返回 2 条 resource，首条 URI 为 `viking://resources/openclaw-plugin-config/.abstract.md` | 通过 |
| `tools.invoke` | `ov_read` | 读取 `viking://resources/openclaw-plugin-config/.abstract.md` | `ok=true`，返回 OpenViking content，文本长度 335 | 通过 |
| `tools.invoke` | `memory_recall` | `query="openclaw plugin config"`、`limit=2`、`resourceTypes=["resource"]` | `ok=true`，`toolName=memory_recall`，`total=19` | 通过 |
| `tools.invoke` | `ov_recall_trace` | `turn="latest"`、`limit=5` | `ok=true`，返回最近 1 条 trace：`memory_recall-1780635643409-v243scn9` | 通过 |

`ov_search` 返回内容示例：

```text
Found 2 OpenViking results for "openclaw plugin config"
1 resource viking://resources/openclaw-plugin-config/.abstract.md score=0.61
2 resource viking://resources/大模型研究/Agent调研/01_OpenClaw生态与最佳实践/openclaw_codex_report/.abstract.md score=0.57
```

### 7.3 RPC 边界用例

| 场景 | 命令/参数 | 实际结果 | 结论 |
|---|---|---|---|
| 未知 RPC method | `openclaw gateway call does.not.exist --params '{}' --json` | `GatewayClientRequestError: unknown method: does.not.exist` | 通过：Gateway 层拒绝未知方法 |
| `tools.effective` 使用不存在 sessionKey | `sessionKey=agent:main:not-exists-openviking-rpc-20260605` | `GatewayClientRequestError: unknown session key ...` | 通过：sessionKey 必须存在 |
| `tools.invoke` 调用不存在工具 | `name=not_a_tool` | `ok=false`、`error.code=not_found`、`Tool not available: not_a_tool` | 通过：工具层返回结构化错误 |
| `ov_search` 缺少必填 query | `args={"limit":2}` | `ok=false`、`toolName=ov_search`、`error.code=internal_error` | 通过：工具失败被包装为失败响应，Gateway 未崩溃 |
| `ov_read` 传非 `viking://` URI | `args={"uri":"not-viking"}` | `ok=false`、`toolName=ov_read`、`error.code=internal_error` | 通过：工具失败被包装为失败响应，Gateway 未崩溃 |

### 7.4 Trace RPC 接口专项验证

通过 WebSocket RPC 调用 trace 相关能力时，统一入口是 `tools.invoke` + `name="ov_recall_trace"`，与 HTTP route 的 `/api/openviking/recall-traces` 系列接口覆盖同一批 trace 数据。专项验证使用 session key：`agent:main:ov-install-verify-jsonl-20260605`。

本机可直接粘贴执行的 OpenClaw 命令如下：

```bash
# 1. 查询最新 trace
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"latest","limit":5}}' \
  --json

# 2. 按 source 查询 ov_search trace
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"all","source":"ov_search","limit":10}}' \
  --json

# 3. 按 traceId 精确查询
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"traceId":"ov_search-1780635606119-h2fl11l5","limit":1}}' \
  --json

# 4. 按 traceId 查询并展开 selected 内容预览
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"traceId":"ov_search-1780635606119-h2fl11l5","includeContent":true,"limit":1}}' \
  --json

# 5. 按 sessionKey 过滤 trace
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"all","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","limit":20}}' \
  --json

# 6. 查询不存在的 traceId，验证空结果边界
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"traceId":"not-exist-trace-20260605","limit":1}}' \
  --json

# 7. 查询不匹配的 sessionKey，验证空结果边界
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"all","sessionKey":"agent:main:no-trace-session-20260605","limit":5}}' \
  --json

# 8. 查询不匹配的 source，验证空结果边界
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"all","source":"not_a_source","limit":5}}' \
  --json

# 9. limit=0 边界验证
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"all","limit":0}}' \
  --json
```

| 场景 | 参数摘要 | 实际结果 | 结论 |
|---|---|---|---|
| 查询最新 trace | `args={"turn":"latest","limit":5}` | `ok=true`、`count=1`、`lookupLayer=memory`，返回 `memory_recall-1780635643409-v243scn9` | 通过 |
| 按 source 查询 `ov_search` | `args={"turn":"all","source":"ov_search","limit":10}` | `ok=true`、`count=1`、返回 `ov_search-1780635606119-h2fl11l5` | 通过 |
| 按 traceId 精确查询 | `args={"traceId":"ov_search-1780635606119-h2fl11l5","limit":1}` | `ok=true`、`count=1`、`source=ov_search`、`stats.selectedCount=2` | 通过 |
| 查询并展开内容预览 | `args={"traceId":"ov_search-1780635606119-h2fl11l5","includeContent":true,"limit":1}` | `ok=true`、`lookupLayer=persistent`，`selected[0]` 包含 `contentPreview` 字段 | 通过 |
| 按 sessionKey 查询 | `args={"turn":"all","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","limit":20}` | `ok=true`、`count=1`，返回当前 session 最近 trace | 通过 |
| 不存在 traceId | `args={"traceId":"not-exist-trace-20260605","limit":1}` | `ok=true`、`count=0`、`entries=[]`，文本提示 `No OpenViking recall traces found` | 通过 |
| 不匹配 sessionKey | `args={"turn":"all","sessionKey":"agent:main:no-trace-session-20260605","limit":5}` | `ok=true`、`count=0`、`entries=[]` | 通过 |
| 不匹配 source | `args={"turn":"all","source":"not_a_source","limit":5}` | `ok=true`、`count=0`、`entries=[]` | 通过 |
| `limit=0` 边界 | `args={"turn":"all","limit":0}` | `ok=true`，返回最近 1 条 trace；Gateway 与插件均未异常 | 通过 |

Trace RPC 返回结构关键字段：

```json
{
  "ok": true,
  "toolName": "ov_recall_trace",
  "output": {
    "details": {
      "action": "queried",
      "count": 1,
      "lookupLayer": "memory|persistent",
      "warnings": [],
      "entries": ["RecallTraceEntry"]
    }
  },
  "source": "plugin"
}
```

结论：trace 相关能力通过 WebSocket RPC 路径验证通过。`ov_recall_trace` 支持 latest/all、source、traceId、sessionKey、includeContent 等查询场景；无匹配数据时返回 `ok=true` + 空 `entries`，工具参数边界不会导致 Gateway 异常。

结论：OpenClaw Gateway WebSocket RPC 可用于自动化发现和调用 OpenViking 插件工具；`tools.effective` 需要传入已存在的 session key，`tools.catalog` 可用于按 agent 查看完整工具目录，OpenViking 工具调用成功与失败均能通过 RPC 返回可识别结果。

## 8. 自动化回归验证

### 8.1 针对性测试

```bash
npm run typecheck
npm test -- --run tests/ut/recall-trace.test.ts tests/ut/tools.test.ts tests/ut/config.test.ts tests/ut/manifest-contracts.test.ts
npm run build
```

结果：

- TypeScript typecheck：通过
- targeted tests：4 个测试文件、143 个测试通过
- build：通过

### 8.2 全量构建验证

`./build.sh` 已执行完整流程：

- `npm install`：通过
- `npm run typecheck`：通过
- `npm test`：通过，29 个测试文件、564 个测试全部通过
- `npm run build`：通过
- package staging + production dependencies：通过
- `output/openviking.tgz` 生成：通过

### 8.3 2026-06-08 追加验证：真实 sessionKey trace 查询修复

背景：线上下载的 JSONL trace 文件 `/Users/bytedance/Downloads/2026-06-08.jsonl` 中，每条记录都包含真实 `sessionKey`，同时 `sessionId/ovSessionId` 为同一个 web session UUID。旧版查询逻辑在 `tools.invoke` 只传外层 `params.sessionKey` 时，可能退化为使用由 `sessionKey` 派生出的 `sha256` 作为 `ovSessionId` 查询；该派生值与线上 JSONL 中保存的 UUID 不一致，导致返回空。

本次修复后验证：

```bash
npm run lint --if-present
npm run typecheck
npm test
npm run build
```

结果：

- lint：项目未配置 lint script，`npm run lint --if-present` 正常退出。
- TypeScript typecheck：通过。
- 全量测试：29 个测试文件、567 个测试全部通过。
- build：通过。

新增/覆盖场景：

| 场景 | 期望 | 结果 |
|---|---|---|
| `ov_recall_trace` 默认查询当前 session | 外层 `params.sessionKey` 作为默认 trace 身份过滤条件 | 通过 |
| 显式 `args.sessionKey` 查询历史 session | 只按显式 `entry.sessionKey` 过滤，不叠加派生 `ovSessionId` | 通过 |
| 历史 JSONL 只有 `ovSessionId`、缺少 `sessionKey` | 默认查询未命中时 fallback 到当前 session 的 `sessionId/ovSessionId` | 通过 |
| 线上 JSONL 回放 | 用真实 `sessionKey=agent:main:web-c6592a8e-5448-4622-9e7f-31a07447eee7` 可查询到 10 条 | 通过 |

发布验证：

- 版本：`2026.6.5`
- 日期路径：`2026.6.8/`
- TOS 对象：`install.sh`、`openviking.tgz`、`manifest.json` 已上传并 head 校验通过。
- `latest/`：未更新。

## 9. 发现的问题与处理

| 问题 | 影响 | 处理 | 状态 |
|---|---|---|---|
| 初始 runtime inspect 不带 `--runtime` 时 `httpRouteCount=0` | 容易误判未注册 | 使用 `--runtime` 加载插件后验证；真实 runtime 为 4 | 已澄清 |
| OpenClaw v2026.5.28 使用 `registerHttpRoute`，旧插件只注册 legacy route 时 HTTP API 不可见 | Recall Trace HTTP API 无法访问 | 插件兼容 `registerHttpRoute`，并保留 legacy `registerRoute` | 已修复 |
| 安装脚本执行 setup 后会重置部分高级配置 | `traceRecall`/JSONL 验证配置被清空 | 安装后重新写入验证配置并重启 Gateway | 已处理 |
| HTTP Gateway camelCase `sessionKey` 查询不稳定 | 按 session key 查询 trace 可能返回 0 | 增加 `sessionkey`、`session_key`、`session-key` alias；真实验证使用 alias | 已修复并测试 |
| `tools.effective` 传不存在的 session key | Gateway 返回 `unknown session key`，无法列出有效工具 | 使用真实已存在 session key `agent:main:ov-install-verify-jsonl-20260605`；文档中补充前置条件 | 已澄清 |
| Gateway RPC 只传外层 `params.sessionKey` 时 trace 查询为空 | 旧版查询没有默认使用外层 `sessionKey`，而是可能按派生 `ovSessionId` 查询；派生值与线上 UUID 不一致 | 默认按外层 `params.sessionKey` 查询；未命中时兼容 fallback 到当前 session 的 `sessionId/ovSessionId` | 已修复并测试 |

## 10. 最终结论

本机安装测试验证通过：

- 插件包可构建、可本地安装、可被 OpenClaw Gateway 加载。
- OpenViking remote 配置、健康检查、slot active 均符合预期。
- 真实 agent 场景中 `ov_search` 能正常调用并返回资源 URI。
- OpenClaw Gateway WebSocket RPC 能发现并调用 OpenViking 插件工具，`tools.catalog`、`tools.effective`、`tools.invoke`、`ov_recall_trace` 正向与边界用例均符合预期。
- Recall Trace HTTP APIs 可访问，成功和错误边界响应符合预期。
- JSONL trace 文件已落盘，schema 校验 0 错误，配置上限符合预期。
- 新增/相关回归测试均通过，全量构建测试通过。

建议后续发布前将 `output/openviking.tgz` 与 `output/install.sh` 作为当前验收产物重新上传到目标 TOS release path，避免线上安装包仍停留在旧版本或旧配置行为。
