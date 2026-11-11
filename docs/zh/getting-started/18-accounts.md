# Account

Account 是 OpenViking 的团队或租户空间。一个服务可以承载多个 Account，每个 Account 拥有自己的用户、资料、记忆和会话。

适合为不同团队、客户或独立业务建立数据边界。同一团队的成员放在同一个 Account 中协作；需要在团队内进一步限制资料访问时，使用 [ACL](21-access-control.md)。

## 理解边界

| 范围 | 内容 | 共享方式 |
| --- | --- | --- |
| Account | 团队的用户与数据 | 与其他 Account 隔离 |
| User | 个人记忆、资料和会话 | 用户自身访问；适合分享的知识可发布到共享资料区 |
| 共享资料区 | `viking://resources/...` | 当前 Account 内共享，可通过 ACL 细分授权 |

两个 Account 可以使用相同的 URI，例如 `viking://resources/team-check`。请求凭据决定实际访问哪个 Account，URI 中不需要拼接 Account ID。这是逻辑数据隔离；独立存储或模型配置见 [Account 配置参考](../api/08-admin.md)。

## 准备工作

先完成[认证](17-authentication.md)，已有 `acme`、管理员 `team-admin` 和 Alice、Bob。平台管理员持有 Root Key，并已在终端中设置 `OV_URL`、`ROOT_KEY`。本例再创建 `beta` 团队。

创建 Account 由平台管理员执行，团队管理员负责本 Account 的成员管理。以下验证只创建空目录，不调用生成模型。

## 创建第二个团队

平台管理员创建 `beta` 及其首个管理员：

```bash
curl -sS -X POST "$OV_URL/api/v1/admin/accounts" \
  -H "X-API-Key: $ROOT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"account_id":"beta","admin_user_id":"beta-admin"}'
```

保存返回的 `result.user_key` 并交给 beta-admin。由 beta-admin 通过 `ov config` 保存名为 `beta-admin` 的连接，使用相同服务地址和自己的 Admin Key。

## 在 acme 创建内容

由 acme 的 Alice 执行。在她的连接下创建一个新的共享目录：

```bash
ov config switch acme-alice
ov health -o json
ov mkdir viking://resources/team-check
ov ls viking://resources
```

确认身份属于 `acme`，并且目录列表中出现 `team-check`。如果目录已存在，选择新名称进行验证。

## 从 beta 核对隔离

由 beta-admin 使用自己的连接检查同一 URI：

```bash
ov config switch beta-admin
ov health -o json
ov stat viking://resources/team-check
```

确认当前 Account 是 `beta`。如果 beta 内还没有同名目录，应返回未找到；acme 中刚创建的目录不会出现在这里。

再由 beta-admin 创建同名目录：

```bash
ov mkdir viking://resources/team-check
ov ls viking://resources
```

创建应成功。现在两个团队各自拥有一个 `team-check`，后续内容变更也各自在所属 Account 内生效。

## 选择团队连接

API Key 模式下，每个用户凭据绑定一个 Account 和 User。需要访问另一团队时，由该团队管理员创建对应身份并提供凭据，再保存新的连接。

业务应用应按登录用户映射到相应的 OpenViking 连接。一个请求的 `--account` 参数无法把已有用户 Key 变成其他团队的身份；上游统一传递身份的部署方式见[身份认证](../guides/04-authentication.md)。

## 检查结果

| 检查项 | 预期结果 |
| --- | --- |
| Alice 的健康响应 | `account_id` 为 `acme` |
| beta-admin 的健康响应 | `account_id` 为 `beta` |
| beta 创建目录前 | 看不到 acme 的 `team-check` |
| beta 创建同名目录后 | 两个 Account 各自保有独立目录 |

如果结果不符，先用 `ov config show` 和 `ov health -o json` 核对当前服务、用户和 Account。默认无鉴权的本机模式不适合作为多用户身份隔离的验证环境。

## 下一步

继续 [User](19-users.md) 验证同一 Account 下的个人数据边界；跨 Account 交付资料时，查看[知识共享](22-sharing.md)。
