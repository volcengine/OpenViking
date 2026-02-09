# 上下文层级 (L0/L1/L2)

OpenViking 使用三层信息模型来平衡检索效率和内容完整性。

## 概览

| 层级 | 名称 | 文件 | Token 限制 | 用途 |
|------|------|------|-----------|------|
| **L0** | 摘要 | `.abstract.md` | ~100 tokens | 向量搜索、快速过滤 |
| **L1** | 概览 | `.overview.md` | ~2k tokens | Rerank 精排、内容导航 |
| **L2** | 详情 | 原始文件/子目录 | 无限制 | 完整内容、按需加载 |

## L0: 摘要

内容的最精简表示，用于快速筛选。

### 特点

- **超短**：最多 ~100 tokens
- **快速感知**：让 Agent 快速感知到

### 示例

```markdown
API 认证指南，涵盖 OAuth 2.0、JWT 令牌和 API 密钥的安全访问方式。
```

### API

```python
abstract = client.abstract("viking://resources/docs/auth")
```

## L1: 概览

包含内容导航的综合摘要，用于理解访问方式。

### 特点

- **适中长度**：~1k tokens
- **导航指引**：告诉 Agent 如何访问详细内容

### 示例

```markdown
# 认证指南概览

本指南涵盖 API 的三种认证方式：

## 章节
- **OAuth 2.0** (L2: oauth.md): 完整 OAuth 流程和代码示例
- **JWT 令牌** (L2: jwt.md): 令牌生成和验证
- **API 密钥** (L2: api-keys.md): 简单的密钥认证

## 要点
- OAuth 2.0 推荐用于面向用户的应用
- JWT 用于服务间通信

## 访问
使用 `read("viking://resources/docs/auth/oauth.md")` 获取完整文档。
```

### API

```python
overview = client.overview("viking://resources/docs/auth")
```

## L2: 详情

完整的原始内容，仅在需要时加载。

### 特点

- **完整内容**：无 Token 限制
- **按需加载**：只在确定需要时读取
- **原始格式**：保留源结构

### API

```python
content = client.read("viking://resources/docs/auth/oauth.md")
```

## 生成机制

### 何时生成

- **添加资源时**：Parser 解析后，SemanticQueue 异步生成
- **会话归档时**：压缩旧消息时生成历史片段的 L0/L1

### 由谁生成

| 组件 | 职责 |
|------|------|
| **SemanticProcessor** | 自底向上遍历目录，为每个目录生成 L0/L1 |
| **SessionCompressor** | 为归档的会话历史生成 L0/L1 |

### 生成顺序

```
叶子节点 → 父目录 → 根目录（自底向上）
```

子目录的 L0 会被聚合到父目录的 L1 中，形成层级导航。

## 目录结构

每个目录都遵循统一的文件结构：

```
viking://resources/docs/auth/
├── .abstract.md          # L0: ~100 tokens
├── .overview.md          # L1: ~1k tokens
├── .relations.json       # 相关资源
├── oauth.md              # L2: 完整内容
├── jwt.md                # L2: 完整内容
└── api-keys.md           # L2: 完整内容
```

## 多模态支持

- **L0/L1**：始终是文本（Markdown）
- **L2**：可以是任何格式（文本、图片、视频、音频）

对于二进制内容，L0/L1 用文本描述：

```markdown
# 图片的 L0
产品截图，展示带有 OAuth 按钮的登录页面。

# 图片的 L1
## 图片：登录页面截图

此截图展示应用的登录页面，包含：
- Google OAuth 按钮（顶部）
- GitHub OAuth 按钮（中部）
- 邮箱/密码表单（底部）

尺寸：1920x1080，格式：PNG
```

目录结构

```
...
└── 第三章 开发者说明/
    ├── .abstract.md
    ├── .overview.md
    ├── content.md
    └── 视频附件1-开发者说明/              ← 递归扩展附件信息
        ├── .abstract.md
        ├── .overview.md
        ├── 音频和字幕提取.md
        ├── 开发者培训.mp4
        └── 视频分段切片/
            ├── 开发者培训_0s-30s.mp4
            └── 开发者培训_30s-60s.mp4
```



## 最佳实践

| 场景 | 推荐层级 |
|------|----------|
| 快速相关性检查 | L0 |
| 理解内容范围 | L1 |
| 详细信息提取 | L2 |
| 为 LLM 构建上下文 | L1（通常足够） |

### Token 预算管理

```python
# 先用 L1 判断，仅在需要时加载 L2
overview = client.overview(uri)

if needs_more_detail(overview):
    content = client.read(uri)
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文类型](./02-context-types.md) - 三种上下文类型
- [Viking URI](./04-viking-uri.md) - URI 规范
- [检索机制](./07-retrieval.md) - 检索流程详解
- [上下文提取](./06-extraction.md) - L0/L1 生成详解
