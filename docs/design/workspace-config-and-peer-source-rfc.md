# RFC: Workspace 分层配置与可配置 Peer 来源

## TL;DR

**问题**：目前 OpenViking 使用「当前工作目录（CWD）」作为项目身份（peer）。这导致工作目录一旦改变，项目身份也会随之改变，之前积累的项目记忆便会失效——无论是更换设备、移动仓库位置、从子目录启动会话，还是使用 git worktree，都会触发此问题。此外，目前无法将「项目级配置」固化到项目中：用户只能设置环境变量，这些配置既不落盘持久化，也无法通过 Git 与团队成员共享。

**提案**：

1. **项目身份改为基于 Git 推导**。使用归一化后的 origin remote（如 `github.com-volcengine-openviking`）作为 peer id。如此一来，同一个仓库无论在何台设备、哪个克隆副本、哪个子目录或哪个 worktree 下运行，其项目身份均保持一致。
2. **非 Git 仓库目录默认不再分配 peer**，其记忆将写入用户级空间——恢复到 peer 功能引入前的默认行为。此举主要是为了应对 Codex desktop 等场景：该类工具会为每个临时任务新建一个日期目录，若按原有规则，每个一次性任务都会生成一个全新的空 peer，既无法检索历史记忆，又会在服务端堆积大量仅含零星记忆的命名空间。若需让某个非 Git 目录拥有独立记忆，只需在其中创建 `.openviking/config.json` 并指定 `peer.id` 即可。
3. **peer 来源调整为可配置项** `peer.source`：内置 `git`（新默认值）、`cwd`（旧行为，保持逐字节兼容）、`none`（彻底禁用）三种模式，同时支持诸如 `"{git_remote}-{harness}"` 的自定义模板。
4. **引入三层配置架构**：包括 `<仓库根>/.openviking/config.json`（提交至 Git，供团队共享）、`config.local.json`（供个人覆盖，不提交）、以及 `~/.openviking/workspaces/`（本机私有，用户对任何仓库均有最终覆盖权）。这三层配置共用同一套 Schema 与合并规则，其优先级高于全局的 `ovcli.conf`，但始终低于环境变量。

**旧记忆处理方案**：无需进行任何数据迁移。旧的 cwd 身份随时可在本地重新计算，recall 检索时会自动将其带入进行双向读取（dual-read），且不设截止期限。此外，默认的跨 peer 广度扫描同样会召回旧记忆，仅在结果排序时相对靠后。

**边界界定**：服务端协议保持不变，peer 依旧作为用户边界内的视图过滤，而非租户级隔离；对提交至仓库的配置采取「直接信任」原则，仅保留结构性底线（如禁止包含凭证类字段、不进行变量展开），不设置复杂的授权门槛。

## 概述

OpenViking 客户端目前仅依赖一份全局配置（`~/.openviking/ovcli.conf` 结合环境变量），项目身份（actor peer）由进程工作目录动态推导：`cwd.replace(/[^A-Za-z0-9]/g, "-")`。这一机制带来了三个长期存在的问题：

1. **Peer 身份脆弱易变**。更换机器、移动目录、在仓库子目录中启动会话或使用 Git worktree，都会推导出截然不同的 Peer，导致此前积累的项目记忆随之“消失”。插件文档已将“仓库移动或重命名后 recall 内容为空”列为已知故障模式。
2. **缺乏对单个 Workspace 的持久化配置能力**。如果想固定 Peer 或调整某个项目的行为，目前只能设置环境变量，这些配置无法随项目落盘，更无法通过 Git 在团队间共享。
3. **配置体系缺乏分层机制**。既没有项目级配置，也没有团队共享配置，更缺少诸如“某个目录应用哪份连接配置”的用户级映射能力。

本 RFC 旨在提出一套统一的解决方案，核心是建立**配置分层体系**，而 Peer 身份机制的改造则是该体系落地的首个核心功能：

- **引入三层配置架构**：包括提交到仓库的 `<root>/.openviking/config.json`（团队共享）、不提交到仓库的 `<root>/.openviking/config.local.json`（个人覆盖），以及用户级注册表 `~/.openviking/workspaces/`（机器本地，按 Workspace 分布为单文件）。这三层配置采用统一的 Schema 和合并引擎。
- **将 Peer 来源重构为可配置规则**：引入 `peer.source` 键，内置 `git`、`cwd`、`none` 预设，支持变量模板与按序回退。该规则可在任意配置层进行设置，也支持完全禁用 Peer。
- **默认策略切换为基于 Git，且只基于 Git**：以归一化的 origin remote URL 作为项目身份，确保跨机器、跨克隆、跨子目录、跨 worktree 的稳定性。不在 Git 仓库里的目录默认**不再派生 peer**，其记忆进入用户级空间——这正是 peer 功能出现之前的基线；按目录路径派生 peer 改为显式 opt-in。旧有基于 cwd 推导的记忆，将由默认的 broad recall 扫描与 dual-read 机制兜底（具体覆盖范围与边界详见「迁移与数据连续性」）。
- **对仓库提交的配置采取“直接信任”策略**：不设复杂的授权门槛，仅保留零摩擦的结构性安全底线（如结构性禁止凭证类键、不做变量展开）。相关风险评估与未来可选的授权机制详见「风险与信任取舍」一节。

## 背景与现状

### Peer 的当前机制

客户端将 Peer ID 放置在 `X-OpenViking-Actor-Peer` 请求头（读路径）和 Session 消息体的 `peer_id` 字段（写路径）中。服务端将 Peer 视为**用户边界内的路径前缀**：`viking://user/<user>/peers/<peer_id>/{memories,resources}`。目前服务端没有 Peer 注册表，也没有 Rename、Alias 或 Merge 机制（参考 `openviking/core/retrieval_targets.py:142`）。因此，变更 Peer ID 意味着开启一个全新的空命名空间；旧的命名空间只能通过默认的 broad recall（`peer_scope: "all"`）扫回，且仅覆盖 memory 分类桶，并因 `other_peer_penalty` 被降分垫底；在 `peer_scope: "actor"` 模式下，旧命名空间完全不可见。

当前的推导逻辑集中在共享库 `examples/memory-plugin-shared/lib/workspace-peer.mjs`（15 行代码）中，并通过 `sync.mjs` 复制到 7 个 Harness 目标。显式指定 Peer 的优先级链已经存在：`OPENVIKING_PEER_ID` 环境变量 → `ovcli.conf` 中的 `actor_peer_id` / `peer_id` → 各 Harness 的遗留配置块 → cwd 推导。

### 配置读取的当前机制

`ovcli.conf` 在仓库中存在多个独立的 Reader（Rust CLI、Python 侧有四处、JS 侧有五处），其行为已出现细微分歧。例如，`/etc` 回退与 `${VAR}` 展开仅存在于 `openviking_cli` 的 Reader，而 SDK 遇到 BOM 头会直接报错。Python 侧的 Schema 守门人拒绝未知的顶层键：Pydantic 模型设置了 `extra: "forbid"`（`ovcli_config.py:60`），SDK 也有白名单机制。唯一开放的扩展点是 `plugin` 字典（`ovcli_config.py:52`），这也顺理成章地成为本方案中所有 `ovcli.conf` 侧新键的落点——**实现零 Schema 变更**。

但在此之前，必须修复一个前置 Bug：Rust 的 `Config` 结构体缺少 `plugin` 字段（`crates/ov_cli/src/config.rs:39`），导致 `ov config add|edit` 与交互向导在重写文件时会**整段丢弃 `plugin` 配置**（包括现有的 `plugin.claude_code` / `plugin.codex`）。这是一个当前已存在的数据丢失问题，其修复（Rust 写路径改用 `serde_json::Value` 级合并以保留未知键）需列入 P0 阶段。

`${VAR}` 展开目前由 `load_json_config` 对整个文件文本执行（`openviking_cli/utils/config/config_loader.py:88`）。这种行为对用户自有文件固然便利，但对任何可通过仓库提交的文件而言，却是一个可能泄露密钥的危险机制（参考 pnpm GHSA-3qhv-2rgh-x77r 漏洞：提交的 `.npmrc` 中的 `${CI_JOB_TOKEN}` 在安装时被展开并外发）。因此，Workspace 文件必须走独立的、不涉及变量展开的解析路径。

## 目标与非目标

目标：

- 建立一套三层、统一 Schema、可扩展的 per-workspace 配置体系，支持团队通过 Git 共享项目级设置。
- 实现 Peer 来源可配置、可禁用；默认切换为 Git 推导机制；避免现有记忆成为“孤儿”数据。
- 产出一份附带 per-consumer 适配标注的规范优先级表，以及通过 doctor 输出 per-key 的解析链路，终结“配置到底从哪里来”的排障黑洞。

非目标：

- 暂不更改服务端协议与鉴权模型（Peer 依然是用户边界内的视图过滤，而非租户边界）。
- 暂不实现复杂的授权/信任（Trust）门槛（仅在风险一节预留设计空间）。
- Python SDK 与 VikingBot 不读取 Workspace 文件（库或服务进程的 cwd 没有意义，详见适配矩阵）。
- 不涉及 Monorepo 子目录级的 Peer 细分（留待 v2 议题）。

## 设计：配置分层

### 文件布局

| 文件 | 位置 | 提交到 Git | 用途 |
| --- | --- | --- | --- |
| `config.json` | `<workspace-root>/.openviking/` | 是 | 团队共享的项目级设置 |
| `config.local.json` | `<workspace-root>/.openviking/` | 否(gitignore) | 个人对当前项目的覆盖配置 |
| `<slot>.json` | `~/.openviking/workspaces/` | 机器本地 | 用户级的 per-workspace 注册表；本次没有 CLI 写入器，按插件 doctor 打印的路径手工创建 |
| `ovcli.conf` | `~/.openviking/` | 机器本地 | 连接与凭证（维持不变）；新键存入 `plugin` 字典 |

Workspace 根目录的确定规则：从生效的 cwd 向上查找，最近的一个持有 `.git`（文件或目录）**或** `.openviking/config.json` / `config.local.json` 的目录即为根目录。`.git` 与标记文件同在一个目录时按 Git 处理；标记文件在仓库内部的子目录先被命中时，该子目录是配置层的根，但 Git 身份仍继续向上取自外层仓库（`{git_remote}` / `{git_root}` 不变，因此默认 peer 不会因为子目录多了一份配置文件而分裂）。对于 linked worktree，通过 `commondir` 收敛**身份**（因此两个 worktree 共用同一个 peer 与同一条注册表记录），但各自的 `.openviking/config.json` 仍跟随所在 checkout——那是分支上的文件；Submodule 被视为独立仓库，不并入父仓库。既无 `.git` 也无标记文件的目录**不是 Workspace**：没有根目录、没有配置层、没有注册表条目，默认也不派生 peer。`$HOME` 与 `/` 不能作为 Workspace 根目录。

用户级注册表设计采用**目录制**而非单一 JSON 文件：每个 Workspace 对应一个权限为 0600、支持原子写入的小文件，以避免多个短生命周期的 Hook 进程对同一文件执行 read-modify-write 时造成更新丢失。定位条目时以 Workspace 根目录路径为依据，条目内部记录 Git 身份键作为**负证据（Negative Evidence）**——当路径命中但记录的 Git 身份与当前仓库冲突时，视为未命中，系统将开启新的条目，绝不继承旧条目的 Peer 绑定与设置。这能有效防止“同一路径先后放置了两个不同仓库”时发生身份串台。

注册表条目内容包括：per-workspace settings（与 Workspace 文件共用 Schema）、`peer` 显式绑定（手工写入，CLI 写入器为后续）、`previous_peer_ids`、`cli_config_profile`（详见安全底线），以及首次/最近可见时间。

同时需一并处理已知的命名冲突问题：`.openviking` 目前也是解析器的机器本地临时目录（`StoragePath.BASE_DIR`，`openviking_cli/utils/storage.py:37`），并且本仓库的 `.gitignore` 也忽略了整个 `.openviking` 目录。解决方案：将仓库自身与文档示例的 `.gitignore` 规则缩小为具体的临时子路径（如 `.openviking/media/`、`.openviking/downloads/` 等）而非整个目录；doctor 增加“存在 `config.json` 但被 Git 忽略”的检查项。

### 优先级

针对同一个配置键，优先级从高到低排列如下：

| 层级 | 说明 |
| --- | --- |
| 1. 环境变量 `OPENVIKING_*` | 用于部署期覆盖，遵循项目惯例，优先级永远最高 |
| 2. CLI 标志参数 | 仅 Rust CLI 存在（如 `--actor-peer-id`） |
| 3. 用户注册表 `~/.openviking/workspaces/<slot>.json` | 用户针对该 Workspace 的私有最终覆盖 |
| 4. `<root>/.openviking/config.local.json` | 个人项目层 |
| 5. `<root>/.openviking/config.json` | 团队项目层（需经过安全底线过滤） |
| 6. `ovcli.conf` 的 `plugin.<harness>` → `plugin` | 现有全局机制保持不变 |
| 7. `ov.conf` 的 `<harness>` 遗留块 | 仅作向后兼容读取 |
| 8. 内置默认值 | — |

项目层（4、5）优先级高于全局层（6）的原因在于：项目声明的约定应当对该项目直接生效，这与 VS Code（workspace > user）和 Claude Code（project > user）的逻辑保持一致；而第 3 层则保证了用户对任何本地仓库始终拥有最终否决权。

必须显式声明本机制与现有 `OPENVIKING_CREDENTIAL_SOURCE` 开关的关系：该开关对凭证字段起全有全无的控制作用——在 `cli` 模式下，连 `OPENVIKING_PEER_ID` 都会被忽略（`credentials.mjs:183`），在 `auto` 模式下，只要出现任一凭证类环境变量，整个链路就会切换到环境变量优先（`credentials.mjs:119`）。本方案规定：Workspace 身份与行为设置（`peer.source`、注册表、两个 Workspace 文件中的各个键）**不属于凭证链**，不受 credential source 模式的影响。上表优先级仅约束这些新键，凭证与连接键继续维持既有的链路与开关语义。

各消费者的适配状态需在文档中逐列标注（已实现 / 计划中 / 不适用），避免规范与实现脱节：

| 层级 | JS Hooks | MCP Proxy | Rust CLI | Python SDK |
| --- | --- | --- | --- | --- |
| 环境变量 | 已有 | 已有 | 部分（无 `OPENVIKING_PEER_ID`） | 已有 |
| 注册表 | 新增 | 经父进程注入 | 后续（本次不含 Rust CLI） | 不适配 |
| Workspace 文件 | 新增 | 不直读（见 Proxy 章节） | 后续 | 不适配 |
| `ovcli.conf` | 已有 | 已有 | 已有 | 已有 |

### 合并语义

- **标量与对象**：高层级覆盖低层级，对象类型按键进行深度合并。
- **列表**：默认跨层做 Union（并集）；若列表首元素为字面量 `"!reset"`，则清空所有低层继承（参考 EditorConfig 的 `unset` 与 Git 对 `safe.directory` 的空值重置逻辑）。
- **未知键**：保留并忽略，永不报错（保障前向兼容性；老版本客户端不能因遇到新版文件而崩溃，反之亦然）。已知键若出现非法枚举值，则回退到默认值并抛出一次警告。
- **`version`**：必填整数，当前固定为 1；缺失或遇到不认识的主版本号（Major）时，忽略整个文件并警告。
- **`$schema`**：可选字段，供编辑器自动补全与校验使用；仓库在 `examples/schemas/workspace-config-v1.json` 提供预留的 JSON Schema（随 P2 的键集一起落地），示例中的 URL 指向其 GitHub raw 地址。客户端在解析时会忽略该键。
- **`min_client_version`**：仅触发软警告，不阻断执行——否则仓库文件将获得对用户插件进行拒绝服务攻击（DoS）的能力。

### Schema (v1)

```jsonc
{
  "$schema": "https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/schemas/workspace-config-v1.json",
  "version": 1,
  "min_client_version": "0.9.0",
  "notes": "本项目的 OpenViking 约定,自由文本,仅展示",

  "peer": {
    "source": "git",            // 预设或模板,见「Peer 来源」
    "id": "openviking-core"     // 显式指定,优先于 source 推导
  },

  "recall": {
    "enabled": true,
    "peer_scope": "all",        // "all" | "actor"
    "dedup_turns": 5,
    "max_items": 12
  },

  "capture": {
    "enabled": true,
    "commit_token_threshold": 20000
  },

  "bypass": {
    "session_patterns": ["*-scratch", "**/tmp/**"]  // union 合并;沿用 isBypassed 语义:对 session id 与 cwd 都匹配,`*` 不跨 `/`,跨层级用 `**`
  },

  "labels": { "project": "OpenViking" }
}
```

`config.local.json` 与注册表的 `settings` 使用同一个 Schema；注册表条目则额外增加 `cli_config_profile` 与 `previous_peer_ids` 等内部登记字段。数值类配置键会在客户端进行区间钳制（例如 `commit_token_threshold` 限制在 1000..1000000，`dedup_turns` 限制在 0..20），非法枚举值直接回退为默认值。

对于来自代码仓库的“关闭”类设置（如 `capture.enabled: false` 或 bypass 模式命中），系统将在 Session-start 注入的上下文块中播报一行提示，并在 doctor 输出中明确标注其来源——确保行为可见，但不做硬性拦截。

### 结构性安全底线

虽然不设复杂的授权门槛，但以下安全规则必须无条件成立，以确保对日常使用零摩擦：

1. **结构性禁止凭证与连接键**。`url`、`mcp_url`、`api_key`、`bearer_token`、`root_api_key`、`gateway_token`、`account`、`user`、`auth_mode`、`extra_headers`、`credential_source`，以及任何指向其他配置文件的路径键，一律**不允许**出现在两个 Workspace 文件中；解析时将直接剔除并触发警告（非静默）。连接与凭证信息唯一的归宿是：`ovcli.conf` + 环境变量。这是回答“我的数据究竟发往哪台服务器”时，不需要去翻阅仓库文件的唯一保证，逻辑上对应 Git 的 `protected configuration` 不变式。
2. **变量展开是配置层的属性，而非解析器的属性**。Workspace 文件与新的注册表文件由同一个引擎解析，一律使用原生的 `JSON.parse`，永不经过 `load_json_config` 或任何包含 `${VAR}` 的展开路径；将在单元测试中断言 `${HOME}` 在这些文件中保持字符串字面量。“维持现有的展开行为”仅针对旧有的 `ov.conf` / `ovcli.conf`（实际上现状本身也不统一：Python Reader 会展开，JS Reader 不展开，统一与否不属于本 RFC 的范畴）。
3. **限制 `cli_config_profile` 仅能作为名称标识**。它只能从注册表（用户侧）选择 `~/.openviking/ovcli.conf.<name>`（复用 Rust CLI 现有的命名 Profile 布局），字符集限制为 `^[a-z0-9][a-z0-9._-]{0,63}$`，不允许包含路径分隔符；如果 Profile 不存在，则触发硬错误而不是静默回退。此键**禁止**出现在仓库提交文件中——因为“选择哪份凭证发往哪台服务器”，等价于直接篡改 `url` 的攻击。
4. Workspace 文件在解析前的常规防御机制：必须是常规文件、realpath 不能逃逸出 Workspace 根目录、设定文件大小上限（64 KiB）、顶级节点必须是对象；解析若发生错误，则进入 Hook 的 fail-open 路径（插件自身的错误不应阻塞用户的会话）。

### Provenance 与诊断

两个插件的 doctor 输出将展示 per-key 的完整解析链路：包括生效值、来源文件、被更高层遮蔽的值，以及因安全底线被剔除的键——功能对标 `git config --show-origin --show-scope`。（注：现有的 `ov doctor` 实际上是 Python 侧面向服务端环境的诊断工具，不承载此项输出功能。）在当前三语言技术栈、多 Reader 并存的现状下，这一诊断输出与配置体系本身同等重要，并将其作为各 Reader 行为一致性的日常验收手段。

## 设计：Peer 来源规则

### `peer.source`

这是新增的配置键，可出现在任意配置层级中（环境变量形式为 `OPENVIKING_PEER_SOURCE`；`ovcli.conf` 侧为 `plugin.peerSource` / `plugin.<harness>.peerSource`）：

- 内置预设 `"git"`：启用 Git 身份推导（这是新的默认行为），推导链路见下文；不在 Git 仓库里则不派生。
- 内置预设 `"cwd"`：保持旧行为，做到字节级一致；这是按目录路径派生 peer 的唯一预设，需显式选择。
- 内置预设 `"none"`：完全不发送 Peer 头，也不写入 `peer_id`。
- 支持模板字符串：例如 `"git-{git_remote}"`、`"{git_root}"`、`"team-{dir}"`。
- 支持模板数组：按序尝试，如果某条模板中的变量解析为空，则整条直接落空并尝试下一条；全部落空则等价于 `"none"`。

`{harness}` 让「同一仓库下不同 agent 各存各的记忆」成为可写得出来的配置，但**没有任何预设使用它**：跨 agent 共享一份项目记忆通常才是用户想要的那一侧，所以拆分是 opt-in。此外 MCP proxy 不参与 peer 推导（其 cwd 不是可靠身份，见「实现落点」），因此模板里用了 `{harness}` 时，只经由 proxy 的读路径解析不出该变量。

v1 支持的变量集：

| 变量 | 含义 | 空值条件 |
| --- | --- | --- |
| `{git_remote}` | 归一化后的 origin URL（`github.com/org/repo` 形式，已清理特殊字符） | 非 Git 仓库或不存在 origin remote |
| `{git_root}` | 仓库根路径（按照 legacy 规则进行字符清理）；标记文件在仓库内部命中时仍是仓库根 | 非 Git 仓库 |
| `{cwd}` | 生效的 cwd（按照 legacy 规则进行字符清理）；默认链路不再使用，仅供 `cwd` 预设与自定义模板 | 无 |
| `{dir}` | Workspace 根目录的目录名（已清理特殊字符） | 不是 Workspace |
| `{harness}` | 当前 agent 的名字，与 User-Agent 携带的一致（`claude-code`、`codex`、`dsh`、`opencode`、`pi`、`cursor`、`trae`、`trae-cn`、`zcode`） | 从不为空 |

预设 `"git"` 实际上等价于模板数组 `["{git_remote}", "{git_root}"]`：优先使用 remote，若无 remote 则退回到仓库根路径（至少修复了子目录分裂的问题），若不是 Git 仓库则**落空**——不发送 peer 头，也不写入 `peer_id`，记忆进入用户级空间 `viking://user/<u>/memories`。默认情况下不添加任何前缀——因为路径类 ID（`{git_root}` / `{cwd}`）在 POSIX 系统下必然以 `-` 开头，与 remote 的表现形态天然不冲突；如果用户需要前缀，可以自定义模板（如 `"git-{git_remote}"`）。

### 非 Git 目录：默认不派生，按路径派生需 opt-in

初稿的 `"git"` 预设末尾还挂着 `{cwd}` 回退，实测证明这一档必须去掉。Codex desktop 为每个不属于任何项目的对话（其状态文件里称为 projectless thread）新建一个 `~/Documents/Codex/<日期>/<slug>/` 目录作为 cwd，这些目录没有一个是 Git 仓库；按 cwd 回退，每个一次性任务都会铸造一个全新的空 peer，既召回不到上一次的东西，又在服务端留下一堆只有几条记忆的命名空间。这不是 Codex 独有的形态——下载解包出来的目录、临时目录、其他 agent 应用生成的任务目录都一样。通用的信号只有一个：它不是 Git 仓库，也没有任何东西声明它是一个项目。

因此规则是：**默认只有 Git 仓库有 peer**。既不是仓库、也没有标记文件的目录，走 peer 功能出现之前的基线——不带 `X-OpenViking-Actor-Peer`，记忆写进用户级空间。想让一个非 Git 目录拥有自己的记忆，需要用户主动开启，三种方式按推荐顺序：

1. 在该目录放 `.openviking/config.json`，写 `{"version": 1, "peer": {"id": "<名字>"}}`。显式名字不含路径，换机器、改目录名都不变；标记文件让任意子目录向上都能找到这个根。
2. 同一文件里改写 `peer.source`（如 `"cwd"` 或 `"team-{dir}"`），适合想按路径或目录名派生的场景；注意 `"cwd"` 在子目录里会得到不同的 id，这是它的旧语义。
3. 全局 `plugin.peerSource: "cwd"`（或 `OPENVIKING_PEER_SOURCE=cwd`）恢复旧行为，适合明知自己的项目多数不是 Git 仓库、且总是从项目根启动的用户。

"这里不是 Workspace"的说明只出现在 doctor，不进入注入模型的上下文——否则 Codex 每个新任务都会吃一行噪音。

已评估并放弃的其他做法见「备选方案」；简言之，读 Codex 的私有状态文件、按 app 路径设黑名单、为一次性目录发固定的 `scratch` peer，都或者不通用，或者只是把问题换了个地方。

### 按场景标注 peer

| 场景 | 建议 |
| --- | --- |
| 有 origin 的仓库 | 默认即可：所有 clone、worktree、子目录共用一个 peer |
| Fork | 默认按 origin 与上游分开；要合并记忆，在两边的 `config.json` 里写同一个 `peer.id` |
| 无 remote 的本地仓库 | 默认按仓库根路径；换机器会变，长期项目建议在 `config.json` 里写 `peer.id` |
| 非 Git 的长期项目目录 | 放 `.openviking/config.json` 写 `peer.id` |
| Monorepo 里某个子项目要单独记忆 | 子目录放 `config.json`，`peer.source: "{git_remote}-{dir}"`；不写则沿用仓库 peer |
| 一次性任务目录（Codex desktop 的日期目录、临时解包目录） | 什么都不做，记忆进用户级空间 |
| 几个目录共享一份记忆 | 各处 `peer.id` 写同一个值 |
| 同一仓库下各个 agent 要各存各的 | `peer.source: "{git_remote}-{harness}"` |
| 不想按项目分 | `peer.source: "none"`（等价 `OPENVIKING_WORKSPACE_PEER=0`） |

### 更进一步的自定义（记录方向，不在本 RFC 范围）

模板变量集与 `peer.source` 的候选链是可以继续加东西的，以下两条已经评估过可行性，但都不随本 RFC 交付：

**更多现成变量。** `{git_branch}` 的读取成本很低：`resolveGitDir` 已经算出了 worktree 自己的 gitdir（linked worktree 的 `HEAD` 在自己的 gitdir 里而不是 commondir 里，因此必须用前者，否则读到的是主 worktree 的分支），只需多读一次 `HEAD` 并匹配 `ref: refs/heads/<name>`，detached HEAD 取不到名字就按既有的 all-or-nothing 规则整条落空。同理，`normalizeGitRemote` 产出的 `host/path` 再切一刀就能免费得到 `{git_host}` / `{git_owner}` / `{git_repo}`，让 `"{git_owner}-{git_repo}"` 这类更短的 id 写得出来。

不做的理由不在实现，而在语义与缓存：分支是天天在换的，按分支拆 peer 意味着每开一个 feature 分支就进一个空的记忆命名空间、切回来才找得到，这与 Session pin「整个会话冻结同一个 peer」的初衷直接冲突；而 identity 结果有 60 秒磁盘缓存，`git checkout` 之后最多 60 秒内 peer 仍是旧分支的。若将来交付，应当与 `{harness}` 同级——提供变量，但不进任何预设，并在文档里写明这个代价。另需注意：旧客户端写下的缓存条目不含新键，升级后的 60 秒窗口内模板会静默判空并漂移到回退档，因此缓存读取需要在键集不匹配时视为未命中。

**`peer.command`：由外部脚本决定 peer。** 做成又一个模板变量 `{command}` 而不是平行的解析路径，即可自动继承既有语义：能与别的变量拼接、能放进候选列表、脚本无输出就整条落空并试下一条。要点是懒执行（模板里没提到 `{command}` 就根本不启动进程）、定长 argv 不过 shell（沿用 `async-writer` / `doctor-core` / `host-compressor` 既有的执行姿势）、带超时且超时即落空、结果与 identity 同一套短 TTL 缓存以保证一个 turn 只付一次代价。

它值不值得做，取决于 `peer.id`（显式命名）与用户级注册表（按 workspace 手工绑定）之外还剩多少诉求——脚本真正独有的场景是「对一批还没访问过的仓库自动套一条规则」。此外，该键不应从仓库提交的 `config.json` / `config.local.json` 读取，只认用户级来源，否则克隆一个仓库即等于执行任意代码，本 RFC「风险与信任取舍」中列出的三条结构性底线会被一次性拆掉。

### 召回隔离

peer 是路径前缀，不是租户边界；隔离程度由召回参数 `peer_scope` 决定，客户端键为 workspace 文件的 `recall.peer_scope`、`ovcli.conf` 的 `plugin.recallPeerScope` 或环境变量 `OPENVIKING_RECALL_PEER_SCOPE`：

- `"all"`（默认）：召回目标是用户级记忆 + 当前 peer 的记忆，再对 `viking://user/<u>/peers` 做一次广度扫描，其他 peer 命中的结果按类别降分（服务端 `other_peer_penalty` 默认 events / entities 0.1，preferences / experiences 0.02），只能垫底、不会抢占。旧 peer 下的记忆靠这一步自然回流。
- `"actor"`：不扫描其他 peer，只看用户级记忆与当前 peer。客户端在这一档下会对 Legacy ID 额外发一次查询（见「迁移与数据连续性」）。
- 用户级记忆在两档下都是全权重目标，这也是"非 Git 目录进用户级空间"的代价：一次性任务里提炼出的内容会在所有项目里参与召回。需要更强隔离的用户可以给一次性目录也放标记文件，或全局切到 `"actor"`。

`peer_scope` 是逐请求参数，旧版服务端不认识时客户端会记录一次降级并告警，不会静默变成 `"all"`。

显式指定的 `peer.id` 优先级始终高于 `source` 推导，其跨层优先级继续沿用现有的 explicit 语义：`OPENVIKING_PEER_ID` 环境变量 → CLI 标志参数（`ov --actor-peer-id`）→ 注册表条目（手工创建）→ `config.local.json` → `config.json` → `ovcli.conf` 中的 `actor_peer_id` / `peer_id` → 遗留的 `ov.conf` 块。出于向后兼容的考虑：`OPENVIKING_WORKSPACE_PEER=0` 将继续等价于 `peer.source: "none"`。

### Git 身份推导（零子进程）

基于热路径的性能约束：每个 Hook 都是新启动的 Node 进程，推导动作发生在每一次提示词级别的 Hook 路径上，而且各个 Hook 的超时预算差异极大（例如 Codex `hooks.json` 中，SessionEnd 仅 3 秒，SessionStart 为 70 秒，UserPromptSubmit 为 130 秒）。出于降低延迟、满足预算极紧场景（如 SessionEnd）的考量，同时保障健壮性（避免因环境缺少 Git 二进制文件或触发 dubious-ownership 告警而失效），推导过程仅执行纯文件系统操作，不启动 Git 子进程：

1. 向上查找 `.git` 目录；若 `.git` 为文件，则读取 `gitdir:` 的指向，通过 `commondir` 将 linked worktree 收敛至主仓库；如果 `gitdir` 路径包含 `modules/` 则判定为 Submodule，按独立仓库处理。
2. 从 `<commondir>/config` 执行纯 INI 解析，读取 `[remote "origin"] url`；不跟随 `include` / `includeIf` 指令（取不到就直接落空，进入下一级回退逻辑）。
3. 结果写入 `~/.openviking/state/` 下基于 cwd 的短 TTL 缓存中，确保同一个 Turn 内的多个 Hook 进程只需付出一次推导代价（复用现成的 `readJsonState(name, { maxAgeMs })` 状态文件机制，见 `examples/claude-code-memory-plugin/scripts/lib/state.mjs:42`）。

`{git_remote}` 的归一化规则：

```text
SCP 形式    git@github.com:volcengine/OpenViking.git
URL 形式    https://user:token@github.com:8443/volcengine/OpenViking.git/
处理步骤：  → host 提取 hostname（丢弃 userinfo 与端口），转换为小写
          → path 移除首尾斜杠与 .git 后缀，转换为小写并折叠
最终结果：  "github.com/volcengine/openviking"；若无法解析（如 file:// 或裸本地路径）则落空
```

由于 userinfo 在归一化过程中被丢弃，因此 remote URL 中内嵌 token 的情况不会泄露到 Peer ID 中。大小写折叠使得同一仓库的不同拼写方式（如 SSH 与 HTTPS、大小写差异）都能收敛到同一个身份；其代价是，在极少数区分大小写的 Forge 平台上，仅大小写不同的两个仓库会合并到同一个命名空间（原始的拼写会保留在注册表的 `label` 中）。

字符清理（Sanitize）存在两套规则，严禁混用：`{git_root}` 与 `{cwd}` 遵循 **Legacy 规则进行逐字节替换**（即 `[^A-Za-z0-9]` → `-`，不折叠连续字符、保留前导 `-`），确保与 `peer.source: "cwd"` 在 Legacy ID 重算时达到字节级完全一致；而 `{git_remote}`、`{dir}` 与 `{harness}` 使用新规则（适配服务端字符集 `^[a-zA-Z0-9_.@-]+$`，详见 `openviking/core/identifiers.py:8`）：非法字符替换为 `-`，折叠连续的 `-`，去除首尾的 `-.`，保留 `.` 使得类似 `github.com` 的域名具备可读性；规避 `__self` 与 `ext-`（实现期核实：二者在服务端校验层并非保留字——`__self` 只是 `session/memory/memory_isolation_handler.py` 的内部哨兵，`ext-` 只是 `ingest/peer.py` 的客户端编码约定；仍然规避，以免与它们撞名）；超过 100 字符时进行截断，并在末尾追加原文哈希的前 12 位（远低于 AGFS 的 255 字节段上限）。

示例：在 `/Users/x/Dev/OpenViking/examples/codex-memory-plugin` 目录下，且 origin 为 `git@github.com:volcengine/OpenViking.git` 时，推导出的 Peer 为 `github.com-volcengine-openviking`——无论是在任何子目录、任何 worktree、任何机器，还是任何一份克隆，身份都保持绝对一致。

由此确立的身份语义：同一仓库的多个本地克隆**共享**同一个 Peer（项目记忆跟着项目走）；Fork 仓库与上游仓库的 origin 不同，因此**默认分开**（通过 `gh pr checkout` 审查外部 PR 时，origin 依然是自己的仓库，身份不受影响；若需合并 Fork 与上游的记忆，在两边写同一个 `peer.id`）。

### 实现落点

- **推导与归一化**：新增共享模块 `workspace-identity.mjs`；`resolveEffectivePeerId` 函数保持 `source ∈ {explicit, workspace, none}` 三值枚举不变，但新增姊妹字段 `origin`（取值为实际产生该 id 的模板字符串，如 `{git_remote}` / `{git_root}` / `{cwd}`，或 `explicit` / `disabled` / `none` / `unresolved`）。因为现有代码中有 5 处针对 `source === "workspace"` 的字面量硬编码比较（Claude Code 的 session pin 两处、Codex 的 session-start 一处，以及两个 doctor），新增枚举会静默破坏 session pinning 逻辑。
- **Session Pin**：Claude Code 的 `ws-peer-<sessionId>.json` 与 Codex 的 `state.workspacePeerId` 继续在 SessionStart 阶段冻结整个会话的 Peer；Pin 文件新增版本字段，读取时忽略旧版本条目（目前的现状是 pin 永不过期，读取时没有 `maxAgeMs` 限制）。
- **身份解析一次、向下传递**：每个提示词级别的 Hook 优先读取 pin 文件，避免重复推导。

## 迁移与数据连续性

默认策略切换为 Git 后，已有用户积累的旧记忆仍然存放在由 cwd 推导的旧 Peer 之下。本方案不依赖用户执行任何一次性迁移动作：

1. **永久的双向读取（Dual-read）兜底**。当前 cwd 对应的 Legacy ID 永远可以在本地重新计算得出（无需专门登记）：当当前生效的 Peer 与 Legacy ID 不一致时——包括非 Git 目录如今根本没有 Peer 的情况——recall 操作会自动兼顾旧 Peer。在默认的 `peer_scope: "all"` 模式下，现有的 peers 目录扫描逻辑已自然覆盖旧 Peer 的 memory 数据，实现零额外成本；而在 `peer_scope: "actor"` 模式下，recall 会额外发起一次针对 `viking://user/<u>/peers/<legacy>/memories` 的 search 请求，但该请求必须**不带** `X-OpenViking-Actor-Peer` 头（带此请求头去读取他人的 Peer 路径会触发 403 硬错误，参考 `retrieval_targets.py:176`）。Dual-read 机制不设截止期限。需注意其覆盖范围：能在本地重算的仅限**当前路径**的 Legacy ID；若仓库已移动/改名，或历史上曾在多个子目录各自铸造过 Peer，旧 ID 将无法重算，此类情况由 broad 扫描机制兜底。
2. **本次交付的保证只有第 1 条**：旧 Peer 下的记忆不搬家，recall 靠 `all` scope 的跨 Peer 扫描与 `actor` scope 的额外查询触达；显式绑定与 `cli_config_profile` 通过手工创建的注册表条目完成。

已知边界（需如实写入文档）：broad 扫描的配额路径仅覆盖 `/memories/<bucket>/` 这样的分类桶（而无配额的扁平路径可能会带回 `profile.md` 等非分类文件），此外，旧 Peer 下的 **resources 文件在两条路径下都不会被自动扫回**——Dual-read 的 actor 分支与手动 migrate 才是彻底的解决方案。服务端的 Alias 映射表（在 `normalize_actor_peer_header` 处做旧→新映射）作为远期的备选方案，不纳入本 RFC 实施范围；需注意，该函数目前仅覆盖了读路径的 Header，写路径中的消息体 `peer_id` 还需另行处理；并且，项目 Changelog 曾明确决定不对 Legacy 有损 Peer 目录进行自动回读（因为多个身份可能发生碰撞，归属权应当交由操作者人工裁决），任何自动 Alias 机制都必须在此先例基础上进行独立论证。

## 风险与信任取舍

仓库提交的 `config.json` 本质上是攻击者可控的输入（只需克隆一个仓库即可生效）。在采取“直接信任”策略的前提下，恶意仓库通过篡改该文件可能造成以下影响（如实列举）：

- **通过 `peer.id` 指向受害者的其他项目**：该仓库产生的会话记忆将被写入受害者其他项目的命名空间（构成记忆投毒——记忆本质上是持久化的 Prompt 注入面），同时，其他项目的记忆也会被 recall 进该恶意仓库控制的上下文中。
- **配置 `peer.source: "none"`**：抑制客户端发送 Peer 头。请注意，发送 Peer 头是一个**收窄**默认检索范围的保护动作，去掉该 Header 后，默认的目标检索集将退化回整个 user root——这意味着该仓库内的会话能够窥探用户所有项目的记忆。
- **配置 `capture.enabled: false` 或命中 bypass 模式**：使该仓库内的活动逃避系统记录（反取证行为）。
- 放大数值类配置键的成本消耗（目前已通过客户端区间钳制予以缓解）。

无法越权执行的操作（由结构性底线保障）：恶意仓库无法篡改数据发往的服务器地址、无法读取或外发凭证、无法通过 `${VAR}` 展开来窃取环境变量、无法切换当前使用的 `ovcli.conf` 配置。

接受上述残余风险的考量：由于 Hook 流程完全是非交互式的，任何设立授权门槛的做法最终都会退化为“要求用户在每个 Workspace 先执行一条前置命令”，这种纯粹的摩擦对于绝大多数良性开发场景是不可接受的。况且本项目目前的主要使用形态（个人开发者、处理自有仓库）中，发生攻击的前提条件较弱。相应的缓解措施为：来自代码仓库的身份配置与关闭类设置一律进行播报（Session-start 时提示一行 + doctor 诊断），绝不允许静默生效。

未来扩展性预留（仅记录，不包含在本 RFC 实施范围内）：若未来确需收紧安全策略，保留了两个互不冲突的演进方向。其一，引入类似 direnv 或 mise 的一次性内容哈希授权机制，但仅对 `peer.*` 等身份敏感键生效；其二，推行“Key, not authority”原则——仓库内的文件仅负责声明 Workspace 的逻辑名称，由用户级的注册表将该名称映射到实际的 Peer，从而剥夺仓库直接指挥写入目标的权力。这两个方向均能在现有分层体系上增量加装，且不会破坏本 RFC 设定的文件格式。

## 实施阶段

- **P0：基础卫生修复（可独立发版）**：`sync.mjs` 改为仅导出各目标清单，并将 `main()` 逻辑挡在入口判断之后（当前机制只要 import 就会触发全量同步）；`sync.test.mjs` 改为导入这些清单（现有测试清单已过期，副本漂移可能会被 CI 静默放过）；修复 Rust 配置写路径中丢弃 `plugin` 段的数据丢失 Bug（`ov config add|edit` 与交互向导改用 `serde_json::Value` 级别的合并机制，确保未知键在往返序列化时得以保留）；为 Session pin 机制增加版本字段；当 `peer_scope: "actor"` 被旧版服务端响应 400/422 降级时，将静默失败改为抛出警告；将新编写的测试文件注册进 `.github/workflows/pr.yml`。
- **P1：搭建配置引擎**：新增共享模块 `workspace-config.mjs`（负责发现、纯粹的 parse、安全底线过滤、合并以及 Provenance 溯源）、`workspace-registry.mjs`（负责目录制注册表读写、负证据查找机制）、`workspace-identity.mjs`（负责 Git 身份推导、归一化、Sanitize 字符清理及缓存）；将上述模块加入 `HARNESS_SHARED_FILES` 以同步至 7 个目标端，同时手工同步 `agent-plugins/servers/` 下的副本；接入 `loadPluginSettings`（函数签名增加 cwd 参数；由于 Hook 顶层调用的 `loadConfig()` 早于 stdin 的解析，Workspace 层的配置必须延迟懒加载），并串联各 Harness 的 cfg 组装点；实现插件 doctor 的 Provenance 诊断输出。此阶段系统行为保持不变（因为新层级默认均为空）。
- **P2：Peer 来源规则与默认策略切换**：确保 `peer.source` 在全链路生效（环境变量 / ovcli 的 `plugin` 字典 / Workspace 文件；为 doctor 增加对 `plugin.*` 内部已知键的拼写检查支持——现有的 `KNOWN_OVCLI_KEYS` 仅校验顶层键，而 `plugin` 自身已经在白名单内）；将默认策略正式切换为 `git` 推导，非 Git 目录默认不派生（根目录查找同时识别 `.openviking/config.json` 标记文件）；上线 Dual-read 兜底机制；在 doctor 中增加对旧 Peer 的检测逻辑；同步更新引用了旧推导规则的 5 处 README 文件、`docs/en/agent-integrations/16-capability-reference.md`、`docs/en/configuration/02-client.md` 以及全部的中文镜像文档，并在 Changelog 中详细说明默认行为的变化与兜底恢复机制。
- **P3：CLI 命令（后续，本次 PR 不含）**：CLI 面整体推迟，本次以纯插件改动合入；Rust 侧保留的只有 `ov config add|edit` 丢 `plugin` 段的修复（`serde_json::Value` 级合并）。
- **独立 PR（涉及行为变更，需附带 Release Note）**：Claude Code、OpenCode 及 agent-plugins 这三个 MCP Proxy 停止使用 `process.cwd()` 推导 Peer（此举违反了其共享模块自身的约定，Codex 侧已有测试禁止此行为），改为由父进程在启动时（Launch-time）注入 `OPENVIKING_PEER_ID` 环境变量（仓库内部已有先例：dsh 父进程就是如此注入的，见 `examples/dsh-memory-plugin/mcp.mjs:27`；若父进程未注入，其 Proxy 仍会回退到 `process.cwd()`，本次将一并修正）；当 Actor 作用域下缺乏显式 Peer 时，触发警告并降级处理，而不是在启动时直接抛出异常。

## 备选方案（已否决）

- **使用 Root-commit SHA 作为 Git 身份**：在本仓库实测中被证伪——`git rev-list --max-parents=0 --all` 会返回 29 个根节点（结果取决于 Fetch 过哪些引用），执行 Fetch 操作会导致身份发生漂移；Shallow clone 根本没有根节点；Fork 仓库继承了上游的 root commit，在常规的审查外部 PR 流程中，会导致完全零配置的身份串台。
- **在提交的文件中随机生成并记录 Workspace ID 作为唯一身份**：稳定性极佳，但严重依赖于该提交文件必须存在（如果仓库未采纳或未提交该文件则彻底无效），并且将该文件作为“权威”来源时，等于暴露了投毒攻击面；其“作为键”的设计思想已部分被未来的“Key, not authority”可选方向收录。
- **设立授权门槛（Direnv 式的 Trust Gate 或 Key-not-authority）**：基于产品决策否决——在完全非交互的 Hook 场景下，任何授权机制都必然退化为要求用户对每个 Workspace 单独执行一条前置审批命令，带来的日常操作摩擦是不可接受的；其设计要点已归档在「风险与信任取舍」中备用。
- **扩展 `ovcli.conf` 的顶层 Schema**：由于存在三个不同机制的 Schema 守门人（Pydantic 设置的 `extra:"forbid"`、SDK 的硬编码白名单、Rust 重写文件时的静默丢键行为），引入任何顶层新键对于旧版客户端都是致命的硬错误；而 `plugin` 字典是目前唯一可行的零变更安全通道。
- **非 Git 目录继续按 cwd 派生（初稿的 `{cwd}` 回退档）**：被 Codex desktop 的 projectless 目录证伪——每个一次性任务铸造一个 peer。为此评估过的补救都没有采纳：读 `~/.codex/.codex-global-state.json` 里的 `projectless-thread-ids`（Electron 私有格式、1.25 MB、仅桌面版存在，SessionEnd 只有 3 秒预算）；按 app 路径设黑名单或在 codex 插件内置 `~/Documents/Codex`（依赖具体应用与操作系统的目录布局，`~/Documents/trae_projects` 之类的目录立刻又要补一条）；用户级"容器根"规则如 `~/Dev/*`（要求每个用户描述自己机器的目录布局，不是通用产品该有的默认值）；为非 Workspace 目录统一发固定的 `scratch` peer（在项目里降分、在 `actor` 下不可见的行为其实更好，但引入一个约定保留 id，且松散目录里学到的偏好也被降分；P2 若需要可在现有规则上加一条）。最终选择最朴素的一条：不是仓库就没有项目记忆，回到 peer 出现前的基线。
- **Remote 查找时支持从 origin 回退到 upstream**：由于执行 `git remote add upstream` 的瞬间会导致身份发生突变，这种不确定性不可接受；因此固定仅使用 origin，如需合并命名空间，统一走显式的 `peer.id`。

## 开放问题

1. Rust `ov` CLI 发起的数据面请求是否也应该按照 Workspace 来推导 Peer（目前它仅响应显式的 `actor_peer_id` 或 flag 参数）？若决定采纳，则需要实现 `serde_json::Value` 级别的 Overlay 覆盖合并，建议在 P3 阶段完成后再行单独评估。
2. 模板变量集是否需要引入诸如 `{git_branch}` 这类在会话生命周期内可能发生改变的变量，以及是否开放 `peer.command` 这样的脚本扩展点？两者的可行性与代价见「更进一步的自定义」，本 RFC 均不交付。
3. Monorepo 的子目录级别 Peer 划分：根目录查找已经采用"就近文件优先"，子目录里的 `.openviking/config.json` 会成为该子树的配置层根，但 Git 身份仍取自外层仓库，因此默认依旧是"一个仓库对应一个 Peer"；想拆分的子项目自己在文件里改 `peer.source`（见「按场景标注 peer」）。根级别的子路径映射表不做。
4. 目前已经可以通过不带 actor-peer 头的 `ov ls viking://user/<u>/peers` 来枚举既有 Peers 目录；对于 doctor 而言，是否值得专门开发一个带有额外元信息（如内部条目数量、最近写入时间戳）的专属清单 API？此问题留待实际实现期再做评估。