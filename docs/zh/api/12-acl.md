# ACL API

ACL API 管理资源节点的直接授权，并返回节点继承后的有效权限。ACL 只在当前 account 内生效。

权限模型和继承规则请先阅读 [资源访问控制（ACL）](../concepts/15-acl.md)。

## 接口总览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/acl?uri={uri}` | 获取直接、继承和有效 ACL |
| PUT | `/api/v1/acl` | 替换当前节点的直接 ACL |
| DELETE | `/api/v1/acl?uri={uri}` | 清空当前节点的直接 ACL |
| POST | `/api/v1/acl/grant` | 设置一个用户的直接权限级别 |
| POST | `/api/v1/acl/revoke` | 删除一个用户的直接授权 |

所有接口都要求调用者对目标节点拥有 `manage`。公共资源由 account `ADMIN` 隐式管理；用户资源由 URI 中的所属用户隐式管理。

## 数据结构

### ACL entry

```json
{
  "user_id": "bob",
  "level": "viewer"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | string | 当前 account 内的用户 ID；`*` 表示任意用户 |
| `level` | string | `viewer`、`editor` 或 `manager` |

### ACL report

```json
{
  "uri": "viking://resources/project-a",
  "acl_enabled": true,
  "direct_entries": [
    {"user_id": "bob", "level": "viewer"}
  ],
  "inherited_entries": [
    {"user_id": "alice", "level": "editor"}
  ],
  "effective_entries": [
    {"user_id": "alice", "level": "editor"},
    {"user_id": "bob", "level": "viewer"}
  ]
}
```

| 字段 | 说明 |
|------|------|
| `direct_entries` | 只包含当前节点直接设置的条目 |
| `inherited_entries` | 所有祖先目录直接 ACL 的合并结果 |
| `effective_entries` | `direct_entries` 与 `inherited_entries` 的合并结果 |
| `acl_enabled` | 当前节点或任一祖先存在直接 ACL 时为 `true`；只读派生字段 |

隐式 manager 不出现在这些列表中。

## 获取 ACL

```
GET /api/v1/acl?uri={uri}
```

```bash
curl "http://localhost:1933/api/v1/acl?uri=viking%3A%2F%2Fresources%2Fproject-a" \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
report = client.acl_get("viking://resources/project-a")
```

**Go SDK**

```go
report, err := client.ACL(ctx, "viking://resources/project-a")
```

## 替换直接 ACL

```
PUT /api/v1/acl
```

请求体：

```json
{
  "uri": "viking://resources/project-a",
  "entries": [
    {"user_id": "bob", "level": "viewer"},
    {"user_id": "ci-bot", "level": "editor"}
  ]
}
```

`entries` 会完整替换当前节点的直接 ACL，不影响祖先或后代节点自己的直接 ACL。重复 principal 会保留最高权限级别。传入空数组等价于删除当前节点的直接 ACL。

```bash
curl -X PUT http://localhost:1933/api/v1/acl \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/project-a",
    "entries": [
      {"user_id": "bob", "level": "viewer"},
      {"user_id": "ci-bot", "level": "editor"}
    ]
  }'
```

**Python SDK**

```python
report = client.acl_set(
    "viking://resources/project-a",
    [
        {"user_id": "bob", "level": "viewer"},
        {"user_id": "ci-bot", "level": "editor"},
    ],
)
```

异步客户端使用相同方法名：

```python
report = await client.acl_set(uri, entries)
```

**Go SDK**

```go
report, err := client.SetACL(ctx, "viking://resources/project-a", []openviking.ACLEntry{
    {UserID: "bob", Level: "viewer"},
    {UserID: "ci-bot", Level: "editor"},
})
```

**CLI**

```bash
ov acl set viking://resources/project-a \
  --entry bob=viewer \
  --entry ci-bot=editor
```

## 设置单个用户权限

```
POST /api/v1/acl/grant
```

```json
{
  "uri": "viking://resources/project-a",
  "user_id": "bob",
  "level": "editor"
}
```

该接口将 Bob 在当前节点上的直接 level 设置为 `editor`。如果已有直接条目，则更新该条目；其他用户条目不变。

```bash
curl -X POST http://localhost:1933/api/v1/acl/grant \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/project-a",
    "user_id": "bob",
    "level": "editor"
  }'
```

```python
report = client.acl_grant(
    "viking://resources/project-a",
    user_id="bob",
    level="editor",
)
```

```bash
ov acl grant viking://resources/project-a --user-id bob --level editor
```

## 删除单个用户的直接授权

```
POST /api/v1/acl/revoke
```

```json
{
  "uri": "viking://resources/project-a",
  "user_id": "bob"
}
```

`revoke` 只删除当前节点上 Bob 的直接条目。Bob 从祖先继承的权限仍然有效。

```python
report = client.acl_revoke("viking://resources/project-a", user_id="bob")
```

```bash
ov acl revoke viking://resources/project-a --user-id bob
```

## 清空当前节点的直接 ACL

```
DELETE /api/v1/acl?uri={uri}
```

该接口不删除后代节点的直接 ACL。清空后，当前节点从祖先 ACL 重新继承；每个后代继续由其祖先与自身的直接 ACL 计算有效权限。

```bash
curl -X DELETE \
  "http://localhost:1933/api/v1/acl?uri=viking%3A%2F%2Fresources%2Fproject-a" \
  -H "X-API-Key: your-key"
```

```python
report = client.acl_delete("viking://resources/project-a")
```

```bash
ov acl rm viking://resources/project-a
```

## 错误处理

| 场景 | 错误 |
|------|------|
| URI 不存在 | `NOT_FOUND` |
| URI 已存在但尚无 context 记录 | `INVALID_ARGUMENT`，需先完成索引 |
| 调用者没有 manage | `PERMISSION_DENIED` |
| `user_id` 非法 | `INVALID_ARGUMENT` |
| level 不是 `viewer/editor/manager` | `INVALID_ARGUMENT` |
| 请求包含 `acl_enabled` 等未知字段 | `INVALID_ARGUMENT` |

ACL 的 direct 和 inherited 字段都保存在 context。更新会在同一子树批处理中修改目标 direct 并重算后代 inherited；写入失败时恢复原 context ACL 字段。

## 相关文档

- [资源访问控制（ACL）](../concepts/15-acl.md) - 权限、继承和检索语义
- [认证](../guides/04-authentication.md) - 请求身份与 account 角色
- [文件系统 API](./03-filesystem.md) - 受 ACL 控制的文件操作
- [检索 API](./06-retrieval.md) - `find/search` 接口
