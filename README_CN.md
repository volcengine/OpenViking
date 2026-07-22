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

OpenViking 是面向 AI 智能体的开源上下文数据库。记忆、资源、技能统一存放在 `viking://` 协议下的虚拟文件系统里，智能体用 `ls`、`tree`、`find` 浏览自己的上下文，不必去查一个黑盒向量库。内容写入时会处理成三层——L0 摘要、L1 概览、L2 详情——按需加载。每次检索都留下轨迹，可以查看，也可以调试。完整介绍见[入门文档](https://docs.openviking.ai/zh/getting-started/01-introduction)。

[![OpenViking Studio playground](docs/images/studio-playground.png)](https://openviking.ai/studio)

*[OpenViking Studio](https://openviking.ai/studio) 实验场——在线 Demo，打开浏览器就能试，无需安装。*

## 为什么用 OpenViking

- **一个文件系统装下所有上下文。** 记忆、资源、技能各有一个 `viking://` URI。智能体像开发者操作文件一样，确定地定位和操作上下文。→ [Viking URI](https://docs.openviking.ai/zh/concepts/04-viking-uri) · [上下文类型](https://docs.openviking.ai/zh/concepts/02-context-types)
- **分层加载省 token。** 每条内容写入时生成 L0（摘要）、L1（概览）、L2（详情）三层，任务需要多深就加载多深。→ [上下文分层](https://docs.openviking.ai/zh/concepts/03-context-layers)
- **目录递归检索。** 向量检索先定位得分最高的目录，再逐层向下探索，结果连同周边上下文一起返回。→ [检索机制](https://docs.openviking.ai/zh/concepts/07-retrieval)
- **检索过程可观察。** 每次查询都保留目录浏览轨迹。结果不对时，能看到它出自哪条路径。→ [检索机制](https://docs.openviking.ai/zh/concepts/07-retrieval)
- **会话沉淀为记忆。** 会话提交后，OpenViking 异步提取用户偏好和智能体经验，写入长期记忆。→ [会话管理](https://docs.openviking.ai/zh/concepts/08-session)

各部分如何配合：见[架构](https://docs.openviking.ai/zh/concepts/01-architecture)。设计思路：[The Database Paradigm for Context Engineering](https://blog.openviking.ai/post/openviking-context-database/)（页内可切换中文）。

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

每个目录都带自己的 L0/L1 层，读完整文件之前就能判断相关性：

```
viking://resources/my_project/
├── .abstract               # L0：约 100 tokens——快速判断相关性
├── .overview               # L1：约 2k tokens——结构和要点
└── docs/
    ├── .abstract
    ├── .overview
    └── api/
        ├── auth.md         # L2：完整内容，按需加载
        └── endpoints.md
```

## 评测结果

OpenViking 0.3.22 的评测覆盖长对话用户记忆（LoCoMo）和多轮智能体任务（tau2-bench）。完整结果和实验设置（含知识库问答）见[评测报告](https://blog.openviking.ai/post/openviking-benchmark-results/)，复现脚本在 [./benchmark](./benchmark)。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/benchmark-dark.svg">
  <img alt="Benchmark results. LoCoMo accuracy: OpenClaw 24.20% native vs 82.08% with OpenViking; Hermes 33.38% vs 82.86%; Claude Code 57.21% vs 80.32%. tau2-bench task success: Retail 70.94% vs 77.81%; Airline 54.38% vs 66.25%." src="docs/images/benchmark-light.svg">
</picture>

- **用户记忆（LoCoMo）**：接入 OpenViking 后，三种 Agent 集成的准确率都到 80–83%，原生记忆只有 24–57%；同时输入 token 减少 34.3%–91.0%，查询时延降低 58.45%–66.10%。
- **智能体经验（tau2-bench）**：经验记忆让任务成功率在 Retail 提升 6.87pp、Airline 提升 11.87pp（对比同一 LLM 无记忆）。

## 快速开始

> 💡 **想先看看实际效果？** 试试 [OpenViking Studio](https://openviking.ai/studio)——官方托管的在线实例，带上下文实验场、语义检索和多智能体 Hub，无需安装。

需要 Python 3.10 或更高版本。

```bash
pip install openviking --upgrade
openviking-server init      # 交互式向导：提供商、模型、ov.conf
openviking-server doctor    # 校验配置
openviking-server           # 启动
```

或者在后台运行：

```bash
nohup openviking-server > /data/log/openviking.log 2>&1 &
```

`init` 引导你完成提供商配置，并写入 `~/.openviking/ov.conf`。它支持火山引擎、OpenAI、Codex OAuth、Kimi、GLM 和本地 Ollama——选 Ollama 时还能检测并安装运行时，按你的硬件拉取合适的模型。`doctor` 检查配置文件、Python 版本、提供商连通性和磁盘空间，不需要先启动服务器。

手写 `ov.conf` 的模板、各提供商示例、环境变量、Windows 配置和 CLI/客户端配置，见[配置指南](https://docs.openviking.ai/zh/guides/01-configuration)和[快速入门文档](https://docs.openviking.ai/zh/getting-started/02-quickstart)。

服务器跑起来之后：

```bash
ov status
ov add-resource https://github.com/volcengine/OpenViking # --wait
ov ls viking://resources/
ov tree viking://resources/volcengine -L 2
# 没加 --wait 的话，语义处理需要等一段时间
ov find "what is openviking"
ov grep "openviking" --uri viking://resources/volcengine/OpenViking/docs/zh
```

重建已有索引：`ov reindex <uri> --mode vectors_only` 只刷新向量；`--mode semantic_and_vectors` 先重新生成语义产物（`.abstract.md`、`.overview.md`）再刷新向量；`--mode prune_orphans` 清理源文件已不存在的向量记录（加 `--dry-run` 可预览）。没有 `semantic` 或 `full` 这样的模式别名。

客户端配置可以用 `ov config` 交互式初始化；有多台服务器时，用 `ov config switch` 切换。

Rust CLI 通过 `npm i -g @openviking/cli` 安装，也可以从源码构建：`cargo install --git https://github.com/volcengine/OpenViking ov_cli`，见 [CLI 安装](https://docs.openviking.ai/zh/getting-started/05-cli-setup)。官方 Docker 镜像也已提供，见[部署指南](https://docs.openviking.ai/zh/guides/03-deployment)。

## 接入你的 Agent

集成会把 OpenViking 的召回注入 Agent 上下文，并自动提交会话记忆：

- [Claude Code](https://docs.openviking.ai/zh/agent-integrations/02-claude-code)
- [Codex](https://docs.openviking.ai/zh/agent-integrations/04-codex)
- [OpenClaw](https://docs.openviking.ai/zh/agent-integrations/03-openclaw)
- [Hermes](https://docs.openviking.ai/zh/agent-integrations/05-hermes)
- [Cursor](https://docs.openviking.ai/zh/agent-integrations/12-cursor)
- [Trae](https://docs.openviking.ai/zh/agent-integrations/13-trae)
- [OpenCode](https://docs.openviking.ai/zh/agent-integrations/10-opencode)
- [pi](https://docs.openviking.ai/zh/agent-integrations/11-pi)
- [MCP 客户端](https://docs.openviking.ai/zh/agent-integrations/06-mcp-clients)
- [LangChain / LangGraph](https://docs.openviking.ai/zh/agent-integrations/07-langchain-langgraph)

各 Agent 的接入步骤：[Agent 集成总览](https://docs.openviking.ai/zh/agent-integrations/01-overview)。

## OpenViking Helper（Beta）

OpenViking Helper 是一个桌面控制台，目前处于 Beta 阶段，支持 macOS 和 Windows x64：

- **可视化接入本地 Agent**：检测 OpenViking CLI、Claude Code、Codex、Cursor、Trae 和 OpenCode，并配置支持的插件、MCP、Hook 和 CLI 接入。
- **查看会话轨迹**：解析 Claude Code、Codex 和 Trae 的会话，展示 OpenViking 的召回、Prompt 注入、MCP 调用、捕获和提交事件。
- **管理本地记忆与技能**：查看本地 memory / rule 文件和 `SKILL.md` 技能，并同步到 OpenViking。

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

生产环境建议把 OpenViking 作为独立 HTTP 服务运行——见[服务器部署](https://docs.openviking.ai/zh/getting-started/03-quickstart-server)和[部署指南](https://docs.openviking.ai/zh/guides/03-deployment)。

不想自己运维？OpenViking Personal 由官方托管、开箱即用，借助 VikingDB 获得远超本地硬件的扩展能力，并提供至多 50 个文件的免费试用；开源版用户可以用迁移工具平滑迁移。→ [openviking.ai](https://www.openviking.ai)

## 研究

OpenViking 开源了 VikingMem 论文中描述的部分核心能力：

> **VikingMem: A Memory Base Management System for Stateful LLM-based Applications**
> Jiajie Fu, Junwen Chen, Mengzhao Wang, Aoxiang He, Maojia Sheng, Xiangyu Ke, Yifan Zhu, and Yunjun Gao.
> arXiv:2605.29640, 2026。已被 VLDB 2026 接收。
> 📄 [在 arXiv 阅读论文](https://arxiv.org/abs/2605.29640)

## 社区与贡献

OpenViking 还在早期阶段，要做的事还很多。

- **文档**：[docs.openviking.ai](https://docs.openviking.ai/) · [FAQ](https://docs.openviking.ai/zh/faq/faq)
- **博客**：[blog.openviking.ai](https://blog.openviking.ai/)
- **团队**：[关于我们](https://docs.openviking.ai/zh/about/01-about-us)
- **交流**：📱 [飞书群](https://docs.openviking.ai/zh/about/01-about-us#飞书群) · 💬 [微信群](https://docs.openviking.ai/zh/about/01-about-us#微信群) · 🎮 [Discord](https://discord.com/invite/eHvx8E9XF3) · 🐦 [X](https://x.com/openvikingai)
- **贡献**：修 bug、加新功能都欢迎——见 [CONTRIBUTING_CN.md](CONTRIBUTING_CN.md)

## 安全与隐私

本项目重视安全问题。
漏洞报告方式和受支持的版本，见 [SECURITY.md](SECURITY.md)

## 许可证

OpenViking 各组件采用不同的许可证：

- **主项目**：AGPLv3——详见 [LICENSE](./LICENSE)
- **crates/ov\_cli**：Apache 2.0——详见 [LICENSE](./crates/LICENSE)
- **examples**：Apache 2.0——详见 [LICENSE](./examples/LICENSE)
- **third\_party**：各三方项目保留其原有协议
