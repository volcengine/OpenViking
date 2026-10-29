# DeepSeek Harness

为 [DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh)（`dsh`）及 DeepSeek Harness Desktop 桌面应用接入跨项目、跨会话的长期记忆。安装后每次对话都会自动召回相关记忆并捕获新内容，模型也会直接拿到 OpenViking 工具以及 `openviking-memory`、`openviking-skills`、`ov-experience-memory` 三个技能，无需额外配置。

源码：[examples/dsh-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)

## 安装

DSH 与其他记忆插件共用同一个安装器。它会询问要安装的 harness 和 OpenViking 凭据；每一步都是幂等的，重复运行完全安全。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

安装器会把插件装到 `web` profile。要装到其他 profile，用 `--dsh-profile <name>` 指定：`curl -fsSL https://openviking.ai/install | bash -s -- --dsh-profile <name>`。使用桌面应用？见 [DeepSeek Harness Desktop](#deepseek-harness-desktop)。

安装后按下方“验证”步骤检查工具接入和跨会话召回。

<details>
<summary><b>手动安装</b></summary>

1. **配置连接** —— 写 `~/.openviking/ovcli.conf`（`url`、`api_key`，可选 `account`/`user`），或设置 `OPENVIKING_URL` 和 `OPENVIKING_API_KEY`。如果用纯本地模式（`http://127.0.0.1:1933`，无鉴权），这步可以跳过，插件默认就指向本地。

2. **把插件装进 profile**：

   ```bash
   dsh plugin --profile web add @openviking/dsh-memory-plugin
   ```

   `dsh plugin` 会转发给 profile 目录下的 pnpm，所以除 `desktop` 外任何 profile 名都可以（`desktop` 归[桌面应用](#deepseek-harness-desktop)所有）；`web` 是 `dsh` 首次运行时自动创建的那个。

3. **确认 profile 已生效**：

   ```bash
   dsh --profile web --dump-config
   ```

   输出里应该能看到 `openviking-memory-runtime` 条目。

> 还没有 `ovcli.conf`？见[部署指南 → CLI](../guides/03-deployment.md#cli)。
>
> 卸载：`dsh plugin --profile web rm @openviking/dsh-memory-plugin`。

</details>

## 验证

启动 `dsh --profile web` 打开一个会话，会话开头应该能看到一条 OpenViking 上下文注入，模型也应该具备 `mcp__openviking__*` 工具。问一句更早会话里聊过的事，确认召回生效。

排查 MCP 代理问题时，在启动 DSH 前设置 `OPENVIKING_DEBUG=1` 和 `OPENVIKING_DEBUG_LOG=/tmp/ov-dsh.log`，然后查看该文件。自动记忆回调使用 DSH 自身的日志系统。旧别名 `OV_DEBUG_LOG=/tmp/ov-dsh.log` 仍然支持，并且优先于这两个规范配置项。

## DeepSeek Harness Desktop {#deepseek-harness-desktop}

插件同样可以在 DeepSeek Harness Desktop 桌面应用中运行，支持 macOS 和 Windows；Desktop 没有 Linux 版本。Desktop 的插件装在它专属的 `desktop` profile（`~/.dsh/profiles/desktop`）里，与 CLI 的 `web` profile 互不相通：给 `dsh web` 装的插件不会出现在 Desktop 里，反之亦然。两者读取同一份 `~/.openviking/ovcli.conf`。

### 用安装器安装（macOS） {#desktop-installer}

在 macOS 上，安装器会检测到 Desktop，并把它作为单独的 harness **DeepSeek Harness Desktop**（`dsh-desktop`）列出，默认选中，可以和 `dsh` CLI 一起安装。修改 Desktop 的 profile 时，应用不应处于运行状态：

1. 先打开一次 Desktop，让它创建 profile，然后用 ⌘Q 退出。关闭窗口只会隐藏它，应用仍在运行。
2. 运行安装器。把命令末尾的 `bash` 换成 `bash -s -- --harness dsh-desktop` 只安装 Desktop；`--dsh-profile desktop`（或 `OPENVIKING_DSH_PROFILE=desktop`）选中的也是这个 harness。
3. 重新打开 Desktop，新开一个会话。

如果 Desktop 正在运行，安装器会提示你先退出（加 `--yes` 时只警告并继续）。如果找不到 Desktop 自带的 `dsh` 命令、profile 还没创建，或者提示后 Desktop 仍在运行，安装器不会报错退出，而是打印插件页的安装步骤。安装器本身需要 Node.js 18 或更高版本，Desktop 并不提供；在没有 Node.js 的 Mac 上，它会打印同样的步骤后退出。

安装器用的是 Desktop 自带的 `dsh` 命令，而不是 npm 装的那个——后者会拒绝 `desktop` profile。它按以下顺序查找：`OPENVIKING_DSH_DESKTOP_CLI`；`/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh`；`~/Applications` 下的同一路径；PATH 上实际指向 `DeepSeek Harness.app` 内部的 `dsh`（即应用菜单 **Manage dsh Command…** 安装的那个）。

### 手动安装 {#desktop-plugins-page}

Windows 上安装器不可用，只能用这种方式；macOS 上不想跑安装器时也可以用。插件页和 Desktop 自带的 `dsh` 命令只负责添加插件，都不会询问 OpenViking 凭据，所以要先配好鉴权。

1. **配置连接。** 写好 `~/.openviking/ovcli.conf`（Windows 上是 `%USERPROFILE%\.openviking\ovcli.conf`）：

   ```json
   { "url": "https://<你的 OpenViking 服务地址>", "api_key": "<你的 API Key>" }
   ```

   trusted 模式的服务端还要填 `account` 和 `user`；如果用的是 `http://127.0.0.1:1933` 上无鉴权的本地服务端，这步可以跳过。参见[部署指南 → CLI](../guides/03-deployment.md#cli)。

2. **添加插件**，两种方式任选：
   - **插件页**（macOS 和 Windows）：在 Desktop 中打开 **插件** → **添加插件**，输入 `@openviking/dsh-memory-plugin`，点击 **安装**。**安装源** 菜单决定从哪个 npm 源下载；镜像源同步新版本可能有延迟。装好后如果插件开关是关闭的，把它打开。
   - **Desktop 自带的 `dsh` 命令**（macOS）：先用 ⌘Q 退出 Desktop，再运行：

     ```bash
     DSH="/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh"
     "$DSH" plugin --profile desktop add @openviking/dsh-memory-plugin
     ```

     Windows 上对应的命令是 Desktop 安装目录下的 `resources\runtime\cli\bin\dsh.cmd`。

3. 新开一个会话。用 `dsh` 命令安装的，要先重新打开 Desktop。

### 在 Desktop 中验证 {#verify-in-desktop}

任何 `dsh` 都无法启动 `desktop` profile，所以 `--dump-config` 对它不可用。可以用 `"$DSH" plugin --profile desktop ls` 检查，或者确认 `~/.dsh/profiles/desktop/package.json` 的 `dsh.profile.bundles` 中有 `@openviking/dsh-memory-plugin`。然后在 Desktop 中新开一个会话，按[验证](#验证)一节检查上下文注入和 `mcp__openviking__*` 工具。

### 配置 Desktop {#configure-desktop}

- 连接和行为参数（写在 `plugin.dsh` 下）优先放在 `~/.openviking/ovcli.conf`。下文 **配置** 中的 patch 块要写进 `~/.dsh/profiles/desktop/cordis.patch.yml`，但 Desktop 启动失败时，恢复对话框（**禁用第三方插件、备份 profile patch 并重启**）会把这个文件挪成 `cordis.patch.yml.bak-<时间戳>`。
- Desktop 看不到在终端里 export 的变量。macOS 上，它在应用启动时读取一次登录 shell 的环境（`~/.zprofile`、`~/.zshrc`），所以改了 `OPENVIKING_*` 变量后要退出并重新打开 Desktop；`DSH_*` 变量不会从 shell 读取。Windows 上，它读取用户环境变量：用 `setx` 设置后退出并重新打开 Desktop。
- Desktop 不显示 DSH 的日志输出，而自动记忆回调的日志正写在那里。排查 MCP 代理时，按上一条的方式设置 `OPENVIKING_DEBUG=1` 和 `OPENVIKING_DEBUG_LOG=<文件路径>`，再查看该文件。
- Desktop 从 profile 目录启动 MCP 代理，因此代理拿不到工作区 peer；自动召回和捕获仍按每个会话的工作区计算。设置了 `OPENVIKING_RECALL_PEER_SCOPE=actor` 时，在 `ovcli.conf` 中设置 `actor_peer_id` 或设置 `OPENVIKING_PEER_ID`，才能限定 `mcp__openviking__*` 工具的范围，否则它们会检索所有 peer。显式 peer 也会取代每个会话的工作区 peer，用于自动召回和捕获，因此 Desktop 的所有会话共用这一个 peer。

卸载：在插件页中该插件的条目上点击 **卸载**；或者退出 Desktop 后运行 `"$DSH" plugin --profile desktop rm @openviking/dsh-memory-plugin`。

## 工作方式

插件以 Cordis 插件的形式跑在 DSH 进程内，而不是外挂 hook，因此能贴着会话走。会话开始时注入 OpenViking 画像块、可用记忆索引和 OpenViking 技能清单 `<available-skills>`；模型步骤带有新的用户输入时，用用户输入的文本做语义检索，把结果作为持久消息追加到同一步骤，因此注入会随会话重放，也对压缩可见。DSH 或其他插件注入的上下文（例如 `time-context`、job 通知）和工具结果既不触发召回，也不拼进检索文本。它直接从 DSH 的事件流捕获 user、assistant 以及（可选的）工具结果消息，并跳过注入的上下文，待同步 token 超过阈值或 DSH 开始压缩时即 commit，每次 commit 都归档全部已捕获的消息。写入失败会进入待写队列，在下次会话开始时重放。

每个 DSH 会话映射为 OpenViking 中的 `dsh-<session-id>`，子 agent 各自拥有独立会话。

模型看到的工具面就是 OpenViking 的 MCP 工具集，经由与其他记忆集成相同的 stdio 代理接入，以 `mcp__openviking__` 前缀发布。由于该代理每个 profile 只起一个进程，`mcp__openviking__remember` 写入的是服务端一个短生命周期的会话而不是当前会话（对话本身仍由自动捕获记录）。设置 `OPENVIKING_RECALL_PEER_SCOPE=actor` 后，固定的 `OPENVIKING_PEER_ID` 才会把该 profile 的工具调用归属到同一个 peer；默认 `all` 不发送 actor-peer 头。多个工作区需要不同工具身份时，使用独立 profile/进程。插件同时附带三个共享技能：`openviking-memory` 让模型知道何时该检索、读取和写入，`openviking-skills` 讲如何查找、使用、创建、共享和迁移存放在 OpenViking 里的技能，`ov-experience-memory` 让模型在执行类任务前检索并应用以往任务的 Experience。插件默认捕获工具结果（`captureToolResults: true`），服务端据此把该技能的读取关联回所用的 Experience；设为 `false` 时，它只检索和应用 Experience。

文件工具误把 `viking://` URI 当本地路径时，调用会被拦截，并提示改用对应的 OpenViking 工具；写入或编辑的若是 `viking://~/skills/<name>/` 这类技能目录，提示的工具是 `mcp__openviking__add_skill`，它用完整的 `SKILL.md` 文本创建或替换整个技能。shell 命令带 `viking://` URI 时照常执行，模型会收到一条改用 OpenViking 工具的提示，URI 是有意传入的数据时可以忽略。

<details>
<summary><b>配置</b></summary>

凭证解析顺序为 `OPENVIKING_*` 环境变量 → `~/.openviking/ovcli.conf` → `~/.openviking/ov.conf`，与 Claude Code、Codex、OpenCode、pi 共用同一条链路；这些文件变更后会自动重载。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | `http://127.0.0.1:1933` | 服务端点 |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | — | API Key（以 `Authorization: Bearer` 发送） |
| `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` | — | 可信模式下的 account 与 user |
| `OPENVIKING_PEER_ID` | — | 显式指定 actor peer |
| `OPENVIKING_WORKSPACE_PEER` | `true` | 按每个会话的工作区推导 peer；设为 `0` 则不发送 peer |
| `OPENVIKING_RECALL_PEER_SCOPE` | `all` | 设为 `actor` 可将召回限制在当前工作区 |
| `OPENVIKING_DEBUG` | `false` | 启用 MCP 代理调试日志；还需配置日志路径 |
| `OPENVIKING_DEBUG_LOG` | `""` | MCP 代理调试日志的文件路径 |
| `OV_DEBUG_LOG` | — | 旧别名：启用 MCP 代理日志，并覆盖规范的调试开关和日志路径 |

行为参数写在 profile 的 Cordis patch 条目里：

```yaml
- id: openviking-memory-runtime
  config:
    recallTokenBudget: 2000
    scoreThreshold: 0.35
    captureToolResults: true
    commitTokenThreshold: 20000
```

同一个 `config` 块里的 `syncTurns: false` 关闭自动捕获、commit 和待写队列重放，画像注入和召回照常。排队的写入保留到后续开启写入的会话处理；该开关不会撤销模型的 MCP 写工具或改变服务端权限。

同一个 `config` 块里的 `peerSource` 决定工作区 peer 的派生方式。默认的 `"git"` 取仓库归一化后的 `origin` URL（`git@github.com:volcengine/OpenViking.git` 得到 `github.com-volcengine-openviking`），其次是仓库根路径，因此同一个仓库的每个 clone、worktree 和子目录共用同一个 peer；不在仓库中则完全不发送 peer，在那里记下的内容进入用户级空间 `viking://user/<you>/memories`。`"cwd"` 恢复此前的行为——把工作目录路径中的非字母数字字符全部替换成 `-`；`"none"` 则完全不发送 peer。要让仓库之外的目录拥有独立记忆，请为它设置 `OPENVIKING_PEER_ID`（见[让一个目录拥有独立记忆](../configuration/02-client.md#让一个目录拥有独立记忆)）。

同一个 `config` 块里的 `skillCatalog` 和 `skillCatalogTokenBudget` 控制会话开始时注入的技能清单。清单先列你自己的技能，再列账号内共享在 `viking://agent/skills` 下的技能，每条描述截到约 40 token；它有独立的预算（默认 `1200` token，不占用画像预算），描述放不下时只列名称。`skillCatalog: false` 或把预算设为 `0` 即可关闭；对应的环境变量是 `OPENVIKING_SKILL_CATALOG` 和 `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`。

patch 中写的凭证优先于环境变量。行为配置按优先级从高到低解析：`OPENVIKING_*` 环境变量、工作区的 `.openviking/config.json` 与 `config.local.json`、`ovcli.conf` 的 `plugin.dsh`、`ovcli.conf` 的 `plugin`，最后才是这个 patch 块。完整参数列表见[插件 README](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)。

</details>

## 常见问题

| 现象 | 排查方向 |
|------|----------|
| 没有注入，也没有 OpenViking 工具 | `dsh --profile web --dump-config` 里应能看到 `openviking-memory-runtime`；重新运行安装器或 `dsh plugin --profile web add …`。Desktop 见[在 Desktop 中验证](#verify-in-desktop) |
| 装到了错误的 profile | 安装器默认 `web`；用 `--dsh-profile <name>` 重新运行 |
| DSH 提示插件与当前 dsh 版本不兼容 | 插件接受 `0.1.0-rc.6` 起的所有 `@deepseek-ai/dsh` 0.x 版本（peer 范围 `>=0.1.0-rc.6 <1.0.0-0`），出现这个提示说明 DSH 已是 1.0 或更高；升级插件，或者确认风险后用 `dsh plugin allow-version` 放行。所有 `@deepseek-ai/dsh-*` 宿主包要保持同一版本。 |
| 升级 DSH 后插件启动失败 | 已验证的版本：`0.1.0-rc.6`、`0.1.5-rc.1`、`0.1.5-rc.2`、`0.1.7-rc.2`、`0.2.0-rc.2`、`0.2.1-alpha.1`、`0.2.1-rc.2`、`0.2.1-rc.3`。更新的 0.x 版本不经预先验证就会被接受；请把 DSH 固定到已验证的版本，并提 issue。 |
| 安装时报包「不在 npm registry 中」 | 检查该 profile 的 pnpm 是否设置了 24 小时的最小发布年龄（`minimumReleaseAge`）。等一等，或把该精确版本加进 profile 的 `pnpm-workspace.yaml` 的 `minimumReleaseAgeExclude` |
| 召不回任何内容 | `curl "<OpenViking 服务地址>/health"`；检查端点配置，以及 prompt 是否长于最小查询长度（3 个字符） |
| OpenViking 返回 401 / 403 | 检查 `OPENVIKING_API_KEY`；可信模式部署还要检查 `OPENVIKING_ACCOUNT` 与 `OPENVIKING_USER` |
| 串入了其他项目的记忆 | 设置 `OPENVIKING_RECALL_PEER_SCOPE=actor`，将 peer 记忆限定为当前 peer；用户级记忆仍会共享 |
| 崩溃后没有 commit | commit 由 token 阈值、压缩开始和 teardown 触发；排队的写入会在下次会话开始时重放 |
| `profile "desktop" is managed exclusively by the Electron application` | `desktop` profile 不能启动，也不能 dump 配置，只有 Desktop 自带的 `dsh` 能修改它的插件。改用 [Desktop 自带的 `dsh` 命令](#desktop-plugins-page)，并[不借助 `--dump-config` 验证](#verify-in-desktop) |
| `Open DeepSeek Harness Desktop once to initialize its profile…` | Desktop 还没创建 profile。先打开一次 Desktop，用 ⌘Q 退出，再重新运行安装器 |
| 给 Desktop 装好了，但什么都没出现 | 用 ⌘Q 退出 Desktop（关闭窗口只会隐藏）后重新打开；在 **插件** 页确认插件已启用 |
| Desktop 弹出恢复对话框后插件消失了 | 恢复操作会禁用第三方插件并备份 `cordis.patch.yml`；到 **插件** 页重新启用，需要的设置从 `.bak-<时间戳>` 文件挪到 `ovcli.conf` |
| **极简模式** 下没有技能 | Web 和 Desktop 的 **极简模式** agent 预设没有技能工具，插件附带的技能在那里不可用；自动召回和捕获照常 |

## 延伸阅读

- [集成能力参考](./16-capability-reference.md)
