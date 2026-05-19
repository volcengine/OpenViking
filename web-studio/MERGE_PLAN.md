# Web Studio 首版合入规划

## 0. 结论

首版 PR 目标是先把 Web Studio 作为 frontend-only 变更合入，缩短 review 周期。PR 主体限制在 `web-studio/` 下；根 `.gitignore` 只在保留设计稿生成物 ignore 时作为窄例外。

本版不夹带 `new-frontend` 分支里的实验性后端实现。后端能力与接口实现后续由官方后端 PR 承接；Web Studio 首版只消费 target `upstream/main` 已经存在的接口。

关键决策：

1. target 以当前本地 `upstream/main` 为准；本地已确认包含 #2016 `9d36b2fd833d9cedb140f5f8ba9977f8142aec21`。
2. 可以消费 #2016 已合入的 Console BFF，但只能通过 target OpenAPI 重新生成的 generated client 使用。
3. 禁止在业务代码里手写 `/api/v1/console/*` 路径，也禁止用 `ovClient.instance.get('/api/v1/console/...')` 做临时封装。
4. 不把 Typed API response 后端四个 commit 直接带进首版 PR；如需更强类型，只在 `web-studio/types/ov-server/` 内维护首版实际使用接口的 TS contract 子集。
5. 根 `.gitignore` 不为 Web Studio 删除 `data/`；如果 Web Studio 内部路径被根规则误忽略，用 `web-studio/.gitignore` 做局部反忽略。
6. `src/gen/ov-client` 必须从 target `upstream/main` 的实际 OpenAPI 重新生成，不能沿用实验后端生成物，不能手改 generated client。

本规划核实基于当前本地 refs：`upstream/main` / `main` 均指向 `af4c54ff`，且包含 #2016。这里未执行网络 fetch；开 PR 前如果远端有新变化，需要 fetch 后复核。

## 1. Scope 边界

目录外改动核对口径：

- 从 `7a6f5748764c45f78d4ed32277cf6f8da94248c7` 到 `new-frontend`。
- 排除 `8e628b2b` `sync upstream (#6)`。
- 只看 `web-studio/` 之外会影响 Web Studio 首版的变更。

首版 PR 的默认边界：

| 类型 | 处理 |
| --- | --- |
| `web-studio/` | 主体带入。 |
| 根 `.gitignore` | 默认不改；只有设计稿生成物 ignore 可作为窄例外。 |
| 后端 `openviking/server/**` | 不带。 |
| 后端 docs/tests | 不带。 |
| 根目录开发脚本、AI 文档、根配置 | 不带。 |

首版 PR 描述必须明确：

- This PR is frontend-only and primarily touches `web-studio/`.
- It consumes existing OpenViking APIs and the Usage/Audit Console BFF from #2016.
- It does not introduce new backend contracts.
- It keeps a narrow typed result subset under `web-studio/types/ov-server/` for APIs used by the first PR.
- Backend-dependent features such as resource tags, bot file attachments, config editing, and token-level bot streaming are intentionally omitted or degraded.

## 2. 首版保留范围

| 模块 | 首版处理 | 说明 |
| --- | --- | --- |
| App shell | 保留 | 布局、侧边栏、主题、i18n、连接设置。 |
| 请求适配层 | 保留 | `src/lib/ov-client` 继续负责 baseUrl、认证头、telemetry 注入和错误归一化。 |
| Request logs | 保留本地请求日志 | 当前是前端本地请求日志。#2016 有官方 audit BFF，但首版不强行替换整个 request logs 页面。 |
| Home | 保留并改接 #2016 Console BFF | 删除旧实验 `/api/v1/stats/tokens`。 |
| Resources 文件管理 | 保留 | 文件树、列表、预览、搜索、跳转是核心功能。 |
| Resources 上传 | 保留 | 当前 target 已确认 `POST /api/v1/resources/temp_upload`，且 `POST /api/v1/resources` 支持 `temp_file_id` / `source_name`。 |
| Sessions | 降级保留 | 保留列表、创建、删除、历史消息、基础聊天；不承诺 token 级 streaming 和附件。 |
| Code editor | 保留 | 当前 target 已确认 `POST /api/v1/content/write`，可以保留文本编辑与保存。 |

## 3. 必须剔除或替代的能力

| `new-frontend` 目录外能力 | Web Studio 内关联 | 首版处理 |
| --- | --- | --- |
| `/api/v1/stats/tokens` | `home-page.tsx` 的 `fetchTokenStats()` | 删除，改用 #2016 `/api/v1/console/dashboard/summary` 和 `/api/v1/console/tokens`。 |
| Config API `/api/v1/config` | 当前没有实际 UI 依赖 | 不接入，不生成业务入口；连接设置继续只存前端本地状态。 |
| Typed API response rollout | generated client 可能来自实验 OpenAPI；`getOvResult<T>()` 已承担 envelope 解包 | 不带后端 commit；在 target 分支重新生成 client；在 `web-studio/types/ov-server/` 内维护必要 TS contract 子集。 |
| Resource tags 后端/CLI | `file-list.tsx` 展示 `entry.tags`，types/normalize 有 tags 字段 | 删除 tags 展示和 tags 类型映射，避免暗示官方后端已有 tags 功能。 |
| Bot provider token streaming | `content_delta` / `reasoning_delta` UI | 只承诺 final `response` 可用；parser 可兼容，但不作为首版卖点。 |
| Server raw SSE proxy 修复 | `sendChatStream()` 实时性 | 不要求后端改；前端必须接受最后一次性返回 `response` 的降级体验。 |
| Bot `temp_file_id` 工具支持 | chat 附件把 temp file id 拼进用户消息 | 隐藏或删除 chat 附件入口；Resources 上传和 chat 附件不能混在一起承诺。 |
| ParserRegistry 未知文件兜底调整 | 上传失败体验 | 不带后端 parser 行为变更；前端保守过滤文件，解析失败展示后端错误。 |
| 根目录开发脚本、AI 文档和根配置 | `scripts/bootstrap_dev.sh`、`.impeccable.md`、`.github/copilot-instructions.md`、`docs/ai-architecture.md`、`docs/design/web-studio-find-fm-integration.md` | 不进 frontend-only PR。 |

## 4. Home 取舍

#2016 已提供官方 Usage/Audit Dashboard BFF。首版 Home 不应保留 demo 数据，也不应继续依赖旧 `/api/v1/stats/tokens`。

### 4.1 官方接口

| 接口 | 用途 | 首版处理 |
| --- | --- | --- |
| `GET /api/v1/console/dashboard/summary` | 首页 summary cards | 接入。 |
| `GET /api/v1/console/tokens?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&bucket=day` | Token 趋势 | 接入。 |
| `GET /api/v1/console/context-commits?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&bucket=hour\|4h` | 上下文提交热力图 | 接入，首版选一种 bucket，不做复杂 drill-down。 |
| `GET /api/v1/console/audit` | 官方请求审计日志 | 首版可暂不接入，保留前端本地 request logs。 |

Web Studio 请求层以用户配置的 OV Server `baseUrl` 为准。#2016 后端说明里列出的 `/console/api/v1/ov/console/*` 是官方 console 静态服务代理路径；Web Studio 首版不硬编码或重新引入这类 alias。

如果 regenerated client 没有 `/api/v1/console/*` SDK 函数，说明当前 target 合约不可用。首版应暂停接入 Home 的 Console BFF 区块或降级为空态；禁止手写 path 绕过 generated client。

### 4.2 Summary cards 映射

| 当前卡片 | 首版数据源 | 文案调整 |
| --- | --- | --- |
| Context Magnitude | `summary.context_counts.total` | 表达上下文规模。 |
| Token Usage | `summary.today_tokens.total` | 副标题从 `lifetime total` 改成 `today` 或 `today total`。 |
| Retrieval Count | `summary.today_retrievals.total` | 表达今日检索次数，不是 memory 总数。 |
| Agent Visits | `summary.agent_overview.total` | 表达活跃 Agent 数，不是 session 数。 |

### 4.3 图表映射

Token trend 删除 demo 常量，改用 `/api/v1/console/tokens`：

| BFF 字段 | 前端序列 |
| --- | --- |
| `vlm_input` | input |
| `vlm_output` | output |
| `embedding_input` | vector |

Contribution heatmap 删除 demo 常量，改用 `/api/v1/console/context-commits`：

- `count` 使用 row 的 `total`。
- 首版建议只选 `bucket=4h` 或 `bucket=hour` 中一种。
- 不做复杂 drill-down。

### 4.4 Disabled fallback

#2016 约定 Usage/Audit 未启用或未初始化时返回：

```json
{
  "status": "ok",
  "result": {
    "enabled": false,
    "message": "Usage/Audit is disabled or not initialized."
  }
}
```

Home 处理规则：

- `enabled=false` 不进入错误态。
- Summary cards 显示 `0` 或 `-`。
- Token trend / heatmap 显示空图或轻量空态。
- 不展示 demo 数据为真实数据。

### 4.5 MemoryStatsCard

首版建议从 P0 首页移除 `MemoryStatsCard`，或降级成 `ContextCountsCard`：

- 若追求 review 最短：删除该卡片。
- 若想保留上下文结构信息：改成 `ContextCountsCard`，展示 `files / skills / memories`，数据来自 `summary.context_counts`。

不要在同一首页同时混用旧 `/api/v1/stats/memories` 和新 `/api/v1/console/dashboard/summary` 作为主要指标来源。

## 5. Resources 取舍

### 5.1 保留

| 能力 | target 接口 |
| --- | --- |
| 文件树 | `GET /api/v1/fs/tree` |
| 文件列表 | `GET /api/v1/fs/ls` |
| 文件详情 | `GET /api/v1/fs/stat` |
| 内容读取 | `GET /api/v1/content/read` |
| 内容保存 | `POST /api/v1/content/write` |
| 搜索 | `POST /api/v1/search/find` |
| 远程资源添加 | `POST /api/v1/resources` with `path` |
| 本地文件上传 | `POST /api/v1/resources/temp_upload` + `POST /api/v1/resources` with `temp_file_id` / `source_name` |

如果开 PR 前 target 分支变化导致本地上传接口缺失，再隐藏本地文件上传入口，只保留 remote URL 添加。

### 5.2 删除

- `FileList` 中的 tags badge。
- `VikingFsEntry.tags`。
- `FindResultItem.tags`。
- normalize 中对 `tags` / `tag` 的映射。
- tags filter UI 或请求参数。

说明：Typed API response rollout 中仍出现 `tags` 字段，但首版不能把它当成官方 tags 功能已可用的依据。

## 6. Sessions 取舍

### 6.1 保留

- session 列表、创建、删除。
- session 历史消息读取。
- 发送基础 bot 消息。
- final `response` 事件生成最终 assistant message。
- 消息持久化的前端兼容逻辑，除非 target 后端已确认 bot 自动写 session。

### 6.2 降级

- `content_delta`、`reasoning_delta` 可作为兼容解析，但首版不承诺 token 级实时 streaming。
- 若 target 后端的 `/bot/v1/chat/stream` 不稳定，`sendChatStream()` 可回退到 `/bot/v1/chat` 非流式接口。

### 6.3 删除或隐藏

- chat 附件按钮。
- `use-file-attachment.ts`。
- `Thread` 中把附件拼成 `[uploaded_file: ..., temp_file_id: ...]` 的逻辑。
- 附件 preview URL 缓存逻辑。

理由：chat 附件依赖 bot 工具理解 `temp_file_id`，这是后端/bot 能力，不应由 frontend-only PR 暗示已经支持。

## 7. Typed API Response 取舍

本地核实：下面四个 Typed API response commit 当前都不在本地 `upstream/main` 中，且改动主体是 `openviking/server/`、`docs/`、`tests/`，不是 Web Studio 自身代码。

| commit | 主要内容 | 对 Web Studio 首版的意义 |
| --- | --- | --- |
| `c7ad4e3f` `feat(server): generic Response[T] and typed-response infrastructure` | 把 `openviking.server.models.Response` 改成泛型 `Response[T]`；新增 `ExcludeNoneRoute`、`Pagination`、`PaginatedResult`；新增 API schema 约束文档。 | 提供后端 typed OpenAPI 基础设施；不新增 Web Studio 可用接口，也不覆盖 #2016 Console BFF。 |
| `78881385` `feat(server): type responses for sessions, content, search, and bot endpoints` | 为 sessions、content、search、bot 非流式接口加 `response_model=Response[...]`、schema、null policy 测试。 | 覆盖首版会用的 Sessions、内容读取/写入、搜索和非流式 bot chat；optional 字段可能从 `null` 变成缺省。 |
| `453395ba` `feat(server): type responses for resources, filesystem, relations, and pack endpoints` | 为 resources、filesystem、relations、pack 加 typed schemas；新增 `URIRef`、`FromTo`、`FileStat`、`TempUploadResult`、`AddResourceResult` 等。 | 覆盖 Resources 核心接口；`FSListResult` 是 `string[] \| FileStat[]`。仍不保留首版 tags UI。 |
| `47ae308c` `feat(server): type responses for admin, config, system, stats, and task endpoints` | 为 admin、config、system、stats、tasks 加 typed schemas；`/health`、`/ready`、`/metrics` 是非 envelope 或非 JSON 白名单。 | 对 system/tasks 查询有帮助，但 admin/config/stats tokens 不进首版 UI。 |

完整带入这组 feat 会显著扩大 review 面：

- 新增 `openviking/server/schemas/*`。
- 多个 router 增加 `response_model=Response[...]`。
- 多个 router 采用 `ExcludeNoneRoute`，wire 行为可能从 `"field": null` 变成省略字段。
- 新增 typed/null policy/OpenAPI contract 测试。
- 新增 `docs/api_schema_guidelines.md` 和 `docs/api_response_changelog.md`。
- 覆盖 admin、config、stats tokens、relations、pack 等首版不暴露的后端域。

因此首版不带这四个后端 commit。合理带入方式是只在 Web Studio 内维护窄 TS contract 子集：

1. generated client 只负责路径、方法、请求参数和请求体类型。
2. `getOvResult<T>()` 继续负责兼容解包 `Response<T>` envelope。
3. raw API contract 统一收在 `web-studio/types/ov-server/`。
4. normalize/adaptor 层把 raw API contract 转成 UI view model，不把后端历史字段直接扩散到组件里。

### 7.1 `web-studio/types/ov-server/` 组织规则

raw OV Server API contract 必须集中，不要散落到各业务模块的 `-types/` 目录里。

建议目录：

```text
web-studio/types/ov-server/
  common.ts
  api/v1/console.ts
  api/v1/sessions.ts
  api/v1/fs.ts
  api/v1/content.ts
  api/v1/search.ts
  api/v1/resources.ts
  api/v1/system.ts
  api/v1/tasks.ts
  bot/v1/chat.ts
```

规则：

- 路径跟后端路由模块/API 路径对齐，便于后续和官方 typed OpenAPI/generated client 对表、删除或替换。
- 业务模块自己的 `-types/` 目录只保留 UI view model、组件状态、表单状态等前端语义类型。
- `VikingFsEntry`、`FindResultItem` 这类 UI view model 与 raw API contract 分开。
- raw contract 只在 `api.ts` / `normalize.ts` 等边界层使用。
- 如果新增 TS path alias，只用于 type-only import；所有引用写成 `import type`，避免运行时路径依赖。

建议首版 contract 子集：

| 文件 | 类型范围 |
| --- | --- |
| `common.ts` | `OvEnvelope<T>`、`OvErrorEnvelope`、disabled result helper 类型等薄公共类型。 |
| `api/v1/console.ts` | `ConsoleDashboardSummaryResult`、`ConsoleTokenSeriesResult`、`ConsoleContextCommitsResult`、`ConsoleDisabledResult`。 |
| `api/v1/sessions.ts` | `SessionListItem`、`SessionDetail`、`SessionContextResult`、`MessageAddedResult`、`CommitResult`、`SessionDeletedResult`。 |
| `bot/v1/chat.ts` | `BotChatResponse` 和 SSE event union。 |
| `api/v1/fs.ts` | `FileStat`、`FSListResult = string[] \| FileStat[]`。 |
| `api/v1/content.ts` | `ContentReadResult = string \| Record<string, unknown>`、`ContentWriteResult`。 |
| `api/v1/search.ts` | `SearchResult`、`SearchHit`，不把 tags 暴露给首版 UI view model。 |
| `api/v1/resources.ts` | `TempUploadResult`、`AddResourceResult`。 |
| `api/v1/system.ts` / `api/v1/tasks.ts` | 仅当 Home 首版仍保留 system health 或 task 列表时定义本地子集。 |

不建议：

- 不把 `openviking/server/schemas/*` 或后端 docs/tests 夹进 frontend-only PR。
- 不提交基于 typed-response 实验后端生成的 `src/gen/ov-client`。
- 不为了获得类型而在 Web Studio 中手写 URL 调用。
- 不把 typed rollout 中仍存在的 tags 字段当成首版 Resources tags 功能依据。

后续后端 PR 优先级建议：

1. typed infra：`Response[T]`、`ExcludeNoneRoute`、schema guidelines。
2. Web Studio 首版已用接口：sessions、content、search、resources、filesystem、system/tasks。
3. #2016 Console BFF typed schema：dashboard summary、tokens、context commits、audit、disabled result union。
4. 非首版 UI 域：admin、config、relations、pack、stats tokens 等。

## 8. Generated client 处理

首版不能沿用 `new-frontend` 上由实验后端生成的 client。应在 target 分支上：

1. 启动 target `upstream/main` 后端。
2. 运行：

```bash
npm run gen-server-client
```

3. 检查 `src/gen/ov-client` 是否出现 console BFF endpoints。
4. 修复业务代码中不再存在的生成函数引用。
5. 不手写修改 `src/gen/ov-client`。
6. 不在业务代码里手写 `/api/v1/console/*` 路径绕过 generated client。

如果基于 target OpenAPI 生成后没有 Console BFF endpoints，首版应暂停接入 Home 的 Console BFF 区块或将相关区域降级为空态。

## 9. Gitignore 取舍

`.gitignore` 拆成两个层面处理：根 `.gitignore` 默认不改；`web-studio/.gitignore` 属于 Web Studio 自身，应随 `web-studio/` 一起带入。若 Web Studio 需要覆盖根规则，优先在 `web-studio/.gitignore` 内做局部反忽略。

### 9.1 根 `.gitignore`

从 `7a6f5748764c45f78d4ed32277cf6f8da94248c7` 到 `new-frontend`、排除 `8e628b2b` 后，根 `.gitignore` 和 Web Studio 可能相关的改动只有两类：

| 改动 | 来源 | 首版处理 | 原因 |
| --- | --- | --- | --- |
| 删除未锚定的 `data/` | `b9ee46a4` | 不带入 | `data/` 会忽略任意层级 `data` 目录，但这类冲突应由 `web-studio/.gitignore` 局部反忽略解决。target 中保留 `data/` 和 `/data/*` 更稳。 |
| 新增 `docs/design/ai-chat.html`、`docs/design/ai-chat/` | `4452505a` | 可选保留 | 这是 Web Studio 设计/原型生成物 ignore。若首版 PR 完全不带 `docs/design` 工作流，可以不带。 |

不要把当前 `new-frontend` 相对 `upstream/main` 缺失的其他根 `.gitignore` 规则当作首版改动删除。下面这些保持 target `upstream/main` 原样：

- `.ttadk`
- `.local-data/`
- `ovcli.conf`
- `docs/node_modules/`
- `docs/.vitepress/cache/`
- `docs/.vitepress/dist/`
- `.codex/`
- `.ttadk/`

`tests/oc2ov_test/config/settings.py` 是 `8e628b2b sync upstream (#6)` 带来的上游安全修复 ignore，当前 target 已包含，和 Web Studio 首版无关。

如果决定保留设计稿生成物 ignore，才在根 `.gitignore` 额外加入：

```diff
+docs/design/ai-chat.html
+docs/design/ai-chat/
```

### 9.2 `web-studio/.gitignore`

`web-studio/.gitignore` 应随 Web Studio 带入。

| 规则 | 首版处理 | 原因 |
| --- | --- | --- |
| `node_modules`、`dist`、`dist-ssr`、`.vite` | 保留 | Vite/npm 本地产物。 |
| `*.local`、`.env`、`.nitro`、`.tanstack`、`.wrangler`、`.output`、`.vinxi`、`__unconfig*`、`todos.json` | 保留 | 前端工具链和本地临时状态。 |
| `!src/lib/`、`!src/lib/**` | 保留 | 根 `.gitignore` 有未锚定的 `lib/`，必须反忽略 `web-studio/src/lib`。 |
| `!AGENTS.md` | 保留 | 根 `.gitignore` 有未锚定的 `AGENTS.md`，必须反忽略 `web-studio/AGENTS.md`。 |
| `!src/**/data` 类规则 | 按实际目录保留或补齐 | 根 `.gitignore` 有未锚定的 `data/`。 |
| `script/gen-server-client/generate` | 保留 | OpenAPI 生成中间产物，不应进入 PR。 |
| `!src/components/legacy/data` | 视实际目录决定 | 若最终首版没有该目录，可删掉或替换成实际仍存在的 data 目录反忽略规则。 |

如果实际带入的 Web Studio 目录包含 `data` 目录，应在 `web-studio/.gitignore` 里显式反忽略对应路径，例如：

```gitignore
!src/routes/data/
!src/routes/data/**
!src/components/data/
!src/components/data/**
```

## 10. 建议执行顺序

1. 从包含 #2016 的最新 `upstream/main` 新建干净分支。
2. 按原提交重放 `web-studio/` 下文件，优先保留贡献者信息：
   - 只改 `web-studio/` 的 commit 可以直接 cherry-pick。
   - 混合改动 commit 用 `cherry-pick -n` 暂存后只保留 `web-studio/`，再用原 commit message/author 提交。
   - 不建议直接复制整个 `web-studio/` 目录后做一个新提交；这会丢失原 commit author/granularity。
3. 带入 `web-studio/.gitignore`；若 Web Studio 实际包含 `data` 目录，在 `web-studio/.gitignore` 内补齐局部反忽略。
4. 根 `.gitignore` 默认不改；只在决定保留 `docs/design/ai-chat*` 生成物 ignore 时做窄 patch。
5. 启动 target 后端并重新生成 `src/gen/ov-client`。
6. 建立 `web-studio/types/ov-server/` typed result 子集，按 OV Server API 模块路径组织。
7. 修 Home：
   - 删除 `/api/v1/stats/tokens` 和 demo 数据。
   - 接 generated client 中的 Console BFF endpoints。
   - 处理 `enabled=false`。
   - 若 generated client 没有 Console BFF endpoints，降级或暂停相关区块，不手写 path。
8. 修 Resources：
   - 删除 tags 展示和 tags 类型映射。
   - 保留文件管理、内容读取/保存、搜索、本地上传。
   - 若 target 缺失本地上传接口，隐藏本地上传入口。
9. 修 Sessions：
   - 隐藏 chat 附件。
   - 保证 final `response` 模式可用。
   - token streaming 不作为 PR 承诺。
10. 运行验证：

```bash
npm run build --prefix web-studio
```

如需 lint：

```bash
npm run lint --prefix web-studio
```

## 11. PR 描述建议

标题：

```text
feat(web-studio): add frontend console workspace
```

描述重点：

- This PR is frontend-only and primarily touches `web-studio/`; root `.gitignore` is only changed if we keep optional design-artifact ignore rules.
- It consumes existing OpenViking APIs and the Usage/Audit Console BFF from #2016.
- It does not introduce new backend contracts.
- It keeps a narrow typed result subset under `web-studio/types/ov-server/` for APIs used by the first PR; generated client output still comes from the target branch OpenAPI.
- Backend-dependent features such as resource tags, bot file attachments, config editing, and token-level bot streaming are intentionally omitted or degraded.
- Follow-up backend and integration PRs can add those capabilities incrementally.

## 12. 后续项

不进入首版：

- Resource tags 全链路。
- Config 编辑页。
- 官方 audit logs 页面替换当前本地 request logs。
- bot token 级 streaming 和 reasoning delta。
- bot 文件附件消费。
- session rename。
- 更完整的 Home drill-down 和时间范围筛选。
- 多实例生产环境下的 Usage/Audit store 扩展。
- 后端完整 Typed API response rollout。
- #2016 Console BFF 的官方 typed schema。
