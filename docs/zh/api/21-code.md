# 代码导航

代码导航 API 对已经导入 OpenViking 的源代码执行结构提取、符号搜索和符号展开。所有 `uri` 必须是 `viking://` URI；本地代码需要先通过资源 API 导入。

**代码入口**：

- `openviking/server/routers/code.py` - HTTP 路由与请求模型
- `openviking/parse/parsers/code/ast/code_tools.py` - 大纲、搜索和符号展开实现

## API 参考

### outline()

提取单个源文件的符号大纲。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `uri` | string | 是 | 已导入源文件的 `viking://` URI |

**HTTP API**

```http
POST /api/v1/code/outline
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/code/outline \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"uri":"viking://resources/project/src/main.py"}'
```

**响应示例**

`result` 是适合直接展示或注入 Agent 上下文的文本大纲。

```json
{
  "status": "ok",
  "result": "viking://resources/project/src/main.py  [python, 24 lines]\n\nclass SessionService  L16-24\n\ndef main()  L8-12"
}
```

### search()

在指定目录下的受支持源文件中搜索符号。一次请求最多扫描 200 个文件；达到上限时，`result` 末尾会提示缩小 URI 范围。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `uri` | string | 是 | 要递归搜索的文件或目录 URI |
| `query` | string | 是 | 非空的符号名称或搜索词 |

**HTTP API**

```http
POST /api/v1/code/search
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/code/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"uri":"viking://resources/project/src","query":"SessionService"}'
```

**响应示例**

```json
{
  "status": "ok",
  "result": "1 matches for \"SessionService\" (scanned 8 files)\n\nviking://resources/project/src/session.py\n  SessionService  L16-24"
}
```

### expand()

读取单个文件中指定符号的完整代码片段。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `uri` | string | 是 | 包含目标符号的源文件 URI |
| `symbol` | string | 是 | 要展开的非空符号名称 |

**HTTP API**

```http
POST /api/v1/code/expand
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/code/expand \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"uri":"viking://resources/project/src/main.py","symbol":"main"}'
```

**响应示例**

```json
{
  "status": "ok",
  "result": "# viking://resources/project/src/main.py  L8-10  (main)\n\ndef main():\n    service = SessionService()\n    service.run()"
}
```

当前实现把“非 `viking://` URI”“空查询或空符号”“非文本文件”等业务失败作为 `status="ok"` 返回，错误说明位于以 `Error:` 开头的 `result` 字符串中。请求体缺少必填字段时返回 HTTP `400` 和 `INVALID_ARGUMENT`。

## 当前客户端支持

这些端点当前是 Server HTTP 能力，尚未进入 Python、TypeScript、Go 公共 SDK 或 `ov` CLI，因此本页只展示 HTTP Tab。

## 相关文档

- [资源](02-resources.md) - 导入源代码
- [检索](06-retrieval.md) - 通用语义与模式检索
- [内容](12-content.md) - 读取源文件内容
