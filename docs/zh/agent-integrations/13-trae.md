# TRAE、TRAE CN 与 TraeCode CLI 2.0 记忆集成

为 TRAE、TRAE CN 和 TraeCode CLI 2.0 添加跨项目、跨会话的长期记忆。安装后，OpenViking Hook 会自动加载相关上下文、捕获每轮对话并提交给记忆抽取器；MCP 用于主动搜索、读取和管理记忆。

## 安装

前置条件：macOS 或 Linux、Node.js 18+，以及支持 `SessionStart`、`UserPromptSubmit`、`PreToolUse`、`Stop` Hook 的 TRAE/TRAE CN 版本。TraeCode CLI 2.0 直接使用兼容 Codex 的插件格式。安装过程中会引导配置 OpenViking 连接信息。

安装器询问连接方式时，火山引擎云服务用户请选择 **火山引擎 OpenViking 云服务** 并填写 API Key。只有本机已运行 OpenViking 服务时才选择 **自建 / 本地**。

```bash
# TRAE
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae

# TRAE CN
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cn

# 同时安装
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae,trae-cn

# TraeCode CLI 2.0
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cli
```

GitHub 访问受限时使用 TOS 镜像：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae,trae-cn --dist tos

# TraeCode CLI 2.0
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae-cli --dist tos
```

安装后完全退出并重启对应客户端。

### TraeCode CLI 2.0：首次启动信任 hooks

TraeCode CLI 2.0 的 hooks 要先信任才会运行。启动 `trae-cli` 时会停在这个确认上，选 **Trust all and continue**；想先看一眼 hook 命令就选 Review hooks：

```text
Hooks need review
6 hooks are new or changed.
Hooks can run outside the sandbox after you trust them.

  1. Review hooks
> 2. Trust all and continue
  3. Continue without trusting (hooks won't run)
```

全新安装会一次列出插件注册的全部 6 个 hook。之后每次插件更新只要动了 hook，启动时都会再拦一次，数字是这次新增或改动的条数（比如只改了一个就是 `1 hook is new or changed`），同样选 Trust all and continue。

选第 3 项或错过这一步，hooks 就不会运行：MCP 工具仍能调用，但自动召回和捕获全部停摆。要恢复，得让两个彼此独立的开关都处于开启状态：

- `/hooks` — hook 的信任与开关，把标着 *New hook - review required* 或 *Modified since last trusted* 的条目信任并打开。
- `/plugins` — 插件本身的启用状态，确认 `openviking-memory` 是 enabled。

TRAE 和 TRAE CN 走 `hooks.json`，重启客户端后直接生效，没有这一步。

## 安装内容

- `SessionStart`：加载用户画像、当前项目记忆，以及 OpenViking skill 清单 `<available-skills>`。
- `UserPromptSubmit`：根据当前问题召回并注入相关内容，召回范围包括你自己的 skill 和账号内共享在 `viking://agent/skills` 下的 skill。
- `PreToolUse`：在 TRAE 和 TRAE CN 上，`Read`、`Glob`、`Grep` 的路径是 `viking://` URI 时拒绝调用，并提示改用 OpenViking MCP 工具；`Bash` 或 `RunCommand` 命令带 `viking://` URI 时照常执行，同时附加改用建议。TraeCode CLI 2.0 使用 Codex 插件，它的 `PreToolUse` 只匹配 `Bash`，只附加同样的提示，不拒绝调用。
- `Stop`：捕获本轮消息并立即提交，使短会话也能进入记忆抽取流程。
- OpenViking MCP Server：透传服务端完整 MCP 工具集（16 个工具）：`find`、`search`、`read`、`list`、`tree`、`remember`、`write`、`edit`、`add_resource`、`add_skill`、`list_watches`、`cancel_watch`、`grep`、`glob`、`forget`、`health`。其中 `search` 的 `mode="context"` 可返回组装后的上下文。
skill 清单先列你自己的 skill，再列账号内共享的 skill，每条描述截到约 40 token。清单的预算 `skillCatalogTokenBudget`（默认 `1200` token）独立于画像预算；描述放不下时只列名称。把 `skillCatalog` 设为 `false` 或把预算设为 `0` 即可关闭，既可以写在 `~/.openviking/ovcli.conf` 的 `plugin` 段（见[插件配置](../configuration/02-client.md#插件配置)），也可以用环境变量 `OPENVIKING_SKILL_CATALOG` 和 `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`。

## 验证

1. 重启 TRAE、TRAE CN 或 TraeCode CLI 2.0，并新建 Agent 会话。
2. 在客户端的 MCP 设置中确认 `openviking` 已连接。
3. 提问一个与已有项目或个人偏好相关的问题，确认回答使用了已有记忆。
4. 告诉 Agent 一个临时偏好，等待回复完成；新建会话后再次询问，确认捕获、提交和跨会话召回均生效。
5. 对 TraeCode CLI 2.0，运行 `trae-cli plugin list` 确认 `openviking-memory` 已启用，并在会话里输入 `/hooks`，确认 OpenViking 的条目已信任且处于开启状态。

需要排查 Hook 时，设置 `OPENVIKING_DEBUG=1` 后启动客户端，并查看：

- TRAE：`~/.openviking/logs/trae-hooks.log`
- TRAE CN：`~/.openviking/logs/trae-cn-hooks.log`
- TraeCode CLI 2.0：`~/.openviking/logs/codex-hooks.log`

## 升级与卸载

重复运行对应安装命令即可升级。卸载时也应使用原安装渠道：

```bash
# GitHub，以 TRAE CN 为例
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cn --uninstall --yes

# TOS，以 TRAE CN 为例
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae-cn --uninstall --yes
```

将 `trae-cn` 替换为 `trae` 可管理 TRAE 集成。TraeCode CLI 2.0 请执行 `trae-cli plugin uninstall openviking-memory@openviking`。安装器的 `--harness trae-cli --uninstall` 只用于移除旧安装中已弃用的独立 Hooks 集成。

## 故障排查

| 现象 | 原因与处理 |
|------|-----------|
| 安装后没有自动召回 | 完全退出客户端后重新启动，并新建 Agent 会话。 |
| TraeCode CLI 2.0 插件已装，但召回和捕获都不发生 | 启动时的 hook 信任确认被跳过，或当时选了 *Continue without trusting*；插件更新动了 hook 后也会重新要求信任。`/hooks` 里信任并开启 OpenViking 的条目，`/plugins` 里确认 `openviking-memory` 已启用——两个开关相互独立，都要是开着的。 |
| MCP 未连接 | 检查 `~/.openviking/ovcli.conf` 中的 URL/API Key，然后重启客户端。 |
| 新会话无法回忆上一轮内容 | 查看 Hook 日志，确认 `Stop` 已执行且 `/commit` 没有连接或鉴权错误。 |
| 同一内容被捕获多次 | 检查用户级与项目级 Hook 中是否仍有旧版 `trae-auto-recall.mjs` 或 `trae-auto-capture.mjs`；重跑安装器会移除由 OpenViking 管理的旧条目。 |
| TraeCode CLI 2.0 未列出插件 | 运行 `trae-cli plugin list`；若没有 `openviking-memory`，使用 `--harness trae-cli` 重跑安装器。 |

## 参见

- [集成能力参考](./16-capability-reference.md)
- [鉴权](../guides/04-authentication.md)
