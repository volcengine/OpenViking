<a id="一条提示词接入-agent"></a>

<a id="一键集成-agent"></a>

# 一键接入

通过一条提示词，将 OpenViking 接入你正在使用的编程 Agent。Agent 会在本机检测到的各个 Agent 里安装 OpenViking 记忆插件，检查连接，问你要不要导入近期工作，再告诉你怎么重启。重启后，你问的第一个问题就能用上 OpenViking。

Agent 按一份简短的指南执行。指南以纯 Markdown 发布，任何 Agent 都能直接获取：

- 中文：`https://docs.openviking.ai/agents/zh/onboarding.md`
- 英文：`https://docs.openviking.ai/agents/en/onboarding.md`

两份也都可以从 `docs.openviking.net` 访问，中国大陆访问通常更快。

## 提示词

复制提示词，发送前把 `<API_KEY>` 换成你的 key，在 [OpenViking 控制台](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing?utm_source=opensource_docs&utm_medium=referral&utm_campaign=getting-started_07-agent-onboarding)的**用户管理 → API Key** 中获取。

<AgentPrompt>

```text
帮我安装 OpenViking 记忆插件，并带我开始使用。OpenViking 是为 Agent 提供的上下文数据库（https://github.com/volcengine/OpenViking），下面的命令和链接来自它的官方文档。

1. 运行官方安装器。它需要联网并写入我的主目录，如果被你的沙箱拦住，请向我申请批准：
   curl -fsSL https://openviking.ai/install | bash -s -- --yes --lang zh --url https://api.vikingdb.cn-beijing.volces.com/openviking --api-key <API_KEY>
2. 装好后读 OpenViking 的接入引导，按它带我完成设置。导入历史、写协作规则这类选择，引导会让你先问我。docs.openviking.ai 和 docs.openviking.net 是同一个文档站，哪个打得开用哪个：
   https://docs.openviking.ai/agents/zh/onboarding.md

API key 是我在 OpenViking 控制台给自己生成的，安装器会把它存到本机的 ~/.openviking/ovcli.conf。命令里原样使用就行，回复里不用再显示它，也不要写进别的文件或规则。全程用中文和我交流。
```

</AgentPrompt>

安装器会给本机检测到的所有受支持的 Agent 都装上插件，所以你装过的其他 Agent 也会装上；每个 Agent 都要各自重启后，插件才会在里面生效。安装器还会同时安装 `ov` 命令，Agent 要用它检查连接、添加文件。`--lang` 设置安装器的语言，安装器最后打印的引导链接也随之选择语言。想只装其中几个，加上 `--harness` 并用逗号分隔列出，例如 `--harness claude,cli`，详见[统一安装器](../agent-integrations/16-capability-reference.md#_3-1-2-统一安装器)。

**自部署服务。** 把 URL 换成你自己服务的地址。要用 user key，root key 不能读写记忆。本地服务没有开启鉴权时，传 `--api-key ''`。

**关于 key。** 安装器只能通过命令行参数接收 key，所以 key 会出现在这段对话里，也会留在 Agent 本地的会话记录和日志中。指南要求 Agent 不复述 key、不把它写进任何地方。插件目前还不会从保存的对话里删除 key，重启后的第一次保存会把安装对话也存进去：在 Codex 上，不管回到原对话还是开新会话都会这样，这段安装对话连同 key 可能被保存到你自己的 OpenViking 账号里；在 Claude Code 上开新会话可以避免，所以 Agent 会请你不要回到安装对话。如果你不想把 key 贴进聊天，可以自己在终端运行 `curl -fsSL https://openviking.ai/install | bash`，再把[已经装好](#已经装好)一节的提示词发给 Agent。

## 已经装好

如果插件已经装好，在 Agent 里开一个新会话，发送：

```text
按 https://docs.openviking.ai/agents/zh/onboarding.md 带我完成 OpenViking 首次使用。
```

## Agent 会做什么

1. **检查安装结果。** 读取安装器的输出并运行 `ov health`，告诉你已经连上、插件装进了哪些 Agent；装进了不止一个时，它们用的是同一份上下文。
2. **说明哪些事会自动发生。** 每次提问前，插件会召回相关记忆；每轮对话结束后，插件保存对话，服务端在后台提取长期记忆。这一步不需要你另外批准，请求安装本身就是同意。
3. **问你要不要带入已有工作。** 默认导入当前项目最近 14 天的会话。你也可以描述某一类工作，例如「和支付重构有关的讨论」：Agent 会列出候选会话和每个会话的摘要，挑出相符的，先给你看挑选结果，再上传。Claude Code 和 Codex 的历史由导入脚本直接读取；其他 Agent 会先写一个小脚本把自己的会话转成 JSONL，再交给同一个导入脚本。Agent 会先告诉你找到了哪些工作、有多大，你确认后才导入；已经在 OpenViking 里的会话不会重复上传。导入后，Agent 会马上从 OpenViking 读回这项工作做到哪、定过什么，让你在重启前就看到导入的用处。只发送用户和助手的文字，工具调用及其输出会被丢弃，API key 会被删除。本机有 Claude Code 或 Codex 的历史时，在其他 Agent 上也会一并提议导入。你也可以点名要保存的文件，例如设计文档，Agent 用 `ov add-resource` 把它添加到你个人资源里这个项目的目录。
4. **提议一套长期协作规则。** Agent 会提议在你的用户级规则文件（例如 Claude Code 的 `~/.claude/CLAUDE.md`、Codex 的 `~/.codex/AGENTS.md`）里加一段规则，对你所有项目生效：先用 OpenViking 召回的上下文，不够再检索；把重要决定（连同理由）、阶段进展和下一步，以及方案、调研、交付物等工作产出主动存进 OpenViking，放在你个人资源下每个项目一个的目录里（`resources/<项目名>/`，进展和决定记在其中的 `notes/`）；继续一个项目时先看之前存下的内容；用到或存了东西会简短告诉你。你也可以让它按你的角色（产品、研发、市场、设计等）调整，补上这个角色还要保存的内容。规则放在一对 `<!-- openviking:start -->` / `<!-- openviking:end -->` 标记之间，文件里原有的内容不动；你同意才写，写完会读回确认。个人规则只能在应用设置里改的 Agent（例如 TRAE、Cursor），会请你自己粘贴进去。它不会把规则写进项目文件。
5. **告诉你怎么重启。** 插件要在 Agent 重新启动后才会加载。Agent 会给出你正在对话的这个 Agent 的具体操作：Codex 里先信任 OpenViking 的 hooks（CLI 在会话里输入 `/hooks`，桌面版点输入框下方的 hooks 图标），再退出、回到同一段对话（CLI 运行 `codex resume --last`），说一句「已重启」，如果仍出现「Hooks need review」，选 **Trust all and continue**；Claude Code 和其他 Agent 开一个新会话，发它给你的第一句话。安装器如果还装进了其他 Agent，它会告诉你那些也需要重启。
6. **让你看到 OpenViking 在起作用。** 重启之后，你的第一个问题会基于召回的上下文回答，Agent 会告诉你在哪里能看到：Claude Code 的来源卡片、Codex 的 OV-Usage 行，等等。

Agent 只在需要你做决定时提问：带入哪些工作（看到数量后再确认一次），以及要不要写入协作规则；每次都会先讲清对你有什么用、会发送或改动什么。已经选过的不会重复问。

## 另请参阅

- [Agent 接入概览](../agent-integrations/01-overview.md)
- [Agent 集成能力](../agent-integrations/16-capability-reference.md)
- [安装和使用 CLI](05-cli-setup.md)
