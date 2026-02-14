# Nanobot 设计文档索引

本文档目录包含 Nanobot 项目的完整设计规范，用于基于规范的开发。

## ⚠️ 重要约定

**本项目必须采用 Spec-First 研发方式：**

1. **先设计，后编码**
   - 任何新功能、模块、接口的设计变更，必须先在 `specs/` 文档中体现
   - 设计文档通过评审后，再进行编码实现
   - 代码实现必须与 specs 文档保持一致

2. **文档驱动开发**
   - `specs/` 文档是项目设计和实现的唯一事实来源
   - 代码变更必须基于已批准的 specs 文档
   - 不得在未更新 specs 文档的情况下直接修改核心设计

3. **变更流程**
   - 提出新功能/变更 → 更新相关 specs 文档 → 评审通过 → 实现代码
   - 代码实现过程中发现设计问题 → 先更新 specs 文档 → 再修改代码
   - 代码实现完成后 → 确认与 specs 文档一致

4. **文档同步**
   - 每个模块的文档必须与实际代码保持同步
   - 发现代码与文档不一致时，优先更新文档或提出问题
   - 重大架构变更必须更新 `01-project-overview.md`

**违反此约定的变更将被拒绝。**

## 文档列表

### 1. [项目概览](./01-project-overview.md)
- 项目简介和核心特性
- 技术栈
- 项目结构
- 核心架构
- 设计模式
- 核心组件介绍
- 配置系统
- 支持的 LLM 提供商
- 支持的聊天平台
- CLI 命令参考
- 扩展点
- 安全特性
- 性能优化
- 开发路线图

### 2. [Agent 模块](./02-agent.md)
- AgentLoop (代理循环)
  - 核心处理引擎，协调 LLM 调用和工具执行
  - 管理会话和记忆
  - 处理系统消息（子代理通知）
- ContextBuilder (上下文构建器)
  - 构建 LLM 提示词
  - 加载 Bootstrap 文件
  - 整合记忆和技能
- MemoryStore (记忆存储)
  - 双层记忆系统（长期 + 历史）
  - MEMORY.md: 长期事实
  - HISTORY.md: 可搜索的历史日志
- SkillsLoader (技能加载器)
  - 加载工作空间和内置技能
  - 检查技能依赖
  - 构建技能摘要
- SubagentManager (子代理管理器)
  - 管理后台子代理执行
  - 子代理共享 LLM 提供商但具有隔离的上下文

### 3. [Tools 模块](./03-tools.md)
- Tool (工具基类)
  - 定义工具的抽象接口
  - 提供参数验证
- ToolRegistry (工具注册表)
  - 动态注册和执行工具
  - 工具查找和验证
- Filesystem Tools (文件系统工具)
  - ReadFileTool: 读取文件内容
  - WriteFileTool: 写入文件
  - EditFileTool: 编辑文件
  - ListDirTool: 列出目录
- Shell Tool (Shell 执行工具)
  - ExecTool: 执行 Shell 命令
- Web Tools (Web 工具)
  - WebSearchTool: Web 搜索
  - WebFetchTool: 获取网页内容
- Message Tool (消息发送工具)
  - MessageTool: 发送消息到聊天平台
- Spawn Tool (子代理生成工具)
  - SpawnTool: 生成子代理执行后台任务
- Cron Tool (定时任务工具)
  - CronTool: 管理定时任务

### 4. [Channels 模块](./04-channels.md)
- BaseChannel (通道基类)
  - 定义聊天通道的抽象接口
  - 提供权限检查
- ChannelManager (通道管理器)
  - 管理多个聊天平台
  - 协调消息路由
- 支持的通道
  - Telegram: Bot API 集成
  - Discord: Bot API 集成
  - WhatsApp: Bridge 集成
  - Feishu: WebSocket 长连接
  - MoChat: Socket.IO WebSocket 集成
  - DingTalk: Stream 模式
  - Slack: Socket 模式
  - Email: IMAP/SMTP 集成
  - QQ: WebSocket (botpy SDK) 集成

### 5. [Bus 模块](./05-bus.md)
- Message Events (消息事件)
  - InboundMessage: 从聊天平台接收的消息
  - OutboundMessage: 要发送到聊天平台的消息
- MessageBus (消息队列)
  - 异步消息队列
  - 线程安全的消息传递
  - 解耦消息生产者和消费者

### 6. [Providers 模块](./06-providers.md)
- LLMProvider (LLM 提供商基类)
  - 定义 LLM 提供商的抽象接口
  - 提供统一的消息格式
- ProviderRegistry (提供商注册表)
  - LLM 提供商的单一事实来源
  - 提供商元数据管理
  - 模型名称匹配
- 支持的提供商
  - Gateways (网关提供商)
    - OpenRouter: 路由任何模型
    - AiHubMix: API 网关
  - Standard Providers (标准提供商)
    - Anthropic: Claude 直接访问
    - OpenAI: GPT 直接访问
    - DeepSeek: DeepSeek 直接访问
    - Gemini: Gemini 直接访问
    - Zhipu: Zhipu GLM
    - DashScope: Qwen
    - Moonshot: Moonshot/Kimi
    - MiniMax: MiniMax 直接访问
  - Local Providers (本地提供商)
    - vLLM: 本地 OpenAI 兼容服务器
  - Auxiliary (辅助提供商)
    - Groq: Whisper 语音转录 + LLM

### 7. [Session 模块](./07-session.md)
- Session (会话)
  - 存储对话会话
  - JSONL 格式持久化
- SessionManager (会话管理器)
  - 管理会话生命周期
  - 会话持久化和缓存
  - 会话列表管理
- 会话存储格式
- 会话键生成
- 会话生命周期

### 8. [Cron 模块](./08-cron.md)
- CronService (定时任务服务)
  - 管理定时任务
  - 支持 cron 表达式、at、every 调度
  - 任务持久化
  - 定时器管理
- Cron Types (定时任务类型)
  - CronSchedule: 调度配置
  - CronPayload: 任务负载数据
  - CronJobState: 任务状态
  - CronJob: 定时任务
- 调度类型
  - Cron 表达式: 标准格式
  - At 调度: 指定时间执行
  - Every 调度: 按固定间隔执行
- 任务执行流程
- 任务存储格式

### 9. [TUI 模块](./12-tui.md)
- NanobotTUI (主应用)
  - 基于 Textual 框架的终端用户界面
  - 响应式设计，支持实时对话
- ChatScreen (聊天屏幕)
  - 主交互界面
  - 消息显示和输入
  - 思考状态指示
- TUI Components (TUI 组件)
  - MessageList: 消息列表显示
  - ChatInput: 聊天输入框
  - ThinkingIndicator: 思考状态动画
  - StatusBar: 状态栏显示
- TUIState (应用状态)
  - 消息历史管理
  - 会话信息
  - UI 状态追踪
- 功能特性
  - Markdown 渲染
  - 代码语法高亮
  - 消息历史导航
  - 键盘快捷键支持
  - 会话持久化

### 10. [数据模型和数据库设计](./09-data-model-database.md)
- 数据存储位置
  - 配置文件
  - 工作空间
  - Cron 存储
- 数据模型
  - 配置模型
  - 会话模型
  - 记忆模型
  - Cron 任务模型
  - 技能模型
  - Bootstrap 文件模型
- 数据访问模式
  - 读取
  - 写入
  - 缓存
- 数据一致性
  - 并发控制
  - 错误恢复
  - 数据迁移
- 性能优化
  - 文件格式选择
  - 缓存策略
  - 延迟加载
- 安全考虑
  - 敏感数据
  - 路径遍历
  - 数据备份

### 10. [API 接口文档](./10-api-interfaces.md)
- 内部模块接口
  - MessageBus 接口
  - ToolRegistry 接口
  - Tool 接口
  - LLMProvider 接口
  - BaseChannel 接口
  - SessionManager 接口
  - CronService 接口
- CLI 命令接口
  - vikingbot onboard
  - vikingbot agent
  - vikingbot gateway
  - vikingbot status
  - vikingbot channels
  - vikingbot cron
- 工具接口规范
  - 内置工具列表
  - 工具参数和返回值
- 事件数据结构
  - InboundMessage
  - OutboundMessage
  - LLMResponse
  - ToolCallRequest
- 配置接口
  - 配置加载
  - 配置保存
  - 工作空间路径
- 错误处理
  - 工具执行错误
  - 配置错误
  - 通道错误
- 扩展接口
  - 添加自定义工具
  - 添加自定义通道
  - 添加自定义提供商
- 性能考虑
  - 异步 I/O
  - 错误传播
  - 资源清理
- 安全考虑
  - 输入验证
  - 权限控制
  - 敏感数据

### 11. [沙箱集成技术规范](./11-sandbox-integration.md)
- 概述
  - 基于 @anthropic-ai/sandbox-runtime 的沙箱支持
  - 为每个 session 提供独立的文件系统和网络隔离环境
- 架构设计
  - 模块结构
  - 集成点
  - 扩展设计原则
- 配置设计
  - 配置结构
  - 配置 Schema (Pydantic)
- 核心组件设计
  - 抽象接口 (sandbox/base.py)
  - 后端注册机制 (sandbox/backends/__init__.py)
  - SandboxManager (sandbox/manager.py)
  - SRT 后端实现 (sandbox/backends/srt.py)
  - Docker 后端示例 (sandbox/backends/docker.py)
- 工具集成
  - 修改 ExecTool
- 生命周期管理
  - Session 创建时
  - Session 销毁时
- 错误处理
  - SandboxError
  - SandboxNotStartedError
  - SandboxDisabledError
  - SandboxExecutionError
  - UnsupportedBackendError
- 依赖管理
  - 新增依赖
  - 安装脚本
- 测试策略
  - 单元测试
  - 集成测试
- 文档更新
  - README.md
- 实现优先级
  - P0: 配置 Schema
  - P0: 抽象接口
  - P0: 沙箱管理器
  - P1: SRT 后端
  - P1: Shell 工具集成
  - P1: Session 集成
  - P2: 文件系统工具集成
  - P3: Docker 后端
  - P3: 文档更新

## 快速导航

### 按主题查找

- **架构设计**: [01-project-overview.md](./01-project-overview.md)
- **模块设计**: 
  - [02-agent.md](./02-agent.md) - Agent 模块
  - [03-tools.md](./03-tools.md) - Tools 模块
  - [04-channels.md](./04-channels.md) - Channels 模块
  - [05-bus.md](./05-bus.md) - Bus 模块
  - [06-providers.md](./06-providers.md) - Providers 模块
  - [07-session.md](./07-session.md) - Session 模块
  - [08-cron.md](./08-cron.md) - Cron 模块
- **数据模型**: [09-data-model-database.md](./09-data-model-database.md)
- **API 规范**: [10-api-interfaces.md](./10-api-interfaces.md)
- **沙箱集成**: [11-sandbox-integration.md](./11-sandbox-integration.md)

### 按开发任务查找

- **添加新功能**: 阅读 [01-project-overview.md](./01-project-overview.md) 了解架构
- **实现新模块**: 参考相应的模块设计文档（02-08）
- **设计数据结构**: 查看 [09-data-model-database.md](./09-data-model-database.md) 的数据模型
- **集成外部服务**: 参考 [10-api-interfaces.md](./10-api-interfaces.md) 的接口定义
- **沙箱集成**: 参考 [11-sandbox-integration.md](./11-sandbox-integration.md) 的沙箱集成规范

## 开发工作流

1. **理解架构**: 阅读 [01-project-overview.md](./01-project-overview.md)
2. **设计模块**: 参考相应的模块设计文档
3. **定义数据**: 使用 [09-data-model-database.md](./09-data-model-database.md) 的数据模型
4. **实现接口**: 遵循 [10-api-interfaces.md](./10-api-interfaces.md) 的接口规范
5. **测试验证**: 运行测试确保功能正常

## 版本信息

- **项目**: Nanobot
- **版本**: 0.1.3.post7
- **文档版本**: 1.0.0
- **最后更新**: 2026-02-13

## 贡献指南

如果发现文档错误或有改进建议，请：

1. 创建 Issue 描述问题
2. 提交 PR 更新文档
3. 遵循现有的文档格式

## 许可证

Nanobot 采用 MIT 许可证。详见项目根目录的 LICENSE 文件。
