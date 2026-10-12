<div align="center">

<a href="https://openviking.ai/" target="_blank">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/volcengine/OpenViking/main/docs/images/readme-logo-dark.png">
    <img alt="OpenViking" src="https://raw.githubusercontent.com/volcengine/OpenViking/main/docs/images/readme-logo-light.png" width="300" height="56">
  </picture>
</a>

### AI 智能体的上下文数据库

[English](../../README.md) / 中文 / [日本語](README_JA.md)

<a href="https://www.openviking.ai">官网</a> · <a href="https://openviking.ai/studio">在线体验</a> · <a href="https://github.com/volcengine/OpenViking">GitHub</a> · <a href="https://github.com/volcengine/OpenViking/issues">问题反馈</a> · <a href="https://docs.openviking.ai/">文档</a> · <a href="https://blog.openviking.ai/">博客</a>

<p>
  <a href="https://github.com/volcengine/OpenViking/releases"><img src="https://img.shields.io/github/v/release/volcengine/OpenViking?color=369eff&labelColor=black&logo=github&style=flat-square" alt="release"></a>
  <a href="https://github.com/volcengine/OpenViking"><img src="https://img.shields.io/github/stars/volcengine/OpenViking?labelColor&style=flat-square&color=ffcb47" alt="stars"></a>
  <a href="https://github.com/volcengine/OpenViking/issues"><img src="https://img.shields.io/github/issues/volcengine/OpenViking?labelColor=black&style=flat-square&color=ff80eb" alt="issues"></a>
  <a href="https://github.com/volcengine/OpenViking/graphs/contributors"><img src="https://img.shields.io/github/contributors/volcengine/OpenViking?color=c4f042&labelColor=black&style=flat-square" alt="contributors"></a>
  <a href="https://github.com/volcengine/OpenViking/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-AGPLv3-white?labelColor=black&style=flat-square" alt="license"></a>
  <a href="https://github.com/volcengine/OpenViking/commits/main"><img src="https://img.shields.io/github/last-commit/volcengine/OpenViking?color=c4f042&labelColor=black&style=flat-square" alt="last commit"></a>
</p>

<p>
  <a href="https://railway.com/deploy/openviking"><img src="https://railway.com/button.svg" alt="Deploy on Railway" height="30"></a>
</p>

<p>
  <a href="https://docs.openviking.ai/zh/about/01-about-us#飞书群"><img src="../images/community/lark.svg" width="18" height="18" alt="飞书">&nbsp;飞书</a> ·
  <a href="https://docs.openviking.ai/zh/about/01-about-us#微信群"><img src="../images/community/wechat.svg" width="18" height="18" alt="微信">&nbsp;微信</a> ·
  <a href="https://discord.com/invite/eHvx8E9XF3"><img src="../images/community/discord.svg" width="18" height="18" alt="Discord">&nbsp;Discord</a> ·
  <a href="https://x.com/openvikingai"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/community/x-dark.svg"><img src="../images/community/x.svg" width="16" height="16" alt="X"></picture>&nbsp;X</a>
</p>

<a href="https://trendshift.io/repositories/19668" target="_blank"><img src="https://trendshift.io/api/badge/repositories/19668" alt="volcengine%2FOpenViking | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

</div>

***

## OpenViking 是什么

OpenViking 是面向 AI 智能体的开源上下文数据库——用一个文件系统装下 Agent 所知道的一切：知识、记忆和技能。

大多数 Agent 记忆是个黑盒：文本进去，向量出来，没人看得到里面到底存了什么。OpenViking 换一种做法，把上下文组织成 `viking://` 虚拟文件系统。Agent 像操作文件一样用 `ls`、`tree`、`read`、`write`、`grep` 浏览和修改；你也可以随时打开目录，查看和编辑 Agent 记住的内容。每个目录都带有自动生成的摘要，Agent 先扫摘要，再决定读哪些内容。

<a href="https://openviking.ai/studio" target="_blank" rel="noopener noreferrer">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../images/studio-playground-dark.png">
    <img src="../images/studio-playground.png" alt="OpenViking Studio：浏览上下文，体验语义检索">
  </picture>
</a>

[在线体验 OpenViking Studio](https://openviking.ai/studio)，无需安装。 [自行部署 Web Studio](../../web-studio/README_CN.md)。

## 为什么用 OpenViking

- **一个文件系统，装下知识、记忆和技能。** 资源存放文档和代码，记忆保留用户偏好与经验，技能定义任务的执行方式——不只是抽取出来的"记忆条目"，而是完整上下文，每一项都有 `viking://` URI 供浏览和检索。→ [Viking URI](https://docs.openviking.ai/zh/concepts/04-viking-uri) · [上下文类型](https://docs.openviking.ai/zh/concepts/02-context-types)
- **在目录里检索，而不是在整个索引里捞。** 把语义检索限定在某个项目或记忆子树内，而不是扫描一个扁平的向量池。`find` 直接执行查询，`search` 结合会话上下文规划检索。→ [检索机制](https://docs.openviking.ai/zh/concepts/07-retrieval)
- **先看摘要，再读原文。** 自动生成的目录摘要（L0）和概览（L1）帮助 Agent 判断相关性，再决定是否读取全文（L2）。→ [上下文分层](https://docs.openviking.ai/zh/concepts/03-context-layers)
- **会话沉淀为可读的文件。** 提交会话后，对话被归档，记忆被提取为可查看、可编辑、可合并的 Markdown。启用 VikingBot 后，`ov compile` 还能把资料整理成 Wiki、知识图谱或报告。→ [会话管理](https://docs.openviking.ai/zh/concepts/08-session) · [上下文编译](https://docs.openviking.ai/zh/context-compilation/01-overview)

[架构](https://docs.openviking.ai/zh/concepts/01-architecture) · [设计思路](https://blog.openviking.ai/post/openviking-context-database/)

```
viking://
├── resources/              # 资源：项目文档、代码库、网页等
│   └── my_project/
│       ├── docs/
│       │   ├── api/
│       │   └── tutorials/
│       └── src/
└── user/
    └── {user_id}/
        ├── memories/
        │   └── preferences/
        │       ├── writing_style
        │       └── coding_habits
        ├── resources/
        │   └── private_project/
        ├── skills/
        │   ├── search_code
        │   └── analyze_data
        └── peers/
            └── web-visitor-alice/
```

三个加载层级：

- **L0（摘要）**：一句话总结，用来快速判断相关性。
- **L1（概览）**：核心信息和使用场景，供规划阶段决策。
- **L2（详情）**：完整原始数据，只在需要时读取。

经过语义处理的目录带有 L0/L1 摘要，Agent 可以先判断相关性，再读取全文：

```
viking://resources/my_project/
├── .abstract.md           # L0：约 100 tokens——快速判断相关性
├── .overview.md           # L1：约 2k tokens——结构和要点
└── docs/
    ├── .abstract.md
    ├── .overview.md
    └── api/
        ├── auth.md         # L2：完整内容，按需加载
        └── endpoints.md
```

## 评测结果

OpenViking 0.3.22 的评测覆盖长对话用户记忆（LoCoMo）和多轮智能体任务（tau2-bench）。完整结果和实验设置（含知识库问答）见[评测报告](https://blog.openviking.ai/post/openviking-benchmark-results/)，复现脚本在 [./benchmark](../../benchmark)。

记忆评测使用 [Doubao 2.0 Pro](https://console.volcengine.com/ark/region:cn-beijing/model/detail?Id=doubao-seed-2-0-pro) 作为 VLM，使用 [Doubao-embedding-vision-251215](https://console.volcengine.com/ark/region:cn-beijing/model/detail?Id=doubao-embedding-vision) 作为 Embedding 模型。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/benchmark-dark.svg">
  <img alt="Benchmark results. LoCoMo accuracy: OpenClaw 24.20% native vs 82.08% with OpenViking; Hermes 33.38% vs 82.86%; Claude Code 57.21% vs 80.32%. tau2-bench task success: Retail 70.94% vs 77.81%; Airline 54.38% vs 66.25%." src="../images/benchmark-light.svg">
</picture>

- **用户记忆（LoCoMo）**：接入 OpenViking 后，三种 Agent 集成的准确率都到 80–83%，原生记忆只有 24–57%；同时输入 token 减少 34.3%–91.0%，查询时延降低 58.45%–66.10%。
- **智能体经验（tau2-bench）**：经验记忆让任务成功率在 Retail 提升 6.87pp、Airline 提升 11.87pp（对比同一 LLM 无记忆）。

## 快速开始

先准备一个 OpenViking 服务；已经有服务的话，直接跳到[接入你的 Agent](#接入你的-agent)。自部署需要 uv、Python 3.10+，以及一个提供 embedding 模型和 VLM 的模型服务。

<details open>
<summary><strong>让 agent 帮你部署</strong></summary>

```text
按这份文档，帮我安装并启动 OpenViking Server：

https://docs.openviking.ai/zh/getting-started/04-setup-for-agent

模型提供商、模型、workspace 目录，以及服务要不要对其他机器开放，都先问我，
不要猜。向我要模型的 API key 时，告诉我不想把它贴进对话的话可以怎么给你；
不要复述。

服务启动后，告诉我服务地址，以及有没有开鉴权。
```

agent 会先问你用哪家模型服务，以及它的 API key。

</details>

<details>
<summary><strong>自己动手部署</strong></summary>

安装 OpenViking 并运行配置向导，在向导里配置模型：

```bash
uv tool install openviking --upgrade && openviking-server init
```

`init` 将配置写入 `~/.openviking/ov.conf`，支持火山引擎、OpenAI、Codex OAuth、Kimi、GLM 和本地 Ollama 等选项，详见[配置指南](https://docs.openviking.ai/zh/guides/01-configuration)。服务在前台运行，终端不要关。

</details>

<details>
<summary><strong>使用 OpenViking Service（火山引擎托管）</strong></summary>

同一个 OpenViking 服务，由火山引擎替你运行。前 50 个文件免费。在[火山引擎产品页](https://www.volcengine.com/product/openviking-service?utm_source=github&utm_medium=referral&utm_campaign=readme)开通后，到控制台的「用户管理 → API Key」创建一个 API key。服务地址是 `https://api.vikingdb.cn-beijing.volces.com/openviking`，接入 agent 时要用到它和 API key。

</details>

### 用 CLI 试一试

`openviking` 安装包包含 `ov` CLI。服务运行时，导入代码库并检索：

```bash
ov status
ov add-resource https://github.com/volcengine/OpenViking
# 将 TASK_ID 替换为返回的 task_id；重复查询，直到状态为 completed
ov task status TASK_ID
ov ls viking://resources/
ov tree viking://resources/volcengine -L 2
ov find "what is openviking"
ov grep "openviking" --uri viking://resources/volcengine/OpenViking/docs/zh
```

`ov find` 返回匹配的上下文及其 URI，可继续查看内容。客户端配置（`ov config`）、CLI 独立安装和索引维护，见 [CLI 安装](https://docs.openviking.ai/zh/getting-started/05-cli-setup)。

构建自己的应用，可使用 [Python](../../sdk/python/README_CN.md)、[Go](../../sdk/go/README_CN.md)、[TypeScript](../../sdk/typescript/README_CN.md) SDK 或 [HTTP API](https://docs.openviking.ai/zh/api/01-overview)。

## 接入你的 Agent

把 coding agent 接入 OpenViking，跨会话保留记忆。记忆插件安装脚本支持 Claude Code、Codex、Cursor、TRAE、OpenCode 等，会自动检测本机装了哪些。

<details open>
<summary><strong>让 agent 帮你安装</strong></summary>

```text
帮我安装 OpenViking 记忆插件，运行：

curl -fsSL https://openviking.ai/install | bash -s -- --yes --url <SERVER_URL>

<SERVER_URL> 是我的 OpenViking 服务地址，向我要，不要猜。服务开了鉴权的话，
要一个 user key（root key 读写不了记忆）：先看看有没有已保存的，没有再向我要
（用 --api-key 传入），并告诉我不想把 key 贴进对话时可以怎么给你。
不要复述 key。没开鉴权就加 --api-key ''，免得沿用之前保存的 key。

脚本需要联网并写入主目录，如果被沙箱拦住，请求我批准后在沙箱外运行。

装完告诉我装进了哪些工具、各自的后续步骤，再问我要不要给其他工具也装。
```

准备好服务地址；服务开了鉴权的话，还要它的 API key。

</details>

<details>
<summary><strong>自己在终端安装</strong></summary>

运行安装脚本，按提示勾选工具、选择服务：本机的服务选「自建 / 本地」，OpenViking Service 选「火山引擎 OpenViking 云服务」，其他地址选「自定义 URL」。开了鉴权的话，填 user key，不是 root key。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

</details>

装完后重启 agent。脚本结尾会列出每个工具的后续步骤，例如：

```text
  Claude Code
    下一步： 重启 Claude Code
    ⋮
    验证： 在会话中运行 /openviking-memory:ov
```

Codex 首次启动会停在 `Hooks need review`，选 `Trust all and continue`。

试一下：让它记住你的一个偏好，过一会儿开个新会话再问。记忆在后台整理，刚说完查不到是正常的。

安装脚本需要 macOS 或 Linux、Node.js 18+ 和 curl，不用 sudo。Windows 请使用[桌面客户端](#桌面客户端beta)。

各集成的接入文档：

<table>
<tbody>
<tr>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/zh/agent-integrations/02-claude-code"><img src="../images/integrations/logos/claude-code.png" width="32" height="32" alt=""><br><strong>Claude</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/zh/agent-integrations/04-codex"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/integrations/logos/openai-dark.svg"><img src="../images/integrations/logos/openai.svg" width="32" height="32" alt=""></picture><br><strong>Codex</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/zh/agent-integrations/12-cursor"><img src="../images/integrations/logos/cursor.png" width="32" height="32" alt=""><br><strong>Cursor</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/zh/agent-integrations/13-trae"><img src="../images/integrations/logos/trae.png" width="32" height="32" alt=""><br><strong>TRAE</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/zh/agent-integrations/03-openclaw"><img src="../images/integrations/logos/openclaw.png" width="32" height="32" alt=""><br><strong>OpenClaw</strong></a><br>
<sub>上下文引擎</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/zh/agent-integrations/05-hermes"><img src="../images/integrations/logos/hermes-agent.png" width="32" height="32" alt=""><br><strong>Hermes</strong></a><br>
<sub>内置记忆</sub>
</td>
</tr>
</tbody>
<tbody>
<tr>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/zh/agent-integrations/10-opencode"><img src="../images/integrations/logos/opencode.png" width="32" height="32" alt=""><br><strong>OpenCode</strong></a><br>
<sub>Plugin&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/zh/agent-integrations/11-pi"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/integrations/logos/pi-dark.svg"><img src="../images/integrations/logos/pi.svg" width="32" height="32" alt=""></picture><br><strong>pi</strong></a><br>
<sub>原生扩展</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="../images/agents/zh/deerflow-memory-manager.md"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/integrations/logos/deerflow-dark.svg"><img src="../images/integrations/logos/deerflow.svg" width="32" height="32" alt=""></picture><br><strong>DeerFlow</strong></a><br>
<sub>Plugin&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/zh/agent-integrations/17-dsh"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/integrations/logos/dsh-dark.svg"><img src="../images/integrations/logos/dsh.svg" width="32" height="32" alt=""></picture><br><strong>DSH</strong></a><br>
<sub>Plugin&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="../images/agents/zh/doubao-work.md"><img src="../images/integrations/logos/doubao-work.png" width="32" height="32" alt=""><br><strong>豆包工作</strong></a><br>
<sub>连接器</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/zh/agent-integrations/07-langchain-langgraph"><img src="../images/integrations/logos/langchain.svg" width="32" height="32" alt=""><br><strong>LangChain</strong></a><br>
<sub>工具&nbsp;+&nbsp;存储</sub>
</td>
</tr>
</tbody>
</table>

**通用接入**

<table>
<tr>
<td align="center" valign="bottom" width="50%">
<a href="https://docs.openviking.ai/zh/agent-integrations/15-agent-plugins"><img src="../images/integrations/logos/agent-plugins.svg" width="32" height="32" alt=""><br><strong>Agent&nbsp;Plugins&nbsp;1.0</strong></a>
</td>
<td align="center" valign="bottom" width="50%">
<a href="https://docs.openviking.ai/zh/agent-integrations/06-mcp-clients"><img src="../images/integrations/logos/mcp.svg" width="32" height="32" alt=""><br><strong>MCP&nbsp;客&#8288;户&#8288;端</strong></a>
</td>
</tr>
</table>

详细接入方式请参考 [Integrations](https://openviking.ai/integrations)。

## 桌面客户端（Beta）

桌面客户端是面向 macOS 和 Windows x64 的控制台（Beta），用于配置支持的本地 Agent 接入、查看会话中的召回与捕获事件，并将本地记忆和技能同步到 OpenViking。

下载：

- [macOS Apple Silicon 版（arm64）](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/darwin-arm64/openviking-helper-0.0.19-arm64.dmg)
- [macOS Intel 版（x64）](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/darwin-x64/openviking-helper-0.0.19-x64.dmg)
- [Windows 版（x64）](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/win32-x64/openviking-helper-0.0.19-x64.exe)

## VikingBot

VikingBot 是构建在 OpenViking 之上的 AI 智能体框架：

```bash
pip install "openviking[bot]"
openviking-server --with-bot
ov chat   # 在另一个终端运行
```

官方 Docker 镜像内置 VikingBot，默认随服务器和控制台 UI 一起启动。详情见 [VikingBot 指南](https://docs.openviking.ai/zh/guides/17-vikingbot)。

## 生产部署

开源服务器采用 [AGPLv3](../../LICENSE)，可在自己的环境部署，无需激活码。见[服务器配置](https://docs.openviking.ai/zh/getting-started/03-quickstart-server)和 [Docker 与部署指南](https://docs.openviking.ai/zh/guides/03-deployment)。

服务器支持[账号与用户隔离](https://docs.openviking.ai/zh/concepts/11-multi-tenant)，并可按需启用[资源 ACL](https://docs.openviking.ai/zh/concepts/15-acl)。开放非本机访问前，需配置[身份认证](https://docs.openviking.ai/zh/guides/04-authentication)。

## 商业版本

<table>
<tr>
<td width="50%" valign="top">

<img src="../images/commercial-saas.png" alt="商业化 SaaS 版" width="100%" />

<h3>☁️ 商业化 SaaS 版</h3>
<p>由<a href="https://www.volcengine.com/product/openviking-service?utm_source=github&amp;utm_medium=referral&amp;utm_campaign=readme">火山引擎</a>托管和运维，提供个人版、企业版，以及开源部署的迁移工具。套餐与额度见<a href="https://docs.volcengine.com/docs/84313/2374478">服务文档</a>。中国以外地区的托管服务计划在 <a href="https://www.byteplus.com">BytePlus</a> 上线。</p>

</td>
<td width="50%" valign="top">

<img src="../images/commercial-self-hosted.png" alt="私有化部署版" width="100%" />

<h3>🏢 私有化部署版</h3>
<p>部署在自己的云账号 / VPC（BYOC）或离线环境中，提供分布式部署和官方技术支持，通过激活码启用。<a href="https://my.feishu.cn/share/base/form/shrcnMFqymCd9sq77sLk34Krxoc">咨询私有化部署</a>。</p>

</td>
</tr>
</table>

## 研究

**让 Agent 的记忆随交互演化。** VikingMem 以事件驱动长期记忆的提取、更新与整合，让有状态 Agent 在持续交互中积累可复用的经验。OpenViking 开源了其中的部分核心能力。

> **VikingMem: A Memory Base Management System for Stateful LLM-based Applications**<br>
> Jiajie Fu, Junwen Chen, Mengzhao Wang, Aoxiang He, Maojia Sheng, Xiangyu Ke, Yifan Zhu, and Yunjun Gao.<br>
> arXiv:2605.29640, 2026。已于 2026 年 9 月在 VLDB 2026 完成演讲。<br>
> 📄 [在 arXiv 阅读论文](https://arxiv.org/abs/2605.29640) · [阅读 PDF](https://arxiv.org/pdf/2605.29640)

**让目录结构成为检索上下文。** 这篇论文为 OpenViking 的目录语义检索提供形式化基础、索引设计与实验验证。论文定义了目录范围查询与结构维护操作，并提出 TrieHI，OpenViking 已将其集成，用于在向量排序前确定目录检索范围。文件系统范式由此贯穿组织与检索：Agent 可以在项目或记忆子树内查找证据、保留周边上下文，并随知识演化调整目录结构。

> **Directory-Aware Query and Maintenance in Vector Databases**<br>
> Mengzhao Wang, Zheng Gong, Jingpei Hu, Jiajie Fu, Maojia Sheng, Junwen Chen, and Yifan Zhu.<br>
> arXiv:2606.16903, 2026。已被 ICDE 接收。<br>
> 📄 [在 arXiv 阅读论文](https://arxiv.org/abs/2606.16903) · [阅读 PDF](https://arxiv.org/pdf/2606.16903)

**用更少的 Token 找齐回答所需的证据。** VikingRAG 将语义检索与文档结构结合，按证据缺口展开相关目录片段，核心机制已集成到 OpenViking。论文进一步研究检索轨迹复用与按需升级多轮检索，在保持回答质量的同时减少重复探索。

> **VikingRAG: Accurate and Token-efficient Retrieval-augmented Generation over Structured Documents**<br>
> Peiyuan Gao, Gaoyuan Zhang, Haojie Qin, Yahui Sun, Qianyi Zhang, Yunhao Zhang, Zeyu Wang, and Wei Lu.<br>
> arXiv:2609.11390, 2026。投递中。<br>
> 📄 [在 arXiv 阅读论文](https://arxiv.org/abs/2609.11390) · [阅读 PDF](https://arxiv.org/pdf/2609.11390)

## 合作伙伴

- [deer-flow](https://github.com/bytedance/deer-flow) - 开源的长周期 SuperAgent 框架
- [NoKV](https://github.com/NoKV-Lab/NoKV) - AI 原生的分布式文件系统
- [loopx](https://github.com/huangruiteng/loopx) - 轻量级循环工程状态内核
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) - 与用户共同成长的智能体

合作提议请[提交 issue](https://github.com/volcengine/OpenViking/issues)。

## 社区与贡献

- **文档**：[docs.openviking.ai](https://docs.openviking.ai/) · [FAQ](https://docs.openviking.ai/zh/faq/faq)
- **博客**：[blog.openviking.ai](https://blog.openviking.ai/)
- **团队**：[关于我们](https://docs.openviking.ai/zh/about/01-about-us)
- **交流**：<a href="https://docs.openviking.ai/zh/about/01-about-us#飞书群"><img src="../images/community/lark.svg" width="18" height="18" alt="飞书">&nbsp;飞书</a> · <a href="https://docs.openviking.ai/zh/about/01-about-us#微信群"><img src="../images/community/wechat.svg" width="18" height="18" alt="微信">&nbsp;微信</a> · <a href="https://discord.com/invite/eHvx8E9XF3"><img src="../images/community/discord.svg" width="18" height="18" alt="Discord">&nbsp;Discord</a> · <a href="https://x.com/openvikingai"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/community/x-dark.svg"><img src="../images/community/x.svg" width="16" height="16" alt="X"></picture>&nbsp;X</a>
- **贡献**：修 bug、加新功能都欢迎——见 [CONTRIBUTING_CN.md](CONTRIBUTING_CN.md)

<a href="https://github.com/volcengine/OpenViking/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=volcengine/OpenViking&amp;columns=15&amp;max=120" alt="OpenViking contributors" />
</a>

## 安全与隐私

漏洞报告方式和受支持的版本，见 [SECURITY.md](../../SECURITY.md)

## 许可证

OpenViking 各组件采用不同的许可证：

- **主项目**：AGPLv3——详见 [LICENSE](../../LICENSE)
- **crates/ov\_cli**：Apache 2.0——详见 [LICENSE](../../crates/LICENSE)
- **examples**：Apache 2.0——详见 [LICENSE](../../examples/LICENSE)。`examples/hermes-plugin` 中的 Hermes 插件保留其 [MIT 许可证](../../examples/hermes-plugin/LICENSE)。
- **third\_party**：各三方项目保留其原有协议
