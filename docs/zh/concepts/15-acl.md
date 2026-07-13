# 资源访问控制（ACL）

OpenViking ACL 用于在同一个 account 内，把共享资源目录或文件授权给用户或用户组。ACL 不改变 account 隔离：任何授权都只在当前 account 内生效。

ACL 采用协作文档式的继承模型。目录授权持续作用于所有后代，子目录和文件可以继续增加直接授权；祖先授权不会被子节点覆盖。

## 适用 URI

ACL 只作用于共享资源：

```text
viking://resources/...
```

- `viking://resources/...` 的 account `ADMIN` 是隐式管理者。
- `viking://user/{user_id}/resources/...` 是个人私有区，不接受 ACL。需要分享时，将资源移动到有权写入的共享目录，并继承该目录的 ACL。

隐式管理权不会写入 ACL 条目，也不能被 ACL 删除。它保证共享资源始终有人能够首次设置或恢复权限。

## Principal 与权限级别

ACL 条目使用带类型的 principal：

- `user:{user_id}`：当前 account 内的用户。
- `group:{group_id}`：当前 account 内由服务端生成 ID 的用户组。
- `user:*`：当前 account 内任意用户。

不支持 `group:*`。用户组是平铺结构；修改成员关系不会重写资源 ACL 或 context 记录，而是在下一次请求构造 `RequestContext.group_ids` 时生效。
请求创建的异步解析和语义任务会携带同一份 group 身份，保证一个已授权操作的前后台阶段使用一致权限。

| Level | 允许的操作 |
|-------|------------|
| `viewer` | 读取、列目录、`find/search/grep` |
| `editor` | `viewer` 的能力，以及写入、创建、修改 tags |
| `manager` | `editor` 的能力，以及删除、移动、管理 ACL |

高等级包含低等级能力。将某个用户设为 `manager`，等价于同时授予 read、write 和 manage。

## 继承规则

节点的有效 ACL 是所有祖先直接 ACL 与节点自身直接 ACL 的并集：

```text
effective(node) = UNION(direct_acl(each ancestor), direct_acl(node))
```

例如：

```text
viewer user:bob   on viking://resources/A
editor group:grp_engineering on viking://resources/A/B
viewer user:carol on viking://resources/A/B/C/report.md
```

`report.md` 的有效权限为：

- Bob：`viewer`
- `grp_engineering` 的成员：`editor`
- Carol：`viewer`

删除 `A/B` 上用户组的直接 ACL 不会删除 `A` 或 `report.md` 上的条目。子节点只会失去由该条目提供的权限。

## 默认行为与 `acl_enabled`

如果节点及其祖先都没有直接 ACL，OpenViking 继续使用原有 URI namespace 可见性和写入规则。

只要节点或任一祖先存在直接 ACL，该节点就进入 ACL 控制域：

```text
acl_enabled = true
```

`acl_enabled` 是系统派生字段，不能由 API 调用者设置。删除最后一个相关直接 ACL 后，它会自动恢复为 `false`。

## 文件操作

所有文件接口使用同一套权限判断：

| 操作 | 所需能力 |
|------|----------|
| read、stat、list、tree、find、search、grep、glob、relations | read |
| write、create、mkdir、set tags、reindex | write |
| delete、管理 ACL | manage |
| move 源节点 | manage |
| move 目标父目录 | write |

服务端会先 canonicalize URI，再在同一个鉴权入口中依次执行 account/owner/actor peer 等硬边界、有效 ACL 或 legacy fallback，以及写入和删除的 namespace 防护。普通写入、删除和 reindex 不维护各自的权限特判。

首次为共享节点设置 ACL 是唯一的 bootstrap 规则：节点尚未进入 ACL 控制域时，只能由共享区隐式管理者设置；启用后，后续 ACL 修改要求有效 `manage` 能力。

目录上的 ACL 授权会被所有后代继承。`list`、`tree` 和批量结果仍逐个检查有效 ACL，因为未设置 ACL 的目录可能按原有 URI 规则可见，而某个后代已经通过自己的 ACL 进入控制域。

共享区内部移动时，节点自己的直接 ACL 随节点移动，继承权限按新祖先重新计算。个人资源移入共享区时不携带 ACL，只继承目标目录权限；共享资源移回个人区时清空 ACL。

递归修改 tags、删除或移动目录会先校验完整目标子树。任一节点缺少所需能力，或子树扫描不完整，操作都会整体中止。

目录 `stat` 的 `count` 使用相同的路径和 ACL 标量过滤，表示当前用户可见的 context 数量。

## 检索过滤

ACL 只保存在 context collection。每条 context 记录维护当前节点和继承权限两组原生标量字段：

```text
acl_enabled
acl_direct_read_principal_ids
acl_direct_write_principal_ids
acl_direct_manage_principal_ids
acl_inherited_read_principal_ids
acl_inherited_write_principal_ids
acl_inherited_manage_principal_ids
```

`acl_direct_*` 是当前节点直接 ACL，`acl_inherited_*` 是所有祖先直接 ACL 的并集。有效权限是两组列表的并集，不维护独立 ACL collection。

请求的可用 principal 为 `user:{ctx.user_id}`、`user:*`，以及 `ctx.group_ids` 中每个 ID 对应的 `group:{group_id}`。`find/search` 只在 `viking://resources` scope 内按 `acl_direct_read_principal_ids` 和 `acl_inherited_read_principal_ids` 做原生 `list<string>` 过滤；个人资源始终按 URI owner 隔离。旧记录缺少 ACL 字段时按 `acl_enabled=false` 处理，无需全量回填。

检索 target URI 只是搜索范围，不要求调用者能够读取 target 节点本身。用户即使不能读取中间目录，也可以检索到深层单独授权给自己的文件。

共享区 context 写入会保留同 URI 已有 direct ACL，并为新节点从父节点生成 inherited ACL。重新向量化和普通覆盖写不会把受控记录恢复为默认可见，也不能通过普通 context 字段直接改 ACL。

## 示例

将目录授权给 Bob 只读：

```bash
ov acl grant viking://resources/project-a --principal user:bob --level viewer
```

Bob 可以读取和检索该目录的后代，但不能写入或删除。升级为 editor：

```bash
ov acl grant viking://resources/project-a --principal user:bob --level editor
```

删除 Bob 在当前节点上的直接授权：

```bash
ov acl revoke viking://resources/project-a --principal user:bob
```

如果 Bob 仍被祖先目录授权，该继承权限继续有效。

## 相关文档

- [ACL API](../api/12-acl.md) - HTTP、SDK 和 CLI 接口
- [多租户](./11-multi-tenant.md) - account、user 和角色边界
- [Viking URI](./04-viking-uri.md) - URI namespace
- [检索](./07-retrieval.md) - 分层检索流程
