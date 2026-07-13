# 资源访问控制（ACL）

OpenViking ACL 用于在同一个 account 内，把资源目录或文件授权给指定用户。ACL 不改变 account 隔离：任何授权都只在当前 account 内生效。

ACL 采用协作文档式的继承模型。目录授权持续作用于所有后代，子目录和文件可以继续增加直接授权；祖先授权不会被子节点覆盖。

## 适用 URI

ACL 作用于两类资源：

```text
viking://resources/...
viking://user/{user_id}/resources/...
```

- `viking://resources/...` 的 account `ADMIN` 是隐式管理者。
- `viking://user/{user_id}/resources/...` 中的 `{user_id}` 是隐式管理者。

隐式管理权不会写入 ACL 条目，也不能被 ACL 删除。它保证公共资源和用户资源始终有人能够首次设置或恢复权限。

## Principal 与权限级别

ACL 条目直接使用当前 account 内的 `user_id`。保留值 `*` 表示当前 account 内任意用户。

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
viewer bob   on viking://resources/A
editor alice on viking://resources/A/B
viewer carol on viking://resources/A/B/C/report.md
```

`report.md` 的有效权限为：

- Bob：`viewer`
- Alice：`editor`
- Carol：`viewer`

删除 `A/B` 上 Alice 的直接 ACL 不会删除 `A` 或 `report.md` 上的条目。子节点只会失去由该条目提供的权限。

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
| write、create、mkdir、set tags | write |
| delete、管理 ACL | manage |
| move 源节点 | manage |
| move 目标父目录 | write |

目录上的 ACL 授权会被所有后代继承。`list`、`tree` 和批量结果仍逐个检查有效 ACL，因为未设置 ACL 的目录可能按原有 URI 规则可见，而某个后代已经通过自己的 ACL 进入控制域。

移动文件或目录时，节点自己的直接 ACL 随节点移动；旧祖先的继承权限不随对象移动，新祖先的 ACL 会重新参与计算。ACL 资源只能在受支持的资源范围之间移动。

递归修改 tags、删除或移动目录会先校验完整目标子树。任一节点缺少所需能力，或子树扫描不完整，操作都会整体中止。

目录 `stat` 的 `count` 使用相同的路径和 ACL 标量过滤，表示当前用户可见的 context 数量。

## 检索过滤

ACL 只保存在 context collection。每条 context 记录维护当前节点和继承权限两组原生标量字段：

```text
acl_enabled
acl_direct_read_user_ids
acl_direct_write_user_ids
acl_direct_manage_user_ids
acl_inherited_read_user_ids
acl_inherited_write_user_ids
acl_inherited_manage_user_ids
```

`acl_direct_*` 是当前节点直接 ACL，`acl_inherited_*` 是所有祖先直接 ACL 的并集。有效权限是两组列表的并集，不维护独立 ACL collection。

`find/search` 直接在向量库中按 `account_id`、URI scope、`acl_direct_read_user_ids` 和 `acl_inherited_read_user_ids` 过滤。旧记录缺少 ACL 字段时按 `acl_enabled=false` 处理，无需全量回填。

检索 target URI 只是搜索范围，不要求调用者能够读取 target 节点本身。用户即使不能读取中间目录，也可以检索到深层单独授权给自己的文件。

所有 context 写入入口都会保留同 URI 已有 direct ACL，并为新节点从父节点生成 inherited ACL。重新向量化和普通覆盖写不会把受控记录恢复为默认可见，也不能通过普通 context 字段直接改 ACL。

## 示例

将目录授权给 Bob 只读：

```bash
ov acl grant viking://resources/project-a --user-id bob --level viewer
```

Bob 可以读取和检索该目录的后代，但不能写入或删除。升级为 editor：

```bash
ov acl grant viking://resources/project-a --user-id bob --level editor
```

删除 Bob 在当前节点上的直接授权：

```bash
ov acl revoke viking://resources/project-a --user-id bob
```

如果 Bob 仍被祖先目录授权，该继承权限继续有效。

## 相关文档

- [ACL API](../api/12-acl.md) - HTTP、SDK 和 CLI 接口
- [多租户](./11-multi-tenant.md) - account、user 和角色边界
- [Viking URI](./04-viking-uri.md) - URI namespace
- [检索](./07-retrieval.md) - 分层检索流程
