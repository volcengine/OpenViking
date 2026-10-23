---
description: OpenViking 网关存储什么、谁能访问、服务商能看到什么，以及数据如何保留、删除和备份。
---

# OpenViking 网关安全与数据

本页写给审批网关部署的运维和安全评审人员，也写给想知道网关保存了自己哪些数据的用户。会话和记忆始终保存在 OpenViking 中，按 OpenViking 自己的规则管理；网关只在 `storage_path` 下的两个 SQLite 文件里保存自身配置、让对话继续所需的状态，以及只含元数据的请求日志。下面每一节回答一个评审问题，都可以单独链接。

## 按用户隔离 {#isolation-by-user}

每把[网关密钥](../guides/15-gateway.md#key-terms)绑定一位 OpenViking 用户。网关用这位用户自己的 OpenViking 密钥检索记忆、保存对话和调用工具，所以能做的事不会超出用户本人的权限。各用户的对话状态互相隔离。

## 网关存储什么 {#what-the-gateway-stores}

网关保存的全部数据都在 `storage_path`（默认 `~/.openviking/gateway`）下的两个 SQLite 数据库里：

| 文件 | 内容 |
| --- | --- |
| `management.sqlite3` | 上游、上下文配置、网关密钥、请求日志，以及每个 Responses 响应由哪个上游创建 |
| `kernel.sqlite3` | 每段对话的状态：补充到消息中的记忆原文、OpenViking 工具的往返记录及其结果、摘要、等待保存的对话文本，以及开启**补全推理内容回传**时模型回复的推理内容 |

其中有些属于敏感数据：

- `management.sqlite3` 存有上游 API Key、请求头的值，以及网关密钥绑定的 OpenViking 密钥。网关密钥本身只存哈希和一段短前缀。
- `kernel.sqlite3` 逐字保存记忆原文，因为网关要靠它把之前几轮补充的内容[原样放回](01-how-it-works.md#replay)。

这两个文件只有运行网关的系统用户能读取。开启[加密](../guides/22-gateway-operations.md#encryption)后，存储的每个值也都会加密；加密关闭时，上面这些凭证以明文存储，只靠文件权限保护。

## 请求日志记什么 {#what-request-logs-record}

请求日志只记元数据，不记内容：

| 会记录 | 不会记录 |
| --- | --- |
| 请求类型、模型、状态、上游、网关请求 ID 和上游响应 ID | 消息文本 |
| 对话 ID 的哈希 | 召回的记忆 |
| token 数、记忆条数和耗时 | 工具参数和结果 |
| 保存状态和异常 | 任何密钥 |

网关不写 HTTP 访问日志，错误信息也不回显提交的值。在 Studio 中查看日志，见[请求日志](10-overview-and-requests.md#requests)。

## 服务商能看到什么 {#what-providers-see}

每个[上游](../guides/15-gateway.md#key-terms)都收到完整的模型输入，包括网关补充的记忆和 OpenViking 工具的结果。只添加你放心交出这些数据的服务商。

也有一些内容不会离开网关：`X-OpenViking-*` 请求头、客户端凭证和 Cookie 从不转发给上游。上游设为由每个客户端提供自己的服务商密钥时，这把密钥只作为该上游的凭证发给服务商。

## 访问控制 {#access-control}

通过网关获得访问权的途径有四种，每种都有需要了解的边界：

| 凭证 | 允许做什么 | 注意 |
| --- | --- | --- |
| [网关密钥](09-gateway-keys.md#gateway-keys)，放在 `Authorization: Bearer` 或 `x-api-key` 中 | 以绑定用户的身份发起模型请求：召回记忆、保存对话、调用 OpenViking 工具 | 随时可以在**密钥**页吊销 |
| [网关管理密钥](../guides/22-gateway-operations.md#gateway-management-key) | 只能修改上游和上下文配置 | 不授予读取记忆、日志或签发密钥的权限 |
| 账号管理员的签发权 | 给本账号的任何用户签发网关密钥 | 实际上等于能读取本账号所有用户的记忆 |
| 上传、下载链接里的一次性令牌 | 调用 `/gateway/uploads` 和 `/gateway/downloads`，这两个接口不需要网关密钥 | 令牌放在查询字符串里 |

其中两项需要特别留意：

- **签发密钥。** 拿到网关密钥就能以该用户的身份召回记忆、调用工具，所以签发权只交给可信的管理员。OpenViking 只存密钥哈希时，需要用户本人提供自己的 OpenViking 密钥。
- **上传和下载链接。** 每个链接由 OpenViking 签发的一次性令牌授权，网关只把它转发到 OpenViking 的上传或下载接口。不要让它们的查询字符串出现在代理日志里。

包括管理 API 在内的完整规则见[权限矩阵](../api/25-gateway.md#credentials-and-account-scope)；各角色在 Studio 中能做什么，见[谁能修改什么](06-manage-in-studio.md#who-can-change-what)。

## 保留期限 {#retention}

网关数据会自动过期，清理任务每小时运行一次：

| 数据 | 保留时长 | 设置 |
| --- | --- | --- |
| 对话状态 | 最后一次使用后 30 天 | `session_ttl_days` |
| 请求日志 | 30 天 | `log_retention_days` |
| Responses 响应与上游的对应关系 | 30 天 | `response_ttl_seconds` |
| OpenViking 中的会话和记忆 | 遵循 OpenViking 自己的规则 | — |

这些设置的最小值和配置位置见[网关字段](13-configuration.md#gateway-fields)。

## 删除数据 {#deleting-data}

吊销网关密钥、或者它绑定的 OpenViking 密钥失效，都不会删除用户数据。要删除，有三种途径：

| 谁 | 怎么做 | 删除什么 |
| --- | --- | --- |
| 账号管理员或 root | 在 Studio 中用**删除该用户的网关数据…**（[操作步骤](09-gateway-keys.md#delete-a-users-gateway-data)），或者调用 `DELETE /gateway/admin/users/{user_id}/data` | 单个用户：吊销其网关密钥，删除其对话状态和日志，包括尚未保存的内容 |
| root | 调用 `DELETE /gateway/admin/account/data?account_id=<account>` | 整个账号的网关数据 |
| 嵌入部署，自动进行 | 在 OpenViking 中删除用户或账号 | 对应的网关数据 |

独立部署没有自动删除这一步，数据会一直保留，直到被显式删除或[过期](#retention)。

已存入 OpenViking 的记忆和会话不受这些操作影响。反过来，在 OpenViking 中删除一条记忆后，网关为进行中的对话保存的那份副本仍会被原样放回，直到对话过期；要立即清除，删除该用户的网关数据。

## 备份与恢复 {#backups}

以下内容要一起备份：

- 整个 `storage_path` 目录：两个数据库及其 `-wal`、`-shm` 文件。
- 开启加密时，还有 OpenViking 的加密根密钥（不是 root API 密钥），按[加密指南](../guides/08-encryption.md)的说明保管。

要得到一致的副本，先停止网关，或者在运行中使用 SQLite 的在线备份：

```bash
sqlite3 kernel.sqlite3 ".backup kernel.backup.sqlite3"
sqlite3 management.sqlite3 ".backup management.backup.sqlite3"
```

丢失不同内容的后果：

| 丢失 | 后果 | 怎么办 |
| --- | --- | --- |
| `kernel.sqlite3` | 之前补充的记忆无法放回。每段进行中的对话会有一次服务商缓存未命中，Claude 对话会丢掉一次之前的思考内容（[**记忆记录缺失**](12-troubleshooting.md#issues-on-degraded-requests)），尚未保存的轮次也会丢失。 | 无需处理，进行中的对话在一次未命中之后就会恢复 |
| `management.sqlite3` | 上游、上下文配置、密钥和请求日志全部丢失。 | 重新配置，并签发新密钥 |
| 加密根密钥（开启加密时） | 两个数据库中已加密的值都无法读取。 | 只能从空的 `storage_path` 重新开始 |

## 下一步 {#next-steps}

- [加密](../guides/22-gateway-operations.md#encryption)：为已存的凭证和对话状态开启加密。
- [删除用户的网关数据](09-gateway-keys.md#delete-a-users-gateway-data)：在 Studio 中删除单个用户数据的步骤。
- [例行检查](../guides/22-gateway-operations.md#day-to-day-operations)：需要定期检查的事项，包括备份。
- [权限矩阵](../api/25-gateway.md#credentials-and-account-scope)：每项操作及其所需的凭证。
