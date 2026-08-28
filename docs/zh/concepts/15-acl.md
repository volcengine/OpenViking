# 资源访问控制（ACL）

OpenViking ACL 用于在同一个 account 内，把共享资源目录或文件授权给用户或用户组。ACL 不改变 account 隔离：任何授权都只在当前 account 内生效。

ACL 采用协作文档式的继承模型。目录授权持续作用于所有后代，子目录和文件可以继续增加直接授权；祖先授权不会被子节点覆盖。

## 适用 URI

ACL 只作用于共享资源：

```text
viking://resources/...
```

- `viking://resources/...` 的 account `ADMIN` 隐式拥有 `manage`。
- `viking://resources` 是固定共享 scope，本身不能设置直接 ACL；ACL 从它下面的文件和目录开始。
- `viking://user/{user_id}/resources/...` 是个人私有区，不接受 ACL。需要分享时，将资源移动到有权写入的共享目录，并继承该目录的 ACL。

隐式管理权不会写入 ACL 条目，也不能被 ACL 删除。它保证共享资源始终有人能够首次设置或恢复权限。

## Principal 与权限级别

ACL 条目使用带类型的 principal：

- `user:{user_id}`：当前 account 内的用户。
- `group:{group_id}`：由调用者指定、在当前 account 内唯一的用户组 ID。
- `user:*`：当前 account 内任意用户。

不支持 `group:*`。用户组是平铺结构；修改成员关系不会重写资源 ACL 或 context 记录，而是在下一次请求构造 `RequestContext.group_ids` 时生效。
请求创建的异步解析和语义任务会携带同一份 group 身份。`add-resource` 写入目标通过鉴权后，自动语义维护会保留原调用者身份，并显式使用内部 ACL bypass，不会把调用者角色改成 `ADMIN`。

| Level | 允许的操作 |
|-------|------------|
| `read` | 读取、列目录、`find/search/grep` |
| `write` | `read` 的能力，以及写入、创建、删除或移动文件、修改 tags |
| `manage` | `write` 的能力，以及删除或移动目录、管理 ACL |

高等级包含低等级能力。授予 `manage`，等价于同时授予 `read` 和 `write`。

## 继承规则

节点的有效 ACL 是所有祖先直接 ACL 与节点自身直接 ACL 的并集：

```text
effective(node) = UNION(direct_acl(each ancestor), direct_acl(node))
```

例如：

```text
read user:bob   on viking://resources/A
write group:engineering on viking://resources/A/B
read user:carol on viking://resources/A/B/C/report.md
```

`report.md` 的有效权限为：

- Bob：`read`
- `engineering` 的成员：`write`
- Carol：`read`

删除 `A/B` 上用户组的直接 ACL 不会删除 `A` 或 `report.md` 上的条目。子节点只会失去由该条目提供的权限。

## 默认行为与 `acl_enabled`

账号级 `resource_acl.auto_protect_new_content` 默认关闭。关闭时，如果父目录没有
ACL，新建文件或目录不会自动开启 ACL，继续使用原有 URI namespace 可见性和写入
规则；父目录已有 ACL 时，创建者获得直接 `manage`，并继承父目录权限。

开启后，新建共享文件或目录即使位于无 ACL 的父目录下，创建者也会在首条 context
记录上获得直接 `manage`；父目录权限仍作为继承 ACL 合并。已有内容不会迁移或
改权，重新关闭也只影响后续创建。`add-resource` 只把本次生成的根目录（`no_split`
时为根文件）作为创建节点：根节点获得创建者直接 `manage`，内部节点只继承，
不重复写直接授权。重新向量化或覆盖已有 context 不会改变直接 ACL。

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
| write、create、mkdir、set tags | write |
| 删除或移动文件 | write |
| 删除或移动目录 | 目录及完整子树的 manage |
| 管理 ACL | manage |
| move 目标父目录 | write |

服务端会先 canonicalize URI，再在同一个鉴权入口中依次执行 account/owner/actor peer 等硬边界、有效 ACL 或 legacy fallback，以及写入和删除的 namespace 防护。

父目录已有 ACL，或账号开启 `auto_protect_new_content` 时，新建共享节点由创建者的
直接 `manage` 完成权限 bootstrap。其他情况下，首次设置 ACL 只能由共享区隐式
管理者完成；启用后，后续 ACL 修改要求有效 `manage` 能力。

目录上的 ACL 授权会被所有后代继承。`list`、`tree` 和批量结果仍逐个检查有效 ACL，因为未设置 ACL 的目录可能按原有 URI 规则可见，而某个后代已经通过自己的 ACL 进入控制域。

共享区内部移动时，节点自己的直接 ACL 随节点移动，继承权限按新祖先重新计算。个人资源移入共享区时不携带 ACL，只继承目标目录权限；共享资源移回个人区时清空 ACL。

递归修改 tags、删除或移动目录会先校验完整目标子树。任一节点缺少所需能力，或子树扫描不完整，操作都会整体中止。

目录 `stat` 的 `count` 使用相同的路径和 ACL 标量过滤，表示当前用户可见的 context 数量。

## 检索过滤

ACL 只保存在 context collection。每条 context 记录维护当前节点和继承权限两组原生标量字段：

```text
acl_enabled
acl_direct_grants
acl_inherited_grants
```

`acl_direct_grants` 是当前节点直接 ACL，`acl_inherited_grants` 是所有祖先直接 ACL 的并集。每个 principal 只保存最高 level，编码为 `{mask}:{principal}`：`1` 表示 `read`、`3` 表示 `write`、`7` 表示 `manage`。例如 `3:group:dev` 表示 `group:dev` 拥有 `write`，同时也具备 `read`。有效权限是两组列表的并集，不维护独立 ACL collection。

请求的可用 principal 为 `user:{ctx.user_id}`、`user:*`，以及 `ctx.group_ids` 中每个 ID 对应的 `group:{group_id}`。`find/search` 读取时为每个 principal 匹配 `1`、`3`、`7` 三种 token，并在 `viking://resources` scope 内对 direct 和 inherited 两个原生 `list<string>` 字段做过滤；个人资源始终按 URI owner 隔离。旧记录缺少 ACL 字段时按 `acl_enabled=false` 处理，无需全量回填。

检索 target URI 只是搜索范围，不要求调用者能够读取 target 节点本身。用户即使不能读取中间目录，也可以检索到深层单独授权给自己的文件。

共享区 context 写入会保留同 URI 已有 direct ACL。父目录已开启 ACL，或账号开启
`auto_protect_new_content` 时，新创建节点为创建者生成直接 `manage`，并从父节点
生成 inherited ACL；否则保持 `acl_enabled=false`。`add-resource` 的内部节点只继承
导入根节点。重新向量化和普通覆盖写不会把受控记录恢复为默认可见，也不能通过
普通 context 字段直接改 ACL。

## 示例

将目录授权给 Bob 只读：

```bash
ov acl grant viking://resources/project-a --principal user:bob --level read
```

Bob 可以读取和检索该目录的后代，但不能写入或删除。升级为 `write`：

```bash
ov acl grant viking://resources/project-a --principal user:bob --level write
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
