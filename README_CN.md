<div align="center">

<a href="https://openviking.ai/" target="_blank">
  <picture>
    <img alt="OpenViking" src="docs/images/ov-logo.png" width="120" height="auto">
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

</div>

***

## OpenViking 是什么

OpenViking 是面向 AI 智能体的开源上下文数据库，用来存储知识、记住用户，并在会话之间复用经验。

上下文存放在 `viking://` 虚拟文件系统中。Agent 可以用 `ls`、`tree` 浏览目录，在项目或记忆目录内检索，按需读取详情。目录摘要帮助 Agent 先选择上下文，再加载全文。

[快速开始](#快速开始) · [Agent 接入](#接入你的-agent) · [SDK 与工具](#基于-openviking-构建应用) · [部署](#生产部署)

[![OpenViking Studio：浏览上下文，体验语义检索](docs/images/studio-playground.png)](https://openviking.ai/studio)

[在线体验 OpenViking Studio](https://openviking.ai/studio)，无需安装。

## 核心概念

### 资源、记忆与技能

| 上下文 | 存什么 | URI 示例 |
| --- | --- | --- |
| **资源** | Agent 可引用的文档、代码库和网页 | `viking://resources/project/` |
| **记忆** | 从会话中提取的用户偏好、事实和经验 | `viking://~/memories/` |
| **技能** | Agent 执行任务所需的指令与配套文件 | `viking://~/skills/` |

`viking://~` 指向当前用户的主目录。资源可以在账号内共享；记忆和会话归属于各自的用户。见[上下文类型](https://docs.openviking.ai/zh/concepts/02-context-types)和 [URI 命名空间](https://docs.openviking.ai/zh/concepts/04-viking-uri)。

### 分层加载上下文

经过语义处理的目录，在内容旁存放摘要和概览：

```text
viking://resources/project/
├── .abstract.md     # L0：摘要，用于判断相关性
├── .overview.md     # L1：概览与导航
├── api.md           # L2：完整内容
└── examples/
```

L0 和 L1 描述目录，不会为每个文件各生成一份。Agent 据此决定往哪里查、何时读取 L2。见[上下文分层](https://docs.openviking.ai/zh/concepts/03-context-layers)。

### 沿目录结构检索

向量检索先找到候选目录，再探索目录中的内容。`find` 直接执行查询；`search` 可以结合会话上下文规划检索。指定目标 URI，即可将查询限定在项目或记忆子树内。见[检索机制](https://docs.openviking.ai/zh/concepts/07-retrieval)。

结果带有来源 URI；可选的[检索遥测](https://docs.openviking.ai/zh/api/06-retrieval)和[运行状态观测](https://docs.openviking.ai/zh/api/18-observer)用于排查检索与处理问题。

### 从会话提取记忆

Session 记录消息和上下文使用情况。提交后，会话被归档，后台开始提取记忆：将候选内容与已有记忆比较，再新建、合并或跳过，供后续会话检索。记忆策略控制提取哪些类型，包括用户偏好和 Agent 经验。见[会话管理](https://docs.openviking.ai/zh/concepts/08-session)。

[架构](https://docs.openviking.ai/zh/concepts/01-architecture) · [设计思路](https://blog.openviking.ai/post/openviking-context-database/)

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

## 接入你的 Agent

将 Agent 接入 OpenViking，跨会话保留记忆。原生集成支持自动召回与会话采集；也可通过 MCP 提供记忆和上下文工具。

<table>
<tr>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/02-claude-code"><img src="docs/images/agents/image/claude-code/logo.png" width="32" height="32" alt=""><br><strong>Claude Code</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/04-codex"><img src="docs/images/integrations/codex.png" width="32" height="32" alt=""><br><strong>Codex</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/12-cursor"><img src="docs/images/agents/image/cursor/logo.png" width="32" height="32" alt=""><br><strong>Cursor</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
</tr>
<tr>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/13-trae"><img src="docs/images/agents/image/trae/logo.png" width="32" height="32" alt=""><br><strong>TRAE</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
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
<sub>Plugin&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/zh/agent-integrations/11-pi"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/integrations/pi-dark.svg"><img src="docs/images/integrations/pi.svg" width="41" height="41" alt=""></picture><br><strong>pi</strong></a><br>
<sub>原生扩展</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="docs/images/agents/zh/deerflow-memory-manager.md"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/integrations/deerflow-dark.svg"><img src="docs/images/integrations/deerflow.svg" width="19" height="24" alt=""></picture><br><strong>DeerFlow</strong></a><br>
<sub>Memory&nbsp;+&nbsp;MCP</sub>
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

## 基于 OpenViking 构建应用

| 工具 | 用途 |
| --- | --- |
| [Python](sdk/python/README_CN.md)、[Go](sdk/go/README_CN.md)、[TypeScript](sdk/typescript/README_CN.md) SDK · [HTTP API](https://docs.openviking.ai/zh/api/01-overview) | 为应用接入上下文存储、检索和会话管理 |
| [上下文编译](https://docs.openviking.ai/zh/context-compilation/01-overview) | 用 `ov compile` 配合技能，将资料整理成 Wiki、知识图谱或报告；需要启用 VikingBot |
| [Web Studio](web-studio/README_CN.md) | 在 Web 控制台浏览上下文、执行语义检索并使用 Agent |

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

### 托管 SaaS

由[火山引擎](https://www.volcengine.com/product/openviking-service)托管和运维，提供个人版、企业版，以及开源部署的迁移工具。套餐与额度见[服务文档](https://docs.volcengine.com/docs/84313/2374478)。中国以外地区的托管服务计划在 [BytePlus](https://www.byteplus.com) 上线。

### 企业私有化部署

部署在自己的云账号 / VPC（BYOC）或离线环境中，提供分布式部署和官方技术支持，通过激活码启用。[咨询私有化部署](https://my.feishu.cn/share/base/form/shrcnMFqymCd9sq77sLk34Krxoc)。

## 研究

VikingMem 研究事件驱动的记忆提取、更新与整合，OpenViking 实现了其中部分能力。

> **VikingMem: A Memory Base Management System for Stateful LLM-based Applications**<br>
> Jiajie Fu, Junwen Chen, Mengzhao Wang, Aoxiang He, Maojia Sheng, Xiangyu Ke, Yifan Zhu, and Yunjun Gao.<br>
> arXiv:2605.29640, 2026。已于 2026 年 9 月在 VLDB 2026 完成演讲。<br>
> 📄 [在 arXiv 阅读论文](https://arxiv.org/abs/2605.29640) · [阅读 PDF](https://arxiv.org/pdf/2605.29640)

目录感知检索利用文档结构限定查询范围。OpenViking 集成了论文中的 TrieHI 索引，在向量排序前确定目录范围。

> **Directory-Aware Query and Maintenance in Vector Databases**<br>
> Mengzhao Wang, Zheng Gong, Jingpei Hu, Jiajie Fu, Maojia Sheng, Junwen Chen, and Yifan Zhu.<br>
> arXiv:2606.16903, 2026。已被 ICDE 接收。<br>
> 📄 [在 arXiv 阅读论文](https://arxiv.org/abs/2606.16903) · [阅读 PDF](https://arxiv.org/pdf/2606.16903)

VikingRAG 将语义检索与文档结构结合，核心机制已集成到 OpenViking。论文还研究了检索轨迹复用和按需多轮检索。

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
