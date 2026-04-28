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
| Git 协议代码仓库 | `git://...` | Git URL, 本地目录, `.zip` 包，会自动过滤 `.git`, `node_modules` 等目录 |
| Git 代码托管平台 | `https://github.com/{org}/{repo}` | GitHub, GitLab, Bitbucket 等代码托管平台的 URL |
| Git 代码托管平台上的 raw 文件 | `https://github.com/{org}/{repo}/raw/{branch}/{path}` | GitHub, GitLab, Bitbucket 等代码托管平台的 raw 文件下载 URL |

媒体类
| 类型 | 资源名 | 说明 |
|------|--------|------|
| 图片 | `*.jpg`, `*.jpeg`, `*.png`, `*.gif` ... | 多种图片格式，通过 VLM 生成描述 |
| 视频 | `*.mp4`, `*.avi`, `*.mov` ... | 提取关键帧后使用 VLM 分析 |
| 音频 | `*.mp3`, `*.wav`, `*.m4a` ... | 进行语音转录处理 |

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

### 资源的增量更新

资源增量更新通过**监控任务 (Watch Task)** 机制实现：

#### 监控任务创建
- 调用 `add_resource` 时设置 `watch_interval > 0` （单位：分钟）创建监控任务
- 需指定 `to` 参数确定目标 URI
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

此接口是资源管理的核心入口，支持多种来源的资源添加，并可选择等待语义处理完成。

**处理流程**：
1. 识别资源来源（URL 或上传的临时文件）
2. 调用对应 Parser 解析内容
3. 构建目录树并写入 AGFS
4. 如指定 `watch_interval`，设置定时更新任务
5. 如指定 `wait=true`，等待语义处理完成

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
| to | string | 否 | - | 目标 Viking URI（精确位置）。与 `parent` 二选一 |
| parent | string | 否 | - | 父级 Viking URI（资源放入此目录下）。与 `to` 二选一 |
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
| watch_interval | float | 否 | 0 | 定时更新间隔（分钟）。>0 创建任务；≤0 取消任务 |
| telemetry | TelemetryRequest | 否 | False | 是否返回遥测数据 |

**补充说明**：
- `to` 和 `parent` 不能同时指定
- `path` 和 `temp_file_id` 不能同时指定
- 裸 HTTP 调用本地文件需要先用 [temp_upload](#temp_upload) 上传获取 `temp_file_id`
- 指定 `to` 且目标已存在时，触发增量更新
- `watch_interval` 仅在指定 `to` 时生效

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

# 使用嵌入式模式
client = ov.OpenViking(path="./data")
client.initialize()

# 或使用 HTTP 客户端
client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# 添加本地文件
result = client.add_resource(
    "./documents/guide.md",
    reason="User guide documentation"
)
print(f"Added: {result['root_uri']}")

# 从 URL 添加到指定位置
result = client.add_resource(
    "https://example.com/api-docs.md",
    to="viking://resources/external/api-docs.md",
    reason="External API docs"
)

# 等待处理完成
client.wait_processed()

# 开启定时更新
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
ov add-resource https://github.com/example/repo.git --to viking://resources/guide.md --watch-interval 60

# 取消定时更新
ov add-resource https://github.com/example/repo.git --to viking://resources/guide.md --watch-interval 0
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://resources/guide.md",
    "source_path": "./documents/guide.md",
    "errors": [],
    "queue_status": {
      "pending": 5,
      "processing": 2,
      "completed": 10
    }
  },
  "time": 0.456
}
```

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

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "skill_uri": "viking://skills/my-skill"
  },
  "time": 0.123
}
```

---

### export_ovpack

将资源树导出为 `.ovpack` 文件。

#### 1. API 实现介绍

将指定 URI 下的所有资源打包成 `.ovpack` 格式文件，用于备份或迁移。需要 ROOT 或 ADMIN 权限。

**处理流程**：
1. 验证用户权限
2. 遍历指定 URI 下的资源
3. 打包成 zip 格式（.ovpack）
4. 以文件流形式返回

**代码入口**：
- `openviking/server/routers/pack.py:export_ovpack` - HTTP 路由
- `openviking/service/pack_service.py` - 核心服务实现
- `crates/ov_cli/src/handlers.rs:handle_export` - CLI 处理

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | string | 是 | - | 要导出的 Viking URI |

**权限要求**：ROOT 或 ADMIN

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/pack/export
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{
    "uri": "viking://resources/my-project/"
  }' \
  --output my-project.ovpack
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-admin-key")
client.initialize()

# 导出到本地文件（HTTP SDK 会自动处理下载）
# 注意：导出功能主要通过 CLI 使用
```

**CLI**

```bash
# 导出资源
ov export viking://resources/my-project/ ./exports/my-project.ovpack
```

**响应示例**

此接口直接返回文件流（`Content-Type: application/zip`），不返回 JSON 包装体。

---

### import_ovpack

导入 `.ovpack` 文件。

#### 1. API 实现介绍

将 `.ovpack` 文件导入到指定位置，用于恢复或迁移数据。需要 ROOT 或 ADMIN 权限。

**处理流程**：
1. 验证用户权限
2. 解析上传的 `.ovpack` 文件
3. 导入资源到目标位置
4. 可选地触发向量化

**代码入口**：
- `openviking/server/routers/pack.py:import_ovpack` - HTTP 路由
- `openviking/service/pack_service.py` - 核心服务实现
- `crates/ov_cli/src/handlers.rs:handle_import` - CLI 处理

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| temp_file_id | string | 是 | - | 临时上传文件 ID（通过 [temp_upload](#temp_upload) 获取） |
| parent | string | 是 | - | 目标父级 URI（导入到此处） |
| force | bool | 否 | False | 是否覆盖已有资源 |
| vectorize | bool | 否 | True | 是否触发向量化 |

**权限要求**：ROOT 或 ADMIN

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/pack/import
Content-Type: application/json
```

```bash
# 第一步：上传 .ovpack 文件
TEMP_FILE_ID=$(
  curl -s -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-admin-key" \
    -F "file=@./exports/my-project.ovpack" \
  | jq -r '.result.temp_file_id'
)

# 第二步：导入
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://resources/imported/\",
    \"force\": true,
    \"vectorize\": true
  }"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-admin-key")
client.initialize()

# 导入 .ovpack 文件（HTTP SDK 会自动处理上传）
# 注意：导入功能主要通过 CLI 使用
```

**CLI**

```bash
# 导入 .ovpack 文件
ov import ./exports/my-project.ovpack viking://resources/imported/

# 强制覆盖已有内容
ov import ./exports/my-project.ovpack viking://resources/imported/ --force

# 不进行向量化
ov import ./exports/my-project.ovpack viking://resources/imported/ --no-vectorize
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/imported/my-project/"
  },
  "time": 0.789
}
```

---

### temp_upload

上传临时文件，用于后续通过 [add_resource](#add_resource)、[add_skill](#add_skill) 或 [import_ovpack](#import_ovpack) 导入本地文件。

#### 1. API 实现介绍

此接口用于上传本地文件到服务器临时存储，返回 `temp_file_id` 供后续 API 使用。这是一个辅助接口，通常不直接调用，而是通过 SDK 或 CLI 自动使用。

**处理流程**：
1. 接收上传的文件
2. 清理过期的临时文件
3. 保存到临时目录并记录原始文件名
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

**Python SDK**

Python SDK 中的 `add_resource`、`add_skill` 等接口会自动处理本地文件上传，无需手动调用此接口。

**CLI**

CLI 命令也会自动处理本地文件上传，无需手动调用此接口。

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "temp_file_id": "upload_abc123def456.md"
  },
  "time": 0.123
}
```

---

## 相关文档

- [文件系统](03-filesystem.md) - 文件和目录操作
- [技能](04-skills.md) - 技能管理 API
- [检索](06-retrieval.md) - 搜索和上下文获取
- [ovpack 指南](../guides/09-ovpack.md) - ovpack 导入导出详细说明
