# API 文档编写说明

本文档定义 `docs/zh/api/` 目录下各 API 模块文档的统一结构和编写规范。

## 保持文档可用与及时

- 在同一 PR 中更新中英文页面、导航和示例。相同内容保留一个主要说明入口，相关页面通过链接引用。
- 先写读者任务、前置条件、可运行示例和预期结果，实现细节放在使用说明之后。
- 根据当前实现核验默认值和支持的选项。区分已发布行为、`main` 上的实现和后续计划，必要时链接到对应 release 或 PR。
- 可复制配置使用合法 JSON；片段或伪代码需明确标注，不在 `json` 代码块中混入注释。
- 外部工具和服务引用官方文档，只介绍当前任务需要的配置，不复制整套手册。
- 提交前在 `docs/` 运行 `npm run check:docs`、`npm run check:api` 和 `npm run docs:build`。自动检查覆盖结构与示例，不能替代事实核验和翻译 review。

## 文案标准

- 先说明接口做什么、适用条件和调用结果，再解释实现。读者应能据此决定下一步。
- 用具体动作和对象代替“赋能”“闭环”“智能升级”等空泛表述。术语保留准确含义，不为缩短句子省掉前提。
- 不写无依据的效果、性能、费用或安全承诺。默认值、支持范围和失败行为以当前实现为准；示例值明确标注。
- 区分已提交与已完成、可选与必需、当前能力与规划。总览、参数表、示例和排障说明应一致。
- 中文按自然语序写，英文按英文习惯写；两者保持相同的事实和限制，不逐词硬译。
- 保留有用的章节链接；改标题时检查旧锚点，必要时添加兼容锚点。

## 目录结构

API 文档按模块组织，每个模块一个文件，使用两位数字序号前缀。

## 文件统一结构

每个 API 模块文档应遵循以下结构：

````markdown
# <模块名称>

<简短介绍，说明本模块的主要功能和用途>

## <可选的概念/介绍章节>

（如需要，介绍本模块涉及的核心概念、工作流程等）

## API 参考

### <API 方法名 1>

#### 1. 用途与前置条件

<说明接口的操作、适用场景，以及需要的权限、配置或已有资源>

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| <参数名> | <类型> | <是/否> | <默认值> | <详细说明> |
| ... | ... | ... | ... | ... |

**<可选的补充说明章节>**

（如需要，说明特殊行为、注意事项、使用场景等）

#### 3. 使用示例

**Python SDK**

```text
<SDK 调用示例>
```

**TypeScript SDK**

```typescript
<SDK 调用示例>
```

**Go SDK**

```go
<SDK 调用示例>
```

**HTTP API**

```
<HTTP 方法> <路径>
```

```bash
<curl 示例>
```

**CLI**

```bash
<CLI 命令示例>
```

**响应示例**

```json
<JSON 响应示例>
```

#### 4. 响应契约（必填）与错误处理（可选）

每个公开操作都必须说明成功返回值。JSON 接口至少给出一个与实现一致的响应包示例；文件下载、SSE、WebDAV 等非 JSON 接口必须说明 HTTP 状态、关键响应头、响应体或事件格式。不能只写“返回某些字段”而不展示结构。错误和异常处理示例可按需补充。

#### 5. 实现细节（可选）

按需解释处理流程，并链接到核心实现、HTTP 路由或 CLI 处理函数，帮助读者理解行为或排查问题。

---

### <API 方法名 2>

（重复上述结构）

---

## <可选的其他章节>

## 相关文档

- [<文档标题>](<相对路径>) - <简短说明>
````

## 结构详解

### 标题与简介

- 一级标题是模块名称
- 简介用一段话说明该模块的用途和主要功能

### API 参考章节

每个接口按以下章节组织。实现细节放在响应契约之后，仅保留有助于理解行为或排障的内容。现有页面仍使用旧的“API 实现介绍”章节和代码入口；新页面及迁移页面时按本结构编写。

<a id="1-api-实现介绍"></a>

#### 1. 用途与前置条件

- 说明接口做什么、适合什么场景。
- 列出必需的权限、配置和已有资源。
- 提前说明调用会产生的影响，包括覆盖或删除已有数据。

#### 2. 接口和参数说明

- 参数表：包含参数名、类型、是否必填、默认值、说明
- 补充说明（可选）：特殊行为、注意事项、使用场景等

#### 3. 使用示例

对于返回后台任务的操作，默认示例优先使用“提交任务 → 查询状态”，尽量不主动设置 `wait`、`timeout`，也不使用全局 `wait_processed()` 代替任务状态。完整参数参考应保留受支持参数及其真实默认值。后续操作依赖异步产物时，应展示按 `task_id` 轮询至 `completed`，并处理 `failed`、`cancelled`；不能仅删除等待参数后立即读取结果。

当一个接口并列展示所有调用方式时，建议按以下顺序提供：
- Python SDK 示例
- TypeScript SDK 示例
- Go SDK 示例（当它能补充说明该接口的调用形态，且可以保持简短时）
- HTTP API（方法 + 路径 + curl 示例）
- CLI 示例
- 响应示例

示例切换由加粗标签自动生成。调用方式标签必须单独成段，并使用以下固定基础写法：
`**Python SDK**`、`**TypeScript SDK**`、`**Go SDK**`、`**HTTP API**`、`**CLI**`。
需要区分调用形态时，把限定词用半角括号写在同一个加粗标签内，例如
`**Python SDK (HTTP)**`；不要把限定词写在加粗标签外，也不要使用全角括号。`**Python HTTP SDK**` 这类写法不会生成 Tab。
只展示实现中真实存在的调用方式；某个 SDK 或 CLI 没有对应能力时应省略该 Tab，并简短说明
可用的替代入口。不要把手写 HTTP 请求包装成不存在的 SDK 方法。

如果现有接口因工作流说明采用了特殊结构，应保持该局部结构稳定，并将 TypeScript 放在其他
SDK 示例附近。

API 文档应按 API 模块和具体接口组织，而不是按客户端语言组织。SDK 片段只作为对应接口
“使用示例”中的简短调用示例出现。语言专属 quick reference、完整 walkthrough 或跨接口串联流程
应放在该 SDK 自己的文档中，不应新增到 API 模块页面里。

## 示例：完整接口文档

````markdown
### add_resource()

#### 1. 用途与前置条件

向知识库添加资源，支持本地文件、目录、URL 等多种来源。

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| path | str | 是 | - | 本地路径、目录路径或 URL |
| to | str | 否 | None | 最终资源 URI；目标已存在时会覆盖 |
| reason | str | 否 | "" | 添加该资源的原因 |
| wait | bool | 否 | False | 是否等待语义处理完成 |

**说明**

- SDK/CLI 可直接传本地路径；裸 HTTP 需要先用 `temp_upload` 上传
- 已存在的 `to` 目标会被覆盖。目标为目录时，本次导入未生成的旧文件可能被删除；未变化的内容在语义和向量处理中会复用。向已有目录新增资源时使用 `parent`。

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/resources
```

```bash
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "https://example.com/guide.md",
    "reason": "User guide documentation"
  }'
```

**Python SDK**

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")

result = client.add_resource(
    path="./documents/guide.md",
    options={"reason": "User guide documentation"},
)
print(f"Task ID: {result['task_id']}")

print(client.get_task(result["task_id"]))
```

**CLI**

```bash
ov add-resource ./documents/guide.md --reason "User guide documentation"
```

#### 4. 响应契约

```json
{
  "status": "ok",
  "result": {
    "status": "accepted",
    "root_uri": "viking://resources/guide",
    "task_id": "uuid-xxx"
  }
}
```

#### 5. 实现细节

**处理流程**：
1. 识别资源类型（本地文件/目录/URL）
2. 调用对应 Parser 解析内容
3. 构建目录树并写入 AGFS
4. 异步生成 L0/L1 语义摘要
5. 建立向量索引

**代码入口**：
- `sdk/python/openviking_sdk/client.py:AsyncHTTPClient.add_resource()` - 异步 SDK 入口
- `sdk/python/openviking_sdk/client.py:SyncHTTPClient.add_resource()` - 同步 SDK 入口
- `openviking/service/resource_service.py:ResourceService.add_resource()` - 核心实现
- `openviking/server/routers/resources.py:add_resource()` - HTTP 路由
- `crates/ov_cli/src/handlers.rs:handle_add_resource()` - CLI 处理函数

---
````

## 文档维护清单

新增或修改 API 文档时，请检查：

- [ ] 用途、前置条件和调用影响清楚，引用的代码入口路径正确
- [ ] 参数表完整且准确
- [ ] 示例代码简洁且可运行
- [ ] 调用示例使用固定加粗标签，并且每个 SDK/CLI Tab 都有真实实现
- [ ] HTTP 方法和路径正确
- [ ] 每个公开操作都有成功响应契约，且示例与实际返回一致
