# 配置

OpenViking 使用 JSON 配置文件（`ov.conf`）进行设置。配置文件支持 Embedding、VLM、Rerank、存储、解析器等多个模块的配置。

## 快速开始

在项目目录创建 `ov.conf`：

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
      "model": "doubao-embedding-vision-250615",
      "api_key": "{your-api-key}",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "provider": "volcengine",
      "input": "multimodal"
    }
  },
  "vlm": {
    "model": "doubao-seed-1-8-251228",
    "api_key": "{your-api-key}",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.0,
    "max_retries": 2,
    "provider": "volcengine"
  }
}

```

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

## 环境变量

```bash
export VOLCENGINE_API_KEY="your-api-key"
export OPENVIKING_DATA_PATH="./data"
```

## 配置优先级

1. 构造函数参数（最高）
2. Config 对象
3. 配置文件（`ov.conf`）
4. 环境变量
5. 默认值（最低）

## 编程式配置

```python
from openviking.utils.config import (
    OpenVikingConfig,
    StorageConfig,
    AGFSConfig,
    VectorDBBackendConfig,
    EmbeddingConfig,
    DenseEmbeddingConfig
)

config = OpenVikingConfig(
    storage=StorageConfig(
        agfs=AGFSConfig(
            backend="local",
            path="./custom_data",
        ),
        vectordb=VectorDBBackendConfig(
            backend="local",
            path="./custom_data",
        )
    ),
    embedding=EmbeddingConfig(
        dense=DenseEmbeddingConfig(
            provider="volcengine",
            api_key="your-api-key",
            model="doubao-embedding-vision-250615",
            dimension=1024
        )
    )
)

client = ov.AsyncOpenViking(config=config)
```

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
  "user": "string"
}
```

说明：
- `storage.vectordb.sparse_weight` 用于混合（dense + sparse）索引/检索的权重，仅在使用 hybrid 索引时生效；设置为 > 0 才会启用 sparse 信号。

## Server 配置

将 OpenViking 作为 HTTP 服务运行时，服务端从同一个 JSON 配置文件中读取配置（通过 `--config` 或 `OPENVIKING_CONFIG_FILE`）：

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "api_key": "your-secret-key",
    "cors_origins": ["*"]
  },
  "storage": {
    "path": "/data/openviking"
  }
}
```

Server 配置也可以通过环境变量设置：

| 变量 | 说明 |
|------|------|
| `OPENVIKING_HOST` | 服务主机地址 |
| `OPENVIKING_PORT` | 服务端口 |
| `OPENVIKING_API_KEY` | 用于认证的 API Key |
| `OPENVIKING_PATH` | 存储路径 |

详见 [服务部署](./03-deployment.md)。

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

- [火山引擎购买指南](./volcengine-purchase-guide.md) - API Key 获取
- [API 概览](../api/01-overview.md) - 客户端初始化
- [服务部署](./03-deployment.md) - Server 配置
- [上下文层级](../concepts/03-context-layers.md) - L0/L1/L2
