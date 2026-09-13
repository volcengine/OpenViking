# ACL API

ACL API 管理 `viking://resources/...` 共享资源的直接授权和 restricted 模式，并返回节点继承后的有效权限。个人资源不接受 ACL，需要分享时应移动到共享区。

权限模型和继承规则请先阅读 [资源访问控制（ACL）](../concepts/15-acl.md)。

## 接口总览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/acl?uri={uri}` | 获取直接、继承和有效 ACL |
| PUT | `/api/v1/acl` | 更新当前节点的直接 ACL 或 restricted 模式 |
| DELETE | `/api/v1/acl?uri={uri}` | 清空直接 ACL 并退出 restricted 模式 |
| POST | `/api/v1/acl/grant` | 设置一个 principal 的直接权限级别 |
| POST | `/api/v1/acl/revoke` | 删除一个 principal 的直接授权 |

所有接口都要求调用者对目标节点拥有 `manage`。共享资源由 account `ADMIN` 隐式管理。

`viking://resources` 是固定共享 scope，不能设置直接 ACL。账号配置
`acl.enabled` 默认为 `false`。关闭时，共享资源完全使用原有公开规则，不执行 ACL
鉴权；开启后，新建共享文件、目录和 `add-resource` 根节点会给创建者直接
`manage`，同时继承父目录 ACL。已有且未设置 ACL 的内容仍按公开规则访问；
`add-resource` 的内部节点只继承，不重复写直接权限。

## 数据结构

### ACL entry

```json
{
  "principal": "user:bob",
  "level": "read"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `principal` | string | `user:{user_id}`、`group:{group_id}` 或 `user:*` |
| `level` | string | `read`、`write` 或 `manage` |

`group_id` 由调用者通过 [Admin API](./08-admin.md#用户组) 指定，是 account 内唯一且稳定的标识；用户组没有单独的展示名称。删除用户组后，旧 principal 不再匹配请求，除非重新创建同一个 `group_id`。

### ACL report

```json
{
  "uri": "viking://resources/project-a",
  "acl_mode": "inherit",
  "direct_entries": [
    {"principal": "user:bob", "level": "read"}
  ],
  "inherited_entries": [
    {"principal": "group:engineering", "level": "write"}
  ],
  "effective_entries": [
    {"principal": "group:engineering", "level": "write"},
    {"principal": "user:bob", "level": "read"}
  ]
}
```

| 字段 | 说明 |
|------|------|
| `direct_entries` | 只包含当前节点直接设置的条目 |
| `inherited_entries` | 父节点当前的有效权限；restricted 期间也会继续更新 |
| `effective_entries` | inherit 时合并 direct 与 inherited；restricted 时仅使用 direct |
| `acl_mode` | `none`：不受 ACL 控制；`inherit`：直接与继承权限均生效；`restricted`：仅直接权限生效 |

account `ADMIN` 的隐式 `manage` 权限不出现在这些列表中。

## 获取 ACL

```
GET /api/v1/acl?uri={uri}
```

GET 可以在目标尚无 context 记录时返回结果：`direct_entries` 为空，继承权限从已有祖先 context 计算。修改 ACL 的接口要求目标已有 context 记录。

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

## 更新直接 ACL 或 restricted 模式

```
PUT /api/v1/acl
```

请求体：

```json
{
  "uri": "viking://resources/project-a",
  "entries": [
    {"principal": "user:bob", "level": "read"},
    {"principal": "group:engineering", "level": "write"}
  ],
  "acl_mode": "restricted"
}
```

`entries` 和 `acl_mode` 至少传一个。`entries` 完整替换直接权限；`acl_mode` 支持 `restricted`（只使用直接权限）和 `inherit`（恢复继承）。未传的字段保持不变。restricted 期间继承权限仍随父节点更新，恢复继承后立即使用最新值。重复 principal 保留最高权限级别。

不能直接设置 `none` 来绕过父目录的 ACL。恢复继承或删除 ACL 后，如果当前节点没有任何直接权限，父目录也不受 ACL 控制，系统会自动返回 `none`。

```bash
curl -X PUT http://localhost:1933/api/v1/acl \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/project-a",
    "entries": [
      {"principal": "user:bob", "level": "read"},
      {"principal": "group:engineering", "level": "write"}
    ],
    "acl_mode": "restricted"
  }'
```

**Python SDK**

```python
report = client.acl_set(
    "viking://resources/project-a",
    [
        {"principal": "user:bob", "level": "read"},
        {"principal": "group:engineering", "level": "write"},
    ],
    acl_mode="restricted",
)
```

异步客户端使用相同方法名：

```python
report = await client.acl_set(uri, entries, acl_mode="restricted")
```

**Go SDK**

```go
report, err := client.SetACL(ctx, "viking://resources/project-a", []openviking.ACLEntry{
    {Principal: "user:bob", Level: "read"},
    {Principal: "group:engineering", Level: "write"},
}, openviking.SetACLOptions{ACLMode: "restricted"})

// 只切换模式，不修改 direct ACL
report, err = client.SetACLMode(ctx, "viking://resources/project-a", "restricted")
```

**CLI**

```bash
ov acl set viking://resources/project-a \
  --acl-mode restricted \
  --entry user:bob=read \
  --entry group:engineering=write

# 只退出 restricted 模式
ov acl set viking://resources/project-a --acl-mode inherit
```

## 设置单个 principal 权限

```
POST /api/v1/acl/grant
```

```json
{
  "uri": "viking://resources/project-a",
  "principal": "user:bob",
  "level": "write"
}
```

该接口将 Bob 在当前节点上的直接 level 设置为 `write`。如果已有直接条目，则更新该条目；其他用户条目不变。

```bash
curl -X POST http://localhost:1933/api/v1/acl/grant \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/project-a",
    "principal": "user:bob",
    "level": "write"
  }'
```

```python
report = client.acl_grant(
    "viking://resources/project-a",
    principal="user:bob",
    level="write",
)
```

```bash
ov acl grant viking://resources/project-a --principal user:bob --level write
```

## 删除单个 principal 的直接授权

```
POST /api/v1/acl/revoke
```

```json
{
  "uri": "viking://resources/project-a",
  "principal": "user:bob"
}
```

`revoke` 只删除当前节点上 Bob 的直接条目。Bob 从祖先继承的权限仍然有效。

```python
report = client.acl_revoke("viking://resources/project-a", principal="user:bob")
```

```bash
ov acl revoke viking://resources/project-a --principal user:bob
```

## 清空当前节点的直接 ACL

```
DELETE /api/v1/acl?uri={uri}
```

该接口清空当前节点的直接 ACL 并退出 restricted；不会删除已保存的 inherited，也不删除后代节点的直接 ACL。清空后立即使用最新继承权限；父目录也不受 ACL 控制时，`acl_mode` 恢复为 `none`。

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

接口先校验 manage，再向已授权调用者确认 URI 是否存在，避免通过错误类型探测资源。

| 场景 | 错误 |
|------|------|
| URI 不在 `viking://resources/...` | `INVALID_ARGUMENT` |
| 调用者没有 manage | `PERMISSION_DENIED` |
| 已授权调用者访问不存在的 URI | `NOT_FOUND` |
| 修改 ACL 时 URI 尚无 context 记录 | `INVALID_ARGUMENT`，需先完成索引 |
| `principal` 格式非法，或使用 `group:*` | `INVALID_ARGUMENT` |
| level 不是 `read/write/manage` | `INVALID_ARGUMENT` |
| `acl_mode` 不是 `inherit/restricted`，或请求包含 inherited 等只读字段 | `INVALID_ARGUMENT` |

ACL 的 mode、direct 和 inherited 字段都保存在 context。更新会在同一子树批处理中修改目标字段并重算后代 inherited；写入失败时恢复原 context ACL 字段。

## 相关文档

- [资源访问控制（ACL）](../concepts/15-acl.md) - 权限、继承和检索语义
- [认证](../guides/04-authentication.md) - 请求身份与 account 角色
- [文件系统 API](./03-filesystem.md) - 受 ACL 控制的文件操作
- [检索 API](./06-retrieval.md) - `find/search` 接口
