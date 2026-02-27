# 配置

OpenViking 使用 JSON 配置文件（`ov.conf`）进行设置。配置文件支持 Embedding、VLM、Rerank、存储、解析器等多个模块的配置。

## 快速开始

在项目目录创建 `~/.openviking/ov.conf`：

```json
{
  "storage": {
    "workspace": "./data",
    "vectordb": {
      "name": "context",
      "backend": "local"
    },
    "agfs": {
      "port": 1833,
      "log_level": "warn",
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
    "max_concurrent": 10,
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
| `max_concurrent` | int | 最大并发 Embedding 请求数（`embedding.max_concurrent`，默认：`10`） |
| `provider` | str | `"volcengine"`、`"openai"`、`"vikingdb"` 或 `"jina"` |
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
- `jina`: Jina AI Embedding API

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

**jina provider 配置示例:**

```json
{
  "embedding": {
    "dense": {
      "provider": "jina",
      "api_key": "jina_xxx",
      "model": "jina-embeddings-v5-text-small",
      "dimension": 1024
    }
  }
}
```

可用 Jina 模型:
- `jina-embeddings-v5-text-small`: 677M 参数, 1024 维, 最大序列长度 32768 (默认)
- `jina-embeddings-v5-text-nano`: 239M 参数, 768 维, 最大序列长度 8192

**本地部署 (GGUF/MLX):** Jina 嵌入模型是开源的, 在 [Hugging Face](https://huggingface.co/jinaai) 上提供 GGUF 和 MLX 格式。可以使用任何 OpenAI 兼容的推理服务器 (如 llama.cpp、MLX、vLLM) 本地运行, 并将 `api_base` 指向本地端点:

```json
{
  "embedding": {
    "dense": {
      "provider": "jina",
      "api_key": "local",
      "api_base": "http://localhost:8080/v1",
      "model": "jina-embeddings-v5-text-nano",
      "dimension": 768
    }
  }
}
```

获取 API Key: https://jina.ai

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
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

**参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `api_key` | str | API Key |
| `model` | str | 模型名称 |
| `api_base` | str | API 端点（可选） |
| `thinking` | bool | 启用思考模式（仅对部分火山模型生效，默认：`false`） |
| `max_concurrent` | int | 语义处理阶段 LLM 最大并发调用数（默认：`100`） |

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

用于存储上下文数据 ，包括文件存储（AGFS）和向量库存储（VectorDB）。

#### 根级配置

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `workspace` | str | 本地数据存储路径（主要配置） | "./data" |
| `agfs` | object | agfs 配置 | {} |
| `vectordb` | object | 向量库存储配置 | {} |


```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "timeout": 10
    },
    "vectordb": {
      "backend": "local"
    }
  }
}
```

#### agfs

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `mode` | str | `"http-client"` 或 `"binding-client"` | `"http-client"` |
| `backend` | str | `"local"`、`"s3"` 或 `"memory"` | `"local"` |
| `url` | str | `http-client` 模式下的 AGFS 服务地址 | `"http://localhost:1833"` |
| `timeout` | float | 请求超时时间（秒） | `10.0` |
| `s3` | object | S3 backend configuration (when backend is 's3') | - |


**配置示例**

<details>
<summary><b>HTTP Client（默认）</b></summary>

通过 HTTP 连接到远程或本地的 AGFS 服务。

```json
{
  "storage": {
    "agfs": {
      "mode": "http-client",
      "url": "http://localhost:1833",
      "timeout": 10.0
    }
  }
}
```

</details>

<details>
<summary><b>Binding Client（高性能）</b></summary>

通过共享库直接使用 AGFS 的 Go 实现。

**配置**：
```json
{
  "storage": {
    "agfs": {
      "mode": "binding-client",
      "backend": "local"
    }
  }
}
```

</details>


##### S3 后端配置

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `bucket` | str | S3 存储桶名称 | null |
| `region` | str | 存储桶所在的 AWS 区域（例如 us-east-1, cn-beijing） | null |
| `access_key` | str | S3 访问密钥 ID | null |
| `secret_key` | str | 与访问密钥 ID 对应的 S3 秘密访问密钥 | null |
| `endpoint` | str | 自定义 S3 端点 URL，对于 MinIO 或 LocalStack 等 S3 兼容服务是必需的 | null |
| `prefix` | str | 用于命名空间隔离的可选键前缀 | "" |
| `use_ssl` | bool | 为 S3 连接启用/禁用 SSL（HTTPS） | true |
| `use_path_style` | bool | true 表示对 MinIO 和某些 S3 兼容服务使用 PathStyle；false 表示对 TOS 和某些 S3 兼容服务使用 VirtualHostStyle | true |

</details>

<details>
<summary><b>PathStyle S3</b></summary>
支持 PathStyle 模式的 S3 存储， 如 MinIO、SeaweedFS.

```json
{
  "storage": {
    "agfs": {
      "backend": "s3",
      "s3": {
        "bucket": "my-bucket",
        "endpoint": "s3.amazonaws.com",
        "region": "us-east-1",
        "access_key": "your-ak",
        "secret_key": "your-sk"
      }
    }
  }
}
```
</details>


<details>
<summary><b>VirtualHostStyle S3</b></summary>
支持 VirtualHostStyle 模式的 S3 存储， 如 TOS.

```json
{
  "storage": {
    "agfs": {
      "backend": "s3",
      "s3": {
        "bucket": "my-bucket",
        "endpoint": "s3.amazonaws.com",
        "region": "us-east-1",
        "access_key": "your-ak",
        "secret_key": "your-sk",
        "use_path_style": false
      }
    }
  }
}
```

</details>


#### vectordb

向量库存储的配置 

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `backend` | str | VectorDB 后端类型: 'local'（基于文件）, 'http'（远程服务）, 'volcengine'（云上VikingDB）或 'vikingdb'（私有部署） | "local" |
| `name` | str | VectorDB 的集合名称 | "context" |
| `url` | str | 'http' 类型的远程服务 URL（例如 'http://localhost:5000'） | null |
| `project_name` | str | 项目名称（别名 project） | "default" |
| `distance_metric` | str | 向量相似度搜索的距离度量（例如 'cosine', 'l2', 'ip'） | "cosine" |
| `dimension` | int | 向量嵌入的维度 | 0 |
| `sparse_weight` | float | 混合向量搜索的稀疏权重，仅在使用混合索引时生效 | 0.0 |
| `volcengine` | object | 'volcengine' 类型的 VikingDB 配置 | - |
| `vikingdb` | object | 'vikingdb' 类型的私有部署配置 | - |

默认使用本地模式
```
{
  "storage": {
    "vectordb": {
      "backend": "local"
    }
  }
}
```

<details>
<summary><b>volcengine vikingDB</b></summary>
支持火山引擎云上部署的 VikingDB

```json
{
  "storage": {
    "vectordb": {
      "name": "context",
      "backend": "volcengine",
      "project": "default",
      "volcengine": {
        "region": "cn-beijing",
        "ak": "your-access-key",
        "sk": "your-secret-key"
      }
  }
}
```
</details>



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
  "agent_id": "my-agent",
  "output": "table"
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `url` | 服务端地址 | （必填） |
| `api_key` | API Key 认证（root key 或 user key） | `null`（无认证） |
| `agent_id` | Agent 标识，用于 agent space 隔离 | `null` |
| `output` | 默认输出格式：`"table"` 或 `"json"` | `"table"` |

详见 [服务部署](./03-deployment.md)。

## server 段

将 OpenViking 作为 HTTP 服务运行时，在 `ov.conf` 中添加 `server` 段：

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "root_api_key": "your-secret-root-key",
    "cors_origins": ["*"]
  }
}
```

| 字段 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `host` | str | 绑定地址 | `0.0.0.0` |
| `port` | int | 绑定端口 | `1933` |
| `root_api_key` | str | Root API Key，启用多租户认证，不设则为开发模式 | `null` |
| `cors_origins` | list | CORS 允许的来源 | `["*"]` |

配置 `root_api_key` 后，服务端启用多租户认证。通过 Admin API 创建工作区和用户 key。不配置时为开发模式，不需要认证。

启动方式和部署详情见 [服务部署](./03-deployment.md)，认证详情见 [认证](./04-authentication.md)。

## 完整 Schema

```json
{
  "embedding": {
    "max_concurrent": 10,
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
    "api_base": "string",
    "thinking": false,
    "max_concurrent": 100
  },
  "rerank": {
    "provider": "volcengine",
    "api_key": "string",
    "model": "string"
  },
  "storage": {
    "workspace": "string",
    "agfs": {
      "backend": "local|s3|memory",
      "url": "string",
      "timeout": 10
    },
    "vectordb": {
      "backend": "local|remote",
      "url": "string",
      "project": "string"
    }
  },
  "server": {
    "host": "string",
    "port": 1933,
    "root_api_key": "string",
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
