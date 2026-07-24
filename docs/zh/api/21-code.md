# 代码导航

代码导航 API 对已经导入 OpenViking 的源代码执行结构提取、符号搜索和符号展开。所有 `uri` 必须是 `viking://` URI；本地代码需要先通过资源 API 导入。

## API 参考

### outline()

提取单个源文件的符号大纲。

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

### search()

在指定目录下的受支持源文件中搜索符号。一次请求最多扫描 200 个文件；达到上限时响应会提示缩小 URI 范围。

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

### expand()

读取单个文件中指定符号的完整代码片段。

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

## 当前客户端支持

这些端点当前是 Server HTTP 能力，尚未进入 Python、TypeScript、Go 公共 SDK 或 `ov` CLI，因此本页只展示 HTTP Tab。

## 相关文档

- [资源](02-resources.md) - 导入源代码
- [检索](06-retrieval.md) - 通用语义与模式检索
- [内容](12-content.md) - 读取源文件内容
