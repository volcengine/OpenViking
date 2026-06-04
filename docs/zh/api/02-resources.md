# 资源管理

资源是智能体可以引用的外部知识。本模块提供资源的添加、导入/导出、临时文件上传等功能。

## 核心概念

### 资源类型

OpenViking 支持多种资源类型，按照功能分类如下：

文档类
| 类型 | 扩展名 | 说明 |
|------|--------|------|
| PDF | `.pdf` | 支持本地解析和 MinerU API 转换 |
| Markdown | `.md`, `.markdown`, `.mdown`, `.mkd` | 原生支持，会提取结构并分段存储 |
| HTML | `.html`, `.htm` | 清理导航/广告后提取内容，转换为 Markdown |
| Word | `.docx` | 提取文本、标题、表格并转换为 Markdown |
| 纯文本 | `.txt`, `.text` | 直接导入处理 |
| EPUB | `.epub` | 电子书格式，支持 ebooklib 或手动提取 |

表格类
| 类型 | 扩展名 | 说明 |
|------|--------|------|
| Excel | `.xlsx`, `.xls`, `.xlsm` | 支持新版和老版 Excel，按工作表转换为 Markdown 表格 |
| PowerPoint | `.pptx` | 按幻灯片提取内容，支持提取备注 |

代码类
| 类型 | 资源名 | 说明 |
|------|--------|------|
| 代码文件 | `*.py`, `*.js`, ... | 支持常见编程语言（Python, JavaScript, Go, Rust, Java 等） |
| Git 协议代码仓库 | `git://...` | Git URL, 本地目录, `.zip` 包，遵循 `.gitignore` 并自动过滤 `.git`, `node_modules` 等目录 |
| Git 代码托管平台 | `https://github.com/{org}/{repo}` | GitHub, GitLab, Bitbucket 等代码托管平台的 URL |
| Git 代码托管平台上的 raw 文件 | `https://github.com/{org}/{repo}/raw/{branch}/{path}` | GitHub, GitLab, Bitbucket 等代码托管平台的 raw 文件下载 URL |

媒体类
| 类型 | 资源名 | 说明 |
|------|--------|------|
| 图片 | `*.jpg`, `*.jpeg`, `*.png`, `*.gif` ... | 多种图片格式，通过 VLM 生成描述（实验特性） |
| 视频 | `*.mp4`, `*.avi`, `*.mov` ... | 提取关键帧后使用 VLM 分析（规划） |
| 音频 | `*.mp3`, `*.wav`, `*.m4a` ... | 进行语音转录处理（规划） |

云文档类
| 类型 | 说明 |
|------|------|
| 飞书/Lark | URL 方式，支持 docx, wiki, sheets, bitable，需要配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET |

### 资源处理流程

资源添加经过以下处理阶段：

```
源输入 → 解析 → 资源树构建 → 持久化 → 语义处理
  ↓        ↓         ↓          ↓          ↓
URL/文件  Parser  TreeBuilder  AGFS    Summarizer/Vector
```

#### 阶段 1：源解析 (Parse)
- 使用 `UnifiedResourceProcessor` 根据资源类型解析内容
- 支持多种格式：文档（PDF/Markdown/Word）、表格（Excel/PPT）、代码、媒体文件等
- 解析结果写入临时 VikingFS 目录
- 媒体文件通过 VLM（视觉语言模型）生成描述

#### 阶段 2：资源树构建 (TreeBuilder)
- `TreeBuilder.finalize_from_temp()` 扫描临时目录结构
- 构建资源树节点，处理 URI 冲突（自动重命名）
- 建立目录与资源的关联关系

#### 阶段 3：持久化存储 (Persist)
- 检查目标 URI 是否已存在
- 新资源：移动临时文件到正式 AGFS 位置
- 已存在资源：保留临时树用于后续差异比较
- 获取生命周期锁防止并发修改
- 清理临时目录

#### 阶段 4：语义处理 (Semantic Processing)
- **摘要生成**：`Summarizer` 生成 L0（摘要）和 L1（概述）
- **向量索引**：将内容向量化用于语义搜索
- 通过 `SemanticQueue` 异步处理，可通过 `wait=True` 等待完成

#### 异步 `add_resource` 任务
- `wait=false` 时，OpenViking 仍会先校验资源来源、解析目标 URI，并预占最终 `root_uri`，然后再返回。
- 立即响应包含 `status`、`root_uri` 和 `task_id`；抓取/解析/finalize 以及队列等待会在持久化后台任务中继续执行。
- 可通过 `GET /api/v1/tasks/{task_id}` 查询任务状态。资源导入任务的阶段包括 `queued`、`fetching`、`parsing`、`finalizing`、`processing_queue`。
- 任务状态响应只暴露当前 `stage`；实时 semantic 和 embedding 队列计数可通过 observer queue 查询，导入完成后的任务 `result` 会包含 `queue_status`。

### 资源的增量更新

资源增量更新通过**监控任务 (Watch Task)** 机制实现：

#### 监控任务创建
- 调用 `add_resource` 时设置 `watch_interval > 0` （单位：分钟）创建监控任务
- 可指定 `to` 参数确定目标 URI；未指定时，系统会使用本次导入返回的 `root_uri` 作为监控目标
- `WatchManager` 负责任务持久化存储
- 支持多租户权限控制（ROOT/ADMIN/USER 权限分级）

#### 任务调度执行
- `WatchScheduler` 每 60 秒检查到期任务
- 默认并发控制，避免重复执行
- 到期任务自动重新调用 `add_resource` 处理
- 更新任务的最后执行时间和下次执行时间

#### 任务管理操作
- **创建**：`watch_interval > 0` 时创建新任务或重新激活已停用任务
- **更新**：对同一目标 URI 重新设置参数
- **取消**：对同一目标 URI 设置 `watch_interval <= 0` 时停用任务
- **查询**：通过任务 ID 或目标 URI 查询任务状态

## API 参考

### add_resource

向知识库添加资源，支持本地文件/目录、URL 等多种来源。

#### 1. API 实现介绍

此接口是资源管理的核心入口，支持多种来源的资源添加，并可选择等待语义处理完成。SDK 可直接处理本地文件/目录、URL 等来源；直接 HTTP 调用只通过 `path` 接受远程 URL，或通过 `temp_file_id` 引用先上传的本地文件。

**处理流程**：
1. 识别并校验资源来源（URL 或上传的临时文件）
2. 解析目标 URI；`wait=false` 时会先预占最终 `root_uri` 再返回
3. 调用对应 Parser 解析内容
4. 构建目录树并写入 AGFS
5. `wait=true` 时等待语义处理完成；否则通过返回的 `task_id` 查询后台任务状态
6. 如指定 `--watch-interval`，设置定时更新任务

**代码入口**：
- `openviking/client/local.py:LocalClient.add_resource` - SDK 入口（嵌入式）
- `openviking_cli/client/http.py:AsyncHTTPClient.add_resource` - SDK 入口（HTTP）
- `openviking/server/routers/resources.py:add_resource` - HTTP 路由
- `openviking/service/resource_service.py` - 核心服务实现
- `crates/ov_cli/src/handlers.rs:handle_add_resource` - CLI 处理

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| path | string | 否 | - | 远程资源 URL（HTTP/HTTPS/Git）。与 `temp_file_id` 二选一 |
| temp_file_id | string | 否 | - | 临时上传文件 ID。与 `path` 二选一 |
| to | string | 否 | - | 目标 Viking URI（精确位置）。与 `parent` 互斥 |
| parent | string | 否 | - | 父级 Viking URI（资源放入此目录下）。与 `to` 互斥 |
| create_parent | bool | 否 | False | 如果父目录不存在，自动创建父目录（服务端标志） |
| reason | string | 否 | "" | 添加资源的原因（用于文档化和相关性提升，实验特性） |
| instruction | string | 否 | "" | 语义提取的处理指令（实验特性） |
| wait | bool | 否 | False | 是否等待语义处理和向量化完成才返回 |
| timeout | float | 否 | None | 超时时间（秒），仅 `wait=true` 时生效 |
| strict | bool | 否 | False | 是否使用严格模式 |
| ignore_dirs | string | 否 | None | 要忽略的目录名（逗号分隔） |
| include | string | 否 | None | 包含的文件模式（glob） |
| exclude | string | 否 | None | 排除的文件模式（glob） |
| directly_upload_media | bool | 否 | True | 是否直接上传媒体文件 |
| preserve_structure | bool | 否 | None | 是否保留目录结构 |
| watch_interval | float | 否 | 0 | 定时更新间隔（分钟）。>0 创建任务；≤0 取消任务；显式 `to` 优先，否则绑定本次导入的 `root_uri` |
| telemetry | TelemetryRequest | 否 | False | 是否返回遥测数据 |

**补充说明**：
- `to` 和 `parent` 不能同时使用；如果使用 `parent` 且希望父目录不存在时自动创建，请传 `create_parent=true`。指定 `to` 且目标已存在时，触发增量更新。
- `path` 和 `temp_file_id` 不能同时指定，上传本地文件需要先通过 [temp_upload](#temp_upload) 上传获取 `temp_file_id`，在 SDK 和 CLI 中已经封装好。
- `wait=false` 时，请求仍会先完成资源 preflight 和目标规划。远程 URL/仓库不可达或父级目标无效会立即失败，不会创建后台任务。
- 当 OpenViking 在 `viking://resources` 或 `parent` 下自动选择目标时，会在 `wait=false` 响应前完成冲突重命名和 URI 预占，因此返回的 `root_uri` 是稳定的。
- `watch_interval > 0` 时，如果指定了 `to`，监控任务绑定该目标；如果未指定 `to`，监控任务绑定本次导入返回的 `root_uri`。如果无法得到稳定 `root_uri`，请求会报错并要求显式传 `to`。
- 本地目录输入会遵循 `.gitignore`（根目录和子目录，标准 Git 语义）；`ignore_dirs`、`include`、`exclude` 会在此基础上进一步过滤。
- 如果要直接创建或更新纯文本内容，请使用 [content/write](03-filesystem.md#write)，不要使用 `add_resource`。资源导入和内容写入后都会自动刷新语义与 embedding。

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/resources
Content-Type: application/json
```

```bash
# 从 URL 添加资源
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "https://example.com/guide.md",
    "reason": "User guide documentation",
    "wait": true
  }'

# 从本地文件添加（需先使用 temp_upload 上传）
TEMP_FILE_ID=$(
  curl -s -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F "file=@./documents/guide.md" \
  | jq -r '.result.temp_file_id'
)

curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"to\": \"viking://resources/guide.md\",
    \"reason\": \"User guide\"
  }"
```

**Python SDK**

```python
import openviking as ov

# 使用嵌入式模式（以后不再推荐和详细介绍）
client = ov.OpenViking(path="./data")
client.initialize()

# 使用 HTTP 客户端模式
client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

## 添加本地文件
result = client.add_resource(
    "./documents/guide.md",
    reason="User guide documentation"
)
print(f"Added: {result['root_uri']}")

## 从 URL 添加到指定位置
result = client.add_resource(
    "https://example.com/api-docs.md",
    to="viking://resources/external/api-docs.md",
    reason="External API docs"
)

## 等待处理完成
client.wait_processed()

## 开启定时更新
client.add_resource(
    "./documents/guide.md",
    to="viking://resources/guide.md",
    watch_interval=60  # 每60分钟更新一次
)
```

**CLI**

```bash
# 添加本地文件
ov add-resource ./documents/guide.md --reason "User guide"

# 从 URL 添加
ov add-resource https://example.com/guide.md --to viking://resources/guide.md

# 等待处理完成
ov add-resource ./documents/guide.md --wait

# 开启定时更新（每60分钟检测一次）
ov add-resource https://github.com/example/repo.git --to viking://resources/my_repo --watch-interval 60

# 开启定时更新并自动绑定本次导入生成的 URI
ov add-resource https://github.com/example/repo.git --watch-interval 60

# 取消定时更新
ov add-resource https://github.com/example/repo.git --to viking://resources/my_repo --watch-interval 0

# 添加到指定父目录（父目录必须存在）
ov add-resource ./documents/guide.md --parent viking://resources/docs

# 添加到指定父目录（父目录不存在时自动创建）
ov add-resource ./documents/guide.md -p viking://resources/docs/2026/05/07
# 或使用完整参数名
ov add-resource ./documents/guide.md --parent-auto-create viking://resources/docs/2026/05/07

# 使用路径变量配合自动创建父目录
ov add-resource ./documents/guide.md -p viking://resources/docs/{calendar:today}
```

#### 4. 响应示例

**HTTP API 响应 (JSON, `wait=true`)**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://resources/guide.md",
    "temp_uri": "viking://temp/username/04291108_b62dc7/guide.md",
    "source_path": "./documents/guide.md",
    "meta": {},
    "errors": [],
    "queue_status": {
      "pending": 5,
      "processing": 2,
      "completed": 10
    }
  },
  "telemetry": {
    "operation_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

**HTTP API 响应 (JSON, `wait=false`)**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://resources/guide",
    "task_id": "uuid-xxx"
  }
}
```

使用返回的 `task_id` 轮询 `/api/v1/tasks/{task_id}`。任务完成后的 `result` 会包含完整导入结果，包括 `queue_status`。

**CLI 响应 (默认表格格式)**

```
Note: Resource is being processed in the background.
Use 'ov wait' to wait for completion, or 'ov observer queue' to check status.
status       success
root_uri     viking://resources/01-overview
task_id      uuid-xxx
```

**CLI 响应 (JSON 格式，使用 -o json)**

```json
{
  "status": "success",
  "root_uri": "viking://resources/01-overview",
  "task_id": "uuid-xxx"
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 处理状态："success" 成功，"error" 失败 |
| `root_uri` | string | 资源在 OpenViking 中的最终 URI |
| `task_id` | string | （可选，仅当 `wait=false` 时）后台任务 ID，可轮询 `/api/v1/tasks/{task_id}` |
| `temp_uri` | string | 处理过程中的临时 URI（仅在后台处理阶段有效） |
| `source_path` | string | 原始源文件路径或 URL |
| `meta` | object | 资源解析过程中的元数据（如文件类型、大小等） |
| `errors` | array | 处理过程中的错误列表 |
| `warnings` | array | （可选）处理过程中的警告列表（仅在 `strict=False` 时可能出现） |
| `queue_status` | object | （可选，仅当 `wait=true` 时）队列处理状态，包含 `pending`、`processing`、`completed` 计数 |

对于 `wait=false`，后台任务的 `task_type="add_resource"`，`resource_id` 等于返回的 `root_uri`。运行中的任务记录可能包含 `stage`；完成后的任务 `result` 会包含带有 semantic 和 embedding 汇总的 `queue_status`。

---

### Watch Management（监控任务管理）

列出、查看、更新和触发通过 [`add_resource`](#add_resource) 配合 `watch_interval > 0` 创建的监控任务。控制面在 REST（`/api/v1/watches`）、`ov task watch` CLI 子命令组以及面向 Agent 的最小闭包 MCP 接口（`list_watches` / `cancel_watch`）三处镜像。

#### 1. API 实现介绍

此控制面封装了 `WatchManager` 原语，未改动任何服务端行为。每个端点和 CLI 命令都支持通过 `task_id`（路径）或 `to_uri`（查询参数）定位目标任务，两种键可以互换；如果同时提供，二者必须指向同一任务，否则返回 400。

**操作**：
- **列出**（`GET /api/v1/watches`）— 返回 `{tasks, total}`；可传 `?active_only=true` 过滤；传 `?to_uri=...` 时降级为单任务查找
- **查看**（`GET /api/v1/watches/{task_id}`）— 查看单个任务；可选 `?to_uri=` 做跨键一致性校验
- **更新**（`PATCH /api/v1/watches/{task_id}` 或 `PATCH /api/v1/watches?to_uri=...`）— 部分更新 `watch_interval`、`is_active`、`reason`、`instruction`。`is_active` 与 `watch_interval` 正交：翻转 `is_active` 可在不丢失配置周期的前提下暂停/恢复任务。
- **删除**（`DELETE /api/v1/watches/{task_id}` 或 `DELETE /api/v1/watches?to_uri=...`）
- **触发**（`POST /api/v1/watches/{task_id}/trigger` 或 `POST /api/v1/watches/trigger?to_uri=...`）— 触发即返回（fire-and-forget），重新摄取在后台异步执行

**代码入口**：
- `openviking/server/routers/watches.py` — `/api/v1/watches` REST 路由
- `crates/ov_cli/src/commands/watch.rs` — `ov task watch` CLI 子命令组
- `openviking/server/mcp_endpoint.py` — MCP `list_watches` / `cancel_watch` 工具，以及 `add_resource` 上的 `watch_interval` / `to` 参数
- `openviking/resource/watch_manager.py:WatchManager` — 任务持久化与调度原语

#### 2. 接口和参数说明

对每个单任务端点，路径中的 `{task_id}` 都可用查询参数 `?to_uri=` 替代。CLI 的 `<key>` 参数会自动分类：任何以 `viking://` 开头的值走 by-URI 路径，其他值视为 task_id（其它 scheme 如 `http://` 会在本地直接报错，避免静默 404）。

**`PATCH /watches` 请求体**（字段均可选，至少需提供一个）

| 字段 | 类型 | 说明 |
|------|------|------|
| watch_interval | float | 新的检查周期（分钟），必须 `> 0`；如需暂停而保留周期请改用 `is_active=false`。 |
| is_active | bool | 切换激活状态而保留配置周期（暂停 / 恢复）。 |
| reason | string | 更新该监控任务的记录原因。 |
| instruction | string | 更新语义处理指令。 |

未识别字段会被 422 拒绝（`extra="forbid"`）。未传字段保留原值。

#### 3. 使用示例

**HTTP API**

```bash
# 列出活跃监控任务（去掉 ?active_only 可同时包含已暂停的任务）
curl -s "http://localhost:1933/api/v1/watches?active_only=true" \
  -H "X-API-Key: your-key"

# 暂停一个监控任务而保留其检查周期
curl -X PATCH "http://localhost:1933/api/v1/watches/<task_id>" \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"is_active": false}'

# 触发一次立即刷新（fire-and-forget，立即返回，再次摄取在后台执行）
curl -X POST "http://localhost:1933/api/v1/watches/<task_id>/trigger" \
  -H "X-API-Key: your-key"

# 按 URI 而非 task_id 定位任务
curl -X DELETE "http://localhost:1933/api/v1/watches?to_uri=viking://resources/guide.md" \
  -H "X-API-Key: your-key"
```

**CLI**（`ov task watch` 子命令）

```bash
# 列出活跃监控任务（去掉 --active-only 可同时包含已暂停的任务）
ov task watch ls --active-only

# 查看单个监控任务（key 可以是 viking:// URI 或 task_id）
ov task watch show viking://resources/guide.md

# 暂停 / 恢复，不丢失配置周期
ov task watch pause viking://resources/guide.md
ov task watch resume viking://resources/guide.md

# 更新周期（或 --active / --reason / --instruction 的任意组合）
ov task watch update viking://resources/guide.md --interval 30

# 触发一次立即刷新（fire-and-forget）
ov task watch trigger viking://resources/guide.md

# 删除监控任务
ov task watch rm viking://resources/guide.md
```

**MCP**（Agent 控制面——仅最小闭包）

```text
list_watches()                                            # 每个任务一行；只暴露 URI，不暴露 task_id
cancel_watch(to_uri="viking://resources/guide.md")        # 按 URI 幂等删除
```

暂停 / 恢复 / 触发 / 更新故意不通过 MCP 暴露——这些 power-user 操作放在 CLI/REST 一侧，以保持 Agent 系统提示词的紧凑。Agent 侧若需创建监控任务或调整周期，仍走 [`add_resource`](#add_resource) 配合 `watch_interval`；可显式传 `to`，也可让系统绑定本次导入返回的 `root_uri`。

---

### add_skill

向知识库添加技能。

#### 1. API 实现介绍

技能是一种特殊的资源，用于定义智能体可以执行的操作或工具。

**处理流程**：
1. 接收技能数据或上传的临时文件
2. 解析技能定义
3. 存储到技能目录
4. 如指定 `wait=true`，等待技能处理完成

**代码入口**：
- `openviking/client/local.py:LocalClient.add_skill` - SDK 入口（嵌入式）
- `openviking_cli/client/http.py:AsyncHTTPClient.add_skill` - SDK 入口（HTTP）
- `openviking/server/routers/resources.py:add_skill` - HTTP 路由
- `openviking/service/resource_service.py` - 核心服务实现
- `crates/ov_cli/src/handlers.rs:handle_add_skill` - CLI 处理

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| data | Any | 否 | - | 内联技能内容或结构化数据。与 `temp_file_id` 二选一 |
| temp_file_id | string | 否 | - | 临时上传文件 ID（通过 [temp_upload](#temp_upload) 获取）。与 `data` 二选一 |
| wait | bool | 否 | False | 是否等待技能处理完成 |
| timeout | float | 否 | None | 超时时间（秒），仅 `wait=true` 时生效 |
| telemetry | TelemetryRequest | 否 | False | 是否返回遥测数据 |

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/skills
Content-Type: application/json
```

```bash
# 使用内联数据
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": {
      "name": "my-skill",
      "description": "My custom skill",
      "steps": []
    }
  }'

# 使用本地文件（需先使用 temp_upload 上传）
TEMP_FILE_ID=$(
  curl -s -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F "file=@./skills/my-skill.json" \
  | jq -r '.result.temp_file_id'
)

curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\"
  }"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# 从本地文件添加技能
result = client.add_skill("./skills/my-skill.json")

# 等待处理完成
client.wait_processed()
```

**CLI**

```bash
# 添加技能
ov add-skill ./skills/my-skill.json

# 等待处理完成
ov add-skill ./skills/my-skill.json --wait
```

#### 4. 响应示例

**HTTP API 响应 (JSON)**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://user/skills/my-skill",
    "uri": "viking://user/skills/my-skill",
    "name": "my-skill",
    "auxiliary_files": 2,
    "queue_status": {
      "pending": 0,
      "processing": 0,
      "completed": 1
    }
  },
  "telemetry": {
    "operation_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

**CLI 响应 (默认表格格式)**

```
Note: Skill is being processed in the background.
Use 'ov wait' to wait for completion, or 'ov observer queue' to check status.
status          success
root_uri        viking://user/skills/my-skill
uri             viking://user/skills/my-skill
name            my-skill
auxiliary_files 2
```

**CLI 响应 (JSON 格式，使用 -o json)**

```json
{
  "status": "success",
  "root_uri": "viking://user/skills/my-skill",
  "uri": "viking://user/skills/my-skill",
  "name": "my-skill",
  "auxiliary_files": 2
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 处理状态："success" 成功，"error" 失败 |
| `root_uri` | string | 技能在 OpenViking 中的最终 URI（同 `uri`） |
| `uri` | string | 技能在 OpenViking 中的最终 URI（同 `root_uri`） |
| `name` | string | 技能名称 |
| `auxiliary_files` | number | 技能附带的辅助文件数量 |
| `queue_status` | object | （可选，仅当 `wait=true` 时）队列处理状态，包含 `pending`、`processing`、`completed` 计数 |

---

### temp_upload

上传临时文件，用于后续通过 [add_resource](#add_resource) 或 [add_skill](#add_skill) 导入本地文件。

#### 1. API 实现介绍

此接口用于把本地文件上传到服务端托管的临时存储中，返回 `temp_file_id` 供后续 API 使用。这是一个辅助接口，通常不直接调用，而是通过 SDK 或 CLI 自动使用。

**处理流程**：
1. 接收上传的文件
2. 根据 `upload_mode` 选择临时上传后端
3. 保存文件并记录原始文件名
4. 返回临时文件 ID

**代码入口**：
- `openviking/server/routers/resources.py:temp_upload` - HTTP 路由
- `openviking/service/resource_service.py` - 服务实现

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| file | UploadFile | 是 | - | 上传的文件（multipart/form-data） |
| telemetry | bool | 否 | False | 是否返回遥测数据 |
| upload_mode | string | 否 | `"local"` | 临时上传模式。`local` 保持现有单机行为；`shared` 将文件上传到共享临时存储，适用于分布式部署。 |

说明：

- 默认值是 `local`，所以现有客户端在不改动的情况下仍保持原有行为。
- 只有在你明确需要分布式共享临时上传时，才应显式使用 `upload_mode=shared`。
- `shared` 模式下返回的一次性 `temp_file_id` 形如 `shared_<upload_id>`。
- shared 上传对象存放在内部 `viking://upload/...` 命名空间下，不属于普通文件系统浏览空间。

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/resources/temp_upload
Content-Type: multipart/form-data
```

```bash
curl -X POST http://localhost:1933/api/v1/resources/temp_upload \
  -H "X-API-Key: your-key" \
  -F "file=@./documents/guide.md"
```

分布式 / shared 上传：

```bash
curl -X POST http://localhost:1933/api/v1/resources/temp_upload \
  -H "X-API-Key: your-key" \
  -F "file=@./documents/guide.md" \
  -F "upload_mode=shared"
```

**Python SDK**

Python SDK 中的 `add_resource`、`add_skill` 等接口会自动处理本地文件上传，无需手动调用此接口。在 Python HTTP client 模式下，如果要启用分布式 shared 临时上传，可以在 `ovcli.conf` 中设置 `upload.mode = "shared"`。

**CLI**

CLI 命令也会自动处理本地文件上传，无需手动调用此接口。

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "temp_file_id": "upload_abc123def456.md"
  },
  "telemetry": {
    "operation_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

shared 模式的响应示例：

```json
{
  "status": "ok",
  "result": {
    "temp_file_id": "shared_7f3c1b8d4f2e4b1bb0f6e8b2d9a4c123"
  }
}
```

---

## 相关文档

- [文件系统](03-filesystem.md) - 文件和目录操作
- [技能](04-skills.md) - 技能管理 API
- [检索](06-retrieval.md) - 搜索和上下文获取
- [ovpack 指南](../guides/09-ovpack.md) - ovpack 导入导出详细说明
