# 认证

API Key 用于确认请求来自谁，以及可以执行哪些操作。团队使用 OpenViking 时，可以由平台管理员配置 Root Key，再为团队管理员和普通用户创建各自的访问凭据。

本页完成一个最小流程：启用 API Key 认证，创建 `acme` 团队及管理员 `team-admin`，再创建普通用户 Alice 和 Bob。后续团队管理文档沿用这些身份。

## 准备工作

本例适用于可以修改配置并重启的自建服务。准备已安装的 `ov` CLI、curl，以及已有的服务端配置。模型和存储的配置沿用[快速开始](02-quickstart.md)；本文的 JSON 只展示需要合并的字段。

火山引擎云服务由平台管理认证和账号，按控制台提供的流程获取凭据。以下 Root Key 配置用于自建服务。

| 身份 | 凭据 | 用途 |
| --- | --- | --- |
| 平台管理员 ROOT | `server.root_api_key` | 创建和管理 Account 等平台操作 |
| 团队管理员 ADMIN | 创建管理员时返回的 `user_key` | 管理本 Account 的用户，以及该管理员自己的数据和团队共享资料 |
| 普通用户 USER | 创建用户时返回的 `user_key` | 访问个人数据和已授权的共享资料 |

API Key 模式下，服务端从凭据解析 Account、User 和角色。Root Key 没有绑定租户用户，日常资料、记忆和会话操作使用 User/Admin Key。

## 配置 Root Key

由平台管理员先备份当前使用的 `ov.conf`。生成一个随机密钥，例如在本机终端运行：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

将生成的值保存在受控位置，并填入服务端配置的 `server` 段。保留原有的模型、存储和其他服务参数：

```json
{
  "server": {
    "host": "127.0.0.1",
    "auth_mode": "api_key",
    "root_api_key": "<随机生成的 Root Key>"
  }
}
```

用原配置路径和启动参数重启服务。默认路径的本机服务可以使用：

```bash
openviking-server --config ~/.openviking/ov.conf
```

如需同时使用 VikingBot，在原启动命令中保留 `--with-bot`。网络部署的地址、HTTPS 和反向代理配置见[服务部署](../guides/03-deployment.md)。

认证启用后，客户端需要使用对应凭据。原有数据仍保留在原存储位置和 Account 下，创建新团队不会自动迁移此前的默认账号数据。

## 创建团队管理员

由平台管理员执行。本例使用 macOS / Linux 的 bash 或 zsh；将 `OV_URL` 换成实际服务地址。执行 `read` 后粘贴刚才设置的 Root Key，再按回车，输入时不会回显：

```bash
export OV_URL="http://127.0.0.1:1933"
read -r -s ROOT_KEY
export ROOT_KEY
```

创建 `acme` 团队，并同时创建第一个管理员 `team-admin`：

```bash
curl -sS -X POST "$OV_URL/api/v1/admin/accounts" \
  -H "X-API-Key: $ROOT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"account_id":"acme","admin_user_id":"team-admin"}'
```

检查响应的 `status` 为 `ok`，并保存 `result.user_key`。这是 **team-admin 的 Admin Key**，与 Root Key 不同。通过安全渠道将它交给该团队管理员。响应包含凭据，保存和转发时仅提供给对应用户。

如果 `acme` 已存在，使用已有账号继续；需要独立体验时，选择新名称并同步替换后续命令中的 Account ID。

## 创建普通用户

由团队管理员打开 CLI 连接向导：

```bash
ov config
```

添加名为 `acme-admin` 的自定义连接，填写上述服务地址和 team-admin 的 Admin Key，保存并激活。检查连接后，创建两个普通用户：

```bash
ov health
ov admin register-user acme alice --role user
ov admin register-user acme bob --role user
```

两次注册分别返回各自的 `user_key`。管理员将对应凭据交给 Alice 和 Bob；两人使用自己的凭据分别保存 `acme-alice`、`acme-bob` 命名连接。详细连接步骤见 [CLI 入门](05-cli-setup.md)。

需要增加另一位管理员时，可以注册 `--role admin` 的用户；角色管理接口见 [Admin API](../api/08-admin.md)。团队管理员无需持有 Root Key 即可管理本团队成员。

## 核对身份

在 Alice 的终端中选中她的连接：

```bash
ov config switch acme-alice
ov health -o json
ov ls viking://~/
```

健康响应应显示 `account_id: acme`、`user_id: alice`、`role: user`，并且能够列出 Alice 的家目录。用 team-admin 的连接检查时，身份应为 `team-admin`、角色为 `admin`。

每个人可以在自己的电脑上使用这些连接名称。如果由同一位操作者验证多个身份，先保存相应命名连接，再在每组操作前切换。修改 `--user` 或 `--account` 不会改变 API Key 绑定的身份。

## 常见问题

| 现象 | 下一步 |
| --- | --- |
| 启用认证后旧连接失败 | 在 `ov config` 中补充该用户的 User/Admin Key |
| Root Key 可以创建团队，但不能读取资料 | 使用团队内的 User/Admin Key 执行数据操作 |
| 普通用户创建成员被拒绝 | 切换到该 Account 的管理员连接 |
| 新团队看不到之前的数据 | 核对原数据所属 Account，按[知识共享](22-sharing.md)选择发布或迁移 |

平台管理员完成操作后，可用 `unset ROOT_KEY` 清除当前终端的变量。密钥轮换和其他认证模式见[身份认证](../guides/04-authentication.md)。

## 下一步

继续阅读 [Account](18-accounts.md)，创建第二个团队并验证数据隔离。
