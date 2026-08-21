# DeepSeek Harness 记忆插件

为 [DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh)（`dsh`）接入跨项目、跨会话的长期记忆。安装后每次对话都会自动召回相关记忆并捕获新内容，模型也会直接拿到 OpenViking 工具和 `openviking-memory` 技能，无需额外配置。

源码：[examples/dsh-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)

## 安装

DSH 与其他记忆插件共用同一个安装器。它会依次询问语言（English/中文）、要安装的 harness、下载源和 OpenViking 凭据；每一步都是幂等的，重复运行完全安全。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
```

GitHub 访问困难的地区，可以从火山引擎 TOS 镜像运行同一个安装器（或在下载源选项里选「TOS 镜像」）：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

选择 DSH 后，安装器会询问装到哪个 profile，默认 `web`。也可以用 `--dsh-profile <name>` 提前指定。

用一段时间后，开一个新会话问问之前提过的事情——它会记得。

<details>
<summary><b>手动安装</b></summary>

1. **配置连接** —— 写 `~/.openviking/ovcli.conf`（`url`、`api_key`，可选 `account`/`user`），或设置 `OPENVIKING_URL` 和 `OPENVIKING_API_KEY`。如果用纯本地模式（`http://127.0.0.1:1933`，无鉴权），这步可以跳过，插件默认就指向本地。

2. **把插件装进 profile**：

   ```bash
   dsh plugin --profile web add @openviking/dsh-memory-plugin
   ```

   `dsh plugin` 会转发给 profile 目录下的 pnpm，所以任何 profile 名都可以；`web` 是 `dsh` 首次运行时自动创建的那个。

3. **确认 profile 已生效**：

   ```bash
   dsh --profile web --dump-config
   ```

   输出里应该能看到 `openviking-memory` 插件组。

> 还没有 `ovcli.conf`？见[部署指南 → CLI](../guides/03-deployment.md#cli)。
>
> 卸载：`dsh plugin --profile web rm @openviking/dsh-memory-plugin`。

</details>

## 验证

启动 `dsh --profile web` 打开一个会话，会话开头应该能看到一条 OpenViking 上下文注入，模型也应该具备 `mcp__openviking__*` 工具。问一句更早会话里聊过的事，确认召回生效。

如果什么都没有，设置 `OV_DEBUG_LOG=/tmp/ov-dsh.log` 后查看该文件。

## 工作方式

插件以 Cordis 插件的形式跑在 DSH 进程内，而不是外挂 hook，因此能贴着会话走。会话开始时注入 OpenViking 画像块和可用记忆索引；每个模型步骤前用当前输入做语义检索，把结果作为持久消息追加到同一步骤——因此注入会随会话重放，也对压缩可见。它直接从 DSH 的事件流捕获 user、assistant 以及（可选的）工具结果消息，待同步 token 超过阈值即 commit，并保留最近十条消息在本地上下文中。写入失败会进入待写队列，在下次会话开始时重放。

每个 DSH 会话映射为 OpenViking 中的 `dsh-<session-id>`，子 agent 各自拥有独立会话。

模型看到的工具面就是 OpenViking 的 MCP 工具集，经由与其他记忆集成相同的 stdio 代理接入，以 `mcp__openviking__` 前缀发布。由于该代理每个 profile 只起一个进程，`mcp__openviking__remember` 写入的是服务端一个短生命周期的会话而不是当前会话（对话本身仍由自动捕获记录），工具调用带的也是启动时解析的 actor peer。若一个进程要服务多个工作区且需要精确归属工具调用，请显式设置 `OPENVIKING_PEER_ID`。插件同时附带共享的 `openviking-memory` 技能，让模型知道何时该检索、读取和写入。

误把 `viking://` URI 当本地路径的文件或 shell 调用会被拦截，并提示改用对应的 OpenViking 工具。

<details>
<summary><b>配置</b></summary>

凭证解析顺序为 `OPENVIKING_*` 环境变量 → `~/.openviking/ovcli.conf` → `~/.openviking/ov.conf`，与 Claude Code、Codex、OpenCode、pi 共用同一条链路；这些文件变更后会自动重载。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | `http://127.0.0.1:1933` | 服务端点 |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | — | API Key（以 `Authorization: Bearer` 发送） |
| `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` | — | 可信模式下的 account 与 user |
| `OPENVIKING_PEER_ID` | — | 显式指定 actor peer |
| `OPENVIKING_WORKSPACE_PEER` | `true` | 按每个会话的工作区推导 peer |
| `OPENVIKING_RECALL_PEER_SCOPE` | `all` | 设为 `actor` 可将召回限制在当前工作区 |
| `OV_DEBUG_LOG` | — | 把调试日志写到该路径 |

行为参数写在 profile 的 Cordis patch 条目里：

```yaml
- insert:
    - id: openviking-memory
      name: '@deepseek-ai/cordis-plugin-group'
      group: true
      isolate:
        openvikingMemory: true
      config:
        - id: openviking-memory-runtime
          name: '@openviking/dsh-memory-plugin'
          config:
            recallTokenBudget: 2000
            scoreThreshold: 0.35
            captureToolResults: false
            commitTokenThreshold: 20000
```

patch 中写的凭证优先于环境变量；行为开关则优先读环境变量。完整参数列表见[插件 README](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)。

</details>

## 常见问题

| 现象 | 排查方向 |
|------|----------|
| 没有注入，也没有 OpenViking 工具 | `dsh --profile web --dump-config` 里应能看到 `openviking-memory`；重新运行安装器或 `dsh plugin --profile web add …` |
| 装到了错误的 profile | 安装器默认 `web`；用 `--dsh-profile <name>` 重新运行 |
| 安装时报 `ERESOLVE` | `@deepseek-ai/dsh-*` 各包预发布 tag 不同步；请精确安装 `@deepseek-ai/dsh@0.1.0-rc.6` |
| 安装时报包「不在 npm registry 中」 | pnpm 默认拒绝发布不满 24 小时的版本（`minimumReleaseAge`）。等一等，或把该精确版本加进 profile 的 `pnpm-workspace.yaml` 的 `minimumReleaseAgeExclude` |
| 召不回任何内容 | `curl http://localhost:1933/health`；检查端点配置，以及 prompt 是否长于最小查询长度（3 个字符） |
| OpenViking 返回 401 / 403 | 检查 `OPENVIKING_API_KEY`；可信模式部署还要检查 `OPENVIKING_ACCOUNT` 与 `OPENVIKING_USER` |
| 串入了其他项目的记忆 | 设置 `OPENVIKING_RECALL_PEER_SCOPE=actor` |
| 崩溃后没有 commit | commit 由 token 阈值和 teardown 触发；排队的写入会在下次会话开始时重放 |

## 延伸阅读

- [集成能力参考](./16-capability-reference.md)
