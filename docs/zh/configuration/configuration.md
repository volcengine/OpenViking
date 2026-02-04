# 配置

OpenViking 使用 JSON 配置文件（`ov.conf`）进行设置。配置文件支持 Embedding、VLM、Rerank、存储、解析器等多个模块的配置。

## 快速开始

在项目目录创建 `ov.conf`：

```json
{
  "user": "default_user",
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-seed-1-8-251228",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  },
  "storage": {
    "agfs": {
      "backend": "local",
      "path": "./data"
    },
    "vectordb": {
      "backend": "local",
      "path": "./data"
    }
  }
}
```

## 配置部分

### user

用户标识符，用于会话管理和数据隔离。

```json
{
  "user": "default_user"
}
```

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

详见 [Embedding 配置](./embedding.md)。

### vlm

用于语义提取的视觉语言模型。

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

详见 [LLM 配置](./llm.md)。

### rerank

用于搜索精排的 Rerank 模型。

```json
{
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-rerank-250615"
  }
}
```

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

配置值可以通过环境变量设置：

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

## 配置参考

### 完整 Schema

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

## 相关文档

- [Embedding 配置](./embedding.md) - Embedding 设置
- [LLM 配置](./llm.md) - LLM 设置
- [客户端](../api/client.md) - 客户端初始化
