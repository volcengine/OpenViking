# 为 OpenViking 做贡献

[English](CONTRIBUTING.md) / 中文 / [日本語](CONTRIBUTING_JA.md)

感谢你参与 OpenViking。本指南旨在帮助贡献者提交清晰、聚焦且便于评审的改动。

我们欢迎 Bug 报告、功能请求、文档改进和代码贡献。

## 我们重视什么

OpenViking 重视聚焦且经过充分理解的改动。无论是否使用 AI 工具，贡献者都要对
理解、解释和验证自己的改动负责。

优先提交最小而完整的改动。代码简洁，是减少概念、分支、重复规则和猜测式抽象，
不是压缩必要的代码行数。好的改动应当直接、易读，并且能从入口到可观察行为解释清楚。

具体来说：

- 一个 PR 只解决一个内聚的问题，不要混入无关清理或重构。
- 复用现有规则的 Owner，不要引入平行机制。
- 避免猜测式 Fallback、Flag、状态字段和抽象。
- 删除被新实现替代的代码、测试和兼容路径。
- 当必要结构能让职责、生命周期或失败处理更清楚时，应当保留它。

### 评审优先级

维护者精力有限，因此会优先查看聚焦的 PR：

- **改动不超过 100 行**的 PR，通常能得到更及时的查看。
- **改动不超过 200 行**的 PR，会比更大的 PR 优先查看。

这只是评审优先级，不是硬性限制或响应时间承诺。改动行数按手写源码、测试和文档的
新增行与删除行之和计算；生成文件、第三方代码和锁文件不计入规模判断。

不要为了控制行数省略必要的测试或文档。只有当拆分后的每个 PR 都能独立理解且保持
正确时，才拆分大改动。PR 小不代表可以降低正确性、设计质量或兼容性要求。

## 开始之前

1. 全局搜索已有 Issue、PR 和代码，确认是否已经存在相同的行为或领域规则。
2. 修复 Bug 时，尽量通过真实生产入口复现问题。
3. 确认 Owner 模块，并追踪相关值或状态在哪里创建、规范化、存储和消费。
4. 开发功能时，先说明要解决的问题和预期行为，再设计实现。

以下改动应在实现前先提交 Issue 或发起讨论：

- 公开的 REST、SDK、CLI、MCP 或配置语义；
- 持久化数据、存储 Schema、VFS/AGFS 路径或加密文件行为；
- 异步任务归属、队列、取消、清理或结果状态；
- 资源导入与监听、Session 生命周期或记忆抽取；
- 检索 Level、目录范围或排序语义；
- Tenant、Account、User 或 Peer 身份边界；
- 涉及多个 Owner 模块或大型架构重构。

讨论中请给出当前行为、目标行为、具体请求或配置示例，以及兼容性影响。这样维护者
可以在开始实现前确认设计边界。

请使用仓库提供的 GitHub 模板提交 [Bug 报告](https://github.com/volcengine/OpenViking/issues/new?template=bug_report.yml)、
[功能请求](https://github.com/volcengine/OpenViking/issues/new?template=feature_request.yml)和
[使用问题](https://github.com/volcengine/OpenViking/issues/new?template=question.yml)。

## 找到正确的模块

如果已知受影响模块，请在 Issue 或 PR 中注明。如果不确定，先描述可观察行为和使用场景，
维护者会协助路由。

这张表根据 2026 年 6 月 24 日至 8 月 24 日已合并 PR 中持续的提交和评审活动整理。
它用于协助路由，不代表排他性的代码所有权；只需 @ 与改动直接相关的联系人。

| 领域 | 模块 | 代表路径或主题 | 近期活跃维护者 / 评审者 |
|---|---|---|---|
| Platform | Server、API、Auth、Identity、Admin、Task | `openviking/server`、`openviking/service` | `@qin-ctx` |
| Resource | 导入、Watch 与任务流水线 | `openviking/resource` | `@qin-ctx`、`@KCHENPENGFEI` |
| Resource | 资源解析 | `openviking/parse` | `@zihengli-bytedance`、`@KCHENPENGFEI` |
| Memory | Session、记忆抽取与编译 | `openviking/session`、记忆抽取、`ov compile` | `@chenjw`、`@heaoxiang-ai`、`@fujiajie666` |
| Retrieval | Search 与 VectorDB | `openviking/retrieve`、`openviking/storage/vectordb` | `@zhoujh01`、`@t0saki` |
| Storage | RAGFS、PathLock、QueueFS 与加密 | `openviking/storage`、`openviking/pyagfs`、`openviking/crypto`、`crates/ragfs*` | `@baojun-zhang` |
| Integration | Agent Plugin 与 MCP | `agent-plugins`、记忆插件示例、Server MCP | `@t0saki`、`@ZaynJarvis` |
| Integration | VikingBot 与 Agent 编译 | `bot`、`ov compile` | `@yeshion23333`、`@fujiajie666` |
| Client | SDK、CLI 与 LangChain | `sdk`、`crates/ov_cli`、`integrations/langchain` | `@zhoujh01`、`@t0saki`、`@ehz0ah` |
| Product | Web Studio | `web-studio` | `@yufeng201`、`@ZaynJarvis` |
| Project | 文档、CI 与 Plugin 发布 | `docs`、`.github/workflows` | `@yufeng201`、`@ZaynJarvis` |

跨模块改动或 Owner 不明确时，请先确认主要影响域，再 @ `@qin-ctx`、`@ZaynJarvis` 或 `@zhoujh01`。

## 开发环境

### 前置要求

- Python 3.10+
- 从源码构建、开发 Rust Binding 或内置 `ov` CLI 时需要 Rust 1.91.1+
- 仅开发 `sdk/go` 时需要 Go 1.22+
- 支持 C++17 的编译器：GCC 9+ 或 Clang 11+
- CMake 3.15+

Linux 请安装 `build-essential`，部分环境还需要 `pkg-config`。macOS 请安装 Xcode
Command Line Tools。Windows 本地原生构建请安装 CMake 和 MinGW。

### 安装

Fork 仓库，然后克隆自己的 Fork：

```bash
git clone https://github.com/YOUR_USERNAME/OpenViking.git
cd OpenViking
```

推荐使用 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --all-extras
```

验证环境：

```bash
uv run python -c "import openviking; print(openviking.__version__)"
```

配置本地 Server：

```bash
uv run openviking-server init
uv run openviking-server doctor
```

配置说明和 Provider 示例见[配置指南](https://docs.openviking.ai/zh/guides/01-configuration)。

修改 RAGFS Rust Binding、内置 Rust CLI 或 C++ 扩展后，需要重新构建原生组件：

```bash
uv pip install -e . --force-reinstall
```

SDK、Integration、Plugin 和 Benchmark 可能有额外要求，请查看对应目录中的 README 或
包配置。

## 修改代码

### 职责与设计

- 行为应放在所属模块中。上层只负责传输或消费结果，不要重复实现相同规则。
- 在边界把外部兼容表达转换为唯一的规范领域模型，内层业务逻辑不应猜测输入形态。
- 面向客户端的边界应保留有意义的 Server、Network、Timeout、Auth 和 Conflict 错误。
- 任务状态必须与产生它的任务保持因果关联，不能通过全局队列状态或无关回调推断完成。
- 每个值和规则只保留一个权威来源。

如果局部边缘 Case 开始改变任务边界、公开语义或整体架构，应暂停实现并回到设计讨论，
不要在主流程中不断增加特殊分支。

### 代码风格

Python 使用 Ruff 进行格式化和 Lint，使用 mypy 进行类型检查，配置行宽为 100 字符。

对改动路径运行检查：

```bash
uv run ruff format <changed-paths>
uv run ruff check <changed-paths>
uv run mypy <changed-paths>
```

公开 API 应包含简短且有用的 Docstring。优先使用清晰命名和直接控制流，不要用注释
重复解释代码本身。

Rust、Go、TypeScript、文档和 Plugin 改动，请使用对应组件定义的格式化、Lint、类型检查
和测试命令。

### 测试

验证受影响的最小有效公开契约和主要失败边界。

- 优先修改已有的高价值契约测试。
- 默认不要新增单元测试或测试文件。
- 不要测试私有 Helper 是否存在、Mock 调用顺序、简单字段透传或框架行为，除非它保护
  长期公开契约。
- 小而明确的修复不必自动新增测试，但必须说明验证方式。
- 临时复现、诊断、压测和验证脚本统一放在 `test_scripts/`，不要放入源码、Benchmark
  或维护脚本目录。

运行相关的聚焦测试，例如：

```bash
uv run pytest tests/client/test_http_client_config.py
uv run pytest tests/server/ -k "search"
```

只有改动范围和风险需要时才运行完整 Python 测试：

```bash
uv run pytest
```

## 提交 Pull Request

基于最新的 `main` 创建分支，完成聚焦改动后向 `main` 提交 PR。

Commit Message 和 PR 标题使用 [Conventional Commits](https://www.conventionalcommits.org/)：

```text
feat(parser): support xlsx resources
fix(retrieval): preserve rerank score order
docs: clarify server configuration
refactor(storage): remove duplicate path normalization
```

完整填写仓库的 PR 模板。有效的 PR 描述应说明：

- 改动前后的可观察行为；
- Bug 的根因和真实执行路径；
- 受影响的入口和 Owner 模块；
- 兼容性或迁移影响；
- 实际执行的验证命令；
- 问题已经复现，还是仅根据代码推断。

请准确选择 Human Involvement。项目接受 AI 辅助贡献，但作者仍对改动负责，并且必须
能解释它与系统其他部分如何交互。

提交前：

- 完整检查 Diff，删除无关或意外生成的改动。
- 确认被替代的 Helper、分支、Mock 和注释已经删除。
- 公开行为变化时更新相关文档。
- 如实说明未运行的检查及具体原因，不要声称执行了实际未运行的测试。

CI 会根据受影响路径运行相应检查。CI 通过是必要条件，但不能替代作者验证和维护者评审。

## 文档

项目文档位于 `docs/en/` 和 `docs/zh/`。代码示例必须可运行，语言应清晰简洁；当对应
翻译存在时，应同步更新两种语言。

## 社区

请保持尊重、包容、建设性，并聚焦技术讨论。开放式设计或使用讨论请前往
[GitHub Discussions](https://github.com/volcengine/OpenViking/discussions)，可执行的 Bug
和功能请求请提交到 [GitHub Issues](https://github.com/volcengine/OpenViking/issues)。
