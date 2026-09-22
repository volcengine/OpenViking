# ACL 属性

ACL 统一通过 `attrs` 查询和修改，只适用于 `viking://resources/...` 共享资源。权限模型见[资源访问控制](../concepts/15-acl.md)。旧 `/api/v1/acl`、`ov acl` 和 SDK ACL 方法已删除，不保留兼容入口。

## 接口

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/api/v1/fs/attrs?uri={uri}&key=acl` | 查询直接、继承和有效权限 |
| POST | `/api/v1/fs/attrs/set_acl` | 设置直接授权、继承模式 |
| POST | `/api/v1/fs/attrs/grant_acl` | 设置单个 principal 的直接权限 |
| POST | `/api/v1/fs/attrs/revoke_acl` | 删除单个 principal 的直接授权 |
| POST | `/api/v1/fs/attrs/reset_acl` | 清空直接授权并恢复 inherit |

以上操作均要求 `manage`。GET 不传 `key` 时返回调用者可见的属性：无 manage 时省略 ACL；明确请求 `key=acl` 时返回 403。账号 ADMIN 隐式拥有 manage。

查询响应中的 `result`：

```json
{
  "uri": "viking://resources/project-a",
  "context_type": "resource",
  "attrs": {
    "acl": {
      "uri": "viking://resources/project-a",
      "acl_mode": "restricted",
      "direct_entries": [{"principal": "user:bob", "level": "read"}],
      "inherited_entries": [{"principal": "user:*", "level": "manage"}],
      "effective_entries": [{"principal": "user:bob", "level": "read"}]
    }
  }
}
```

修改接口直接返回 ACL report。`set_acl` 请求：

```json
{
  "uri": "viking://resources/project-a",
  "acl_mode": "restricted",
  "entries": [{"principal": "user:bob", "level": "read"}]
}
```

- `entries`：完整替换直接授权；省略则保留；`[]` 清空。
- `acl_mode`：`inherit` 合并直接与继承授权，`restricted` 仅使用直接授权；省略则保留。
- 两个字段至少传一个。继承和有效权限是只读字段。
- principal 支持 `user:{id}`、`group:{id}`、`user:*`，level 支持 `read`、`write`、`manage`。重复 principal 保留最高 level。
- `grant_acl` 请求为 `{uri, principal, level}`；`revoke_acl` 为 `{uri, principal}`；`reset_acl` 为 `{uri}`。增删单个条目由服务端在锁内完成。

## 创建和写入时设置

`POST /api/v1/resources`、`POST /api/v1/fs/mkdir`、`POST /api/v1/content/write` 都接受：

```json
{
  "attrs": {
    "acl": {
      "acl_mode": "restricted",
      "entries": [{"principal": "user:bob", "level": "read"}]
    }
  }
}
```

不传 ACL：新节点直接授权为空并继承父目录，已有节点保留原权限。显式传 ACL：新节点要求调用者从父目录继承 manage，已有节点要求自身 manage；在内容修改前校验，write 权限不能用来提权。没有创建者额外权限。

导入只把直接 ACL 设置到最终导入根节点，子节点继承；自动创建的中间父目录不接收该授权。相同内容重新导入也会更新显式传入的 ACL。

账号 `acl.enabled` 默认 false，关闭时按原 namespace 规则访问，ACL 不参与鉴权；开启后共享根目录固定 `user:* = manage` 且不可修改。传入 ACL 不会自动开启账号开关，也不会自动切换 restricted。

ACL 仍保存在 context 索引内，允许短暂不一致，按现有异步任务和 wait 语义生效。独立修改 ACL 要求目标已有 context 记录；本次不增加无向量记录或空文件支持。

## CLI

```bash
ov attrs get viking://resources/project-a acl
ov attrs set-acl viking://resources/project-a --acl-mode restricted --entry user:bob=read
ov attrs grant-acl viking://resources/project-a --principal user:bob --level write
ov attrs revoke-acl viking://resources/project-a --principal user:bob
ov attrs reset-acl viking://resources/project-a

ov mkdir viking://resources/project-a --attrs '{"acl":{"acl_mode":"restricted","entries":[{"principal":"user:bob","level":"read"}]}}'
ov add-resource ./docs --to viking://resources/docs --attrs '{"acl":{"acl_mode":"restricted","entries":[]}}'
ov write viking://resources/project-a/a.md --content hello --mode create --attrs '{"acl":{"acl_mode":"inherit"}}'
```

## SDK

```python
attrs = {"acl": {"acl_mode": "restricted", "entries": [{"principal": "user:bob", "level": "read"}]}}
client.mkdir(uri, attrs=attrs)
client.add_resource("./docs", to=uri, options={"attrs": attrs})
client.write(file_uri, "hello", options={"attrs": attrs})
report = client.attrs(uri, key="acl")["attrs"]["acl"]
client.attrs_set_acl(uri, [], acl_mode="restricted")
client.attrs_grant_acl(uri, "user:bob", "read")
client.attrs_revoke_acl(uri, "user:bob")
client.attrs_reset_acl(uri)
```

异步 Python 使用相同方法名。TypeScript 对应 `attrs(uri, "acl")`、`attrsSetAcl`、`attrsGrantAcl`、`attrsRevokeAcl`、`attrsResetAcl`；创建接口的 options 支持 `attrs`，mkdir 使用第三个参数。Go 对应 `Attrs(ctx, uri, "acl")`、`AttrsSetACL`、`AttrsGrantACL`、`AttrsRevokeACL`、`AttrsResetACL`，通过 `ResourceAttrs` / `ACLSpec` 设置属性。
