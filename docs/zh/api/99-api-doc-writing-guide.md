# API 文档编写说明

本文档定义 `docs/zh/api/` 目录下各 API 模块文档的统一结构和编写规范。

## 目录结构

API 文档按模块组织，每个模块一个文件，使用两位数字序号前缀。

## 文件统一结构

每个 API 模块文档应遵循以下结构：

```markdown
# <模块名称>

<简短介绍，说明本模块的主要功能和用途>

## <可选的概念/介绍章节>

（如需要，介绍本模块涉及的核心概念、工作流程等）

## API 参考

### <API 方法名 1>

#### 1. API 实现介绍

<介绍该 API 的用途，指向对应的代码入口，简单介绍原理和流程>

**代码入口**：
- `openviking/<模块>/<文件>.py:<类名>.<方法名>` - 核心实现
- `openviking/server/routers/<路由文件>.py` - HTTP 路由
- `openviking_cli/commands/<命令文件>.py` - CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| <参数名> | <类型> | <是/否> | <默认值> | <详细说明> |
| ... | ... | ... | ... | ... |

**<可选的补充说明章节>**

（如需要，说明特殊行为、注意事项、使用场景等）

#### 3. 使用示例

**HTTP API**

```
<HTTP 方法> <路径>
```

```bash
<curl 示例>
```

**Python SDK**

```python
<SDK 调用示例>
```

**CLI**

```bash
<CLI 命令示例>
```

**响应示例**

```json
<JSON 响应示例>
```

#### 4. 响应示例、错误和异常处理等（可选）

---

### <API 方法名 2>

（重复上述结构）

---

## <可选的其他章节>

## 相关文档

- [<文档标题>](<相对路径>) - <简短说明>
```

## 结构详解

### 标题与简介

- 一级标题是模块名称
- 简介用一段话说明该模块的用途和主要功能

### API 参考章节

每个接口按以下三个部分组织：

#### 1. API 实现介绍

- 说明该 API 的用途
- 提供代码入口路径，方便读者查阅源码
- 简单介绍实现原理和处理流程

**代码入口说明**：
- 核心实现：指向主要业务逻辑代码
- HTTP 路由：指向 FastAPI 路由定义
- CLI 命令：指向 CLI 命令实现（如有）

#### 2. 接口和参数说明

- 参数表：包含参数名、类型、是否必填、默认值、说明
- 补充说明（可选）：特殊行为、注意事项、使用场景等

#### 3. 使用示例

按顺序提供：
- HTTP API（方法 + 路径 + curl 示例）
- Python SDK 示例
- CLI 示例
- 响应示例

## 示例：完整接口文档

```markdown
### add_resource()

#### 1. API 实现介绍

向知识库添加资源，支持本地文件、目录、URL 等多种来源。

**处理流程**：
1. 识别资源类型（本地文件/目录/URL）
2. 调用对应 Parser 解析内容
3. 构建目录树并写入 AGFS
4. 异步生成 L0/L1 语义摘要
5. 建立向量索引

**代码入口**：
- `openviking/core/client.py:OpenViking.add_resource()` - SDK 入口
- `openviking/resource/importer.py:ResourceImporter.import_resource()` - 核心实现
- `openviking/server/routers/resources.py` - HTTP 路由
- `openviking_cli/commands/resources.py` - CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| path | str | 是 | - | 本地路径、目录路径或 URL |
| to | str | 否 | None | 目标 Viking URI（必须在 resources 作用域内） |
| reason | str | 否 | "" | 添加该资源的原因 |
| wait | bool | 否 | False | 是否等待语义处理完成 |

**说明**

- SDK/CLI 可直接传本地路径；裸 HTTP 需要先用 `temp_upload` 上传
- 当指定 `to` 且目标已存在时，走增量更新流程

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
    "reason": "User guide documentation",
    "wait": true
  }'
```

**Python SDK**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

result = client.add_resource(
    "./documents/guide.md",
    reason="User guide documentation"
)
print(f"Added: {result['root_uri']}")

client.wait_processed()
```

**CLI**

```bash
openviking add-resource ./documents/guide.md --reason "User guide documentation" --wait
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://resources/documents/guide.md",
    "source_path": "./documents/guide.md",
    "errors": []
  },
  "time": 0.123
}
```

---
```

## 文档维护清单

新增或修改 API 文档时，请检查：

- [ ] 实现介绍清晰，代码入口路径正确
- [ ] 参数表完整且准确
- [ ] 示例代码简洁且可运行
- [ ] HTTP 方法和路径正确
- [ ] 响应示例与实际返回一致
