# OpenViking Memory for Magpie

为经过 Magpie 网关的模型请求自动召回 OpenViking 记忆，直接读取现有
`ovcli.conf`，无需在 Magpie 中重复填写服务地址和凭据。

## 安装

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
`account`、`user`、`actor_peer_id`、`timeout`（秒）、`extra_headers`，支持身份字段的已有别名。
账户、用户请求头沿用共享客户端的身份处理方式，需要与服务端认证模式匹配。
自定义请求头不能覆盖鉴权、身份或传输请求头。
工具必须由 `plugin.magpie.mcpEnabled: true` 显式开启，共享的 `plugin.mcpEnabled` 不会开启此网关的工具。

不会修改文件、合并环境变量中的凭据，或回退读取 `ov.conf`、Root Key、Gateway Token。
配置缺失、损坏或服务不可用时，召回跳过，模型请求继续；不会沿用旧配置。

## 自动召回

- 仅处理 Magpie 宿主确认为本机的 `local`、`magpie-key` 请求。共享网关 Key、
  远程或转发请求、未知来源均跳过，避免使用本机身份向其他人提供记忆。
- 支持 Chat Completions、Responses、Anthropic Messages、Gemini。
  保留原有指令、消息、图片、工具、模型和流式设置，增加带来源 URI 的历史参考上下文。
- 默认召回当前配置用户跨项目的上下文。需要项目隔离时设置
  `recallPeerScope: "actor"`，并在连接配置顶层明确填写 `actor_peer_id`；缺失时跳过，
  不从 `cwd` 猜测身份。
- 默认 700ms 召回预算，关闭查询扩展和重写。提高超时需要同时调整 Magpie 插件选项
  `gatewayTimeoutMs`，为文件读取和宿主启动留出余量。客户端 `timeout` 也会限制召回时长。
- 取消请求会中止检索；错误、无结果、鉴权失败均保留原请求，诊断仅记录固定状态和字节数。
- 同一个请求不重复注入本插件的上下文。已有 Agent 原生 OV 插件时，应关闭其中一方的自动召回。

本插件不自动采集会话，也不为召回创建 OV Session；原生插件仍可负责采集。

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
返回任务 ID 不代表记忆已生成。写入不自动重试；中断可能留下未完成的 Session，
结果不明确时应先检查 OV 再决定是否重试。

开发、测试与完整接口说明见 [English README](README.md)。
