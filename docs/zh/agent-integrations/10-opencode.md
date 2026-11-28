# OpenCode

为 [OpenCode](https://opencode.ai/) 提供跨项目、跨会话的长期记忆。插件运行在 OpenCode 进程内：每次提问时召回相关记忆，把每一轮对话捕获进 OpenViking session，并向模型提供 OpenViking MCP 工具。

源码：[examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)

## 安装

前置条件：[OpenCode](https://opencode.ai/) 2.0.15+、Node.js 18+，以及一个 OpenViking 服务（托管或自建；本地服务可用 `curl http://localhost:1933/health` 检查）。服务端启用了鉴权时，还需要 OpenViking API key。

> **从 OpenCode 1.x 升级：** 插件只支持 OpenCode 2，在 OpenCode 1.x 上不做任何事。请升级 OpenCode，或固定使用 `@openviking/opencode-plugin@2026.11.26`，这是最后一个支持 OpenCode 1.x 的版本。

### 一键安装（推荐）

连接托管服务时选择 **OpenViking Managed Service**，然后：

- **火山引擎：** 输入服务 API key，URL 由安装器填写。
- **BytePlus：** 输入 API key。见 [BytePlus 接入指南](../guides/27-byteplus-setup.md)。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

安装器把插件作为本地文件插件复制到 `~/.config/opencode/plugins/`，在 `~/.config/opencode/opencode.json` 中写入 `openviking` MCP server 条目，并配置 `~/.openviking/ovcli.conf`。

### 手动 npm 安装

npm 包名为 `@openviking/opencode-plugin`。首次配置 OpenCode 时：

```bash
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["@openviking/opencode-plugin"]
}
JSON
opencode
```

如果 `~/.config/opencode/opencode.json` 已存在，不要覆盖，只把 `"@openviking/opencode-plugin"` 合并进现有的 `plugin` 数组。OpenCode 启动时下载 npm 包，插件会自动注册 MCP server。

### 源码安装

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
node examples/memory-plugin-shared/sync.mjs
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.js ~/.config/opencode/plugins/openviking.js
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/servers ~/.config/opencode/plugins/openviking/
```

`sync.mjs` 生成不在 git 中的 `lib/shared/`；复制前先运行，每次 `git pull` 后再运行一次。

凭据从 `OPENVIKING_*` 环境变量或 `~/.openviking/ovcli.conf` 读取；向导 `node examples/opencode-plugin/scripts/setup.mjs` 会写入该文件。`ovcli.conf` 中 `plugin.opencode` 下的设置覆盖共享的 `plugin` 键。

## 验证

重启 OpenCode 并开启会话。`openviking` MCP server 应已连接，工具以 `openviking_*` 列出。问一件之前会话里提过的事，确认召回生效。插件日志在 `~/.config/opencode/openviking/openviking-memory.log`。

出现问题时，运行 `node ~/.config/opencode/plugins/openviking/scripts/ov-memory-doctor.mjs`，或让 agent 使用 `ov-memory-doctor` skill。

## OpenCode 上的不同之处

- **只支持 OpenCode 2**；OpenCode 1.x 用户固定使用 `@openviking/opencode-plugin@2026.11.26`。
- **画像在首个 prompt 注入：** session-start 块（画像和 skill 目录）随第一个 prompt 送达，而不是在会话创建时。
- **工具名：** OpenCode 给 MCP 工具加上 server 名前缀，所以工具是 `openviking_find`、`openviking_read`、`openviking_search` 等。插件把 MCP server 设为 `codemode: false`，工具保持可直接调用，不会被折叠进 Code Mode。
- **子 agent：** 子 agent 会话捕获进独立的 OpenViking session `oc-<parent>__subagent-<child>`；`skipSubagentSessions` 可跳过。
- **事件：** 在 `execution.ended` 捕获，压缩前和 `session.deleted` 时提交。没有 transcript 文件，插件从 OpenCode 读取会话消息。
- **Skills：** 插件通过 OpenCode 的 `skill.transform` 注册自带的 skills；OpenViking MCP server 被禁用时不注册。

召回、捕获、提交、离线重试等与其他集成共用：见 [集成能力参考](./16-capability-reference.md)。

## 参见

- [集成能力参考](./16-capability-reference.md)
- [插件 README](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)
