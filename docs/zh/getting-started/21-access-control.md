# 访问控制

ACL 用于决定同一个 Account 内，哪些用户可以读取、修改或管理共享资料。适合项目协作、指定成员可读和分组维护等场景。

本页创建一个仅 Alice 可管理、Bob 可读的共享目录，再验证读取、拒绝写入和撤销授权。Account 管理员保留共享资料的隐式管理权。

## 准备工作

先完成[认证](17-authentication.md)，准备 `acme-admin`、`acme-alice` 和 `acme-bob` 三个连接。Alice 与 Bob 均为普通用户，便于验证授权效果。

ACL 适用于 `viking://resources/...`。个人记忆、个人资料和 Peer 空间保持 User 边界；分享其中的内容可参考[知识共享](22-sharing.md)。

## 开启 ACL

由 acme 的团队管理员执行。在终端设置服务地址，并通过隐藏输入提供 team-admin 的 Admin Key：

```bash
export OV_URL="http://127.0.0.1:1933"
read -r -s ADMIN_KEY
export ADMIN_KEY
```

开启当前 Account 的 ACL 检查：

```bash
curl -sS -X PATCH "$OV_URL/api/v1/admin/accounts/acme/configuration" \
  -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"settings":{"acl":{"enabled":true}}}'
```

检查返回的 `result.settings.acl.enabled` 为 `true`。该开关默认关闭，关闭时共享资料不执行 ACL 过滤；单独写入授权条目不会自动打开开关。

## 创建受限目录

共享根目录默认授予当前 Account 全员 `manage`，下级目录继承父目录权限。要限定到指定成员，使用 `restricted` 截断继承，并在创建时设置直接授权。

由团队管理员创建一个新的示例目录：

```bash
ov config switch acme-admin
ov mkdir viking://resources/team-knowledge \
  --acl '{"acl_mode":"restricted","entries":[{"principal":"user:alice","level":"manage"},{"principal":"user:bob","level":"read"}]}'
```

目录已存在时先用 `ov acl get` 检查其权限；本次体验可换一个新名称，避免覆盖原有授权。

| 权限 | 可以执行的操作 |
| --- | --- |
| `read` | 读取、列目录和检索 |
| `write` | 包含 read，并允许写入、创建、删除或移动文件 |
| `manage` | 包含 write，并允许管理 ACL、删除或移动目录 |

## 写入一份资料

由 Alice 发布一份说明。写入会触发语义与向量处理，需要可用模型，并产生模型调用：

```bash
ov config switch acme-alice
ov write viking://resources/team-knowledge/access-check.md \
  --mode create --content "团队约定：每周五汇总项目反馈。" --wait
```

新文件继承目录权限。查看目录授权与文件正文：

```bash
ov acl get viking://resources/team-knowledge
ov read viking://resources/team-knowledge/access-check.md
```

如果等待超时，使用返回的任务 ID 查询处理状态，完成后继续。

## 用 Bob 验证

Bob 使用自己的连接读取并检索：

```bash
ov config switch acme-bob
ov read viking://resources/team-knowledge/access-check.md
ov find "什么时候汇总项目反馈？" --uri viking://resources/team-knowledge
```

预期能读到正文，检索结果包含这份资料。再尝试创建一个文件：

```bash
ov write viking://resources/team-knowledge/bob-check.md \
  --mode create --content "Bob 的写入检查。"
```

由于 Bob 只有 `read`，写入应被拒绝。如果成功，检查当前身份是否为管理员、账号 ACL 是否已开启，以及目录的有效权限中是否还有其他授权。

## 调整授权

Alice 拥有 `manage`，可以授予 Bob 编辑权限：

```bash
ov config switch acme-alice
ov acl grant viking://resources/team-knowledge --principal user:bob --level write
```

Bob 重新执行刚才的写入命令应成功。后续只需阅读时，将权限设置回 `read`；合作结束时撤销直接授权：

```bash
ov acl grant viking://resources/team-knowledge --principal user:bob --level read
ov acl revoke viking://resources/team-knowledge --principal user:bob
```

以上两条由 Alice 执行。撤销后切换到 Bob，再读取 `access-check.md` 应被拒绝。权限索引更新可能有短暂延迟，可每隔 2 秒检查，先观察最多 30 秒；持续可读时检查有效权限和其他授权来源。

`revoke` 删除当前节点的直接条目。若用户还通过父目录或用户组获得权限，相应访问仍然有效。撤销也不会删除用户已下载的副本。需要继续下一篇共享体验时，由 Alice 恢复 Bob 的 `read` 权限。

## 授权给用户组

成员较多时，可以由管理员创建 Account 内的用户组，维护成员后，把目录授权给 `group:<group_id>`。组内成员变更从后续请求生效，适合为多个项目小组管理访问。创建组与成员的完整步骤见 [Admin API](../api/08-admin.md#用户组)。

## 下一步

继续[知识共享](22-sharing.md)，将个人积累的项目知识发布到这个受控目录。权限继承和移动目录的完整规则见 [ACL 概念](../concepts/15-acl.md)。
