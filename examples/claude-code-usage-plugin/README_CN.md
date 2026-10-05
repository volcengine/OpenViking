# OV-Usage

[English](./README.md) / 中文

显示 Claude Code 每次回答参考了哪些 OpenViking 来源：哪些是 OpenViking 自动召回的，哪些是 Claude 自己搜索、读取的。同时显示本会话和安装以来的累计数据。

这些信息可以显示在侧边栏，也可以作为卡片直接显示在对话中。

## 侧边栏与对话中

**侧边栏**（默认）：对话旁边的一个面板，会话开始时自动打开。

```
全局
  [▸ 设置]  召回开 · 记录开
  38 次提问自动带上了记忆
  5 段对话沉淀了 12 次记忆更新
  启动加载：167 条记忆索引 · 你的画像：产品经理

本次对话
  本会话
    本会话共参考 9 个 OpenViking 来源
    写回 OpenViking 1 条
  之前的回答
    [#5] 15:02 “权限怎么控制？” ✓2
    [#4] 14:51 “openviking 是什么？”

  本次回答  [详情]
  “权限模型现在什么进度”

  ✓ 参考了 2 个 OpenViking 来源 · 2 你的工作记忆 · 1 条完整打开
    ◆ 你的笔记 · 主动查找
      权限模型设计
    ◆ 你的笔记 · 主动查找
      权限模型待定问题

  Claude 主动查找
    ⌕ 搜索 “权限模型” · 2 条结果
    ▤ 读取 权限模型待定问题
```

**对话中**：不打开面板。每次回答下方有一张卡片，列出这次回答用到的来源；对话第一条提问上方有一张卡片，显示全局和本会话的累计数据。没有召回、也没有主动查找的回答不显示卡片。

```
⏺ …Claude 的回答…
  ╭────────────────────────────────────────────────╮
  │ OpenViking · 本次回答                          │
  │ ✓ 参考了 12 个 OpenViking 来源 · 1 你的偏好
  │   · 1 你的经历 · 6 你的工作记忆 · 3 团队文档
  │   · 1 技能 · 2 条完整打开                      │
  │   ◆ 权限模型设计 · 主动查找                    │
  │   ◆ 权限模型待定问题 · 主动查找                │
  │   ▤ 项目规则 ×5 · 主动查找                     │
  │   …                                            │
  │   [还有 2 条]                                  │
  │ Claude 主动查找                                │
  │   ⌕ 搜索 “权限模型” · 8 条结果                 │
  │   ▤ 读取 权限模型待定问题                      │
  ╰────────────────────────────────────────────────╯
```

卡片显示相关度最高的 10 个来源：Claude 打开过的文件排在最前，其后按分数排列搜索结果和召回的记忆。其余来源（包括低相关的召回）收在 `[还有 N 条]` 里。同一份文档的多个副本（同名、不同 URI）合并为一行，标为 `×N`。

**卡片详情**有两档：**简略**（默认）只显示标题，**详细**会加上每个来源完整的 `viking://` URI。用 `/openviking-usage card low|high` 或设置中的**卡片详情**切换。

用 `/openviking-usage show conversation` 或 `/openviking-usage show sidebar` 切换显示位置，也可以在侧边栏设置中的**显示位置**切换。选择会跨会话保留。无论哪种方式，`/openviking-usage` 都能随时打开侧边栏。

**状态行**，两种方式下都有：

```
OV · 本次 ✓12 (★1 ◷1 ◆6 ▤3 ⚙1) · 本会话 45 · 写回 1
```

依次是：本次回答按分组统计的来源数、本会话参考过的去重来源数、成功写回 OpenViking 的次数。第一次回答之前显示 `OV · 启动加载 167 条记忆索引`。回答没有参考任何来源时显示 `OV · 本次未参考`。

这些内容都不会发给 Claude，不占用 token。

## 各部分的含义

- **✓ 参考了 N 个 OpenViking 来源**：这次回答时 OpenViking 提供给 Claude 的所有记忆和文档，即自动召回为这条提问加入的内容，加上 Claude 自己读取、搜索返回的内容。这是检索记录，不是根据回答措辞推测的，所以回答里是否注明来源都不影响统计。之前的回答已计入的来源不会重复计数。
- **后面的分组**（只显示数量大于 0 的）：

  | 分组 | 图标 | 包含内容 |
  |---|---|---|
  | 你的偏好 | ★ | 你的画像、身份和偏好 |
  | 你的经历 | ◷ | 你的历史中带日期的事件 |
  | 你的工作记忆 | ◆ ⇄ | 人物、项目、经验教训、案例，以及来自你的 Agent 的记忆 |
  | 团队文档 | ▤ | `viking://resources` 下的文档、代码和页面 |
  | 技能 | ⚙ | 个人或共享的 Skill |
  | 完整打开 | | Claude 完整打开过的来源，而不只是看到搜索结果或召回摘要 |

- **标签**：“自动召回”（附相关度分数）或“主动查找”。低相关的召回也会计入，但默认收起：侧边栏中在 `[展开]` 后面，卡片中在 `[还有 N 条]` 里。
- **Claude 主动查找**：Claude 回答时对 OpenViking 做的每次搜索、读取或写入，附查询词和结果数或打开的文件。在 shell 中只统计真正的 `ov` 命令调用，只是提到 `viking://` 路径的命令不算。
- **[详情]**（侧边栏）：召回诊断信息，包括召回为空的原因、已在上下文中的条目、跳过的空文档、其中关于你的记忆数，以及 Claude 完整打开的数量。
- **之前的回答**（侧边栏）：本次对话中任意一次之前的回答。`✓3` 表示这次回答参考了 3 个 OpenViking 来源。
- **全局**：自动召回为多少次提问带上了记忆，以及 OpenViking 从多少段对话中沉淀了多少次记忆更新。openviking-memory 本身不保留历史，所以这些数据由 OV-Usage 从安装当天开始自行统计。`/openviking-usage clear` 会清零。
- **启动加载**：启动上下文索引了多少个记忆文件；如果你的画像中有标明角色的一行（职业、角色、Role、Job title、Occupation 等），也会显示角色。
- **设置**（侧边栏）：只读。显示自动召回及其阈值、自动记录及其写入记忆的频率（openviking-memory 的 `commitTokenThreshold`，默认每 20,000 token 写入一次）、启动画像、服务地址，以及是否设置了 API Key（从不显示 Key 本身）。显示位置、卡片详情和语言也在这里选择。

如果 OpenViking 配置中关闭了自动召回（`~/.openviking/ovcli.conf` 中的 `"autoRecall": false`），OV-Usage 会隐藏所有与之相关的内容，包括 [详情] 中的召回文件、召回次数和召回耗时。重新打开后会恢复显示。

记忆按类型标注：★ 你的偏好、◷ 你的经历、◆ 你的笔记和经验教训、⇄ 来自你的 Agent、⚙ 技能、▤ 文档。标签可切换中英文（`/openviking-usage lang en|zh|system`）。

## 环境要求

- Claude Code 2.1.286 或更高版本。OV-Usage 基于 Claude Code 的插件 hooks（`hooks.json` 中的 `modules`），这项能力仍处于 early access 阶段。在更早的版本或不支持这类 hooks 的环境中，插件可以安装，但不会显示任何内容。
- OpenViking 记忆插件（`openviking-memory@openviking`），通过 Claude Code 安装并已登录。OV-Usage 通过 Claude Code 的插件注册表找到它。“Claude 主动查找”部分需要启用它的 MCP 工具。
- PATH 中有 `node`。OV-Usage 用它运行 openviking-memory 的配置加载器来读取你的设置。如果读取失败，设置中会显示原因，并注明尝试的 openviking-memory 版本。

OV-Usage 是可选插件。OpenViking 安装脚本不会安装它；除了你自己屏蔽的记忆（见下文），它不会改变 openviking-memory 的召回和记录行为。

## 安装

```bash
claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
claude plugin install ov-usage@openviking
```

如果之前已经添加过这个 marketplace，先运行 `claude plugin marketplace update openviking`。

然后开启一个新的 Claude Code 会话，或者退出并重新打开桌面应用。终端宽度至少 144 列时，侧边栏会自动打开；终端较窄时，运行 `/openviking-usage` 打开，或用 `/openviking-usage show conversation` 改为卡片显示。

## 命令

| 命令 | 作用 |
|---|---|
| `/openviking-usage` | 打开面板 |
| `/openviking-usage history` | 显示全部之前的回答，或只显示最近三条 |
| `/openviking-usage answer 12` | 打开第 12 次回答 |
| `/openviking-usage latest` | 回到最新的回答 |
| `/openviking-usage details` | 在每条记忆下显示分数、层级、token 和 URI，在每次查找下显示工具和 URI |
| `/openviking-usage weak` | 显示或隐藏低相关条目 |
| `/openviking-usage settings` | 打开或关闭设置 |
| `/openviking-usage show conversation` | 以卡片形式显示在对话中，不再自动打开侧边栏 |
| `/openviking-usage card low` | 卡片只显示来源标题（默认） |
| `/openviking-usage card high` | 卡片同时显示每个来源的 `viking://` URI |
| `/openviking-usage show sidebar` | 显示在侧边栏（默认） |
| `/openviking-usage unmute` | 取消本会话屏蔽的所有记忆 |
| `/openviking-usage clear` | 删除所有保存的回答历史、会话数据和全局累计数据 |
| `/openviking-usage lang zh` | 设置面板语言：`en`、`zh` 或 `system` |

面板中的按钮与这些命令作用相同。如果点击没有反应（重启后恢复的面板可能出现这种情况），请改用命令。

## 读取和保存的内容

所有数据都留在你的电脑上，插件不发起任何网络请求。

**读取**
- openviking-memory 自己的文件：它注入的启动上下文（`~/.openviking/last_inject.md`），以及最近一次召回和记录的快照（`$OPENVIKING_HOME/state` 下的 `last-recall.json` 和 `last-capture.json`，默认是 `~/.openviking/state`）。只有属于当前会话的快照才会被使用；召回快照还必须是在你发送提问之后写入的。
- 你的 OpenViking 设置：通过 Claude Code 实际运行的那个 openviking-memory 安装（以 `~/.claude/plugins/installed_plugins.json` 中的记录为准）的配置加载器读取。当前项目范围的安装优先于用户范围的安装。API Key 不会被读入面板，面板只显示是否已设置。
- Claude 调用 OpenViking 工具（搜索、读取、写入）的结果。只收集其中出现的 `viking://` URI。

**保存**，在 Claude Code 的插件存储（`~/.claude/plugins/store/`）中，保留最近 30 个会话：
- 每条提问的前 120 个字符，其中的 key 和 token 会被脱敏。
- 每次回答：OpenViking 召回的记忆的标题、摘要和 URI；Claude 每次查找 OpenViking 时的工具名、查询词（前 80 个字符，已脱敏）和 URI。shell 命令本身从不保存；在 shell 中运行的 `ov` 命令只保留其中的 `viking://` URI。
- Claude 打开过哪些记忆，以及你屏蔽了哪些记忆。
- 卡片定位所需的信息：第一条提问和每次回答最后一个文本块在对话记录中的行 id，以及它们结尾若干长度文本的简短指纹（哈希）。原文不会被保存。
- 搜索结果返回时的相关度分数，用于卡片中的来源排序。

**跨会话保存**，用于**全局**累计数据：自动召回带上记忆的提问次数，以及每个会话最近一次记录的提交次数（按会话 id 保存）。openviking-memory 不保留历史，所以这些数据从安装 OV-Usage 时开始统计。

运行 `/openviking-usage clear` 可以删除以上全部内容。屏蔽一条记忆只会在当前会话中对 Claude 隐藏它，不会改动 OpenViking 中的任何内容。

## 更新

```bash
claude plugin marketplace update openviking
claude plugin update ov-usage@openviking
```

## 卸载

```bash
claude plugin uninstall ov-usage@openviking
```

卸载不会影响 openviking-memory 和 OpenViking 中的任何内容。如果想先删除 OV-Usage 保存的数据，先运行 `/openviking-usage clear`。

## 限制

- OV-Usage 读取的是 openviking-memory 供自身使用的文件（`last_inject.md`、`last-recall.json`、`last-capture.json`），并运行它的 `scripts/config.mjs`。这些不是公开接口。如果以后的 openviking-memory 版本改动了它们，受影响的数字会消失，或者设置中显示错误；回答和记忆本身不受影响。
- **全局**累计数据从安装 OV-Usage 时开始统计，openviking-memory 没有可供统计的历史。
- 屏蔽会把一条记忆从 openviking-memory 加入提问的召回上下文中移除，仅对当前会话有效，不会修改或删除 OpenViking 中的任何内容。
- 面板、卡片和状态行依赖 Claude Code 的插件 hooks，在不支持的环境中没有替代方案。

## 开发

- `hooks/register.tsx`：hooks，以及所有用到 Claude Code 引擎句柄 `$` 的代码：会话状态、读取 openviking-memory 的文件、`/openviking-usage` 命令、侧边栏和卡片。Claude Code 不会跟随 `$` 进入被导入的文件，所以这些代码必须放在 `hooks.json` 指定的模块里。
- `hooks/parse.ts`：纯解析：召回块、启动上下文、`viking://` URI 和脱敏，以及把回答对应到对话记录中某一行的文本指纹。
- `hooks/sources.ts`：判断哪些工具调用是对 OpenViking 的查找或写入（`classifyToolCall`），来源的类型、分组和相关度，以及一次回答参考了哪些来源。
- `hooks/openviking.ts`：openviking-memory 设置和注册表记录的格式，以及跨会话的累计数据。
- `hooks/strings.ts`：中英文标签。
- `types/index.d.ts`：插件保存的数据结构。
- `tests/`：用 `claude plugin test examples/claude-code-usage-plugin` 运行；用 `claude plugin validate examples/claude-code-usage-plugin` 检查插件。

不安装就试用改动：用 `claude --plugin-dir examples/claude-code-usage-plugin` 启动 Claude Code。加载插件时，Claude Code 会把它的 API 类型写入 `.claude-plugin/types/`。这个目录是自动生成的，已被 Git 忽略。目录出现后，可以用 `tsc -p examples/claude-code-usage-plugin` 做类型检查。

这些测试运行在 Claude Code 的插件测试器上，而不是 `node --test`，所以仓库的 Node CI 不会运行它们。在本目录下，`npm test` 运行测试，`npm run check` 校验插件并做类型检查，`npm run format` 按代码风格格式化（Prettier 默认规则，每行 100 列）。
