# DeepSeek Harness

为 [DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh)（`dsh`）及 DeepSeek Harness Desktop 应用提供跨项目、跨会话的长期记忆。bundle 作为 Cordis 插件运行在 DSH 进程内：每次提问时召回相关记忆，把每一轮对话捕获进 OpenViking session，并向模型提供 OpenViking MCP 工具和自带的 skills。

源码：[examples/dsh-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)

## 安装

前置条件：DSH 0.1.7-rc.2 或更新版本。

> **从 DSH 0.1.5 或更早版本升级：** 当前 bundle 需要 DSH 0.1.7-rc.2 或更新版本，请升级 DSH。

连接托管服务时选择 **OpenViking Managed Service**，然后：

- **火山引擎：** 输入服务 API key，URL 由安装器填写。
- **BytePlus：** 输入 API key。见 [BytePlus 接入指南](../guides/27-byteplus-setup.md)。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

安装器会把插件装到 `web` profile。要装到其他 profile，用 `--dsh-profile <name>` 指定：`curl -fsSL https://openviking.ai/install | bash -s -- --dsh-profile <name>`。使用桌面应用？见 [DeepSeek Harness Desktop](#deepseek-harness-desktop)。

<details>
<summary><b>手动安装</b></summary>

1. **配置连接** —— 写 `~/.openviking/ovcli.conf`（`url`、`api_key`，可选 `account`/`user`），或设置 `OPENVIKING_URL` 和 `OPENVIKING_API_KEY`。本地无鉴权服务（`http://127.0.0.1:1933`）可跳过。
2. **把 bundle 加到 profile：** `dsh plugin --profile web add @openviking/dsh-memory-plugin`。除 `desktop`（属于桌面应用）外任何 profile 名都可以。
3. **检查：** `dsh --profile web --dump-config` 的输出中应有 `openviking-memory-runtime` 条目。

卸载：`dsh plugin --profile web rm @openviking/dsh-memory-plugin`。

</details>

## 验证

启动 `dsh --profile web` 并打开对话。会话顶部应出现 OpenViking 上下文注入，模型应有 `mcp__openviking__*` 工具。问一件之前会话里提过的事，确认召回生效。

出现问题时，让 agent 运行 `ov-memory-doctor` skill，或运行 `node ~/.dsh/profiles/web/node_modules/@openviking/dsh-memory-plugin/scripts/ov-memory-doctor.mjs`（把 `web` 换成你的 profile）。

## DeepSeek Harness Desktop {#deepseek-harness-desktop}

bundle 也能在 macOS 和 Windows 上的桌面应用中运行。Desktop 把插件放在自己的 `desktop` profile（`~/.dsh/profiles/desktop`），与 CLI 的 `web` profile 分开；两者读取同一个 `~/.openviking/ovcli.conf`。

### 用安装器安装（macOS） {#desktop-installer}

1. 先打开一次 Desktop 让它创建 profile，然后用 ⌘Q 退出（关闭窗口只是隐藏）。
2. 运行安装器并选择 **DeepSeek Harness Desktop**（`dsh-desktop`）。把命令末尾的 `bash` 换成 `bash -s -- --harness dsh-desktop` 只安装 Desktop。
3. 重新打开 Desktop，开始新对话。

安装器需要 Node.js 18 或更新版本，Desktop 不自带；没有 Node.js，或找不到 Desktop 的 `dsh` 命令时，安装器会打印手动步骤。

### 手动安装 {#desktop-plugins-page}

1. **配置连接**，写入 `~/.openviking/ovcli.conf`（Windows 上为 `%USERPROFILE%\.openviking\ovcli.conf`）：`{ "url": "https://<your-openviking-server>", "api_key": "<your-api-key>" }`。
2. **添加 bundle：** 在 Desktop 中打开 **Plugins** → **Add plugin**，输入 `@openviking/dsh-memory-plugin`，点击 **Install**，确认开关已打开。macOS 上也可以退出 Desktop 后运行 `"/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" plugin --profile desktop add @openviking/dsh-memory-plugin`。
3. 打开新对话。

### 在 Desktop 中验证 {#verify-in-desktop}

`desktop` profile 不能用 `--dump-config`。在 `~/.dsh/profiles/desktop/package.json` 的 `dsh.profile.bundles` 下查找 `@openviking/dsh-memory-plugin`，然后在新对话中检查上下文注入和 `mcp__openviking__*` 工具。

### 配置 Desktop {#configure-desktop}

- 设置放在 `~/.openviking/ovcli.conf`（行为键放在 `plugin.dsh` 下）。Desktop 的恢复对话框会把 `cordis.patch.yml` 移走，只写在那里的设置在恢复后会丢失。
- Desktop 看不到终端里 export 的变量。macOS 上它在应用启动时读取一次登录 shell 的环境；Windows 上读取用户环境变量。修改 `OPENVIKING_*` 变量后需退出并重新打开 Desktop。

## DSH 上的不同之处

- **版本：** DSH 0.1.7-rc.2 及以后。
- **Cordis patch：** 除 `ovcli.conf`（`plugin.dsh` 覆盖 `plugin`）外，行为键也可以写在 profile 的 `cordis.patch.yml` 中 `openviking-memory-runtime` 条目的 `config` 下；`ovcli.conf` 和环境变量优先于 patch。
- **事件：** 在 `agent/pre-step` 召回，在 `session/event` 捕获，在 `compaction/start` 和 `session/dispose` 时提交，`session/dispose` 的预算为 3 秒。压缩把 session-start 块摘要掉后，下一步会重新发送。
- **子 agent** 被标记为子会话；`skipSubagentSessions` 可跳过。
- **工具** 通过共享 MCP 代理以 `mcp__openviking__*` 发布，该代理每个 profile 运行一份。
- **客户端召回压缩：** `recallCompress: "client"`（放在 `plugin.dsh` 下）用 DSH 自己配置的模型、通过一次不带工具的调用压缩召回结果，而不是交给服务端；`recallCompressModel` 选择同一 provider 下的其他模型，`recallCompressThinking` 设置推理强度。`auto` 在可用时使用它，否则使用服务端 rewrite。压缩默认关闭。

```json
{"plugin":{"dsh":{"recallCompress":"client","recallCompressTimeoutMs":10000}}}
```

召回、捕获、提交、离线重试等与其他集成共用：见 [集成能力参考](./16-capability-reference.md)。

## 延伸阅读

- [集成能力参考](./16-capability-reference.md)
- [Bundle README](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)
