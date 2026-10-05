# OV-Usage

[English](./README.md) / 中文

OV-Usage 在 Claude Code 每次回答下方加一张卡片，列出这次回答用到的 OpenViking 来源，包括自动召回加进提示的内容，以及 Claude 自己搜索、读取的内容。

卡片默认折叠，只占一行：

```
OV · 14 sources · 6 past events · 5 work memories · 2 team docs · 1 skill · 1 read in full  [Expand]
```

展开后列出全部来源和 Claude 自己的查询：

```
OV · 14 sources · … · 1 read in full  [Collapse]
  ◷ 10/3 发版检查清单补充 · found by Claude · read in full
  ⚙ openviking-release · auto-recalled
  ▤ lark-daemon · auto-recalled
  …
Claude's own lookups
  ⌕ Searched “lark-daemon release” · 6 results
  ▤ Read 10/3 发版检查清单补充
```

- 分组：★ 偏好，◷ 过往事件，◆ 工作记忆（笔记、经验、你的 agent 的记忆），▤ 团队文档，⚙ Skill。
- **read in full**：Claude 用 `read`（MCP 工具或 `ov read`）打开了这个文件。只出现在搜索结果里的不算。
- 没用到任何 OpenViking 内容的回答不显示卡片。

用卡片上的 `[Expand]` / `[Collapse]` 按钮展开或折叠单张卡片；也可以用 `/openviking-usage expand` 或 `/openviking-usage collapse` 一次展开或折叠全部卡片。

界面文字只有英文。

## 前提

- Claude Code 2.1.286 或更高版本。插件依赖 Claude Code 的插件 hooks（`hooks.json` 里的 `modules`），目前仍是 early access。没有这个能力时，插件能装上，但不显示任何内容。
- 已安装 OpenViking 记忆插件（`openviking-memory@openviking`）。

## 安装

```bash
claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
claude plugin install ov-usage@openviking
```

如果之前已经添加过这个 marketplace，先运行 `claude plugin marketplace update openviking`。装好后开一个新会话。

## 读取和保存的内容

- **自动召回**：openviking-memory 的 `UserPromptSubmit` hook 加进提示的 `<openviking-context>` 块。读取每个 `<memory>` 的 `uri` 和 `score`；digest 格式则读取每行里的 `viking://` URI。不读 openviking-memory 的任何文件或配置。如果 openviking-memory 改了这个块的格式，自动召回的条目就不会再显示。
- **Claude 的查询**：OpenViking MCP 工具调用（`read`、`search`、`find`、`grep`、`glob`、`list`、`tree`），以及通过 Bash 调用的 `ov` CLI（`read`、`cat`、`abstract`、`overview`、`find`、`search`、`grep`、`glob`、`ls`、`tree`）。写入和其他命令不计入。
- **保存**在 Claude Code 的插件存储里，保留最近 20 个会话：每次回答的来源 URI 和分数、搜索词（已脱敏，最多 80 字符）、卡片所在的对话行 id，这样 `claude --continue` 之后卡片还在原位。不保存 shell 命令和提示原文。

插件不发网络请求，不向 OpenViking 写任何内容。卡片不会发给 Claude，不消耗 token。

## 开发

- `hooks/register.tsx`：hooks 和卡片。
- `hooks/sources.ts`：纯解析逻辑，包括召回块、工具输出里的 URI、哪些工具调用算查询，以及一次回答用到了什么。
- `tests/`：`claude plugin test examples/claude-code-usage-plugin`。这些测试跑在 Claude Code 的插件测试器上，不在仓库的 Node CI 里。
- `claude plugin validate examples/claude-code-usage-plugin` 校验插件。用 `claude --plugin-dir examples/claude-code-usage-plugin` 启动一次、生成 `.claude-plugin/types/` 后，可以用 `tsc -p examples/claude-code-usage-plugin` 做类型检查。
