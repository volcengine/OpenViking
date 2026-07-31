# OpenViking 动态查询配置变更与测试报告

> 日期：2026-06-04
> 分支：`feat/sharClawWithOpenViKing`
> 仓库：`iaasng/arkclaw-openviking-plugin`
> 结论：本次动态查询配置能力、URI 详情 API、最近 ov_search 简化清单 API 已完成实现与二次验收；全量测试、类型检查、生产构建、生产风格稳定性模拟均通过。

## 1. 本次变更总结

本次变更围绕 OpenViking 插件的“查询参数运行期动态配置”展开，目标是支持不重启 OpenClaw 即可调整召回数量、候选数量、阈值、资源范围和排序权重，并支持 claw 与 session 两级粒度。整体能力由新增运行期配置存储、召回链路接入、命令入口、Gateway 查询接口和测试保障共同组成。

### 1.1 核心能力

| 能力 | 说明 | 关键实现位置 |
|---|---|---|
| 运行期动态配置 | 新增 `RuntimeQueryConfigStore`，负责参数归一化、分层合并、持久化、热加载、异常容错 | `query-config.ts:173` |
| 分层优先级 | 合并顺序为 request > session > claw > static config > default，并输出字段来源 `sources` | `query-config.ts:263` |
| claw / session 粒度 | claw 以 `agentId` 为 key；session 优先 `ovSessionId`，其次 `sessionId`，再次 `sessionKey` | `query-config.ts:158`、`query-config.ts:367` |
| 自动召回接入 | assemble 阶段获取有效查询配置，传入自动召回链路 | `context-engine.ts:891` |
| 显式工具接入 | `memory_recall`、`ov_search` 使用运行期有效配置作为默认值，请求参数仍可覆盖 | `index.ts:1218` |
| 排序权重参数化 | `rankingWeights`、`categoryWeights`、`resourceTypeWeights` 参与客户端侧排序 | `memory-ranking.ts` |
| CLI 管理入口 | 新增 `/ov-query-config` get/set/unset/reset | `index.ts:1225`、`index.ts:1748` |
| Gateway URI 详情 | 新增只读详情接口，不重新触发搜索 | `index.ts:945`、`index.ts:1132` |
| Gateway 最近 ov_search 清单 | 从 recall trace 中扁平化最近一次 `ov_search` 结果 | `index.ts:1019`、`index.ts:1133` |
| 安装脚本修复 | 修复 resource-only recall 自动配置的 JSON 传参，以及 env 单引号转义 | `scripts/install.sh:242`、`scripts/install.sh:404` |

### 1.2 本次重点修复

| 问题 | 影响 | 修复说明 | 测试覆盖 |
|---|---|---|---|
| session 只设置 `recallLimit` 时错误覆盖 claw 显式 `candidateLimit` | 低优先级显式候选数被高优先级派生值覆盖 | `applyLayer()` 增加 `candidateLimitExplicit` 跟踪 | `tests/ut/query-config.test.ts:78` |
| `/ov-query-config` 不能设置权重参数 | 用户无法运行期调整排序权重 | 增加 `--weight`、`--categoryWeight`、`--resourceTypeWeight`、`--recallPreferAbstract` 解析 | `tests/ut/tools.test.ts:415` |
| 空 patch 覆盖已有配置 | 用户传未知参数会把已有 scope 配置清空 | 空归一化结果直接报错，不落库 | `tests/ut/tools.test.ts:443` |
| install.sh JSON 转义错误 | `openclaw config set` 收到非法 JSON 字符串 | 改为传入 `'["resource"]'` 的实际 JSON 文本 | `tests/ut/package-install-contract.test.ts` |
| 初始 load 与 set 并发竞态 | 插件注册后首次写入可能被旧文件覆盖 | 写操作等待 in-flight initial load | `tests/ut/query-config.test.ts:116` |
| 持久化队列失败后不可恢复 | 一次磁盘/权限错误会让后续写入永久失败 | rejected queue 自动恢复，当前失败仍向调用方抛出 | `tests/ut/query-config.test.ts:141` |
| env 文件单引号转义错误 | 包含单引号的 API key / URL 可能生成不可 source 的 env 文件 | 使用 shell 单引号规范转义 `'\\''` 等价片段（实际输出为 ` '\'' `） | `tests/ut/package-install-contract.test.ts` |

## 2. 新增动态配置规则

### 2.1 生效优先级

每次自动召回、`memory_recall`、`ov_search` 执行前都会生成一次有效配置。字段级优先级如下：

```text
request 覆盖参数
  > session 运行期配置
  > claw 运行期配置
  > plugins.entries.openviking.config 静态配置
  > 代码默认值
```

说明：

- request 级参数只影响当前调用，不写入运行期配置。
- session 级配置只影响当前会话，优先匹配 `ovSessionId`，再匹配 `sessionId`，最后匹配 `sessionKey`。
- claw 级配置以当前 `agentId` 为作用域，影响该 claw 下后续所有 session。
- `sources` 会标记字段来源，便于排查“为什么当前召回数量/阈值/权重是这个值”。

### 2.2 字段合并规则

| 类型 | 规则 |
|---|---|
| 标量字段 | 高优先级覆盖低优先级，如 `recallLimit`、`scoreThreshold` |
| 数组字段 | 整体覆盖，不拼接，如 `resourceTypes` |
| 对象字段 | 浅合并，如 `rankingWeights`、`categoryWeights`、`resourceTypeWeights` |
| `candidateLimit` | 显式设置优先于 `recallLimit * candidateMultiplier` 派生值；且最终保证 `candidateLimit >= recallLimit` |
| 非法数值 | 归一化时 clamp 到允许范围；非法 `targetUri` 会报错 |
| 持久化文件损坏 | 保留 last known-good 内存配置，不阻断查询链路 |

### 2.3 支持字段与范围

| 字段 | 语义 | 范围/约束 | 默认来源 |
|---|---|---|---|
| `recallLimit` | 最终注入或展示结果数 | 1-50 | 静态 `recallLimit` |
| `candidateLimit` | 每个 target URI 的候选检索数 | 1-200 | 派生值 |
| `candidateMultiplier` | 候选数倍数，候选数 = `recallLimit * candidateMultiplier` | 1-20 | 4 |
| `scoreThreshold` | 客户端后处理分数阈值 | 0-1 | 静态 `recallScoreThreshold` |
| `maxInjectedChars` | 自动召回注入字符预算 | 100-50000 | 静态 `recallMaxInjectedChars` |
| `recallPreferAbstract` | 是否优先使用 abstract 注入 | boolean | 静态 `recallPreferAbstract` |
| `resourceTypes` | 默认搜索范围 | `resource` / `user` / `agent` | 静态 `recallTargetTypes` |
| `targetUri` | 强制搜索单一 URI | 必须以 `viking://` 开头 | 无 |
| `ovSearchLimit` | `ov_search` 默认返回数 | 1-100 | 10 |
| `rankingWeights.baseScore` | 语义分权重 | 0-2 | 1 |
| `rankingWeights.leaf` | 叶子节点加权 | 0-2 | 0.12 |
| `rankingWeights.event` | 事件类加权 | 0-2 | 0.1 |
| `rankingWeights.preference` | 偏好类加权 | 0-2 | 0.08 |
| `rankingWeights.lexicalOverlapMax` | 词面重叠最大加权 | 0-2 | 0.2 |
| `resourceTypeWeights` | 按资源类型加权 | -1 到 2 | 空对象 |
| `categoryWeights` | 按 category 加权 | -1 到 2 | 空对象 |

### 2.4 持久化与热加载

运行期配置可以配置持久化路径：

```json
{
  "plugins": {
    "entries": {
      "openviking": {
        "config": {
          "runtimeQueryConfigPath": "/path/to/runtime-query-config.json"
        }
      }
    }
  }
}
```

文件结构：

```json
{
  "schemaVersion": "1.0",
  "updatedAt": 1780520000000,
  "claws": {
    "main": {
      "params": { "recallLimit": 8, "candidateLimit": 80 },
      "updatedAt": 1780520000000,
      "updatedBy": "command",
      "agentId": "main"
    }
  },
  "sessions": {
    "session:oc-session-123": {
      "params": { "scoreThreshold": 0.08 },
      "updatedAt": 1780520000000,
      "updatedBy": "command",
      "agentId": "main"
    }
  }
}
```

## 3. 使用方式

### 3.1 查看当前有效配置

```text
/ov-query-config get --scope session
```

返回内容包含：

- `scope`：当前查看的作用域。
- `effective`：合并后的最终配置。
- `effective.sources`：字段来源，如 `session` / `claw` / `static` / `default`。
- `effective.warnings`：归一化或降级警告。

### 3.2 设置 claw 级默认召回策略

```text
/ov-query-config set --scope claw \
  --recallLimit 4 \
  --candidateLimit 40 \
  --scoreThreshold 0.2 \
  --resourceTypes user,agent
```

效果：当前 claw 下所有 session 默认继承该配置；session 级配置仍可覆盖。

### 3.3 设置 session 级临时策略

```text
/ov-query-config set --scope session \
  --recallLimit 10 \
  --candidateLimit 80 \
  --scoreThreshold 0.08 \
  --resourceTypes resource,user
```

效果：仅当前 session 生效，不影响同 claw 下其他 session。

### 3.4 调整排序权重

```text
/ov-query-config set --scope claw \
  --weight baseScore=0.8,leaf=0.2,preference=0.18,event=0.04,lexicalOverlapMax=0.25 \
  --categoryWeight preferences=0.3,events=-0.1 \
  --resourceTypeWeight user=0.2,resource=0.1
```

说明：

- `--weight` 对应 `rankingWeights`。
- `--categoryWeight` 对应 `categoryWeights`。
- `--resourceTypeWeight` 对应 `resourceTypeWeights`。
- 权重只影响插件客户端侧排序，不改变 OpenViking 服务端搜索算法。

### 3.5 清理字段覆盖

```text
/ov-query-config unset recallLimit scoreThreshold rankingWeights --scope session
```

效果：删除当前 session 的指定字段覆盖，下一次查询回退到 claw 或静态配置。

### 3.6 重置作用域配置

```text
/ov-query-config reset --scope session
/ov-query-config reset --scope claw
```

效果：删除该作用域整条运行期配置。

## 4. API 接口整理

### 4.1 `GET /api/openviking/uri-detail`

用途：按完整 `viking://` URI 查询详情和正文片段。该接口只调用读取能力，不重新发起搜索。

#### 请求示例

```bash
curl --get 'http://127.0.0.1:<gateway-port>/api/openviking/uri-detail' \
  --data-urlencode 'uri=viking://resources/project/spec.md' \
  --data 'includeContent=true' \
  --data 'contentLimit=12000' \
  --data 'offset=0'
```

#### Query 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `uri` | string | 是 | 无 | 完整 `viking://` URI，必须 URL encode |
| `includeContent` | boolean | 否 | `true` | 是否读取正文 |
| `offset` | number | 否 | 0 | 内容分页起始偏移 |
| `contentLimit` | number | 否 | 20000 | 返回正文最大字符数，范围 1-100000 |
| `agentId` | string | 否 | 当前上下文 agent | 读取时的 agent 路由 |
| `sessionId` | string | 否 | 当前上下文 session | trace / session 上下文关联 |
| `sessionKey` | string | 否 | 当前上下文 sessionKey | trace / session 上下文关联 |
| `ovSessionId` | string | 否 | 当前上下文 ovSessionId | trace / session 上下文关联 |
| `traceId` | string | 否 | 无 | 用于从指定 trace 补充摘要、分数、category 等元信息 |
| `preferTracePreview` | boolean | 否 | `true` | 是否优先复用 trace 预览信息 |

#### 响应字段

| 字段 | 说明 |
|---|---|
| `ok` | 是否成功 |
| `uri` | 请求的完整 URI |
| `uriType` | URI 类型，如 `resource` / `session` / `user_memory` / `agent_memory` / `skill` / `archive` / `unknown` |
| `abstractPreview` | trace 或读取结果摘要预览 |
| `metadata` | category、score、level、resultType、sourceTraceId、source 等展示元信息 |
| `content.text` | 正文片段 |
| `content.offset` | 本次分页起始偏移 |
| `content.limit` | 本次分页限制 |
| `content.returnedChars` | 本次返回字符数 |
| `content.totalChars` | 正文总字符数 |
| `content.hasMore` | 是否还有后续内容 |
| `readStatus` | `not_requested` / `ok` / `read_failed` |
| `warnings` | 非致命警告 |
| `error` | 错误码和错误消息 |

#### 状态码

| 状态码 | 场景 |
|---|---|
| 200 | URI 合法且读取成功，或 `includeContent=false` 只返回元信息 |
| 400 | URI 缺失、非 `viking://`、URI 被展示截断、分页参数非法 |
| 502 | OpenViking read 调用失败 |

### 4.2 `GET /api/openviking/recall-traces/latest-ov-search-list`

用途：获取当前会话最近一次 `ov_search` trace 的扁平化 URI 清单，方便前端展示“上次搜索到了哪些资源”。该接口不会重新发起 `ov_search`。

#### 请求示例

```bash
curl --get 'http://127.0.0.1:<gateway-port>/api/openviking/recall-traces/latest-ov-search-list' \
  --data-urlencode 'sessionId=oc-session-123' \
  --data 'limit=20' \
  --data 'includeSelected=true' \
  --data 'dedupe=true'
```

#### Query 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `sessionId` | string | 否 | 当前上下文 session | OpenClaw sessionId |
| `sessionKey` | string | 否 | 当前上下文 sessionKey | OpenClaw sessionKey |
| `ovSessionId` | string | 否 | 当前上下文 ovSessionId | OpenViking storage session id |
| `agentId` | string | 否 | 当前上下文 agent | 过滤同 session 下不同 agent 的 trace |
| `limit` | number | 否 | 20 | 返回条数，范围 1-100 |
| `lookup` | string | 否 | `auto` | 当前响应会回显；trace 查询走内存/持久化 fallback |
| `includeSelected` | boolean | 否 | `true` | 是否合并 selected/displayed 结果 |
| `dedupe` | boolean | 否 | `true` | 是否按 URI 去重；selected 优先 |
| `includeSkills` | boolean | 否 | `true` | 是否包含 skill 结果 |
| `strict` | boolean | 否 | `false` | 未找到 trace 时是否返回 404 |

#### 响应字段

| 字段 | 说明 |
|---|---|
| `ok` | 是否成功；非 strict 且无 trace 时仍为 true |
| `lookupLayer` | 查询来源：`memory` / `persistent` / `none` |
| `fallbackUsed` | 是否使用持久化 fallback |
| `query` | 回显查询条件 |
| `trace.traceId` | 命中的 trace ID |
| `trace.ts` / `trace.isoTime` | trace 时间 |
| `trace.triggerQuery` | 触发 ov_search 的 query |
| `items[]` | 扁平化条目 |
| `items[].uri` | 完整 URI |
| `items[].abstractPreview` | 摘要预览 |
| `items[].resourceType` | 资源类型 |
| `items[].resultType` | `memory` / `resource` / `skill` / `archive_match` |
| `items[].category` | 记忆或资源 category |
| `items[].score` | 相关性分数 |
| `items[].source` | `search_result` / `selected` |
| `items[].targetUri` | 来源搜索 target URI |
| `items[].detailUrl` | 可直接打开 URI 详情的 Gateway URL |
| `totalItems` | 返回条目数 |
| `warnings` | 非致命警告 |

#### 状态码

| 状态码 | 场景 |
|---|---|
| 200 | 查询成功；默认未找到 trace 也返回空列表加 warning |
| 400 | `limit` 等参数非法 |
| 404 | `strict=true` 且未找到最近 `ov_search` trace |

## 5. 本次测试范围

### 5.1 覆盖范围

| 范围 | 覆盖内容 |
|---|---|
| 单元测试 | 参数归一化、clamp、分层合并、字段来源、持久化、热加载、unset/reset、session alias 匹配 |
| 命令测试 | `/ov-query-config` set/get/unset/reset、权重参数、空 patch 拒绝 |
| 工具链路测试 | session 配置影响后续 `memory_recall`、`ov_search` 默认 limit/targetUri |
| 自动召回场景测试 | context engine assemble 阶段使用 session 有效配置 |
| 排序测试 | rankingWeights、categoryWeights、resourceTypeWeights 对入选排序生效 |
| Gateway API 测试 | URI detail route、latest ov_search list route、skill 过滤、detailUrl 生成 |
| 安装脚本契约测试 | standalone package contract、resource-only recall 配置、JSON 传参、env 单引号转义 |
| 类型检查 | `tsc -p tsconfig.json` |
| 生产构建 | `tsc -p tsconfig.build.json` |
| 稳定性模拟 | in-flight load serialization、100 session 并发写入、配置文件损坏恢复、写队列失败恢复 |

### 5.2 未执行范围说明

仓库中的 Python e2e 脚本依赖真实 OpenClaw Gateway、OpenViking 服务、LLM 后端和有效 token。当前本地验收环境未提供这些外部运行前提，因此本次未执行会访问真实外部服务的 Python e2e。对应风险已通过全量 Vitest、真实 server 单测、生产风格稳定性模拟和 install dry-run 覆盖本次变更相关链路。

## 6. 测试用例执行情况

### 6.1 聚焦回归测试

| 命令 | 结果 |
|---|---|
| `npm test -- tests/ut/query-config.test.ts` | 通过：9 tests passed |
| `npm test -- tests/ut/tools.test.ts -t "ov-query-config"` | 通过：4 tests passed |
| `npm test -- tests/ut/package-install-contract.test.ts` | 通过：6 tests passed |
| `bash -n scripts/install.sh` | 通过：无语法错误 |

### 6.2 全量测试 / 类型 / 构建

| 命令 | 结果 |
|---|---|
| `npm test` | 通过：29 test files passed，562 tests passed |
| `npm run typecheck` | 通过 |
| `npm run build` | 通过 |

### 6.3 生产风格稳定性模拟

验证命令覆盖以下行为：

1. `load()` 与 `set()` 并发时，写操作等待初始加载，不会被旧文件覆盖。
2. 100 个 session 并发写入后，session 与 claw 配置仍按优先级正确合并。
3. 配置文件被破坏为非法 JSON 后，`reloadIfChanged({ force: true })` 保留 last known-good 内存配置。
4. 持久化路径临时不可写导致一次写入失败后，修复路径后下一次写入可以恢复。
5. install.sh dry-run 对包含单引号的 URL/API key 不崩溃，resource-only recall 配置输出正确。

执行结果：

```text
production-style runtime config stability check passed: in-flight load serialization + 100 concurrent session writes + corrupt-file recovery + write-queue recovery
```

## 7. 质量与风险结论

### 7.1 已关闭风险

- 动态配置优先级已通过单测与场景测试验证。
- `candidateLimit` 显式配置优先级已修复并回归。
- 权重参数 CLI 设置已补齐。
- 空 patch 不再覆盖已有配置。
- 持久化写入采用临时文件 + rename，写队列失败后可恢复。
- 配置文件损坏时保留 last known-good，不影响查询链路。
- install.sh 的 JSON 参数与 env 单引号转义均已覆盖。

### 7.2 后续建议

| 建议 | 优先级 | 说明 |
|---|---|---|
| 增加 Gateway query-config 写接口 | P2 | 当前已实现 CLI 与内部 store；如前端需要可继续补齐 `GET/PUT/DELETE /api/openviking/query-config` |
| 支持 TTL/过期清理 | P2 | 设计文档预留 `expiresAt`，当前主要覆盖 set/unset/reset，不做自动 TTL 管理 |
| 增加真实环境 e2e 流水线 | P2 | 需要 OpenClaw Gateway、OpenViking、LLM、token 的稳定测试环境 |
| 多实例集中配置 | P3 | 当前为单进程/本地文件模型；多 gateway 实例可后续接远程配置服务 |

## 8. 最终结论

本次变更已完成“OpenViking 查询参数动态配置”主链路：支持 claw/session 粒度、支持运行期即时生效、支持配置持久化与热加载、支持召回数量/候选数量/阈值/资源类型/排序权重调整，并新增 URI 详情与最近 ov_search 简化清单 API。经过二次验收和追加复审后，发现的问题均已修复，当前自动化验证全部通过，可以进入提测/评审阶段。
