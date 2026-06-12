# OpenViking Recall Trace API 使用文档

> 面向插件使用者、排障同学和集成方，专门说明 OpenViking OpenClaw 插件中与 recall trace 相关的配置、Agent 工具、Slash 命令、Gateway HTTP API、返回结构和排障方式。

## 1. 功能概览

Recall Trace 是 OpenViking 插件的召回可观测能力。启用后，插件会把每一次自动召回、显式记忆召回、资源搜索、归档搜索记录成结构化 trace，便于回答以下问题：

- 本轮到底搜索了哪些语义召回范围：`resource`、`user`、`agent`？归档搜索会单独记录为 session archive trace。
- 每个范围请求的目标 URI、limit、阈值和耗时是多少？
- 候选结果有哪些？最终哪些被注入 prompt 或展示给用户？
- 为什么没有召回？是低于分数阈值、预算不足，还是搜索失败？
- Gateway 重启后，是否还能从 JSONL 持久化文件查到近期 trace？

核心实现位于 `recall-trace.ts:20`、`index.ts:704`、`index.ts:774` 和 `index.ts:784`。

## 2. 启用方式与配置项

### 2.1 最小启用配置

> 关键点：必须显式设置 `traceRecall: true`。只配置 `recallResources` 或 `recallTargetTypes` 只会改变召回范围，不会启用 trace 记录。

```json
{
  "plugins": {
    "entries": {
      "openviking": {
        "config": {
          "traceRecall": true
        }
      }
    }
  }
}
```

`traceRecall` 在配置解析中只有等于布尔值 `true` 才会启用：`config.ts:430`。插件注册阶段也只有启用后才创建 `RecallTraceRecorder`：`index.ts:704`。

### 2.2 推荐排障配置

```json
{
  "plugins": {
    "entries": {
      "openviking": {
        "config": {
          "traceRecall": true,
          "traceRecallPersist": true,
          "traceRecallDir": "~/.openclaw/openviking/recall-traces",
          "traceRecallRetentionDays": 14,
          "traceRecallMaxEntries": 1000,
          "traceRecallMaxResultsPerSearch": 20,
          "traceRecallPreviewChars": 240,
          "traceRecallQueryMaxChars": 4000,
          "traceRecallQueryMaxDays": 14,
          "recallTargetTypes": ["user", "agent", "resource"]
        }
      }
    }
  }
}
```

### 2.3 Trace 配置项

| 配置项 | 类型 | 默认值 | 取值/限制 | 说明 |
| --- | --- | --- | --- | --- |
| `traceRecall` | boolean | `false` | 必须为 `true` 才启用 | 总开关；关闭时不记录 trace，查询接口返回空并带 `traceRecall is disabled` warning。实现见 `config.ts:430`、`index.ts:778`。 |
| `traceRecallPersist` | boolean | `false` | `true`/`false` | 是否写入本地 JSONL；关闭时只保留内存环形缓存。实现见 `config.ts:431`、`recall-trace.ts:407`。 |
| `traceRecallDir` | string | `~/.openclaw/openviking/recall-traces` | 支持 `~` 展开 | JSONL 文件目录；按 UTC 日期写入 `YYYY-MM-DD.jsonl`。实现见 `config.ts:432`、`recall-trace.ts:214`。 |
| `traceRecallRetentionDays` | number | `14` | `1` 到 `3650` | 写入新 trace 时清理超过保留期的 JSONL 文件。实现见 `config.ts:436`、`recall-trace.ts:301`。 |
| `traceRecallLoadRecentDays` | number | `2` | `0` 到 `3650` | 配置已解析保留，当前查询路径主要通过内存 + 持久化 fallback 获取数据。实现见 `config.ts:442`。 |
| `traceRecallMaxEntries` | number | `1000` | `1` 到 `1000000` | 内存 ring buffer 最大条数，超出后淘汰最旧记录。实现见 `config.ts:448`、`recall-trace.ts:180`。 |
| `traceRecallMaxResultsPerSearch` | number | `20` | `1` 到 `1000` | 每次子搜索最多保存多少候选结果摘要。实现见 `config.ts:454`、`auto-recall.ts:284`。 |
| `traceRecallPreviewChars` | number | `240` | `20` 到 `10000` | 候选摘要、选中摘要的预览字符数。实现见 `config.ts:460`、`index.ts:724`。 |
| `traceRecallQueryMaxChars` | number | `4000` | `200` 到 `200000` | trace 中保存的 trigger query 最大长度，超出会截断并设置 `queryTruncated`。实现见 `config.ts:466`、`index.ts:239`。 |
| `traceRecallQueryMaxDays` | number | `14` | `1` 到 `3650` | 查询持久化 trace 且未传 `since/until` 时最多扫描最近多少天。实现见 `config.ts:472`、`recall-trace.ts:350`。 |
| `traceRecallIncludeContentByDefault` | boolean | `false` | `true`/`false` | 查询 trace 时是否默认读取 selected URI 的内容预览；也可通过查询参数 `includeContent` 单次开启。实现见 `config.ts:478`、`index.ts:744`。 |
| `traceRecallIncludeRawUserPreview` | boolean | `false` | `true`/`false` | 是否允许把原始用户输入预览持久化到 JSONL；默认会脱敏删除。实现见 `config.ts:479`、`recall-trace.ts:271`。 |

### 2.4 召回范围配置与 Trace 的关系

Trace 会记录实际召回范围，但召回范围本身由 `recallTargetTypes` / `recallResources` 决定：

| 配置 | 默认/行为 | 说明 |
| --- | --- | --- |
| `recallTargetTypes` | 默认 `['user', 'agent']` | 允许值：`resource`、`user`、`agent`；空值回退默认集合。实现见 `config.ts:176`、`recall-trace.ts:113`。 |
| `recallResources` | 默认 `false` | 兼容旧配置；仅在未显式配置 `recallTargetTypes` 时，把 `resource` 追加到默认召回集合。实现见 `config.ts:363`。 |

目标类型会被解析为以下搜索计划：`resolveRecallSearchPlan` 实现见 `recall-trace.ts:141`。

| resourceType | target URI | 说明 |
| --- | --- | --- |
| `resource` | `viking://resources` | 全局资源库。 |
| `user` | `viking://user/memories` | 当前用户长期记忆。 |
| `agent` | `viking://agent/memories` | 当前 Agent 长期记忆。 |

Session history is not a semantic recall target because the server does not vector-index session archives. Use `ov_archive_search` and `ov_archive_expand` for exact session-history recovery.

## 3. Trace 记录来源

| source | operationType | 触发方式 | selected 语义 | 关键实现 |
| --- | --- | --- | --- | --- |
| `auto_recall` | `semantic_find` | Context Engine 在回复前自动召回 | 被注入 `<relevant-memories>` 的记忆或资源，`injected: true` | `auto-recall.ts:194`、`auto-recall.ts:313` |
| `memory_recall` | `semantic_find` | Agent 调用 `memory_recall` 工具 | 工具返回给模型的记忆，通常 `injected: true` 且 `displayed: true` | `index.ts:1391`、`index.ts:1542` |
| `ov_search` | `semantic_find` | Agent 调用 `ov_search` 工具或用户执行 `/ov-search` | 搜索结果列表中展示的资源/技能/记忆，`displayed: true` | trace 记录在 `index.ts` 的 `searchOpenViking` 流程中，工具注册见 `index.ts:1371` |
| `ov_archive_search` | `archive_grep` | Agent 调用 `ov_archive_search` 工具 | 展示的归档匹配行，包含 `line`，`displayed: true` | `index.ts:1992`、`index.ts:2008` |

## 4. Trace 数据结构

### 4.1 `RecallTraceEntry`

`RecallTraceEntry` 的完整类型定义在 `recall-trace.ts:20`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schemaVersion` | `'1.0'` | Trace schema 版本。 |
| `traceId` | string | Trace 唯一 ID，通常形如 `<source>-<timestamp>-<random>`。生成逻辑见 `index.ts:235`。 |
| `ts` | number | Unix timestamp，毫秒。 |
| `sessionId` | string? | OpenClaw session ID。 |
| `sessionKey` | string? | OpenClaw session key。 |
| `ovSessionId` | string? | 映射后的 OpenViking session ID。 |
| `agentId` | string? | 实际发送到 OpenViking 的 agent ID。 |
| `source` | enum | `auto_recall`、`memory_recall`、`ov_search`、`ov_archive_search`。 |
| `operationType` | enum | `semantic_find` 或 `archive_grep`。 |
| `resourceTypes` | array | 本次 trace 覆盖的召回类型：semantic recall 使用 `resource`、`user`、`agent`；`ov_archive_search` trace 会标记为 `session`。 |
| `trigger.query` | string | 触发搜索的查询文本，受 `traceRecallQueryMaxChars` 限制。 |
| `trigger.derivedKeywords` | string[]? | 派生关键词；归档搜索通常保存原 query。 |
| `trigger.rawUserTextPreview` | string? | 原始用户输入预览；默认不持久化。 |
| `trigger.queryTruncated` | boolean? | `query` 是否因过长被截断。 |
| `searches` | array | 本次 trace 中每个目标 URI 的搜索明细。 |
| `selected` | array | 最终被注入或展示的结果。 |
| `stats` | object | 候选数、选中数、注入数、估算 token。 |

### 4.2 `searches[]`

字段定义见 `recall-trace.ts:37`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `resourceType` | `resource` \| `user` \| `agent` \| `archive` | 当前子搜索类型。 |
| `targetUriInput` | string? | 输入或计划中的目标 URI。 |
| `targetUriResolved` | string? | 解析后的目标 URI。 |
| `limit` | number | 请求 limit。自动召回和显式召回通常使用 `max(recallLimit * 4, 20)`。 |
| `scoreThreshold` | number? | 分数阈值。搜索阶段常以 `0` 取候选，后处理再过滤。 |
| `durationMs` | number | 子搜索耗时，毫秒。 |
| `total` | number | OpenViking 返回或插件统计的候选总数。 |
| `results` | array | 候选结果摘要，最多 `traceRecallMaxResultsPerSearch` 条。 |
| `archiveId` | string? | 归档搜索指定 archive 时存在。 |
| `caseInsensitive` | boolean? | 归档 grep 是否大小写不敏感。 |
| `error` | string? | 子搜索失败原因。 |

### 4.3 `results[]`

字段定义见 `recall-trace.ts:10`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `uri` | string | 候选 URI。 |
| `resourceType` | string? | 候选类型。归档匹配为 `archive`。 |
| `category` | string? | OpenViking 返回的分类。 |
| `score` | number? | 相似度分数。 |
| `level` | number? | OpenViking memory 层级；插件优先选 leaf memory。 |
| `abstractPreview` | string? | 摘要预览。 |
| `resultType` | enum | `memory`、`resource`、`skill`、`archive_match`。 |

### 4.4 `selected[]`

字段定义见 `recall-trace.ts:50`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `uri` | string | 选中结果 URI。 |
| `resourceType` | string? | 选中结果类型。 |
| `category` | string? | 分类。 |
| `score` | number? | 分数。 |
| `line` | number? | 归档匹配所在行号。 |
| `abstractPreview` | string? | 选中结果摘要预览。 |
| `contentPreview` | string? | 仅当查询时开启 `includeContent`，并成功读取 URI 内容后出现。 |
| `readError` | string? | 开启 `includeContent` 但读取内容失败时出现。 |
| `injected` | boolean? | 是否注入模型上下文。 |
| `displayed` | boolean? | 是否展示给用户或工具调用结果。 |
| `skippedReason` | enum? | 预留跳过原因：`score_threshold`、`dedupe`、`non_leaf`、`budget`、`not_top_k`、`search_error`。 |

### 4.5 返回示例

```json
{
  "schemaVersion": "1.0",
  "traceId": "ov_search-1780329600000-a1b2c3d4",
  "ts": 1780329600000,
  "sessionId": "test-session",
  "sessionKey": "agent:main:example",
  "ovSessionId": "8d6e...",
  "agentId": "main",
  "source": "ov_search",
  "operationType": "semantic_find",
  "resourceTypes": ["resource"],
  "trigger": {
    "query": "OpenViking trace API"
  },
  "searches": [
    {
      "resourceType": "resource",
      "targetUriInput": "viking://resources",
      "targetUriResolved": "viking://resources",
      "limit": 20,
      "scoreThreshold": 0,
      "durationMs": 35,
      "total": 1,
      "results": [
        {
          "uri": "viking://resources/project/spec.md",
          "resourceType": "resource",
          "score": 0.88,
          "abstractPreview": "Recall trace design spec",
          "resultType": "resource"
        }
      ]
    }
  ],
  "selected": [
    {
      "uri": "viking://resources/project/spec.md",
      "resourceType": "resource",
      "score": 0.88,
      "abstractPreview": "Recall trace design spec",
      "displayed": true
    }
  ],
  "stats": {
    "candidateCount": 1,
    "selectedCount": 1,
    "injectedCount": 0
  }
}
```

## 5. Agent 工具：`ov_recall_trace`

### 5.1 用途

`ov_recall_trace` 用于在 Agent 内部查询已记录的 trace。它不会重新调用 OpenViking 搜索接口，只查询插件记录；仅当传入 `includeContent: true` 或配置了 `traceRecallIncludeContentByDefault: true` 时，才会额外调用 OpenViking `read` 给 selected 结果补充内容预览。工具注册见 `index.ts:1637`。

### 5.2 参数

参数类型定义见 `index.ts:167`，工具参数声明见 `index.ts:1642`。

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `turn` | `'latest'` \| `'all'` | `'latest'` | `latest` 只返回过滤后最新 1 条；`all` 返回最多 `limit` 条。解析见 `index.ts:732`、`recall-trace.ts:187`。 |
| `traceId` | string | 无 | 精确查询某条 trace。 |
| `sessionId` | string | 当前 session | 按 OpenClaw session ID 过滤；未传时默认当前工具上下文 session。解析见 `index.ts:734`。 |
| `sessionKey` | string | 无 | 按 OpenClaw session key 过滤。 |
| `ovSessionId` | string | 当前 session 映射值 | 按 OpenViking session ID 过滤。解析见 `index.ts:736`。 |
| `source` | string | 无 | `auto_recall`、`memory_recall`、`ov_search`、`ov_archive_search`。 |
| `resourceTypes` | string[] 或逗号分隔 string | 无 | 按 trace 的 `resourceTypes` 过滤；允许 `resource`、`user`、`agent`。归一化见 `recall-trace.ts:113`。 |
| `since` | number | 无 | 毫秒时间戳下界，包含。 |
| `until` | number | 无 | 毫秒时间戳上界，包含。 |
| `includeContent` | boolean | `false` | 是否读取 selected URI 的内容预览；可能带来额外读请求。实现见 `index.ts:747`。 |
| `limit` | number | `20` | 最大返回条数；仅 `turn: 'all'` 时返回多条。解析见 `index.ts:741`。 |

> 当前接口不支持自由文本模糊查询 trace trigger。需要按 `source`、`sessionId`、`ovSessionId`、`resourceTypes`、`traceId` 或时间范围过滤。

### 5.3 调用示例

查询当前 session 最新一条 trace：

```json
{
  "turn": "latest"
}
```

查询当前 session 内最近 10 条 `ov_search` trace：

```json
{
  "turn": "all",
  "source": "ov_search",
  "limit": 10
}
```

查询某条 trace 并补充 selected 内容预览：

```json
{
  "traceId": "ov_search-1780329600000-a1b2c3d4",
  "includeContent": true
}
```

按时间范围和召回类型查询：

```json
{
  "turn": "all",
  "resourceTypes": ["user"],
  "since": 1780320000000,
  "until": 1780406399999,
  "limit": 50
}
```

### 5.4 返回值

工具返回 OpenClaw ToolResult：

```json
{
  "content": [
    {
      "type": "text",
      "text": "## Trace 1: ov_search\ntraceId: ...\nquery: ..."
    }
  ],
  "details": {
    "action": "queried",
    "count": 1,
    "lookupLayer": "memory",
    "warnings": [],
    "entries": []
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `content[0].text` | 人类可读摘要，由 `formatRecallTraceText` 生成，格式见 `index.ts:845`。 |
| `details.count` | 本次返回条数。 |
| `details.lookupLayer` | `memory` 表示来自内存环形缓存；`persistent` 表示内存未命中后从 JSONL 文件 fallback 查询。实现见 `recall-trace.ts:431`。 |
| `details.warnings` | 读取 JSONL 或 selected 内容失败等 warning。 |
| `details.entries` | 完整结构化 trace 数组。 |

## 6. Slash 命令：`/ov-recall-trace`

### 6.1 用途

用户可以在 OpenClaw 会话中直接执行 `/ov-recall-trace` 查询 trace。命令注册见 `index.ts:1676`。

### 6.2 参数

Slash 命令使用 `--kebab-case` 参数，解析逻辑见 `index.ts:1687`。

| 参数 | 对应工具参数 | 示例 |
| --- | --- | --- |
| `--turn` | `turn` | `--turn all` |
| `--trace-id` | `traceId` | `--trace-id ov_search-1780329600000-a1b2c3d4` |
| `--session-id` | `sessionId` | `--session-id test-session` |
| `--session-key` | `sessionKey` | `--session-key agent:main:xxx` |
| `--ov-session-id` | `ovSessionId` | `--ov-session-id 8d6e...` |
| `--source` | `source` | `--source auto_recall` |
| `--resource-types` | `resourceTypes` | `--resource-types user,agent` |
| `--since` | `since` | `--since 1780320000000` |
| `--until` | `until` | `--until 1780406399999` |
| `--include-content` | `includeContent` | `--include-content` |
| `--limit` | `limit` | `--limit 20` |

### 6.3 示例

```bash
/ov-recall-trace --turn all --source auto_recall --limit 5
```

```bash
/ov-recall-trace --trace-id ov_search-1780329600000-a1b2c3d4 --include-content
```

```bash
/ov-recall-trace --turn all --resource-types user,agent --since 1780320000000 --until 1780406399999
```

### 6.4 返回值

Slash 命令返回：

```json
{
  "text": "## Trace 1: auto_recall\ntraceId: ...",
  "details": {
    "count": 1,
    "lookupLayer": "memory",
    "warnings": [],
    "entries": []
  }
}
```

返回结构与 `ov_recall_trace` 的 `details` 基本一致；`text` 是人类可读摘要，`details.entries` 是机器可读数据。

## 7. Gateway HTTP API

插件 service 启动时会尝试注册 Recall Trace Gateway 路由：`index.ts:2540`。如果当前 Gateway 不支持 route adapter，日志会提示使用 `ov_recall_trace` 工具或 `/ov-recall-trace` 命令替代：`index.ts:2548`。

### 7.1 `GET /api/openviking/recall-traces`

#### 用途

查询多条 trace。路由注册见 `index.ts:830`。

#### Query 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `turn` | `latest` \| `all` | `latest` | 是否只返回最新一条。 |
| `traceId` | string | 无 | 精确过滤 trace ID。 |
| `sessionId` | string | 无 | OpenClaw session ID。 |
| `sessionKey` | string | 无 | OpenClaw session key。 |
| `ovSessionId` | string | 无 | OpenViking session ID。 |
| `source` | string | 无 | `auto_recall`、`memory_recall`、`ov_search`、`ov_archive_search`。 |
| `resourceTypes` | string | 无 | 逗号或换行分隔，如 `user,agent`。 |
| `since` | number | 无 | 毫秒时间戳下界。 |
| `until` | number | 无 | 毫秒时间戳上界。 |
| `includeContent` | boolean/string | 配置默认值 | 支持 `1`、`true`、`yes`。解析见 `index.ts:799`。 |
| `limit` | number | `20` | 最大返回条数。 |

#### 请求示例

```bash
curl 'http://127.0.0.1:<gateway-port>/api/openviking/recall-traces?turn=all&source=ov_search&limit=10'
```

```bash
curl 'http://127.0.0.1:<gateway-port>/api/openviking/recall-traces?turn=all&resourceTypes=user,agent&since=1780320000000&until=1780406399999'
```

#### 返回值

Handler 返回结构见 `index.ts:812`。

```json
{
  "status": 200,
  "body": {
    "ok": true,
    "entries": [],
    "lookupLayer": "memory",
    "warnings": []
  }
}
```

根据 Gateway 适配层，客户端通常会看到 `body` 中的 JSON：

```json
{
  "ok": true,
  "entries": [],
  "lookupLayer": "memory",
  "warnings": []
}
```

### 7.2 `GET /api/openviking/recall-traces/:traceId`

#### 用途

按 `traceId` 查询单条 trace。路由注册见 `index.ts:831`。

#### Path 参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `traceId` | string | 需要查询的 trace ID。 |

#### Query 参数

除 `traceId` 外，支持与列表接口相同的 query 参数，例如 `includeContent=true`。

#### 请求示例

```bash
curl 'http://127.0.0.1:<gateway-port>/api/openviking/recall-traces/ov_search-1780329600000-a1b2c3d4?includeContent=true'
```

#### 返回值

```json
{
  "ok": true,
  "entries": [
    {
      "traceId": "ov_search-1780329600000-a1b2c3d4",
      "source": "ov_search"
    }
  ],
  "lookupLayer": "memory",
  "warnings": []
}
```

## 8. 查询与存储行为

### 8.1 内存 Ring Buffer

- `RecallTraceMemoryStore` 保存最近 N 条 trace，N 由 `traceRecallMaxEntries` 控制。
- 超出容量时删除最旧记录。
- 查询时先过滤，再按 `ts` 降序排序。
- `turn: 'latest'` 返回过滤结果中最新一条；`turn: 'all'` 返回最多 `limit` 条。

实现见 `recall-trace.ts:172`、`recall-trace.ts:187`。

### 8.2 JSONL 持久化

启用 `traceRecallPersist: true` 后，每条 trace 会追加到 `traceRecallDir/YYYY-MM-DD.jsonl`。

- 文件名使用 trace 的 UTC 日期：`recall-trace.ts:214`。
- 默认不会持久化 `trigger.rawUserTextPreview`，除非设置 `traceRecallIncludeRawUserPreview: true`：`recall-trace.ts:271`。
- 查询时如果内存命中，直接返回内存结果；只有内存未命中且存在持久化 store，才 fallback 扫描 JSONL：`recall-trace.ts:431`。
- JSONL 中的损坏行会被跳过，并返回 warning：`recall-trace.ts:373`。

### 8.3 `includeContent` 行为

默认 trace 只保存摘要预览，不读取完整内容。查询时开启 `includeContent` 后，插件会对每个 `selected[].uri` 调用 OpenViking read，并把结果压缩到 `selected[].contentPreview`：`index.ts:747`。

建议只在定位具体 trace 时使用 `includeContent`，避免一次查询大量 trace 触发额外读请求。

## 9. 常见使用场景

### 9.1 解释为什么自动召回没有注入记忆

1. 打开 trace：`traceRecall: true`。
2. 复现一轮会话。
3. 查询最新自动召回：

```bash
/ov-recall-trace --source auto_recall
```

重点查看：

- `searches[].error` 是否有搜索失败。
- `searches[].total` 是否为 0。
- `stats.candidateCount`、`stats.selectedCount`、`stats.injectedCount` 是否逐步变少。
- `trigger.queryTruncated` 是否为 true。

### 9.2 查看显式 `memory_recall` 查了哪些空间

```bash
/ov-recall-trace --turn all --source memory_recall --limit 5
```

重点查看 `resourceTypes` 和 `searches[].targetUriResolved`，确认是否默认查了 `viking://user/memories` 与 `viking://agent/memories`，或是否按请求 `resourceTypes` 改变范围。

### 9.3 排查 `/ov-search` 或 `ov_search` 为什么结果不符合预期

```bash
/ov-recall-trace --turn all --source ov_search --include-content --limit 3
```

重点查看：

- `trigger.query` 是否与预期一致。
- `searches[].targetUriInput` 是否是正确资源目录。
- `results[]` 候选是否包含预期文档但未进入 `selected[]`。
- `selected[].contentPreview` 是否能读到真实内容。

### 9.4 排查归档搜索没有命中

```bash
/ov-recall-trace --turn all --source ov_archive_search --limit 5
```

重点查看：

- `operationType` 是否为 `archive_grep`。
- `searches[].targetUriResolved` 是否指向正确 session history 或 archive。
- `searches[].caseInsensitive` 是否为 true。
- `stats.candidateCount` 与 `selected[].line`。

## 10. 错误与排障

| 现象 | 可能原因 | 排查/解决 |
| --- | --- | --- |
| 查询为空且 warning 包含 `traceRecall is disabled` | 未配置 `traceRecall: true` | 显式启用 `traceRecall`，重启 Gateway 后复现。 |
| 配了 `recallTargetTypes` 但没有 trace | 召回范围配置不等于 trace 开关 | 同时设置 `traceRecall: true`。 |
| Gateway 路由不可用 | 当前 Gateway 未提供 `registerRoute` adapter | 使用 Agent 工具 `ov_recall_trace` 或 Slash 命令 `/ov-recall-trace`。日志见 `index.ts:2548`。 |
| 重启后查不到历史 trace | 未开启 `traceRecallPersist`，或超过 `traceRecallQueryMaxDays` 查询窗口 | 开启持久化，必要时传 `since/until` 或调大 `traceRecallQueryMaxDays`。 |
| `includeContent` 后有 `readError` | selected URI 已不可读、权限不足或 OpenViking read 失败 | 查看 `warnings` 与 `selected[].readError`，再用 `ov_read` 验证 URI。 |
| JSONL 查询有 corrupted warning | 持久化文件存在损坏行 | 插件会跳过损坏行返回有效记录；可检查对应 `YYYY-MM-DD.jsonl`。实现见 `recall-trace.ts:373`。 |

## 11. 测试覆盖

相关单元测试集中在：

- `tests/ut/recall-trace.test.ts:56`：召回类型归一化、搜索计划、内存 ring buffer、JSONL 持久化、隐私控制。
- `tests/ut/tools.test.ts:1021`：`ov_recall_trace` 工具、Slash 命令、Gateway 路由、`includeContent`、显式召回 trace、查询不重新触发搜索。

建议修改 trace 行为后至少运行：

```bash
npm run typecheck
npm test -- tests/ut/recall-trace.test.ts tests/ut/tools.test.ts
```

## 12. 快速参考

### 开启 trace

```json
{
  "traceRecall": true,
  "traceRecallPersist": true
}
```

### 查最新 trace

```bash
/ov-recall-trace
```

### 查最近 10 条自动召回

```bash
/ov-recall-trace --turn all --source auto_recall --limit 10
```

### 查指定 trace 详情

```bash
/ov-recall-trace --trace-id <traceId> --include-content
```

### HTTP 查询

```bash
curl 'http://127.0.0.1:<gateway-port>/api/openviking/recall-traces?turn=all&source=memory_recall&limit=10'
```

### HTTP 查询单条

```bash
curl 'http://127.0.0.1:<gateway-port>/api/openviking/recall-traces/<traceId>?includeContent=true'
```
