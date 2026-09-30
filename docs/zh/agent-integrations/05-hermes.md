# Hermes Agent

[Hermes Agent](https://hermes-agent.nousresearch.com/) (Nous Research) 以记忆提供方（memory provider）的形式加载 OpenViking。provider 通过 HTTP 存储和召回记忆，把每一轮对话上传到 OpenViking session，由服务端抽取长期记忆。

provider 有两份：

- **外部插件**：由本仓库维护，位于 [`examples/hermes-plugin`](https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin)，当前版本 3.0.0。本页描述的是它。
- **内置 provider**：随仍包含 `plugins/memory/openviking/` 的 Hermes 版本发布，提供旧的 `viking_*` 工具。内置副本存在时优先生效，外部插件不会被加载。

## 隔离 Python 环境

Hermes 通过 HTTP 连接 OpenViking，因此无需把 OpenViking 安装到 Hermes 的
Python 环境中。请在独立的虚拟环境或容器中运行 OpenViking 服务。不要在
已有 Hermes 的环境中使用 `--force-reinstall` 安装或升级 OpenViking：Hermes
版本可能会固定与 OpenViking 已支持、已修复安全问题的版本不同的依赖。如果确实要将
两个应用放在同一环境中，请在同一次依赖求解中安装它们，并在启动任一服务前运行
`python -m pip check`。

## 服务端版本

| OpenViking 服务端 | 可用功能 |
|---|---|
| 0.4.13 及以上 | 自动召回和轮次捕获 |
| 0.4.14 及以上 | `openviking_*` 工具，由服务端 `/mcp` 端点提供 |
| 0.4.22 及以上 | 完整的工具集 |

## 安装与配置

按外部插件 [README](https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin#install) 安装后，运行配置向导：

```bash
hermes memory setup openviking
```

- 云：保持 **OpenViking Service (VolcEngine Cloud)**，粘贴 API Key
- 自托管：填 URL（默认 `http://127.0.0.1:1933`）和 API Key；本地免鉴权可留空
- 向导若发现已有 `ovcli.conf`，直接复用即可
- 选择 **Personal Agent**（召回公共记忆和当前发送者的记忆）或 **Shared Agent**（召回同一 OpenViking 用户下所有发送者的记忆）

工具需要用户 API Key 或账号管理员 API Key：服务端 `/mcp` 端点拒绝 root key。

## 插件做什么

- **召回**：每轮开始前，在 7.5 秒的总预算内，每个 session 注入一次 session-start 画像块，并做查询召回。共享范围和按发送者限定的召回走服务端 context 模式；不知道发送者时走列表搜索。
- **捕获**：每一轮上传到 OpenViking session `hermes-<Hermes session id>`，包括工具调用和结果。因网络错误或服务端错误失败的上传保留在内存里，在下一次上传或提交前补发。pending token 达到 20,000 时提交，session 结束或切换时也会提交。
- **工具**：服务端的 MCP 工具，注册为 `openviking_*`。默认提供 `openviking_find`、`openviking_search`、`openviking_read`、`openviking_list`、`openviking_tree`、`openviking_grep`、`openviking_glob`、`openviking_remember`、`openviking_forget`、`openviking_add_resource` 和 `openviking_health`。`write`、`edit`、`add_skill`、`list_watches` 和 `cancel_watch` 通过 `extra_tools` 配置打开。
- **原生记忆镜像**：Hermes 内置 `memory` 的新增、替换和删除会同步到 OpenViking。

从 2.x 升级时，工具改名且不保留别名，新上传的 OpenViking session id 也会变化，详见 [Upgrading to 3.0.0](https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin#upgrading-to-300)。

## 验证

```bash
hermes memory status
```

`available` 表示 provider 已配置，不会检查服务端连通性，也不代表记忆已完成抽取。

## 参见

- [Hermes 插件 README](https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin) — 配置、召回路由、工具和升级说明
- [集成能力参考](./16-capability-reference.md)
- [Hermes — OpenViking memory provider 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — 内置 provider
- [部署指南](../guides/03-deployment.md) — 搭建 OpenViking 服务
- [鉴权](../guides/04-authentication.md) — 远程访问的 API Key 设置
