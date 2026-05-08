# 路线图

本文档概述 OpenViking 的开发路线图。

## 已完成功能

### 核心基础设施
- 三层信息模型（L0/L1/L2）
- Viking URI 寻址系统
- 双层存储（AGFS + 向量索引）
- 异步/同步客户端支持
- QueueFS SQLite 存储后端

### 资源管理
- 文本资源管理（Markdown、HTML、PDF）
- 自动 L0/L1 生成
- 带向量索引的语义搜索
- 资源关联和链接
- 内容写入 API
- Agent 命名空间管理

### 多模态解析
- 图像 OCR 和解析
- 音频转写（Whisper ASR）
- 视频解析
- PDF 书签提取
- Word、PowerPoint、Excel、EPub、ZIP 解析器
- 代码文件解析
- 飞书/Lark 文档解析器

### 检索
- 基本语义搜索（`find`）
- 带意图分析的上下文感知搜索（`search`）
- 基于会话的查询扩展
- 多供应商重排序流水线（OpenAI、LiteLLM、Cohere、Volcengine）

### 会话与记忆
- 对话状态追踪
- 上下文和技能使用追踪
- 自动记忆提取
- 使用 LLM 的记忆去重
- 会话归档和压缩
- Working Memory V2 及冷存储归档

### 技能
- 技能定义和存储
- MCP 工具自动转换
- 技能搜索和检索

### 多租户与安全
- 多租户支持与账户隔离
- 文件和文档加密
- 用户级隐私配置 API
- API Key 认证

### 配置与供应商
- 可插拔的 Embedding 提供者（OpenAI、Gemini、Volcengine、MiniMax、LiteLLM、Jina、Cohere、DashScope、Voyage、本地）
- 可插拔的 LLM 提供者
- 可插拔的重排序提供者
- 基于 YAML 的配置
- 安装向导（`openviking-server init`）

### Server 与 Client 架构
- HTTP Server（FastAPI）
- 内置 MCP 端点
- Python HTTP Client
- 客户端抽象层（LocalClient / HTTPClient）
- Web 控制台

### CLI
- Rust CLI（`ov` 命令）
- TUI 文件系统浏览器
- 隐私、搜索、会话、资源及管理命令

### Bot 集成
- VikingBot 框架
- 飞书/Lark 频道
- Telegram 频道

### 生态与插件
- OpenClaw 插件（编程 Agent 上下文引擎）
- Claude Code 记忆插件
- Codex 记忆插件

### 可观测性
- Prometheus 指标
- OpenTelemetry 链路追踪
- HTTP 可观测性中间件

### 部署
- Docker 镜像和 Docker Compose
- Kubernetes Helm Chart
- 云端 VikingDB 支持

---

## 未来计划

### 上下文管理
- 上下文修改对上层的传导更新
- 支持对上下文的版本管理和回滚（参考 git）

### 分布式存储
- 分布式存储后端

### 生态
- 更多 Agent 框架适配器

欢迎在 issue 中提出建议和反馈。

---

## 贡献

我们欢迎贡献以帮助实现这些目标。请参阅 [贡献指南](contributing.md)。
