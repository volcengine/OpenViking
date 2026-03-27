# 基础使用示例：OpenViking Python SDK 快速入门

本示例演示如何使用 Python SDK 操作 OpenViking 的核心功能，涵盖构建 AI 智能体应用所需的基本操作。

## 你将学到什么

- **初始化 OpenViking**：嵌入式模式与 HTTP 客户端模式
- **添加资源**：URL、文件和目录
- **浏览虚拟文件系统**：使用 `ls`、`tree`、`glob`
- **语义搜索**：使用 `find` 和 `search` 查找相关上下文
- **分层上下文加载**：L0（摘要）、L1（概览）、L2（完整内容）
- **会话管理**：存储和召回对话记忆

## 前置条件

1. **Python 3.10+**
2. **安装 OpenViking**：
   ```bash
   pip install openviking --upgrade
   ```
3. **配置文件** 位于 `~/.openviking/ov.conf`（见下方[配置说明](#配置说明)）

## 快速开始

### 1. 运行示例脚本

```bash
# 克隆仓库
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking/examples/basic-usage

# 运行基础使用示例
python basic_usage.py
```

### 2. 预期输出

```
=== OpenViking 基础使用示例 ===

1. 初始化 OpenViking...
   状态: healthy

2. 添加资源 (URL)...
   根 URI: viking://resources/raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md
   已索引文件: 1

3. 浏览虚拟文件系统...
   viking://resources/raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/
   └── README.md

4. 等待语义处理...

5. 分层上下文加载:
   L0 (摘要): OpenViking 是专为 AI 智能体设计的开源上下文数据库...

   L1 (概览):
   [包含核心要点和使用场景]

6. 语义搜索:
   查询: "what is openviking"
   结果:
   - viking://resources/.../README.md (得分: 0.8523)

7. 内容搜索 (grep):
   模式: "Agent"
   找到 15 处匹配

8. 关闭 OpenViking...
   完成!
```

## 代码详解

### 初始化

OpenViking 支持两种模式：

**嵌入式模式**（本地开发默认）：
```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()
```

**HTTP 客户端模式**（连接远程服务器）：
```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
```

### 添加资源

添加 URL、本地文件或目录：

```python
# 添加 URL
result = client.add_resource(
    path="https://example.com/documentation",
    wait=True  # 等待语义处理完成
)

# 添加本地文件
result = client.add_resource(
    path="/path/to/your/document.pdf"
)

# 添加目录（代码仓库）
result = client.add_resource(
    path="/path/to/your/project",
    instruction="这是一个 Python Web 应用"
)
```

### 浏览文件系统

OpenViking 使用 `viking://` URI 的虚拟文件系统范式：

```python
# 列出目录内容
files = client.ls("viking://resources/")

# 显示目录树
tree = client.tree("viking://resources/my-project", level_limit=3)

# 按模式查找文件
matches = client.glob("**/*.md", uri="viking://resources/my-project")
```

### 语义搜索

使用自然语言查询查找上下文：

```python
# 快速语义搜索
results = client.find(
    query="如何处理 API 认证",
    target_uri="viking://resources/my-project",
    limit=5
)

# 高级搜索（带意图分析）
results = client.search(
    query="数据库连接配置和错误处理",
    target_uri="viking://resources/",
    limit=10
)
```

### 分层上下文加载

OpenViking 将内容处理为三个层次，实现高效检索：

```python
uri = "viking://resources/my-project/docs/api.md"

# L0: 快速摘要（约 100 tokens）
abstract = client.abstract(uri)

# L1: 概览（约 2k tokens，包含核心要点）
overview = client.overview(uri)

# L2: 完整内容
content = client.read(uri)
```

### 会话管理

存储对话记忆以实现长期召回：

```python
# 创建会话
session_info = client.create_session()
session_id = session_info["session_id"]

# 添加对话消息
client.add_message(session_id, "user", "我更喜欢 TypeScript 而不是 JavaScript")
client.add_message(session_id, "assistant", "好的！我会在你的项目中使用 TypeScript。")

# 提交会话以提取长期记忆
client.commit_session(session_id)

# 之后召回相关记忆
memories = client.find(
    query="用户编程偏好",
    target_uri="viking://user/memories/"
)
```

## 配置说明

创建 `~/.openviking/ov.conf` 配置文件：

```json
{
  "storage": {
    "workspace": "~/.openviking/data"
  },
  "embedding": {
    "dense": {
      "provider": "openai",
      "api_key": "your-api-key",
      "model": "text-embedding-3-large",
      "dimension": 3072
    }
  },
  "vlm": {
    "provider": "openai",
    "api_key": "your-api-key",
    "model": "gpt-4o"
  }
}
```

> **提示**：你也可以使用火山引擎（豆包）、Azure OpenAI 或 LiteLLM（支持 Anthropic、DeepSeek、Gemini 等）。详见主 README 的提供商配置说明。

## 应用场景

### 构建文档感知智能体

```python
import openviking as ov

class DocumentationAgent:
    def __init__(self):
        self.ov = ov.OpenViking(path="./data")
        self.ov.initialize()
    
    def ingest_docs(self, doc_path: str):
        """将文档添加到知识库"""
        self.ov.add_resource(doc_path, wait=True)
    
    def answer(self, question: str) -> str:
        """查找相关文档并回答"""
        results = self.ov.find(question, limit=3)
        
        context = []
        for r in results.resources:
            content = self.ov.read(r.uri)
            context.append(content)
        
        # 使用上下文和 LLM 生成回答
        return context

# 使用
agent = DocumentationAgent()
agent.ingest_docs("https://docs.python.org/3/")
agent.ingest_docs("/path/to/your/project/docs")
```

### 创建记忆感知助手

```python
import openviking as ov

class MemoryAssistant:
    def __init__(self, user_id: str):
        self.ov = ov.SyncHTTPClient(url="http://localhost:1933")
        self.user_id = user_id
        self.session_id = None
    
    def start_conversation(self):
        """开始新的对话会话"""
        session = self.ov.create_session()
        self.session_id = session["session_id"]
    
    def remember(self, user_input: str, assistant_response: str):
        """存储对话轮次"""
        self.ov.add_message(self.session_id, "user", user_input)
        self.ov.add_message(self.session_id, "assistant", assistant_response)
    
    def recall(self, query: str) -> list:
        """召回相关记忆"""
        return self.ov.find(query, target_uri="viking://user/memories/")
    
    def end_conversation(self):
        """从会话中提取长期记忆"""
        self.ov.commit_session(self.session_id)
```

## 下一步

- **[OpenClaw 插件](../openclaw-plugin/)**：与 OpenClaw AI 助手集成
- **[Claude 记忆插件](../claude-memory-plugin/)**：在 Claude Code 中使用
- **[OpenCode 记忆插件](../opencode-memory-plugin/)**：与 OpenCode 集成
- **[技能示例](../skills/)**：基于 CLI 的搜索和管理技能

## 常见问题

| 问题 | 解决方案 |
|------|---------|
| `ImportError: pyagfs not found` | 从源码运行：`pip install -e third_party/agfs/agfs-sdk/python` |
| `Connection refused` | 确保 OpenViking 服务器正在运行：`openviking-server` |
| `API key error` | 检查 `~/.openviking/ov.conf` 配置 |
| `语义处理缓慢` | 等待 `wait_processed()` 或使用 `add_resource(..., wait=True)` |

## 许可证

Apache License 2.0