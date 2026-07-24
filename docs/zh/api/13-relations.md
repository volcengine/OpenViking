# 关系

关系 API 用于创建、读取和删除 Viking URI 之间的显式关联。

## API 参考

### link()

创建资源之间的关联。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| from_uri | str | 是 | - | 源 URI |
| to_uris | str 或 List[str] | 是 | - | 目标 URI |
| reason | str | 否 | "" | 关联原因 |


**Python SDK (Embedded / HTTP)**

```python
# 单个关联
client.link(
    "viking://resources/docs/auth/",
    "viking://resources/docs/security/",
    reason="Security best practices for authentication"
)

# 多个关联
client.link(
    "viking://resources/docs/api/",
    [
        "viking://resources/docs/auth/",
        "viking://resources/docs/errors/"
    ],
    reason="Related documentation"
)
```

**TypeScript SDK**

```typescript
await client.link(
  "viking://resources/docs/api/",
  [
    "viking://resources/docs/auth/",
    "viking://resources/docs/errors/",
  ],
  "Related documentation",
);
```

**HTTP API**

```
POST /api/v1/relations/link
```

```bash
# 单个关联
curl -X POST http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/auth/",
    "to_uris": "viking://resources/docs/security/",
    "reason": "Security best practices for authentication"
  }'

# 多个关联
curl -X POST http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/api/",
    "to_uris": ["viking://resources/docs/auth/", "viking://resources/docs/errors/"],
    "reason": "Related documentation"
  }'
```

**CLI**

```bash
openviking link viking://resources/docs/auth/ viking://resources/docs/security/ --reason "Security best practices"
```


**响应**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/docs/auth/",
    "to": "viking://resources/docs/security/"
  },
  "time": 0.1
}
```

---

### relations()

获取资源的关联关系。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | Viking URI |


**Python SDK (Embedded / HTTP)**

```python
relations = client.relations("viking://resources/docs/auth/")
for rel in relations:
    print(f"Related: {rel['uri']}")
    print(f"  Reason: {rel['reason']}")
```

**TypeScript SDK**

```typescript
const relations = await client.relations("viking://resources/docs/auth/");
console.log(relations);
```

**HTTP API**

```
GET /api/v1/relations?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/relations?uri=viking://resources/docs/auth/" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking relations viking://resources/docs/auth/
```


**响应**

```json
{
  "status": "ok",
  "result": [
    {"uri": "viking://resources/docs/security/", "reason": "Security best practices"},
    {"uri": "viking://resources/docs/errors/", "reason": "Error handling"}
  ],
  "time": 0.1
}
```

---

### unlink()

移除关联关系。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| from_uri | str | 是 | - | 源 URI |
| to_uri | str | 是 | - | 要取消关联的目标 URI |


**Python SDK (Embedded / HTTP)**

```python
client.unlink(
    "viking://resources/docs/auth/",
    "viking://resources/docs/security/"
)
```

**TypeScript SDK**

```typescript
await client.unlink(
  "viking://resources/docs/auth/",
  "viking://resources/docs/security/",
);
```

**HTTP API**

```
DELETE /api/v1/relations/link
```

```bash
curl -X DELETE http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/auth/",
    "to_uri": "viking://resources/docs/security/"
  }'
```

**CLI**

```bash
openviking unlink viking://resources/docs/auth/ viking://resources/docs/security/
```


**响应**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/docs/auth/",
    "to": "viking://resources/docs/security/"
  },
  "time": 0.1
}
```

---

### build_graph()

从多个记忆根目录生成一个自包含的 HTML 关系图，并把结果写入指定 Viking URI。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `space_uris` | string[] | 是 | 要合并的记忆根目录 |
| `output_uri` | string | 是 | 输出 HTML 文件的 Viking URI |

**HTTP API**

```http
POST /api/v1/relations/build_graph
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/relations/build_graph \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "space_uris":[
      "viking://user/default/memories",
      "viking://user/default/peers/project-a/memories"
    ],
    "output_uri":"viking://user/default/memories/.graph.html"
  }'
```

该端点当前没有公共 SDK 或 CLI 封装，因此本节只展示 HTTP Tab。

---

## 相关文档

- [检索](06-retrieval.md) - 检索并使用关联内容
- [文件系统](03-filesystem.md) - 管理关联目标
- [记忆](16-memory.md) - 记忆命名空间和类型
