# 一条提示词接入 Agent

把一条提示词粘贴给你正在用的编程 Agent。Agent 会在本机检测到的各个 Agent 里安装 OpenViking 记忆插件，检查连接，问你要不要导入近期工作，再告诉你怎么重启。重启后，你在新会话里问的第一个问题就能用上 OpenViking。

Agent 按一份简短的指南执行。指南以纯 Markdown 发布，任何 Agent 都能直接获取：

- 中文：`https://docs.openviking.net/agents/zh/onboarding.md`
- 英文：`https://docs.openviking.net/agents/en/onboarding.md`

两份也都可以从 `docs.openviking.ai` 访问。中国大陆访问 `docs.openviking.net` 通常更快。

## 提示词

复制提示词，发送前把 `<API_KEY>` 换成你的 key，在 [OpenViking 控制台](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing?utm_source=opensource_docs&utm_medium=referral&utm_campaign=getting-started_07-agent-onboarding)的**用户管理 → API Key** 中获取。

<AgentPrompt>

```text
帮我安装 OpenViking 记忆插件，并带我开始使用。

1. 运行官方安装器。它需要联网并写入我的主目录，如果被你的沙箱拦住，请向我申请批准：
   curl -fsSL https://openviking.ai/install | bash -s -- --yes --lang zh --url https://api.vikingdb.cn-beijing.volces.com/openviking --api-key <API_KEY>
2. 然后按 OpenViking 官方接入引导继续（打不开就把 docs.openviking.net 换成 docs.openviking.ai）：
   https://docs.openviking.net/agents/zh/onboarding.md

命令里的 API key 原样使用，但不要在回复里复述它，也不要把它写进任何文件或规则。全程用中文和我交流。
```

</AgentPrompt>

安装器会给本机检测到的所有受支持的 Agent 都装上插件，所以你装过的其他 Agent 也会装上；每个 Agent 都要各自重启后，插件才会在里面生效。安装器还会同时安装 `ov` 命令，Agent 要用它检查连接、添加文件。`--lang` 设置安装器的语言，安装器最后打印的引导链接也随之选择语言。想只装其中几个，加上 `--harness` 并用逗号分隔列出，例如 `--harness claude,cli`，详见[统一安装器](../agent-integrations/16-capability-reference.md#_3-1-2-统一安装器)。

**自部署服务。** 把 URL 换成你自己服务的地址。要用 user key，root key 不能读写记忆。本地服务没有开启鉴权时，传 `--api-key ''`。

**关于 key。** 安装器只能通过命令行参数接收 key，所以 key 会出现在这段对话里，也会留在 Agent 本地的会话记录和日志中。指南要求 Agent 不复述 key、不把它写进任何地方，并在重启后让你开新会话，而不是恢复或重新加载这段对话。插件目前还不会从保存的对话里删除 key，所以在部分 Agent 上（例如 Codex 退出后旧会话仍在后台运行），这段安装对话连同 key 仍可能被保存到你自己的 OpenViking 账号里。如果你不想把 key 贴进聊天，可以自己在终端运行 `curl -fsSL https://openviking.ai/install | bash`，再把[已经装好](#已经装好)一节的提示词发给 Agent。

## 已经装好

如果插件已经装好，在 Agent 里开一个新会话，发送：

```text
按 https://docs.openviking.net/agents/zh/onboarding.md 带我完成 OpenViking 首次使用。
```

## Agent 会做什么

1. **检查安装结果。** 读取安装器的输出并运行 `ov health`，告诉你装进了哪些 Agent、连的是哪个服务。
2. **说明哪些事会自动发生。** 每次提问前，插件会召回相关记忆；每轮对话结束后，插件保存对话，服务端在后台提取长期记忆。这一步不需要你另外批准，请求安装本身就是同意。
3. **问你要不要导入近期工作。** 导入读取的是 Claude Code 和 Codex 的历史，默认导入当前项目最近 14 天的会话。Agent 会先告诉你会话数和消息数，你确认后才导入。只发送用户和助手的文字，工具调用及其输出会被丢弃，API key 会被删除。在其他 Agent 上，只有本机存在 Claude Code 或 Codex 的历史时才会提议；这些 Agent 自己的历史暂不支持导入。你也可以点名要保存的文件，例如设计文档，Agent 用 `ov add-resource` 添加。
4. **在部分 Agent 上提议一条规则。** TRAE、TRAE CN、ZCode 和 Kimi Code 的插件不带 OpenViking skill，Agent 会提议在你的个人规则里加一条简短规则，告诉它什么时候该去 OpenViking 检索。Kimi Code 上 Agent 会把规则加到 `~/.kimi-code/AGENTS.md`；其他几个会请你粘贴到应用的规则设置里。它不会把规则写进项目文件。
5. **告诉你怎么重启。** hook 只在新会话里加载。Agent 会给出你正在对话的这个 Agent 的具体操作（例如 Codex 里选 **Trust all and continue**），以及重启后要发的第一句话。安装器如果还装进了其他 Agent，它会告诉你那些也需要重启。
6. **让你看到 OpenViking 在起作用。** 在新会话里，你的第一个问题会基于召回的上下文回答，Agent 会告诉你在哪里能看到：Claude Code 的来源卡片、Codex 的 OV-Usage 行，等等。

整个流程最多问你两个问题：导入什么（看到数量后再确认一次），以及在部分 Agent 上要不要加规则。

## 另请参阅

- [Agent 接入概览](../agent-integrations/01-overview.md)
- [Agent 集成能力](../agent-integrations/16-capability-reference.md)
- [安装和使用 CLI](05-cli-setup.md)
