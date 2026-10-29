---
description: 根据现象、Studio 标签或错误码，找到 OpenViking 网关问题的原因和解决办法。
---

# OpenViking 网关排障

客户端报错、记忆没有补充或对话没有保存、Studio 标出异常时，都可以从这一页查起。先看三个地方：**概览**标签页上的 **OpenViking** 卡片、筛选为**异常**的**请求日志**标签页，以及 Server 日志（Docker Compose 部署用 `docker compose logs openviking`，独立网关看它自己的输出）。然后在下面的表格里找到对应现象；每一节都能单独阅读，并链接到相关设置。

## 常见现象 {#common-symptoms}

| 现象 | 可能原因 | 处理 |
| --- | --- | --- |
| 开启网关后 Server 随即停止 | 嵌入网关没有通过启动检查。 | [网关无法启动](#the-gateway-does-not-start) |
| Studio 显示一张卡片，看不到网关标签页 | 网关没有开启、OpenViking API 密钥缺失或无效，或者网关连不上 OpenViking。 | [Studio 显示设置卡片](#studio-shows-a-setup-card) |
| “上游”和“上下文配置”标签页只读 | 部署者设置了网关管理密钥。 | [解锁编辑](06-manage-in-studio.md#unlock-editing)；没人记得密钥时，见[忘记网关管理密钥](../guides/22-gateway-operations.md#lost-the-management-key) |
| **无法签发密钥** | 新网关密钥要绑定的 OpenViking API 密钥没有通过校验。 | [签发密钥失败](#issuing-a-key-fails) |
| 客户端报错，请求日志里却没有这条请求 | 请求在到达服务商之前就被网关拒绝了。 | [客户端收到网关返回的错误](#clients-get-an-error-from-the-gateway) |
| 请求日志中某条请求的状态码为 400 及以上 | 服务商拒绝了请求或调用失败。 | [上游返回错误](#the-upstream-returned-an-error) |
| 某段对话没有召回，也没有保存 | 检测到 OpenViking 插件，网关把召回、保存和工具都让给了插件。 | 正常行为，避免同一份内容被补充、保存两次，见[两者同时使用时](../guides/15-gateway.md#when-both-are-in-use)。 |
| 请求不带记忆 | OpenViking 无法访问或超时、预算用完、没有相关内容等。 | [没有补充记忆](#no-memory-is-added) |
| 保存显示**重试中**，之后变成**已暂停** | OpenViking 拒绝保存或无法访问；或者这段对话的网关密钥已被吊销（`gateway_key_revoked`），例如用户的 OpenViking 密钥被重新生成过。 | OpenViking 恢复后网关会自动恢复保存。原因为 `gateway_key_revoked` 时：签发的网关密钥需要重新签发，之后的下一个请求会恢复保存；直接使用的 OpenViking 密钥若记录过期、被驱逐或保存时收到 401，用户的下一个有效请求会恢复保存（account 租户模式；`tenancy: key` 下换用新密钥会开始新的对话）。见[OpenViking 中看不到对话](#conversations-do-not-appear-in-openviking)。 |
| 上下文配置开启了工具，对话里却没有 | 还没有能用工具的请求，第一个能用的请求加载工具清单失败，客户端没有回传完整历史，或者客户端自己接了 OpenViking 工具。 | 在请求日志里查看原因，见[什么时候不提供工具](04-openviking-tools.md#when-tools-are-not-offered)。客户端接了 OpenViking MCP 服务器时出现**客户端已接入 OpenViking 工具**（`tool_name_collision`）是正常的；其他原因修正后开始新对话。 |
| **首次调用缓存命中率**下降，或者概览提示缓存连续未命中 | 记忆无法原样放回、对话换了上游，或者客户端、中转服务改动了请求开头的内容。 | [首次调用缓存命中率下降](#the-first-call-cache-hit-rate-dropped) |
| 长对话被服务商以过长为由拒绝 | 网关不知道模型的实际窗口，压缩得太晚。 | [长对话超出上下文长度](#long-conversations-hit-the-context-limit) |
| 文件导入或下载链接失败 | 没有设置 `public_url` 或设置有误，或者代理没有转发文件相关路径。 | [文件导入或下载失败](#file-imports-or-downloads-fail) |
| 请求被标上了异常名称 | 网关没能完整处理这条请求。 | [降级请求的异常](#issues-on-degraded-requests) |

## 网关无法启动 {#the-gateway-does-not-start}

嵌入网关无法启动时，整个 OpenViking Server 都会停止。请在 Server 日志中找到启动错误（Compose 中用 `docker compose logs openviking`），再对照下表处理。

| 问题（日志原文） | 原因和处理 |
| --- | --- |
| v0.5 数据缺少旧密钥（`holds data encrypted by OpenViking Gateway 0.5`） | 存储目录里有 v0.5 网关加密的数据，但既没有 `OPENVIKING_GATEWAY_ENCRYPTION_KEY`，`storage_path` 下也没有 `encryption.key`。提供原来的密钥，见[迁移](../guides/22-gateway-operations.md#migration)第 5 步。 |
| 已移除的参数或字段（`unrecognized argument --…`、`Extra inputs are not permitted`） | 独立网关启动时用了已移除的 `openviking-gateway` 参数，或者 `gateway.conf` 里还留着它不接受的字段，例如 `enabled`。把这些设置移到 `gateway.conf` 或环境变量中，见[迁移](../guides/22-gateway-operations.md#migration)第 2 步。`ov.conf` 的 `gateway` 段里残留的旧字段只会打印 `Ignoring removed gateway settings`，不会阻止启动。 |
| 管理密钥太短（`management_key must resolve to at least 32 characters`） | [网关管理密钥](../guides/22-gateway-operations.md#gateway-management-key)至少要 32 个字符；用 `$VAR` 引用的环境变量没有设置时，也会报这个错。 |
| 加密设置（`Gateway data is encrypted; configure the encryption settings it was written with`，或加密配置错误） | Server 的 `encryption` 段（嵌入）或 `gateway.conf` 的 `encryption` 块（独立）缺失、不合法，或者加载不了加密根密钥。见[加密指南](../guides/08-encryption.md)。 |
| 存储格式不兼容（`Unsupported gateway schema`） | 存储目录由不兼容的网关版本写入（更旧的版本，或降级前更新的版本）。换一个空的 `storage_path`。 |
| 独立运行缺少 OpenViking 地址（`OpenViking Gateway needs an OpenViking server URL`） | 运行 `openviking-gateway init`，或者设置 `OPENVIKING_GATEWAY_OPENVIKING_URL`。 |
| 独立运行找不到配置文件（`Gateway config file not found`） | `--config` 或 `OPENVIKING_GATEWAY_CONFIG_FILE` 指定的文件不存在。改正路径，或者用 `openviking-gateway init --config <路径>` 生成它。 |
| 独立运行绑定非回环地址（`A publicly bound OpenViking Gateway requires a successful synchronous probe`） | 启动检查必须连上 OpenViking 并确认它使用 `api_key` 认证，否则网关退出。先启动 OpenViking（在容器中要等它就绪），或者绑定 `127.0.0.1`。 |
| OpenViking 处于 Trusted 模式（`OpenViking Gateway does not support trusted authentication`） | 仅独立运行：无论绑定什么地址，启动检查都拒绝 OpenViking 的 Trusted 模式。把 OpenViking Server 改为 `api_key` 认证（`server.auth_mode`），见[启动检查](../guides/22-gateway-operations.md#startup-checks)。 |

## Studio 显示设置卡片 {#studio-shows-a-setup-card}

Studio 无法为你加载网关时，会用一张设置卡片代替网关标签页。卡片标题说明了问题所在：

| 卡片 | 处理 |
| --- | --- |
| **这台 OpenViking 没有开启网关**（404） | 运行 `openviking-server init` → **Update gateway**，或者在 `ov.conf` 中设置 `gateway.enabled: true` 后重启，见[在 Server 中启用](../guides/22-gateway-operations.md#enable-it-in-the-server)。使用 API 密钥的服务端上，root 也可以直接在卡片上点**开启网关**。账号管理员和普通用户看到的卡片只提示联系部署者开启网关。 |
| **先连接 OpenViking**，或者 **OpenViking API 密钥无效**（401） | 点**打开连接设置**，填入当前的 OpenViking API 密钥。 |
| **网关不支持 Trusted 模式**（`trusted_mode_unsupported`） | 把 Server 改为 `api_key` 认证。 |
| **网关连不上 OpenViking**（`openviking_unreachable`） | 只出现在独立网关：检查 `openviking_url`（`gateway.conf` 或 `OPENVIKING_GATEWAY_OPENVIKING_URL`）和网络。 |
| **无法加载 OpenViking 网关** | 其他各种失败。卡片会显示网关返回的提示；点**重试**，再根据提示到本页对应的小节排查。 |

**上游**和**上下文配置**标签页只读不属于设置卡片：这是因为部署者设置了[网关管理密钥](../guides/22-gateway-operations.md#gateway-management-key)（`management_key_required`），而 账号管理员或 root 的 API 密钥并不能解锁编辑。点**输入网关管理密钥**，见[解锁编辑](06-manage-in-studio.md#unlock-editing)。服务端更换管理密钥后（`management_key_invalid`），Studio 会重新锁定；没人记得密钥时，见[忘记网关管理密钥](../guides/22-gateway-operations.md#lost-the-management-key)。

## 签发密钥失败 {#issuing-a-key-fails}

签发[网关密钥](09-gateway-keys.md#issue-a-key)之前，网关会先向 OpenViking 校验要绑定的 OpenViking API 密钥。校验不通过时，**签发网关密钥**对话框会显示原因，括号里是管理 API 返回的 `reason`。

| Studio 中的提示 | 原因和处理 |
| --- | --- |
| 服务端只保存了这个用户 API 密钥的哈希，无法自动读取。请粘贴该用户的用户 API 密钥。（`user_key_unreadable`） | OpenViking Server 只保存了密钥哈希（`encryption.api_key_hashing.enabled`）。在**粘贴用户 API 密钥**中填入该用户自己的密钥。 |
| 这是 Root API 密钥，请改用本账号中某个用户的用户 API 密钥。（`root_key_not_allowed`） | root API 密钥不能绑定到网关密钥，改用某个用户或账号管理员的 API 密钥。本地 dev 模式的 `default/default` 不受此限。 |
| root API 密钥不属于任何用户，请选择这把密钥对应的用户。（`user_required`） | 你以 root 身份登录 Studio，既没有选择用户，也没有粘贴密钥。选择用户，或者粘贴该用户的密钥。 |
| 这个 OpenViking API 密钥属于其他账号。（`other_account`） | 密钥所属的账号不是你在 Studio 中管理的账号。 |
| OpenViking 拒绝了这个密钥。（`openviking_http_401`、`openviking_http_403`） | 密钥不完整、已被重新生成，或者用户已被删除。使用该用户当前的密钥。 |
| OpenViking 无法识别这个密钥所属的用户（`openviking_identity_missing`） | OpenViking 没有返回这个密钥对应的用户。使用本账号中某个用户的密钥。 |
| 网关连不上 OpenViking，无法校验这个密钥。（`openviking_unavailable`） | 网关访问不到 OpenViking；独立网关检查 `openviking_url`。 |
| OpenViking 版本低于网关的要求。（`openviking_version_mismatch`） | 仅独立网关：OpenViking Server 低于 `min_server_version`（0.4.16），请升级。从源码安装、没有版本标签的服务可能报告 `0.1.dev123` 这样的开发版本号；请安装正式版本，或者把 `min_server_version` 改成与之匹配的值。 |
| 没有执行此操作的权限。（`forbidden`） | 粘贴的 OpenViking API 密钥不属于对话框中选中的用户，例如管理员粘贴了自己的密钥，而不是所选用户的。请粘贴所选用户自己的密钥，或者改选密钥所属的用户。普通用户粘贴的密钥不是自己的，也会报这个错。 |
| 这个用户已不在本账号中，请重新选择。（`unknown_user`） | 对话框打开期间，所选用户被移出了本账号。`unknown_policy` 和 `unknown_upstream` 同理，分别表示所选的上下文配置或上游已被删除。刷新后重新选择。 |

## 客户端收到网关返回的错误 {#clients-get-an-error-from-the-gateway}

网关在转发每个客户端请求之前，会先检查网关密钥、模型和是否有匹配的上游。下面这些拒绝都发生在请求到达服务商之前，所以不会出现在请求日志里。服务商自己返回的错误见[上游返回错误](#the-upstream-returned-an-error)。

| 状态码和提示 | 原因和处理 |
| --- | --- |
| 401 "Invalid gateway key. It may have been revoked, or removed because its OpenViking API key was regenerated…" | 客户端没有发送网关密钥，发送了已吊销的密钥或服务商的 API Key，或者用户的 OpenViking API 密钥重新生成后，网关删除了这把密钥。检查客户端实际发送的密钥，必要时[重新签发](09-gateway-keys.md#issue-a-key)。 |
| 402 `openviking_http_402` "OpenViking service is not ordered for this API key."，403 `openviking_http_403` "OpenViking request failed." | 网关检查密钥时，OpenViking 拒绝了这把 OpenViking 密钥（或网关密钥背后的 OpenViking 密钥），例如服务未下单；请求不会转发。请恢复服务订购或密钥权限。 |
| 403 "Claude subscription OAuth credentials are not supported" | 客户端发送了 Claude 订阅令牌，例如 Claude Code 用订阅账号登录且没有设置 `ANTHROPIC_AUTH_TOKEN`；或者上游保存的就是订阅令牌。请改用 API Key。 |
| 403 "Model is not allowed by this key" | 模型不在密钥[允许的模型](09-gateway-keys.md#allowed-models)范围内。签发一把允许这个模型的密钥。 |
| 400 "Request model is missing"、415 "Compressed request bodies are not supported" | 只出现在限定了模型的密钥上：请求体必须写明模型，且不能经过压缩编码。 |
| 404 "This upstream is an exact endpoint for … only" | 密钥绑定的上游关闭了**拼接协议接口路径**（见[直接发往完整请求地址](07-upstreams.md#exact-endpoint)），只接受该协议的接口地址，而客户端调用了别的路径，例如 `/v1/models`。 |
| 404 "No allowed upstream matches this protocol and model" | 密钥绑定的已启用上游中，没有一个使用客户端调用的 API 并提供所请求的模型。检查上游的协议、模型和别名、是否启用，以及密钥绑定了哪些上游。 |
| 403 "Upstream URL is not allowed" | `X-OpenViking-Upstream-URL` 与密钥绑定的任何上游的 Base URL 都不相同。见[按请求指定上游](07-upstreams.md#upstream-url-header)。 |
| 401 "Upstream API key is missing" | 上游要求每个客户端在 `X-OpenViking-Upstream-Key` 中自带 API Key，或者由网关保管 API Key 的上游没有填写。见[凭证](07-upstreams.md#credentials)。 |
| 403 "Coding Plan upstreams are disabled; configure a model API key" | 上游被标记为 [Coding Plan 密钥](07-upstreams.md#coding-plan-keys)。改用模型 API Key，或者有意勾选**允许通过网关使用这个套餐**。 |
| 403 "Context policy is unavailable" | 密钥绑定的上下文配置已不存在。用现有的上下文配置重新签发密钥。 |
| 404 "Unknown response for this key"、403 "Response upstream is no longer allowed" | 查询的 Responses 响应不是这把密钥创建的，或者创建它的上游已停用、不再绑定到这把密钥。 |
| 413 "Request exceeds configured body limit" | 请求体超过了 `max_body_bytes`（见[请求大小上限](05-long-conversations.md#side-effects)），或者超过了代理自己的上限。 |
| 426 | 对 Responses API 发起了 WebSocket 连接。这是预期行为，Codex 会改用 HTTP。 |
| 502 "Model upstream is unavailable" | 网关连不上服务商，或者调用超过了 `upstream_timeout_seconds`。在上游上点**测试**（见[测试和删除](07-upstreams.md#test-and-delete)）。 |

"Hidden tool request timed out"（504）发生在调用服务商之后，会记入请求日志，见[失败处理](04-openviking-tools.md#failure-handling)。

## 上游返回错误 {#the-upstream-returned-an-error}

上游错误是服务商自己返回的错误。它在**请求日志**标签页中的那一行，HTTP 状态码为 400 及以上，又没有网关异常，详情里写着**上游返回了 HTTP …，客户端收到了这个错误。**网关把服务商的错误原样交给了客户端。

常见原因有 API Key 填错或过期、服务商不认识这个模型名、触发限流或额度用完、对话超出了模型的上下文窗口，或者服务商故障。在[上游](07-upstreams.md#test-and-delete)上点**测试**，再和客户端收到的错误对照。上下文长度相关的错误见[长对话超出上下文长度](#long-conversations-hit-the-context-limit)。

## OpenViking 异常 {#openviking-is-degraded}

“概览”标签页上的 **OpenViking** 卡片显示网关最近一次检查 OpenViking 的结果。**已连接**表示正常，其他状态如下：

- **启动中**：网关还没完成第一次检查。嵌入网关每秒检查一次，直到 OpenViking 启动。稍后刷新即可。
- **异常**：最近一次检查失败，卡片会写明原因：

| 原因 | 处理 |
| --- | --- |
| OpenViking 无法访问或响应超时（`openviking_unavailable`） | OpenViking 没有运行或无法访问。独立网关检查 `openviking_url` 和网络。 |
| OpenViking 返回了 HTTP …（`openviking_http_<status>`），或 OpenViking 报告了错误 …（`openviking_<CODE>`） | OpenViking 返回了错误。查看 OpenViking Server 的日志。 |
| `trusted_mode_unsupported` | OpenViking 使用 Trusted 模式。把它改为 `api_key` 认证。 |

OpenViking 异常期间，请求照常发给模型，只是不带记忆；保存对话会一直重试，直到 OpenViking 恢复。网关会以逐渐拉长的间隔重新检查，最长间隔为 `health_interval_seconds`（默认 60 秒）。

## 没有补充记忆 {#no-memory-is-added}

[召回](08-context-profiles.md#recall-memory)会在每条新的用户消息到达时检索 OpenViking，把找到的内容补充进去；没有补充时，请求日志会写明原因。先在**请求日志**标签页里找到这个请求。找不到，说明客户端没有经过网关，或者请求被网关拒绝了（见[客户端收到网关返回的错误](#clients-get-an-error-from-the-gateway)）。找到后展开它，查看详情中的**召回**：

| 召回结果 | 含义 | 处理 |
| --- | --- | --- |
| **没有找到相关内容**（`empty`） | 新用户通常如此。对话提交、OpenViking 提取之后才会有记忆。 | 继续使用即可；一直没有记忆时，见[OpenViking 中看不到对话](#conversations-do-not-appear-in-openviking)。 |
| **召回已关闭或预算已用完**（`disabled`） | 上下文配置关闭了召回，当前上下文窗口剩下的召回预算不足 64 token，或者消息短于 3 个字符。 | 召回被关闭时，在上下文配置中打开。 |
| **剩余预算不足，未补充记忆**（`budget`） | 这条消息的召回预算扣掉记忆块的包装后，留给检索结果的不足 64 token，所以没有检索。可能是这段对话快用完了**单个上下文窗口预算**（进入下一个上下文窗口后重新计算，例如压缩之后），也可能是**单条消息预算**设得太低，接近 64 token 的下限。 | **单条消息预算**接近下限时，调大它。 |
| **OpenViking 无法访问或响应超时**（`openviking_unavailable`）、**召回超时**（`recall_timeout`） | 检索没有完成。 | 查看 OpenViking 卡片（见[OpenViking 异常](#openviking-is-degraded)）。OpenViking 在运行但响应慢时，调长**超时时间**。 |
| **OpenViking 拒绝了这个密钥**（`openviking_http_401`） | 用户的 OpenViking 密钥已失效。 | 用当前的密钥签发新的网关密钥，见[用户的 OpenViking API 密钥变了](09-gateway-keys.md#when-the-openviking-key-changes)。 |
| **OpenViking 返回了 HTTP …**（`openviking_http_<status>`）或 **OpenViking 报告了错误 …**（`openviking_<CODE>`） | 检索在 OpenViking 内部失败。OpenViking Server 低于 `min_server_version` 时也可能这样失败。 | 查看 OpenViking Server 的日志。 |
| **检测到 OpenViking 插件**（`plugin_present`） | 网关发现了 OpenViking 插件，让出了这段对话。 | 正常行为，见[两者同时使用时](../guides/15-gateway.md#when-both-are-in-use)。 |
| **类型**不是**新消息** | 工具步骤、辅助请求和子 Agent 请求从不检索，它们放回之前的消息得到的记忆。 | 无需处理，见[请求类型](10-overview-and-requests.md#request-types)。 |

检索失败的消息之后也不会补上记忆；下一条消息会重新检索。

## OpenViking 中看不到对话 {#conversations-do-not-appear-in-openviking}

[保存](08-context-profiles.md#save-conversations)会把每一轮完成的对话写进名为 `gateway-…` 的 OpenViking 会话，会话属于网关密钥背后的 OpenViking 用户。预期中的某一轮没有出现时，对照下表：

| 看到的情况 | 原因 | 处理 |
| --- | --- | --- |
| 缺少最新的一轮 | 保存晚一轮：下一条消息到达时，上一轮才会出现；最后一轮要等**最新回复等待时长**（默认 10 分钟）。 | 稍等，或者发送下一条消息。 |
| **保存**列显示**已关闭** | 上下文配置不保存对话。 | 在上下文配置中打开保存；修改只对新对话生效（见[修改何时生效](06-manage-in-studio.md#when-changes-take-effect)）。 |
| **重试中**或**已暂停** | 保存失败，展开的行会显示原因。 | 修复原因（例如让 OpenViking 恢复访问）后，网关会在 5 分钟内重试。原因为 `gateway_key_revoked` 时：签发的网关密钥需要重新签发，之后的下一个请求会恢复保存；OpenViking 密钥若记录过期、被驱逐或收到 401，下一个有效请求会恢复保存（account 租户模式；`tenancy: key` 下换用新密钥会开始新的对话）。对话一直暂停时，用**重新同步对话…**（见[重新同步对话](10-overview-and-requests.md#resync-a-conversation)）。 |
| **检测到 OpenViking 插件**（`plugin_present`） | 检测到 OpenViking 插件的对话，网关不保存任何内容。 | 正常行为；同一个客户端在插件和网关之间二选一。 |
| 会话里只有部分对话 | 以下情况下，网关会把当前历史写进新的 `gateway-…` 会话：客户端编辑、重新生成或压缩了历史（`history_changed`、`history_changed_during_delivery`），已在**最新回复等待时长**之后保存的一轮又继续了（`continued_after_idle`），或者有人用了**重新同步对话…**（`manual_reset`）。之前的轮次留在旧会话里。 | 到旧会话里找，见[保存状态](10-overview-and-requests.md#saving-status)。 |
| 完全看不到 `gateway-…` 会话 | 你查看时用的 OpenViking 用户，不是网关密钥背后的那个用户。 | 换成那个用户查看。 |
| 会话出现了，记忆还没有 | OpenViking 在提交之后才在后台提取记忆。 | 等提取完成。 |

## 首次调用缓存命中率下降 {#the-first-call-cache-hit-rate-dropped}

“概览”标签页上的**首次调用缓存命中率**，是每条新用户消息的第一次模型调用中，命中服务商[提示词缓存](01-how-it-works.md#replay)的输入 token 所占的比例。正常情况下它接近不经过网关时服务商能达到的水平，所以先和这个水平比较，再看最近请求上标出的异常和概览上的提示：

| 看到的情况 | 原因 | 处理 |
| --- | --- | --- |
| **记忆记录缺失**（`missing_injection_record`） | 网关存储丢失、从旧备份恢复，或者记录已过期，之前补充的记忆无法放回。 | 无需处理，进行中的对话在一次未命中之后就会恢复。 |
| **上游已切换**（`upstream_changed`） | 对话原来的上游被停用、没有绑定到客户端当前用的密钥、与请求的 `X-OpenViking-Upstream-URL` 不符，或者不再提供这个模型，于是对话转到了另一个上游。 | 重新启用这个上游，或者让客户端改用包含它的密钥，见[路由](07-upstreams.md#routing)。 |
| **缓存参数有变化**（`ark_cache_parameters_changed`） | 在火山方舟或 BytePlus 方舟上，客户端在同一段对话里改变了模型、思考模式、采样参数、系统提示词或工具。 | 在同一段对话里保持这些参数不变。 |
| 没有标出异常 | 客户端本身可能每轮都在改动之前的消息或系统提示词，例如插入当前时间，网关无法修正。每次压缩、模型每次开启新的上下文窗口，也都会造成一次未命中，这是预期内的。 | 检查客户端的系统提示词里有没有每次都变的内容。 |
| **有 … 段长对话连续没有命中提示词缓存**，或者命中率一直是 0%，而服务商或中转服务后台的缓存记录有命中 | 上游没有把缓存用量传回网关，例如中转服务转换协议时丢掉了缓存字段。网关读取 `cache_read_input_tokens`、`prompt_tokens_details.cached_tokens`、`input_tokens_details.cached_tokens`、`prompt_cache_hit_tokens` 等常见字段。 | 让中转服务把缓存用量原样传回。 |
| 同上，或者提示**上游 … 最近没有返回过缓存命中**，而服务商后台也没有命中 | 根本没有用上缓存。 | 检查服务商是否支持提示词缓存、Anthropic 客户端是否设置了 `cache_control`，以及中转服务是否在多个账号或渠道之间轮换。 |

## 长对话超出上下文长度 {#long-conversations-hit-the-context-limit}

[压缩](05-long-conversations.md#long-conversations)会在长对话占满模型上下文窗口之前，用一份摘要替换较早的历史。服务商仍以对话过长为由拒绝请求时，通常是下面三种原因之一：

| 原因 | 处理 |
| --- | --- |
| 网关不知道模型的窗口大小：上游的**上下文窗口**里没有这个模型，上下文配置也没有设置**默认上下文窗口**，网关就按默认的 1,000,000 token 计算，对窗口更小的模型压缩得太晚。 | 补上模型的窗口大小，见[设置模型的上下文窗口](05-long-conversations.md#context-window)。 |
| 对话所用的上下文配置关闭了压缩。 | 打开压缩；修改上下文配置只对新对话生效（见[修改何时生效](06-manage-in-studio.md#when-changes-take-effect)）。 |
| 压缩失败。请求详情会显示**压缩失败**及下表中的某个原因。 | 见下表。 |

| 原因 | 说明和处理 |
| --- | --- |
| `summary_incomplete` | 摘要回复没有正常结束，通常是碰到了长度上限。调大**摘要长度上限**。 |
| `summary_http_<status>` | 摘要请求被拒绝，可能来自上游，也可能是网关在发送前就拦下了（没有上游 API Key，或 Coding Plan 检查不通过）。检查上游的 API Key、模型和 Coding Plan 设置。状态码为 400 时，网关已经去掉额外的输出余量重试过一次。 |
| `summary_unavailable` | 上游无法访问、超时（`upstream_timeout_seconds`），或者返回的不是 JSON。 |
| `summary_tool_call`、`summary_empty` | 模型调用了工具，或者没有返回文本，而不是写出摘要。 |
| `cut_not_found` | 客户端最新的消息和网关存下的历史对不上，网关无法确定截断点。通常是暂时的。 |
| `summary_failed` | 意外的内部错误。在网关日志里查找 "OpenViking Gateway summary failed"。 |
| `still_over_threshold` | 摘要已经写好并保留，但用量仍达到**压缩时机**：系统提示词、工具和摘要本身就占满了大部分窗口。调小**摘要长度上限**，或者检查模型的上下文窗口设置。 |

除 `still_over_threshold` 外，其他原因下这次请求都发送了完整历史。无论哪种原因，网关都会等 60 秒再尝试压缩这段对话。

## 文件导入或下载失败 {#file-imports-or-downloads-fail}

[导入文件](04-openviking-tools.md#import-files-and-skills)时，网关会在自己的[对外地址](../guides/22-gateway-operations.md#public-address)上给客户端生成上传和下载链接。对照看到的错误处理：

| 错误 | 处理 |
| --- | --- |
| "Set gateway.public_url for client uploads and downloads" | 把 `public_url` 设为客户端能访问的地址，或者把 `mcp_file_links` 设为 `direct`。 |
| 上传或下载链接无法访问或返回 404 | 确认 `public_url` 不是旧的 `:1935` 地址，也没有以 `/gateway` 结尾，并且代理像其他路径一样转发 `/gateway/uploads` 和 `/gateway/downloads`。`mcp_file_links` 设为 `direct` 时，改查 Server 的 `OPENVIKING_PUBLIC_BASE_URL`。 |
| 413 "Upload exceeds configured body limit" | 文件超过了 `max_body_bytes` 或代理的上限。 |
| 502 "OpenViking upload is unavailable" 或 "OpenViking download is unavailable" | 网关连不上 OpenViking Server。 |
| 模型没有导入工具 | OpenViking 服务没有提供该工具，上下文配置没有勾选，或者当前请求不能使用网关工具。检查工具清单和请求详情。 |

## 降级请求的异常 {#issues-on-degraded-requests}

降级请求是网关没能完整处理的请求，“请求日志”和“概览”标签页会把它标出来。Studio 显示异常名称，括号里是管理 API 中对应的值：

| 异常 | 发生了什么 | 怎么处理 |
| --- | --- | --- |
| 记忆不可用（`memory_store_failure`） | 网关无法读写自己的存储，或者该用户的网关数据刚被删除。请求不带记忆发给了模型；Claude 对话中之前的思考内容被去掉。 | 检查磁盘空间、权限和网关日志。刚[删除过该用户的网关数据](09-gateway-keys.md#delete-a-users-gateway-data)时出现属于正常。 |
| 原样转发（`unsafe_json`） | 请求中有无法精确重新编码的数字或重复的键，网关逐字节原样转发，没有补充记忆。 | 通常只与某个客户端有关，网关这边无需调整。 |
| 上游已切换（`upstream_changed`） | 对话原来的上游无法再服务，转到了另一个上游，造成一次缓存未命中，之前的 Claude 思考内容也被去掉。 | 重新启用这个上游，或者让客户端改用包含它的密钥；也可以接受这次切换。 |
| 记忆记录缺失（`missing_injection_record`） | Claude 对话中，之前补充的记忆已经没有记录，之前的思考内容被去掉一次。 | 存储丢失后，或者在很久以前的对话里出现，都在预期之内。反复出现时，开始新对话。 |
| 检测到 OpenViking 插件（`plugin_present`） | 检测到 OpenViking 插件，这段对话不再使用网关记忆。 | 使用插件时出现属于正常。同一个客户端在插件和网关之间二选一。 |
| 缓存参数有变化（`ark_cache_parameters_changed`） | 在火山方舟或 BytePlus 方舟上，影响缓存的参数与对话的第一个请求不同，提示词缓存很可能没有命中。 | 在同一段对话里保持模型、思考模式、采样参数、系统提示词和工具不变。 |
| 工具历史无法继续使用（`hidden_tool_history_unavailable`） | Claude 对话用过 OpenViking 工具，但当前请求不能使用这些工具，之前的思考内容已被去掉。 | 查看请求详情中的工具停用原因，恢复原设置，或者开始新对话。 |
| OpenViking 工具调用失败（`hidden_tool_loop_failed`） | 模型使用 OpenViking 工具时未能完成回答，客户端收到错误。 | 见[失败处理](04-openviking-tools.md#failure-handling)。 |
| 模型未返回回答（`hidden_reply_without_anchor`） | 模型在这次请求中使用了 OpenViking 工具，但没有返回可用于继续对话的内容。客户端仍收到了回复，但下一轮无法沿用这次工具调用的结果。 | 重试本次提问，或开始一段新对话。 |
| 回复未保存（`capture_parse_failure`） | 流式回复无法解析，这条回复没有保存，客户端不受影响。 | 无需处理，仅供了解。 |
| 密钥未校验（`revocation_check_unavailable`） | OpenViking 无法确认这把网关密钥，请求在没有检查是否已吊销的情况下照常处理。 | 检查 OpenViking 是否可达、运行是否正常。 |

## Redis 不可用 {#redis-unavailable}

启动在 Redis PING 处失败时，检查 `storage.redis.endpoints`、TLS 信任链和密码环境变量；官方镜像之外需要安装 `openviking[expiry]`。鉴权时返回纯文本 `Internal Server Error` 的 HTTP 500 也可能是 Redis 不可用，请查看 stderr。运行时 Redis 故障期间 `/health` 仍返回健康，不代表 Redis 连接正常。ACL 和超时配置见[存储后端](13-configuration.md#storage)，持久化配置见 [Redis 内存策略](../guides/22-gateway-operations.md#redis-memory-policy)。

## 下一步 {#next-steps}

- [网关概览与请求日志](10-overview-and-requests.md#requests)：了解请求日志中每一列、每个标签和每种保存状态的含义。
- [观测](../guides/22-gateway-operations.md#monitoring)：用 `/health` 检查存活，通过管理 API 查看网关状态，在用户发现问题之前先察觉。
- [配置参考](13-configuration.md#gateway-fields)：查找本页提到的 `max_body_bytes`、`public_url`、`min_server_version` 等字段。
