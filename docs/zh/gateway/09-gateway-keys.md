---
description: 签发把客户端接入 OpenViking 网关的网关密钥，并在之后管理它们。
---

# 管理网关密钥

本页面向需要签发网关密钥的人：给自己签发的普通用户，以及替他人签发密钥、或为某位用户做清理的账号管理员。读完后你能签发密钥并填进客户端，也清楚密钥被吊销、或其用户的 OpenViking API 密钥变化后会发生什么。开始前至少需要一个[上游](07-upstreams.md#upstreams)和一份[上下文配置](08-context-profiles.md#context-profiles)。

## 什么是网关密钥 {#gateway-keys}

在 Studio 中：**OpenViking 网关** → **密钥**。网关密钥（`ovgw_…`）是客户端用来代替服务商 API Key 的凭证。每把密钥绑定：

- **一位 OpenViking 用户。** 网关以这位用户的身份、用其本人的 [OpenViking API 密钥](../guides/22-gateway-operations.md#keys-and-credentials)召回和保存记忆，所以密钥的权限永远不会超出用户本人，见[按用户隔离](11-security-and-data.md#isolation-by-user)。
- **一份上下文配置**，决定用这把密钥的对话如何召回和保存记忆。
- **一组上游**，还可以进一步限定允许使用的模型。

**密钥**标签页列出你自己的密钥；账号管理员和 root 能看到本账号的全部密钥，并多出一列 **OpenViking 用户**。**密钥**列只显示每把密钥的开头几个字符。

网关密钥是客户端可用的两种凭证之一。另一种是用户自己的 OpenViking API 密钥，需要部署方允许，见[直接使用 OpenViking API 密钥](#openviking-keys)。

## 直接使用 OpenViking API 密钥 {#openviking-keys}

部署方把 [`openviking_keys`](13-configuration.md#gateway-fields) 设为 `true` 后，客户端可以在填网关密钥的地方直接填用户的 OpenViking API 密钥。此时凡是不以 `ovgw_` 开头的密钥，都按 OpenViking API 密钥处理。这个开关默认关闭；关闭时这类密钥直接被拒绝，不会去问 OpenViking。

- **身份就是密钥本身的用户。** 首次请求时，网关先向 OpenViking 确认这把密钥：接受 admin 和 user 密钥，不接受 root 密钥。要换成另一位用户，客户端改用那位用户的密钥即可，没有用来切换身份的 header。
- **使用账号的默认设置。** 使用账号的 `default` [上下文配置](08-context-profiles.md#context-profiles)（没有时用内置默认值）和全部已启用的上游，不限模型。客户端仍可用 `X-OpenViking-Context` 请求头为对话修改这份配置的设置，见[允许客户端覆盖设置](08-context-profiles.md#client-overrides)。
- **网关像保存签发的密钥一样保存它**，所以记忆保存和日志都照常工作。请开启[加密](../guides/22-gateway-operations.md#encryption)。**密钥**标签页把它列为 **OpenViking API 密钥**，配置和上游显示为**账号默认**。
- **只有 OpenViking 能吊销它。** **密钥**标签页上没有它的**吊销**按钮。[删除用户的网关数据](#delete-a-users-gateway-data)会删掉网关保存的副本、日志和对话状态，但仍带着这把密钥的客户端照常可用，从头开始。要让它失效，请在 OpenViking 中重新生成密钥，或关闭 `openviking_keys`。密钥重新生成后，网关会在随后的请求中删除副本，与[网关密钥](#when-the-openviking-key-changes)相同。

默认的 `tenancy: account` 下，同一 OpenViking 账号和用户的密钥共享对话状态。使用 `tenancy: key` 后，每把 OpenViking 密钥都有独立分区，即使两个库返回相同的账号和用户身份也互相隔离。对话、召回、写回、模型绑定和请求日志都使用该分区。此模式要求设置 `management_api: false`，并在启动前配置 `default_upstreams` 和 `default_profile`；Studio 和管理路由不可用。

按密钥隔离时，轮换 OpenViking 密钥会进入新分区并开始新对话。排障时可以不输出密钥，直接计算分区名：`"k_" + hashlib.sha256(("openviking\0" + key).encode()).hexdigest()`。OpenViking 的真实账号身份单独保存，用于吊销检查。

缓存的 OpenViking 密钥及其用户记录，从创建时起经过配置的 `session_ttl_seconds` 后过期，请求不会续期。下一个请求会向 OpenViking 重新确认密钥并重建记录，分区和对话 scope 保持不变。签发的网关密钥没有这项 TTL。待写回内容和清理行为见[保留期限](11-security-and-data.md#retention)。

如果客户端需要自己的上下文配置、上游或允许的模型，或者需要单独吊销，请使用网关密钥。

## 签发密钥 {#issue-a-key}

如果**密钥**标签页提示先添加上游或创建上下文配置，请先完成；自己没有权限时，联系部署网关的人。在全新的网关上，概览页**快速模式**卡片里的**一键生成密钥**可以一步完成这些准备并签发密钥，见[快速开始清单和快速模式](06-manage-in-studio.md#get-started-checklist)。

1. 在**密钥**标签页点**签发密钥**，弹出**签发网关密钥**对话框。
2. 填写**名称**，用来区分不同的密钥，写上使用者和客户端最清楚，例如“Alice · Claude Code”。
3. 在**给谁用**里选择这把密钥读写谁的记忆，见[密钥给谁用](#who-the-key-is-for)。
4. 选择一份**上下文配置**，并至少勾选一个**上游**。密钥只能访问选中的上游；每个请求到达时，网关从中挑一个协议与客户端一致、并且提供所请求模型的上游（见[路由](07-upstreams.md#routing)）。
5. 按需填写**允许的模型**，见[限定密钥可用的模型](#allowed-models)。
6. 点**签发密钥**。网关会先向 OpenViking 核实这位用户的 OpenViking API 密钥，成功后会弹出**复制网关密钥**对话框。如果看到的是**无法签发密钥**，见[签发密钥失败](12-troubleshooting.md#issuing-a-key-fails)。
7. 点**复制网关密钥**，再复制 Claude Code、Codex CLI 或聊天客户端的配置，里面已经填好真实密钥和网关地址。某个客户端的配置页提示这把密钥没有已启用的对应协议上游时，该客户端用不了这把密钥。复制完点**我已复制**。

密钥签发后立即可用。**复制网关密钥**对话框是唯一一次能看到完整密钥的机会：网关只保存它的哈希和开头几个字符。密钥丢了就重新签发。其他客户端的接入方式见[把客户端接入 OpenViking 网关](03-connect-clients.md#connect-clients)。

### 密钥给谁用 {#who-the-key-is-for}

网关会以**给谁用**中所选用户的身份召回和保存记忆。能选哪些用户取决于你的身份：

| 你的身份 | 能给谁签发 |
| --- | --- |
| 普通用户 | 只能给自己。Studio 显示“以你的身份（<用户>）读写记忆。”，不需要粘贴任何密钥。 |
| 账号管理员 | 本账号中的任意用户或管理员。 |
| root | 先在侧边栏切换到目标账号，再选择该账号中的用户。root API 密钥不能绑定到网关密钥。 |
| 本地 dev 模式下的任何人 | 密钥以 `default` 用户的身份读写记忆。 |

除 root 外，给自己签发时都不需要粘贴密钥。给别人签发时，网关要从 OpenViking 读取那位用户的 API 密钥。OpenViking 只保存密钥哈希时读不出来：这位用户旁会标注“服务端读不到这个用户的用户 API 密钥，请改为粘贴”，并出现**粘贴 <用户> 的用户 API 密钥**输入框，填入该用户当前的密钥即可。也可以点**粘贴用户 API 密钥**直接填写。用户列表加载失败时，Studio 改为要求填写 **OpenViking API 密钥**。

持有网关密钥就能使用对应用户的记忆，因此能替他人签发密钥，就等于能读取本账号所有用户的记忆，见[访问控制](11-security-and-data.md#access-control)。

### 限定密钥可用的模型 {#allowed-models}

**允许的模型**默认留空，表示允许这把密钥所绑[上游](07-upstreams.md#upstreams)提供的全部模型。要限制时，填入客户端发送的模型名称，包括别名。之后这把密钥的模型列表只显示这些名称，请求其他模型会在到达服务商之前被拒绝。设置了允许模型的密钥还要求每个请求都写明模型；具体报错见[客户端收到网关返回的错误](12-troubleshooting.md#clients-get-an-error-from-the-gateway)。

## 签发之后 {#after-issuing}

[网关密钥](#gateway-keys)一经签发就不能再改，但它发起的对话属于它的用户。

- **密钥不能编辑。** 要更换上下文配置、上游、允许的模型或用户，就签发新密钥，再吊销旧的。
- **对话属于用户，不属于密钥。** 客户端发送相同的会话 ID 时，同一用户的新密钥会接着原来的[对话](03-connect-clients.md#how-conversations-are-recognized)，换密钥不会打断对话。
- **吊销**（在**密钥**标签页点**吊销**，再点**吊销密钥**确认）后，这把密钥立即失效。用户的对话状态和日志保留，直到被显式删除或过期。尚未保存到 OpenViking 的内容（例如最新一条回复），只有在用新密钥继续这段对话时才会保存。

### 每人一把密钥 {#one-key-per-person}

记忆属于[网关密钥](#gateway-keys)背后的 OpenViking 用户，所以共用一把密钥的人也共用这位用户的记忆。请给每人签发一把密钥。想让不同客户端使用不同的上下文配置，或者想单独吊销某个客户端，再按客户端分开签发。

### 用户的 OpenViking API 密钥变了 {#when-the-openviking-key-changes}

每把网关密钥都依赖其用户当前的 [OpenViking API 密钥](../guides/22-gateway-operations.md#keys-and-credentials)。这个密钥重新生成或用户被删除后，OpenViking 会拒绝网关保存的旧密钥。网关在随后的请求中发现这一点（最多延迟约 30 秒），删除绑定它的网关密钥，对话状态和日志保留。请用当前的 OpenViking API 密钥重新签发。

如果 OpenViking 只是暂时连不上或返回错误，网关不会删除密钥，请求照常发给模型，只是不带记忆。

## 删除用户的网关数据 {#delete-a-users-gateway-data}

吊销密钥不会删除网关为用户保存的数据。要删除，账号管理员或 root 在**密钥**标签页打开该用户任意一把密钥的 **…** 菜单，选择**删除该用户的网关数据…**，再点**删除网关数据**确认。这一操作会：

- 吊销该用户的全部网关密钥，其客户端立即失去访问权限；[直接使用该用户 OpenViking API 密钥](#openviking-keys)的客户端仍可使用，直到在 OpenViking 中重新生成这把密钥；
- 删除网关为该用户保存的对话状态，包括尚未保存到 OpenViking 的内容；
- 删除该用户的请求日志。

已存入 OpenViking 的记忆不受影响。对应的 API、按账号整体删除以及适用场景，见[删除数据](11-security-and-data.md#deleting-data)。

## 下一步 {#next-steps}

- [把客户端接入 OpenViking 网关](03-connect-clients.md#connect-clients)：把密钥填进 Claude Code、Codex 或其他客户端。
- [检查接入是否生效](03-connect-clients.md#check-the-connection)：确认请求和记忆都已到达。
- [OpenViking 网关安全与数据](11-security-and-data.md)：网关为每位用户保存什么、保存多久。
