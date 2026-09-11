<div align="center">

<a href="https://openviking.ai/" target="_blank">
  <picture>
    <img alt="OpenViking" src="docs/images/ov-logo.png" width="200px" height="auto">
  </picture>
</a>

### OpenViking：AI 智能体的上下文数据库

[English](README.md) / 中文 / [日本語](README_JA.md)

<a href="https://www.openviking.ai">官网</a> · <a href="https://openviking.ai/studio">在线体验</a> · <a href="https://github.com/volcengine/OpenViking">GitHub</a> · <a href="https://github.com/volcengine/OpenViking/issues">问题反馈</a> · <a href="https://docs.openviking.ai/">文档</a>

[![](https://img.shields.io/github/v/release/volcengine/OpenViking?color=369eff\&labelColor=black\&logo=github\&style=flat-square)](https://github.com/volcengine/OpenViking/releases)
[![](https://img.shields.io/github/stars/volcengine/OpenViking?labelColor\&style=flat-square\&color=ffcb47)](https://github.com/volcengine/OpenViking)
[![](https://img.shields.io/github/issues/volcengine/OpenViking?labelColor=black\&style=flat-square\&color=ff80eb)](https://github.com/volcengine/OpenViking/issues)
[![](https://img.shields.io/github/contributors/volcengine/OpenViking?color=c4f042\&labelColor=black\&style=flat-square)](https://github.com/volcengine/OpenViking/graphs/contributors)
[![](https://img.shields.io/badge/license-AGPLv3-white?labelColor=black\&style=flat-square)](https://github.com/volcengine/OpenViking/blob/main/LICENSE)
[![](https://img.shields.io/github/last-commit/volcengine/OpenViking?color=c4f042\&labelColor=black\&style=flat-square)](https://github.com/volcengine/OpenViking/commits/main)

👋 加入我们的社区

📱 <a href="https://docs.openviking.ai/zh/about/01-about-us#飞书群">飞书群</a> · <a href="https://docs.openviking.ai/zh/about/01-about-us#微信群">微信群</a> · <a href="https://discord.com/invite/eHvx8E9XF3">Discord</a> · <a href="https://x.com/openvikingai">X</a>

<a href="https://trendshift.io/repositories/19668" target="_blank"><img src="https://trendshift.io/api/badge/repositories/19668" alt="volcengine%2FOpenViking | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

</div>

***

## OpenViking 是什么

OpenViking 是面向 AI 智能体的开源上下文数据库，用来存储知识、记住用户，并在会话之间复用经验。

OpenViking 将上下文组织成 `viking://` 虚拟文件系统。Agent 可以像操作文件一样，通过 `ls`、`tree`、`read`、`write` 等操作浏览目录、读取、创建和编辑内容，也可以在目录内检索。目录摘要支持按需加载。

<a href="https://openviking.ai/studio" target="_blank" rel="noopener noreferrer">
  <img src="docs/images/studio-playground.png" alt="OpenViking Studio：浏览上下文，体验语义检索">
</a>

[在线体验 OpenViking Studio](https://openviking.ai/studio)，无需安装。 [自行部署 Web Studio](web-studio/README_CN.md)。

## 为什么用 OpenViking

- **用文件系统组织上下文。** 资源存放文档和代码，记忆保留用户偏好与经验，技能定义任务执行方式。每项上下文都有 `viking://` URI，供 Agent 浏览和检索。→ [Viking URI](https://docs.openviking.ai/zh/concepts/04-viking-uri) · [上下文类型](https://docs.openviking.ai/zh/concepts/02-context-types)
- **按需加载上下文。** 目录摘要（L0）和概览（L1）帮助 Agent 判断何时读取完整内容（L2）。→ [上下文分层](https://docs.openviking.ai/zh/concepts/03-context-layers)
- **沿目录结构检索。** 向量检索先找到候选目录，再探索其中的内容。`find` 直接执行查询，`search` 可以结合会话上下文规划检索。→ [检索机制](https://docs.openviking.ai/zh/concepts/07-retrieval)
- **从会话提取记忆。** 提交 Session 后，会话被归档，后台按记忆策略提取内容，与已有记忆比较后新建、合并或跳过。启用 VikingBot 后，还可用 `ov compile` 配合技能，将资料整理成 Wiki、知识图谱或报告。→ [会话管理](https://docs.openviking.ai/zh/concepts/08-session) · [上下文编译](https://docs.openviking.ai/zh/context-compilation/01-overview)

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

OpenViking 0.3.22 的评测覆盖长对话用户记忆（LoCoMo）和多轮智能体任务（tau2-bench）。完整结果和实验设置（含知识库问答）见[评测报告](https://blog.openviking.ai/post/openviking-benchmark-results/)，复现脚本在 [./benchmark](./benchmark)。

记忆评测使用 [Doubao 2.0 Pro](https://console.volcengine.com/ark/region:cn-beijing/model/detail?Id=doubao-seed-2-0-pro) 作为 VLM，使用 [Doubao-embedding-vision-251215](https://console.volcengine.com/ark/region:cn-beijing/model/detail?Id=doubao-embedding-vision) 作为 Embedding 模型。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/benchmark-dark.svg">
  <img alt="Benchmark results. LoCoMo accuracy: OpenClaw 24.20% native vs 82.08% with OpenViking; Hermes 33.38% vs 82.86%; Claude Code 57.21% vs 80.32%. tau2-bench task success: Retail 70.94% vs 77.81%; Airline 54.38% vs 66.25%." src="docs/images/benchmark-light.svg">
</picture>

- **用户记忆（LoCoMo）**：接入 OpenViking 后，三种 Agent 集成的准确率都到 80–83%，原生记忆只有 24–57%；同时输入 token 减少 34.3%–91.0%，查询时延降低 58.45%–66.10%。
- **智能体经验（tau2-bench）**：经验记忆让任务成功率在 Retail 提升 6.87pp、Airline 提升 11.87pp（对比同一 LLM 无记忆）。

## 快速开始

需要 Python 3.10+，以及可调用的 Embedding 模型和 VLM（云端或本地）。

```bash
pip install openviking --upgrade
openviking-server init      # 配置模型与提供商
openviking-server doctor    # 检查配置与连通性
openviking-server           # 启动服务器
```

`init` 将配置写入 `~/.openviking/ov.conf`，支持火山引擎、OpenAI、Codex OAuth、Kimi、GLM 和本地 Ollama 等选项。模型配置见[配置指南](https://docs.openviking.ai/zh/guides/01-configuration)，各平台安装说明见[快速入门文档](https://docs.openviking.ai/zh/getting-started/02-quickstart)。

安装包包含 `ov` CLI。在另一个终端导入代码库并检索：

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

构建自己的应用，可使用 [Python](sdk/python/README_CN.md)、[Go](sdk/go/README_CN.md)、[TypeScript](sdk/typescript/README_CN.md) SDK 或 [HTTP API](https://docs.openviking.ai/zh/api/01-overview)。

## 接入你的 Agent

将 Agent 接入 OpenViking，跨会话保留记忆。原生集成支持自动召回与会话采集；也可通过 MCP 提供记忆和上下文工具。

<table>
<tr>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/02-claude-code"><img src="docs/images/agents/image/claude-code/logo.png" width="32" height="32" alt=""><br><strong>Claude</strong></a><br>
<sub>Hooks + MCP</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/04-codex"><img src="docs/images/integrations/codex.png" width="32" height="32" alt=""><br><strong>Codex</strong></a><br>
<sub>Hooks + MCP</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/12-cursor"><img src="docs/images/agents/image/cursor/logo.png" width="32" height="32" alt=""><br><strong>Cursor</strong></a><br>
<sub>Hooks + MCP</sub>
</td>
</tr>
<tr>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/13-trae"><img src="docs/images/agents/image/trae/logo.png" width="32" height="32" alt=""><br><strong>TRAE</strong></a><br>
<sub>Hooks + MCP</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/03-openclaw"><img src="docs/images/integrations/openclaw.jpg" width="24" height="24" alt=""><br><strong>OpenClaw</strong></a><br>
<sub>上下文引擎</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/05-hermes"><img src="docs/images/integrations/hermes-agent.png" width="24" height="24" alt=""><br><strong>Hermes</strong></a><br>
<sub>内置记忆</sub>
</td>
</tr>
<tr>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/10-opencode"><img src="docs/images/agents/image/opencode/logo.png" width="32" height="32" alt=""><br><strong>OpenCode</strong></a><br>
<sub>Plugin + MCP</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/11-pi"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/integrations/pi-dark.svg"><img src="docs/images/integrations/pi.svg" width="41" height="41" alt=""></picture><br><strong>pi</strong></a><br>
<sub>原生扩展</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="docs/images/agents/zh/deerflow-memory-manager.md"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/integrations/deerflow-dark.svg"><img src="docs/images/integrations/deerflow.svg" width="19" height="24" alt=""></picture><br><strong>DeerFlow</strong></a><br>
<sub>Plugin + MCP</sub>
</td>
</tr>
</table>

**通用接入**

<table>
<tr>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/15-agent-plugins"><img src="docs/images/integrations/agent-plugins.svg" width="24" height="23" alt=""><br><strong>Agent&nbsp;Plugins&nbsp;1.0</strong></a>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/06-mcp-clients"><img src="docs/images/integrations/mcp.svg" width="24" height="24" alt=""><br><strong>MCP&nbsp;客&#8288;户&#8288;端</strong></a>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/07-langchain-langgraph"><img src="docs/images/integrations/langchain.svg" width="24" height="24" alt=""><br><strong>LangChain</strong></a>
</td>
</tr>
</table>

详细接入方式请参考 [Integrations](https://openviking.ai/integrations)。

## OpenViking Helper（Beta）

OpenViking Helper 是面向 macOS 和 Windows x64 的桌面控制台（Beta），用于配置支持的本地 Agent 接入、查看会话中的召回与捕获事件，并将本地记忆和技能同步到 OpenViking。

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

开源服务器采用 [AGPLv3](LICENSE)，可在自己的环境部署，无需激活码。见[服务器配置](https://docs.openviking.ai/zh/getting-started/03-quickstart-server)和 [Docker 与部署指南](https://docs.openviking.ai/zh/guides/03-deployment)。

服务器支持[账号与用户隔离](https://docs.openviking.ai/zh/concepts/11-multi-tenant)，并可按需启用[资源 ACL](https://docs.openviking.ai/zh/concepts/15-acl)。开放非本机访问前，需配置[身份认证](https://docs.openviking.ai/zh/guides/04-authentication)。

## 商业版本

<table>
<tr>
<td width="50%" valign="top">

<img src="docs/images/commercial-saas.png" alt="商业化 SaaS 版" width="100%" />

<h3>☁️ 商业化 SaaS 版</h3>
<p>由<a href="https://www.volcengine.com/product/openviking-service">火山引擎</a>托管和运维，提供个人版、企业版，以及开源部署的迁移工具。套餐与额度见<a href="https://docs.volcengine.com/docs/84313/2374478">服务文档</a>。中国以外地区的托管服务计划在 <a href="https://www.byteplus.com">BytePlus</a> 上线。</p>

</td>
<td width="50%" valign="top">

<img src="docs/images/commercial-self-hosted.png" alt="私有化部署版" width="100%" />

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
- **交流**：📱 [飞书群](https://docs.openviking.ai/zh/about/01-about-us#飞书群) · 💬 [微信群](https://docs.openviking.ai/zh/about/01-about-us#微信群) · 🎮 [Discord](https://discord.com/invite/eHvx8E9XF3) · 🐦 [X](https://x.com/openvikingai)
- **贡献**：修 bug、加新功能都欢迎——见 [CONTRIBUTING_CN.md](CONTRIBUTING_CN.md)

## 安全与隐私

漏洞报告方式和受支持的版本，见 [SECURITY.md](SECURITY.md)

## 许可证

OpenViking 各组件采用不同的许可证：

- **主项目**：AGPLv3——详见 [LICENSE](./LICENSE)
- **crates/ov\_cli**：Apache 2.0——详见 [LICENSE](./crates/LICENSE)
- **examples**：Apache 2.0——详见 [LICENSE](./examples/LICENSE)
- **third\_party**：各三方项目保留其原有协议
