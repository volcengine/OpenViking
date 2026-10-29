---
description: OpenViking 网关存储什么、谁能访问、服务商能看到什么，以及数据如何保留、删除和备份。
---

# OpenViking 网关安全与数据

本页写给审批网关部署的运维和安全评审人员，也写给想知道网关保存了自己哪些数据的用户。会话和记忆始终保存在 OpenViking 中，按 OpenViking 自己的规则管理；网关默认在本地 SQLite 中、也可以在配置的 Redis 后端中保存自身配置、让对话继续所需的状态，以及只含元数据的请求日志。下面每一节回答一个评审问题，都可以单独链接。

## 按用户隔离 {#isolation-by-user}

每把[网关密钥](../guides/15-gateway.md#key-terms)绑定一位 OpenViking 用户。网关用这位用户自己的 OpenViking 密钥检索记忆、保存对话和调用工具，所以能做的事不会超出用户本人的权限。各用户的对话状态互相隔离。设置 `tenancy: key` 后，即使账号和用户身份相同，不同 OpenViking 密钥也有独立分区；轮换密钥会进入新分区。

## 网关存储什么 {#what-the-gateway-stores}

默认数据保存在 `storage_path`（默认 `~/.openviking/gateway`）下的两个 SQLite 数据库里。两个存储后端都可以分别改用 [Redis](13-configuration.md#storage)：

| 文件 | 内容 |
| --- | --- |
| `management.sqlite3`（或 Redis management） | 上游、上下文配置、网关密钥、使用默认 `store` sink 时的请求日志，以及每个 Responses 响应由哪个上游创建 |
| `kernel.sqlite3`（或 Redis kernel） | 每段对话的状态：补充到消息中的记忆原文、OpenViking 工具的往返记录及其结果、摘要、等待保存的对话文本，以及开启**补全推理内容回传**时模型回复的推理内容 |

其中有些属于敏感数据：

- `management.sqlite3` 存有上游 API Key、请求头的值，以及网关密钥绑定的 OpenViking 密钥。网关密钥本身只存哈希和一段短前缀。
- `kernel.sqlite3` 逐字保存记忆原文，因为网关要靠它把之前几轮补充的内容[原样放回](01-how-it-works.md#replay)。

SQLite 文件只有运行网关的系统用户能读取；Redis 由网络和 ACL 配置控制访问。开启[加密](../guides/22-gateway-operations.md#encryption)后，存储的每个值也都会加密；加密关闭时，上面这些凭证以明文存储，由文件权限或 Redis 访问控制保护。所有副本必须从 Secret 挂载同一把加密根密钥；只读挂载缺少密钥条目时会拒绝启动。Redis 键名使用哈希，但索引可能包含原始账号、对象和会话标识，除值加密之外也应启用 TLS。

## 请求日志记什么 {#what-request-logs-record}

请求日志只记元数据，不记内容：

| 会记录 | 不会记录 |
| --- | --- |
| 请求类型、模型、状态、上游 | 消息文本 |
| 对话 ID 的哈希 | 召回的记忆 |
| token 数、记忆条数和耗时 | 工具参数和结果 |
| 保存状态和异常 | 任何密钥 |

网关不写 HTTP 访问日志，错误信息也不回显提交的值。默认的 `store` sink 使请求日志可在 Studio 中查看，见[请求日志](10-overview-and-requests.md#requests)。`stdout` sink 将相同元数据连同账号和用户标识输出为明文 JSON；存储加密不作用于 stdout。日志采集和访问由部署环境管理，见[观测](../guides/22-gateway-operations.md#monitoring)。

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

网关数据会自动过期。SQLite 每小时执行清理，Redis 使用键 TTL：

| 数据 | 保留时长 | 设置 |
| --- | --- | --- |
| 对话状态 | 最后一次使用后 30 天 | `session_ttl_seconds` |
| 缓存的 OpenViking 密钥及其用户记录 | 从创建时起经过配置的 TTL，不续期 | `session_ttl_seconds` |
| 存储的请求日志 | 30 天 | `log_retention_days` |
| Responses 响应与上游的对应关系 | 30 天 | `response_ttl_seconds` |
| OpenViking 中的会话和记忆 | 遵循 OpenViking 自己的规则 | — |

OpenViking 密钥记录过期，或被使用 `volatile-lru` 的 Redis 后端驱逐后，写回任务因读不到凭据而以 `gateway_key_revoked` 暂停。用户的下一个请求会重建记录，并在同一 scope 中恢复写回。如果用户不再请求，最后一轮待写回内容不会存入 OpenViking。这些状态属于缓存，允许丢失。与吊销一样，现有的短时进程内缓存可能延迟观察到过期。

Redis 每个账号最多保留 10,000 条请求日志。读取时跳过早于 `log_retention_days` 的条目；这些条目仍留在存储中，直到被更新的条目挤出上限，或者该账号连续 `log_retention_days` 没有新日志。Redis 不需要每小时清理。Redis Responses 路由记录按 TTL 过期，删除用户时不扫描它们；被吊销的密钥无法访问这些记录。

这些设置的最小值和配置位置见[网关字段](13-configuration.md#gateway-fields)。 stdout 日志的保留和删除由日志采集器控制，与网关清理独立。

## Redis 对话状态 {#redis-conversation-state}

配置 `storage.kernel: redis` 后，对话状态按缓存保存，TTL 使用 `session_ttl_seconds`。写入会续期，只读记录不会续期。

驱逐会产生以下影响：

| 丢失的状态 | 影响 |
| --- | --- |
| 回放、召回记忆或恢复的推理 | 无法重放之前的决策，下一次请求发生缓存未命中。压缩摘要丢失后可能需要重新摘要，或使请求超过模型上下文限制。 |
| 待投递的 capture 文档 | 尚未保存的轮次可能丢失。从客户端历史重建时可能重复保存已经投递的消息；持有已丢失文档的 worker 会停止使用旧 lease。 |
| observation 或 thinking 状态 | 召回账本归零，之前的推理可能无法识别。 |
| 另一请求等待期间的工具回执 | 返回超时或结果未知。网关不会重试该操作；重试写操作前应先检查实际状态。 |

Redis 异步主从切换可能丢失最近的写入或 lease，偶尔导致 capture 重复。应持久化 Redis，以保留待投递数据和删除操作；恢复删除之前的快照会使已删除的数据重新可见。见 [Redis 内存策略](../guides/22-gateway-operations.md#redis-memory-policy)。

开启加密后，存储的值会加密。键名使用哈希，但索引成员包含原始 session ID，记录类型、版本、调度时间和 lease owner 仍是明文，应相应保护 Redis 的访问权限和备份。

## 删除数据 {#deleting-data}

吊销网关密钥、或者它绑定的 OpenViking 密钥失效，都不会删除用户数据。要删除，有三种途径：

| 谁 | 怎么做 | 删除什么 |
| --- | --- | --- |
| 账号管理员或 root | 在 Studio 中用**删除该用户的网关数据…**（[操作步骤](09-gateway-keys.md#delete-a-users-gateway-data)），或者调用 `DELETE /gateway/admin/users/{user_id}/data` | 单个用户：吊销其网关密钥，删除其对话状态和日志，包括尚未保存的内容 |
| root | 调用 `DELETE /gateway/admin/account/data?account_id=<account>` | 整个账号的网关数据 |
| 嵌入部署，自动进行 | 在 OpenViking 中删除用户或账号 | 对应的网关数据 |

独立部署没有自动删除这一步，数据会一直保留，直到被显式删除或[过期](#retention)。设置 [`management_api: false`](13-configuration.md#gateway-fields) 后，删除 API 也不可用：在 OpenViking 中删除用户不会通知独立网关，对话状态只能等 `session_ttl_seconds` 到期后清理。请求日志和响应映射按各自的保留期限过期。按账号隔离的嵌入部署仍保留 Server 的自动删除钩子。按密钥隔离时，钩子无法根据 OpenViking 的账号和用户 ID 找到 `k_` 分区，其缓存数据按上述保留期限清理。

已存入 OpenViking 的记忆和会话不受这些操作影响。反过来，在 OpenViking 中删除一条记忆后，网关为进行中的对话保存的那份副本仍会被原样放回，直到对话过期；要立即清除，删除该用户的网关数据。

## 备份与恢复 {#backups}

以下内容要一起备份：

- Redis：启用 AOF，并把 Redis 持久化数据与加密根密钥一起备份。未启用持久化时，重启 Redis 会丢失管理配置和等待写回的对话数据。
- SQLite：整个 `storage_path` 目录：两个数据库及其 `-wal`、`-shm` 文件。
- 开启加密时，还有 OpenViking 的加密根密钥（不是 root API 密钥），按[加密指南](../guides/08-encryption.md)的说明保管。

要得到一致的副本，先停止网关，或者在运行中使用 SQLite 的在线备份：

```bash
sqlite3 kernel.sqlite3 ".backup kernel.backup.sqlite3"
sqlite3 management.sqlite3 ".backup management.backup.sqlite3"
```

丢失不同内容的后果：

| 丢失 | 后果 | 怎么办 |
| --- | --- | --- |
| `kernel.sqlite3`（或 Redis kernel） | 之前补充的记忆无法放回。每段进行中的对话会有一次服务商缓存未命中，Claude 对话会丢掉一次之前的思考内容（[**记忆记录缺失**](12-troubleshooting.md#issues-on-degraded-requests)），尚未保存的轮次也会丢失。 | 无需处理，进行中的对话在一次未命中之后就会恢复 |
| `management.sqlite3`（或 Redis management） | 上游、上下文配置、密钥和请求日志全部丢失。 | 恢复备份，或重新配置并签发新密钥；OpenViking 密钥记录会在后续请求中重建 |
| 加密根密钥（开启加密时） | 两个数据库中已加密的值都无法读取。 | 从空的 `storage_path`（SQLite），或换用 ACL 键模式仍能覆盖的新 `storage.key_prefix`（例如 `ovgw:2:`），或删除旧前缀下的所有键（Redis）后重新开始 |

## 下一步 {#next-steps}

- [加密](../guides/22-gateway-operations.md#encryption)：为已存的凭证和对话状态开启加密。
- [删除用户的网关数据](09-gateway-keys.md#delete-a-users-gateway-data)：在 Studio 中删除单个用户数据的步骤。
- [例行检查](../guides/22-gateway-operations.md#day-to-day-operations)：需要定期检查的事项，包括备份。
- [权限矩阵](../api/25-gateway.md#credentials-and-account-scope)：每项操作及其所需的凭证。
