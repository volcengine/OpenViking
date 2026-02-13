# 配置

OpenViking 使用 JSON 配置文件（`ov.conf`）进行设置。配置文件支持 Embedding、VLM、Rerank、存储、解析器等多个模块的配置。

## 快速开始

在项目目录创建 `~/.openviking/ov.conf`：

```json
{
  "storage": {
    "vectordb": {
      "name": "context",
      "backend": "local",
      "path": "./data"
    },
    "agfs": {
      "port": 1833,
      "log_level": "warn",
      "path": "./data",
      "backend": "local"
    }
  },
  "embedding": {
    "dense": {
      "api_base" : "<api-endpoint>",
      "api_key"  : "<your-api-key>",
      "provider" : "<provider-type>",
      "dimension": 1024,
      "model"    : "<model-name>"
    }
  },
  "vlm": {
    "api_base" : "<api-endpoint>",
    "api_key"  : "<your-api-key>",
    "provider" : "<provider-type>",
    "model"    : "<model-name>"
  }
}

```

## 配置示例

<details>
<summary><b>火山引擎（豆包模型）</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-250615",
      "input": "multimodal"
    }
  },
  "vlm": {
    "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
    "api_key"  : "your-volcengine-api-key",
    "provider" : "volcengine",
    "model"    : "doubao-seed-1-8-251228"
  }
}
```

</details>

<details>
<summary><b>OpenAI 模型</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://api.openai.com/v1",
      "api_key"  : "your-openai-api-key",
      "provider" : "openai",
      "dimension": 3072,
      "model"    : "text-embedding-3-large"
    }
  },
  "vlm": {
    "api_base" : "https://api.openai.com/v1",
    "api_key"  : "your-openai-api-key",
    "provider" : "openai",
    "model"    : "gpt-4-vision-preview"
  }
}
```

</details>

## 配置部分

### embedding

用于向量搜索的 Embedding 模型配置，支持 dense、sparse 和 hybrid 三种模式。

#### Dense Embedding

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024,
      "input": "multimodal",
      "batch_size": 32
    }
  }
}
```

**参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `provider` | str | `"volcengine"`、`"openai"` 或 `"vikingdb"` |
| `api_key` | str | API Key |
| `model` | str | 模型名称 |
| `dimension` | int | 向量维度 |
| `input` | str | 输入类型：`"text"` 或 `"multimodal"` |
| `batch_size` | int | 批量请求大小 |

**可用模型**

| 模型 | 维度 | 输入类型 | 说明 |
|------|------|----------|------|
| `doubao-embedding-vision-250615` | 1024 | multimodal | 推荐 |
| `doubao-embedding-250615` | 1024 | text | 仅文本 |

使用 `input: "multimodal"` 时，OpenViking 可以嵌入文本、图片（PNG、JPG 等）和混合内容。

**支持的 provider:**
- `openai`: OpenAI Embedding API
- `volcengine`: 火山引擎 Embedding API
- `vikingdb`: VikingDB Embedding API

**vikingdb provider 配置示例:**

```json
{
  "embedding": {
    "dense": {
      "provider": "vikingdb",
      "model": "bge_large_zh",
      "ak": "your-access-key",
      "sk": "your-secret-key",
      "region": "cn-beijing",
      "dimension": 1024
    }
  }
}
```

#### Sparse Embedding

```json
{
  "embedding": {
    "sparse": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "bm25-sparse-v1"
    }
  }
}
```

#### Hybrid Embedding

支持两种方式：

**方式一：使用单一混合模型**

```json
{
  "embedding": {
    "hybrid": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-hybrid",
      "dimension": 1024
    }
  }
}
```

**方式二：组合 dense + sparse**

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024
    },
    "sparse": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "bm25-sparse-v1"
    }
  }
}
```

### vlm

用于语义提取（L0/L1 生成）的视觉语言模型。

```json
{
  "vlm": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-seed-1-8-251228",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

**参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `api_key` | str | API Key |
| `model` | str | 模型名称 |
| `base_url` | str | API 端点（可选） |

**可用模型**

| 模型 | 说明 |
|------|------|
| `doubao-seed-1-8-251228` | 推荐用于语义提取 |
| `doubao-pro-32k` | 用于更长上下文 |

添加资源时，VLM 生成：

1. **L0（摘要）**：~100 token 摘要
2. **L1（概览）**：~2k token 概览，包含导航信息

如果未配置 VLM，L0/L1 将直接从内容生成（语义性较弱），多模态资源的描述可能有限。

### rerank

用于搜索结果精排的 Rerank 模型。

```json
{
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-rerank-250615"
  }
}
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `provider` | str | `"volcengine"` |
| `api_key` | str | API Key |
| `model` | str | 模型名称 |

如果未配置 Rerank，搜索仅使用向量相似度。

### storage

存储后端配置。

```json
{
  "storage": {
    "agfs": {
      "backend": "local",
      "path": "./data",
      "timeout": 30.0
    },
    "vectordb": {
      "backend": "local",
      "path": "./data"
    }
  }
}
```

## 配置文件

OpenViking 使用两个配置文件：

| 配置文件 | 用途 | 默认路径 |
|---------|------|---------|
| `ov.conf` | SDK 嵌入模式 + 服务端配置 | `~/.openviking/ov.conf` |
| `ovcli.conf` | HTTP 客户端和 CLI 连接远程服务端 | `~/.openviking/ovcli.conf` |

配置文件放在默认路径时，OpenViking 自动加载，无需额外设置。

如果配置文件在其他位置，有两种指定方式：

```bash
# 方式一：环境变量
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf

# 方式二：命令行参数（仅 serve 命令）
python -m openviking serve --config /path/to/ov.conf
```

### ov.conf

本文档上方各配置段（embedding、vlm、rerank、storage）均属于 `ov.conf`。SDK 嵌入模式和服务端共用此文件。

### ovcli.conf

HTTP 客户端（`SyncHTTPClient` / `AsyncHTTPClient`）和 CLI 工具连接远程服务端的配置文件：

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-secret-key",
  "output": "table"
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `url` | 服务端地址 | （必填） |
| `api_key` | API Key 认证 | `null`（无认证） |
| `output` | 默认输出格式：`"table"` 或 `"json"` | `"table"` |

详见 [服务部署](./03-deployment.md)。

## server 段

将 OpenViking 作为 HTTP 服务运行时，在 `ov.conf` 中添加 `server` 段：

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "api_key": "your-secret-key",
    "cors_origins": ["*"]
  }
}
```

| 字段 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `host` | str | 绑定地址 | `0.0.0.0` |
| `port` | int | 绑定端口 | `1933` |
| `api_key` | str | API Key 认证，不设则禁用认证 | `null` |
| `cors_origins` | list | CORS 允许的来源 | `["*"]` |

启动方式和部署详情见 [服务部署](./03-deployment.md)，认证详情见 [认证](./04-authentication.md)。

## 完整 Schema

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "string",
      "model": "string",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "string",
    "api_key": "string",
    "model": "string",
    "base_url": "string"
  },
  "rerank": {
    "provider": "volcengine",
    "api_key": "string",
    "model": "string"
  },
  "storage": {
    "agfs": {
      "backend": "local|remote",
      "path": "string",
      "url": "string",
      "timeout": 30.0
    },
    "vectordb": {
      "backend": "local|remote",
      "path": "string",
      "url": "string"
    }
  },
  "server": {
    "host": "string",
    "port": 1933,
    "api_key": "string",
    "cors_origins": ["string"]
  }
}
```

说明：
- `storage.vectordb.sparse_weight` 用于混合（dense + sparse）索引/检索的权重，仅在使用 hybrid 索引时生效；设置为 > 0 才会启用 sparse 信号。

## 故障排除

### API Key 错误

```
Error: Invalid API key
```

检查 API Key 是否正确且有相应权限。

### 维度不匹配

```
Error: Vector dimension mismatch
```

确保配置中的 `dimension` 与模型输出维度匹配。

### VLM 超时

```
Error: VLM request timeout
```

- 检查网络连接
- 增加配置中的超时时间
- 尝试更小的模型

### 速率限制

```
Error: Rate limit exceeded
```

火山引擎有速率限制。考虑批量处理时添加延迟或升级套餐。

## 相关文档

- [火山引擎购买指南](./02-volcengine-purchase-guide.md) - API Key 获取
- [API 概览](../api/01-overview.md) - 客户端初始化
- [服务部署](./03-deployment.md) - Server 配置
- [上下文层级](../concepts/03-context-layers.md) - L0/L1/L2
