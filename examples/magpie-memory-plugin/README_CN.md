# OpenViking for Magpie

为经过 Magpie 网关的模型请求自动召回 OpenViking 记忆，直接读取现有
`ovcli.conf`，无需在 Magpie 中重复填写服务地址和凭据。

## 安装

首次 npm 发布完成后，在 Magpie 中安装 OpenViking：

```sh
magpie plugin add @openviking/middleware-openviking
```

Magpie 将包名简化为 `openviking`；通过 npm 安装时，工具名为
`openviking_status`、`openviking_find`、`openviking_read`、`openviking_ls` 和 `openviking_remember`。
本地目录安装的工具前缀使用目录名。

从源码安装时，
在 OpenViking 仓库根目录执行：

```sh
node examples/memory-plugin-shared/sync.mjs
magpie plugin add ./examples/magpie-memory-plugin
```

本地目录安装需要先生成共享模块。`npm pack` 会自动生成并打包这些模块。
按 npm 包名安装需要先完成首次发布；直接将整个 OpenViking 仓库作为插件安装不会选中此子目录。

需要支持异步网关 Hook 和宿主 `origin` 分类的 Magpie，以及支持
`POST /api/v1/search/search` 的 `mode: "context"` 的 OV 服务。
`magpie-key` 分类需要 Magpie v0.1.2208 或更新版本，旧版本的未知来源会跳过召回。

## 配置

默认只读 `~/.openviking/ovcli.conf`。可在 **Magpie 进程的环境变量** 中通过
`OPENVIKING_CLI_CONFIG_FILE` 指定其他路径。远程运行时读取远程机器上的文件。
修改文件后下一次操作生效；修改环境变量需要重启 Magpie。

在现有配置的 `plugin` 下增加：

```json
{
  "magpie": {
    "enabled": true,
    "autoRecall": true,
    "recallContextTimeoutMs": 700,
    "recallMaxTokens": 1600,
    "recallPeerScope": "all",
    "mcpEnabled": false
  }
}
```

行为配置沿用共享插件字段名，`plugin.magpie` 覆盖 `plugin` 的同名设置；
全局 `plugin.enabled: false` 始终关闭本插件。连接读取顶层的 `url`、`api_key`、
`account`、`user`、`actor_peer_id`、`timeout`（秒）、`extra_headers`（或 CLI 别名 `extra_header`），支持身份字段的已有别名。
账户、用户请求头沿用共享客户端的身份处理方式，需要与服务端认证模式匹配。
自定义请求头不能覆盖鉴权、身份或传输请求头，包括 `X-API-Key`。
已有 LDAP 配置按 `auth_mode`、`ldap_username`（或 `user`）、`ldap_password` 构造 Basic 鉴权；
OIDC 配置使用 `oidc_token`（或 `api_key`）作为 Bearer Token。两者由服务端解析身份，不断言账户或用户。
配置类型、别名、范围和召回请求体复用已有共享模块；文件读取策略、宿主超时、工具默认关闭等适配放在 Magpie 自身目录，不改变其他插件。
Magpie 的文件读取策略排除环境变量和 workspace 层。
工具必须由 `plugin.magpie.mcpEnabled: true` 显式开启，共享的 `plugin.mcpEnabled` 不会开启此网关的工具。

不会修改文件、合并环境变量中的凭据，或回退读取 `ov.conf`、Root Key、Gateway Token。
配置缺失、损坏或服务不可用时，召回跳过，模型请求继续；不会沿用旧配置。

## 自动召回

- 仅处理宿主确认为本机的 `local`、`magpie-key` 请求，并要求 Magpie 提供非空会话 ID。
  共享网关 Key、转发请求或身份缺失时跳过，始终使用文件中配置的 OV 身份。
- 只有新的文本用户消息触发检索。工具续轮只回放已有记忆；重试复用首次决策，
  包括无结果和失败的决策。同样的问题出现在后续新轮次时仍可重新检索。
- 记忆附加到对应的**用户消息**，不修改系统指令。先持久化每条消息前缀对应的注入决策，
  后续请求在原位置恢复同样的内容，保持旧上下文稳定。
- 保留 Chat Completions、Anthropic Messages、Gemini 的系统指令、图片、工具、模型和流式设置。
  Responses 必须使用 `store: false` 并回传完整 `input`；服务商保存历史、后台请求和
  `item_reference` 均跳过。
- 状态按 OV 连接身份、peer 范围、配置文件位置，以及 Magpie Agent、会话、workspace、
  协议和模型隔离。凭据轮换不复用旧状态。分支和历史匹配使用完整前缀哈希，
  不根据相同文本猜测归属。未知的截断或压缩历史跳过增强；单条新用户消息可建立新前缀。
- 检测到已有 OV 上下文标记时跳过，避免与原生插件或 OV Gateway 重复增强。
  Magpie 自身跳过内部标题/分类请求；插件也跳过明确标记的辅助、压缩、标题和子 Agent 请求。
- `autoRecall: false` 关闭新检索，但仍回放已知历史；插件整体关闭则停止两者。
  查询过滤、召回参数、预算和结果相关性使用共享模块。
- 默认 700ms 检索预算。提高超时时也应提高 Magpie 的 `gatewayTimeoutMs`，为状态读写留余量。
  请求取消会中止检索。Actor 范围需要明确填写 `actor_peer_id`，缺失时跳过。

### 本机回放状态

默认保存在 `~/.openviking/magpie/recall/`，可用 `plugin.magpie.dataDir` 修改父目录。
文件仅保存消息锚点哈希、注入的记忆原文及空结果/失败状态，不保存原始凭据或客户端完整对话。
记忆原文可能含敏感内容；目录权限为 0700，文件为 0600。通过会话锁和原子替换支持多个宿主进程，
宿主意外退出留下的锁在一分钟后可回收。

本版本不自动删除回放文件。活跃会话应保留状态；删除、损坏或无法匹配历史时，已有对话跳过增强，
新建对话后可重新建立状态。带签名推理的恢复依赖完整历史及其回放状态，尚未验证真实模型服务的签名行为。
单会话状态上限 1 MiB，请求 JSON 上限 2 MiB、最多 2,000 条消息；超限或状态不可用时放行原请求。
诊断只记录固定状态和字节数，不记录凭据或记忆正文。

插件直接连接 OV 的普通 REST/MCP 服务，**不要求 OV 开启网关**。
暂不自动采集会话，也不为自动召回创建 OV Session；原生插件仍可负责采集。

## 可选工具

工具默认关闭。设置 `plugin.magpie.mcpEnabled: true`，然后在 Magpie Library/MCP 路由
中为需要的 Agent 开启插件服务。也可通过 Magpie 的 `/mcp/magpie/tools` 访问。

提供 `status`、`find`、`read`、`ls`、`remember`，实际名称由 Magpie 加插件前缀。
`status` 检查鉴权后的连接；`read` 最多读取 200 行，`ls` 最多返回 100 项。

**工具回调不提供调用者的认证身份。开启工具意味着将当前文件的 OV 身份授权给能调用
该 MCP 服务的调用者。** 共享网关应保持关闭，除非允许的工具调用者都应拥有该访问权限。
工具参数不能更换账户或凭据，资源权限仍由 OV 服务端控制。关闭后工具仍会列出，但拒绝调用，
`status` 返回 `disabled`。

`remember` 仅在用户明确要求记住内容时提交文本，创建 Session 并触发异步提取。
配置了 `actor_peer_id` 时，也将其写入消息的 `peer_id`，保留项目记忆的归属。
返回任务 ID 不代表记忆已生成。写入不自动重试；中断可能留下未完成的 Session，
结果不明确时应先检查 OV 再决定是否重试。

工具错误保留脱敏的 HTTP 状态、错误类别和追踪 ID，区分不存在、冲突、限流、超时和取消。
自动召回仍在错误时放行原请求，诊断仅记录固定状态。

开发、测试与完整接口说明见 [English README](README.md)。
